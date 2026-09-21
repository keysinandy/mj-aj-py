import numpy as np
import pytest
import torch

from mj.game import HU, PASS
from mj.game import Game
from mj.bot import choose_action
from mj.hybrid_policy import HybridPolicy, IllegalLearnedAction
from mj.model import Net
from mj.training.merge_guard import validate_merge
from mj.training.minisuphx_manifest import (
    ACTION_SCOPE_DISCARD,
    FEATURE_CONTRACT,
    VALUE_CONTRACT,
)
from mj.training.ppo_learner import (
    PPOLearner,
    bc_kl_coefficient,
    compute_gae,
    load_shard_arrays,
    norm_entropy,
    shaping_coefficient,
)
from mj.training.ppo_rollout import gather_rollout, write_rollout_shard
from mj.training.rl_discard_env import MahjongDiscardEnv
from mj.training.distributed_jobs import WorkerCapabilities
from mj.training.distributed_rollout import execute_rl_rollout
from mj.training.merge_guard import load_result_manifests
from mj.training.worker_runtime import WorkerSupervisor


class _FakeGame:
    phase = "react"

    def __init__(self, action, legal):
        self.action = action
        self.legal = list(legal)

    def legal_actions(self):
        return self.legal


def test_hybrid_policy_keeps_special_actions_in_legacy_route():
    calls = []
    learned_calls = []
    policy = HybridPolicy(
        legacy_policy=lambda game, seat: calls.append((game, seat)) or HU,
        learned=lambda game, seat: learned_calls.append((game, seat)) or 0)
    game = _FakeGame(HU, [PASS, HU])

    decision = policy.select(game, 0)

    assert decision.selected_action == HU
    assert decision.route == "legacy"
    assert decision.learned_allowed is False
    assert len(calls) == 1
    assert learned_calls == []


def test_hybrid_policy_validates_learned_discard_and_strict_mode():
    game = _FakeGame(3, [0, 1, 3])
    policy = HybridPolicy(
        legacy_policy=lambda _game, _seat: 3,
        learned=lambda _game, _seat: 1)

    learned = policy.select(game, 0)
    assert learned.selected_action == 1
    assert learned.route == "learned_discard"

    with pytest.raises(IllegalLearnedAction):
        policy.resolve(game, 0, learned_action=2, strict=True)


def test_discard_env_legacy_trajectory_is_reproducible():
    first = MahjongDiscardEnv(seed=19)
    second = MahjongDiscardEnv(seed=19)
    first.reset(seed=19)
    second.reset(seed=19)

    for _ in range(4):
        assert first.pending_decision is not None
        assert second.pending_decision is not None
        action = first.pending_decision.legacy_action
        assert action == second.pending_decision.legacy_action
        _, _, _, done_first, _ = first.step(action, strict=True)
        _, _, _, done_second, _ = second.step(action, strict=True)
        assert first.game.hands == second.game.hands
        assert first.game.discards == second.game.discards
        assert first.game.melds == second.game.melds
        assert done_first == done_second
        if done_first:
            break

    assert first.discard_decisions == second.discard_decisions
    assert any(event["route"] == "legacy" for event in first.audit_log)
    assert all("phase" in event and "legal_actions" in event
               and "selected_action" in event for event in first.audit_log)


def test_hybrid_learned_legacy_matches_pure_legacy_trajectory():
    hybrid_game = Game(seed=31, dealer=2)
    legacy_game = Game(seed=31, dealer=2)
    policy = HybridPolicy(learned=choose_action)
    hero = 0

    while not hybrid_game.done:
        assert hybrid_game.current_seat() == legacy_game.current_seat()
        seat = hybrid_game.current_seat()
        legacy_action = choose_action(legacy_game, seat)
        if seat == hero:
            selected = policy.select(hybrid_game, seat).selected_action
        else:
            selected = choose_action(hybrid_game, seat)
        assert selected == legacy_action
        hybrid_game.step(selected)
        legacy_game.step(legacy_action)
        assert hybrid_game.hands == legacy_game.hands
        assert hybrid_game.discards == legacy_game.discards
        assert hybrid_game.melds == legacy_game.melds
        assert hybrid_game.scores == legacy_game.scores


def test_gae_resets_at_terminal_boundaries():
    advantage, returns = compute_gae(
        [0.0, 1.0, 0.0, 2.0], [0.1, 0.2, 0.3, 0.4],
        [False, True, False, True], gamma=1.0, lam=1.0)

    np.testing.assert_allclose(advantage, [0.9, 0.8, 1.7, 1.6])
    np.testing.assert_allclose(returns, [1.0, 1.0, 2.0, 2.0])


def test_rollout_records_bool_mask_and_complete_episode():
    policy = Net(blocks=1, width=8, n_planes=75)
    data = gather_rollout(policy, n_decisions=2, seed=23, max_episodes=4)

    assert data["planes"].shape[1:] == (75, 34)
    assert data["mask"].dtype == np.bool_
    assert data["mask"].shape[1] == 109
    assert np.all(data["mask"][np.arange(len(data["action"])), data["action"]])
    assert data["done"].any()
    assert data["episode_start"][0]


