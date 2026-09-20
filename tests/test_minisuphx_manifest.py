"""P0 契约层:manifest / value contract / seed domains / action scope / league anchor。"""

import pytest

from mj.training import minisuphx_manifest as m


def test_value_contract_matches_design():
    c = m.value_contract()
    assert c.version == "round-score-v2-normalized"
    assert c.scale == 96.0
    assert c.clip_normalized == 1.0      # clip(score/96, -1, 1)


def test_require_value_contract_rejects_old_scale():
    wrong = {"version": "value-v2", "score_units": "hero_round_score_points",
             "scale": 4.0, "clip_normalized": 1.0,
             "output_activation": "tanh", "inverse_transform": "multiply-scale-v1"}
    with pytest.raises(ValueError):
        m.require_value_contract(wrong, expected="round-score-v2-normalized")


def test_fingerprints_stable_and_change():
    a = m.MiniSuphxRunManifest(run_id="run1", git_commit="abc")
    b = m.MiniSuphxRunManifest(run_id="run1", git_commit="abc")
    assert a.fingerprint == b.fingerprint
    c = m.MiniSuphxRunManifest(run_id="run1", git_commit="abd")
    assert c.fingerprint != a.fingerprint


def test_final_test_forbidden_in_training():
    with pytest.raises(ValueError):
        m.MiniSuphxRunManifest(run_id="bad", allow_final_test=True)
    with pytest.raises(ValueError):
        m.MiniSuphxRunManifest(run_id="bad", action_scope="all-109")


def test_seed_domain_bounds():
    sd = m.SeedDomain()
    assert sd.contains(0) == "train"
    assert sd.contains(100) == "train"
    assert sd.contains(1_000_001) == "validation"
    assert sd.contains(2_000_100) == "final-test"
    assert sd.contains(500_000) is None      # 保留间隙,避免误入
    train_lo, val_hi = sd.train_lo, sd.validation_hi
    assert train_lo <= sd.train_hi < sd.validation_lo


def test_opponent_pool_anchors():
    gen0 = m.OpponentPoolManifest.gen0()
    assert gen0.components[0].kind == "legacy"
    assert round(sum(c.weight for c in gen0.components), 6) == 1.0
    gen1 = m.OpponentPoolManifest.gen1(bc_fingerprint="bc1")
    assert round(sum(c.weight for c in gen1.components), 6) == 1.0
    assert gen1.components[0].kind == "legacy"
    assert gen1.components[0].weight == 0.6
    assert gen1.fingerprint != gen0.fingerprint

    # legacy < 20% 拒绝
    with pytest.raises(ValueError):
        m.OpponentPoolManifest(generation=1, components=(
            m.OpponentComponent("legacy", "legacy-v1", 0.1, kind="legacy"),
            m.OpponentComponent("bc", "x", 0.6, kind="bc"),
            m.OpponentComponent("rl", "y", 0.3, kind="rl")))
    # Gen>0 无 BC>=10% 拒绝
    with pytest.raises(ValueError):
        m.OpponentPoolManifest(generation=1, components=(
            m.OpponentComponent("legacy", "legacy-v1", 0.9, kind="legacy"),
            m.OpponentComponent("rl", "y", 0.1, kind="rl")))


def test_policy_manifest_identity():
    p = m.PolicyManifest(policy_version=0, generation=0, git_commit="abc",
                         bc_anchor_fingerprint="anchor1")
    assert p.fingerprint
    assert p.action_scope == "discard-only-v1"
    with pytest.raises(ValueError):
        m.PolicyManifest(policy_version=-1, generation=0, git_commit="x")


def test_rollout_manifest_requires_policy_identity():
    base = dict(campaign_id="c", job_id="j", worker_id="w", policy_version=0,
                git_commit="abc")
    with pytest.raises((ValueError, TypeError)):
        m.RolloutManifest(**base)      # 缺 policy_fingerprint 必须失败
    r = m.RolloutManifest(**base, policy_fingerprint="pf")
    assert r.fingerprint