def test_ppo_learner_uses_bc_anchor_and_restores_state(tmp_path):
    torch.manual_seed(3)
    anchor = tmp_path / "bc_anchor.pt"
    base = Net(blocks=1, width=8, n_planes=75)
    torch.save({"state_dict": base.state_dict(), "blocks": 1, "width": 8}, anchor)
    learner = PPOLearner(
        policy_version=0, blocks=1, width=8, anchor=str(anchor),
        batch_size=4, ppo_epochs=1)
    planes = torch.rand(4, 75, 34)
    scalars = torch.rand(4, 8)
    mask = torch.zeros(4, 109, dtype=torch.bool)
    mask[:, :2] = True
    action = torch.tensor([0, 1, 0, 1])
    with torch.no_grad():
        logits, values = learner.net(planes, scalars)
        logp = torch.log_softmax(logits.masked_fill(~mask, float("-inf")), -1)
        old_log_prob = logp.gather(1, action[:, None]).squeeze(1)
    tensors = {
        "planes": planes, "scalars": scalars, "mask": mask,
        "action": action, "old_log_prob": old_log_prob,
        "old_value": values.detach(),
        "advantage": torch.tensor([1.0, -1.0, 0.5, -0.5]),
        "returns": torch.tensor([0.2, -0.2, 0.1, -0.1]),
    }

    stats = learner.update(tensors)
    assert all(np.isfinite(value) for value in stats.values()
               if isinstance(value, (int, float)))
    assert learner.optimizer.param_groups[0]["lr"] == 3e-5
    assert learner.optimizer.param_groups[1]["lr"] == 1e-4
    assert learner.discard_decisions == 4

    policy_dir = tmp_path / "policies"
    checkpoint, manifest = learner.save_policy(str(policy_dir), git_commit="abc")
    resumed = PPOLearner(
        policy_version=0, blocks=1, width=8, anchor=str(anchor),
        policy_path=checkpoint, batch_size=4, ppo_epochs=1)
    resumed.load_checkpoint(checkpoint)
    assert manifest["value_contract"] == VALUE_CONTRACT
    assert resumed.discard_decisions == learner.discard_decisions
    assert resumed.global_step == learner.global_step


def test_shard_loader_rejects_illegal_masked_action(tmp_path):
    path = tmp_path / "bad.npz"
    rows = 1
    np.savez(
        path,
        planes=np.zeros((rows, 75, 34), dtype=np.float16),
        scalars=np.zeros((rows, 8), dtype=np.float32),
        mask=np.zeros((rows, 109), dtype=bool),
        action=np.array([0], dtype=np.int16),
        old_log_prob=np.zeros(rows, dtype=np.float32),
        old_value=np.zeros(rows, dtype=np.float32),
        reward=np.zeros(rows, dtype=np.float32),
        done=np.ones(rows, dtype=bool),
    )
    with pytest.raises(ValueError, match="illegal masked action"):
        load_shard_arrays([str(path)])


def test_merge_guard_rejects_stale_or_mixed_rollouts():
    base = {
        "status": "SUCCEEDED", "campaign_id": "c", "job_id": "j1",
        "worker_id": "w", "git_commit": "g", "generation": 0,
        "policy_version": 7, "policy_fingerprint": "p7",
        "value_contract": VALUE_CONTRACT,
        "feature_contract": FEATURE_CONTRACT,
        "action_scope": ACTION_SCOPE_DISCARD,
    }
    ok, errors = validate_merge(
        [base], expected_policy_fingerprint="p7",
        expected_policy_version=7, expected_campaign_id="c")
    assert ok, errors

    stale = dict(base, job_id="j2", policy_fingerprint="p6")
    ok, errors = validate_merge(
        [base, stale], expected_policy_fingerprint="p7",
        expected_policy_version=7, expected_campaign_id="c")
    assert not ok
    assert any("stale policy fingerprint" in error for error in errors)


def test_worker_publishes_complete_rollout_provenance(tmp_path):
    anchor = tmp_path / "anchor.pt"
    base = Net(blocks=1, width=8, n_planes=75)
    torch.save({"state_dict": base.state_dict(), "blocks": 1, "width": 8}, anchor)
    learner = PPOLearner(policy_version=0, blocks=1, width=8,
                         anchor=str(anchor), batch_size=2, ppo_epochs=1)
    policy_path, policy_manifest = learner.save_policy(
        str(tmp_path / "policy"), git_commit="abc")
    job = {
        "job_id": "job-1", "campaign_id": "campaign-1", "generation": 0,
        "kind": "rl_rollout", "payload": {
            "policy_path": policy_path, "policy_version": 0,
            "policy_fingerprint": policy_manifest["fingerprint"],
            "n_decisions": 1, "max_episodes": 2, "blocks": 1, "width": 8,
            "seed_start": 11,
        },
    }
    cache = tmp_path / "cache"
    meta = execute_rl_rollout(job, cache_dir=str(cache))
    supervisor = WorkerSupervisor(
        None, WorkerCapabilities(worker_id="worker-1", git_commit="abc",
                                 roles=("rollout",)),
        cache_dir=str(cache), result_root=str(tmp_path / "results"),
        git_commit="abc")
    supervisor._publish(job, str(cache / "job-1"), meta)
    manifests = load_result_manifests(str(tmp_path / "results"), "campaign-1")
    ok, errors = validate_merge(
        manifests, expected_policy_fingerprint=policy_manifest["fingerprint"],
        expected_policy_version=0, expected_campaign_id="campaign-1",
        expected_git="abc")
    assert ok, errors
    assert manifests[0]["rows"] == meta["transition_count"]


def test_schedules_match_the_design_contract():
    assert bc_kl_coefficient(0) == 1.0
    assert bc_kl_coefficient(600_000) == 0.1
    assert bc_kl_coefficient(700_000) == 0.05
    assert shaping_coefficient(100_000) == 0.005
    assert shaping_coefficient(300_000) == 0.0
    probs = torch.tensor([[0.5, 0.5, 0.0]])
    mask = torch.tensor([[True, True, False]])
    assert torch.isfinite(norm_entropy(probs, mask)).all()
