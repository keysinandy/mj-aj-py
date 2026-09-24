"""Mini-Suphx campaign 状态机 + 阶段编排(任务 12.1 / 13.11)。"""

import numpy as np
import pytest
import torch

from mj.training import campaign as cam
from mj.training.minisuphx_manifest import FEATURE_PUBLIC

import os

N_PLANES, N_SCALARS, N_ACTIONS = 75, 8, 109


def _write_shard(dir_, name, n, seed):
    Path = __import__("pathlib").Path
    rng = np.random.default_rng(seed)
    planes = rng.random((n, N_PLANES, 34), dtype=np.float32).astype(np.float16)
    scalars = rng.random((n, N_SCALARS), dtype=np.float32)
    mask = np.zeros((n, N_ACTIONS), dtype=bool)
    action = rng.integers(0, 34, size=n)
    mask[np.arange(n), action] = True
    seat = rng.integers(0, 4, size=n).astype(np.int8)
    score = rng.integers(-96, 96, size=(n, 4))
    np.savez(Path(dir_) / name, planes=planes, scalars=scalars, mask=mask,
             action=action.astype(np.int16), seat=seat, score=score)


@pytest.fixture
def data_dir(tmp_path):
    from pathlib import Path
    d = str(tmp_path / "bcdata")
    Path(d).mkdir()
    for k in range(2):
        _write_shard(d, f"shard_{k:05d}.npz", 32, seed=k)
    return d


@pytest.fixture
def state(tmp_path):
    out = str(tmp_path / "c")
    st = cam.create(out, "demo", blocks=1, width=8,
                    feature_contract_name=FEATURE_PUBLIC, git_commit="abc")
    return st


def test_create_roundtrip_and_fingerprint(state, tmp_path):
    assert state.phase == "created"
    assert state.feature_contract == FEATURE_PUBLIC
    assert state.blocks == 1 and state.width == 8
    p = os.path.join(state.out_dir, "campaign.json")
    assert os.path.exists(p)
    loaded = cam.CampaignState.load(p)
    assert loaded.fingerprint == state.fingerprint

    # 篡改 → 指纹不符,拒绝加载
    import json
    raw = json.load(open(p, encoding="utf-8"))
    raw["width"] = 64
    json.dump(raw, open(p, "w", encoding="utf-8"))
    with pytest.raises(ValueError, match="fingerprint"):
        cam.CampaignState.load(p)


def test_run_bc_sets_anchor(state, data_dir):
    best = cam.run_bc(state, data_dir, epochs=1, batch_size=16,
                      device="cpu", seed=0)
    assert os.path.exists(best)
    assert state.anchor is not None
    assert state.anchor["kind"] == "bc0"
    assert state.anchor["feature_contract"] == FEATURE_PUBLIC
    assert state.anchor["sha256"] == cam.sha256_file(best)


def test_run_ppo_round(state, data_dir):
    cam.run_bc(state, data_dir, epochs=1, batch_size=16, device="cpu", seed=0)
    ckpt, stats = cam.run_ppo_round(state, policy_ckpt=state.anchor["path"],
                                    rollout_n=24, seed=7, device="cpu",
                                    batch_size=8, ppo_epochs=1, bc_reg=0.5)
    assert os.path.exists(ckpt)
    assert stats["policy_version"] == 0          # 首期 policy 版本 0
    assert state.policy_version == 1             # 下一个待发布版本 1
    assert len(state.rollouts) == 1
    assert state.rollouts[0]["policy_version"] == 0
    entry = state.policies[-1]
    assert entry["version"] == 0
    assert entry["sha256"] == cam.sha256_file(ckpt)


def test_gen_dagger_records_round(tmp_path, state, data_dir):
    from pathlib import Path
    # 先 bc 有 anchor 作为 learned ckpt 源?不需要;learned_ckpt 仅记名。
    d = str(tmp_path / "dagger")
    meta = cam.gen_dagger(state, d, "D1", learned_ckpt="runs/x/best.pt",
                          learned_exec_prob=0.3, games=2, seed_start=0, hero=0)
    assert meta["transition_count"] > 0
    assert len(state.dagger_rounds) == 1
    shard = Path(d) / "D1" / "shard_00000.npz"
    assert shard.exists()
    z = np.load(shard)
    assert z["source_policy"].shape[0] == int(meta["transition_count"])


def test_run_gate_and_promote_do_not_crash(state, data_dir):
    cam.run_bc(state, data_dir, epochs=1, batch_size=16, device="cpu", seed=0)
    report = cam.run_gate(state, state.anchor["path"], level="smoke",
                          device="cpu", required_pairs=8)
    assert report["pairs"] == 8
    assert state.paired[-1]["level"] == "smoke"
    # promote 未必 superior(不保证晋级),但不应报错且 state 可序列化
    ck = cam.promote(state, state.anchor["path"], level="full",
                     device="cpu", required_pairs=8)
    assert isinstance(ck, dict)
    if state.champion is None:
        assert state.phase in ("ppo", "bc")


def test_local_smoke_closure(tmp_path):
    """单机小闭环:create → bc → dagger → ppo-smoke → gate(全部只用一个 session)。"""
    from pathlib import Path

    out = str(tmp_path / "smoke")
    st = cam.create(out, "smoke", blocks=1, width=8, git_commit="x")
    dat = str(tmp_path / "d")
    Path(dat).mkdir()
    _write_shard(dat, "shard_00000.npz", 40, seed=0)
    cam.run_bc(st, dat, epochs=1, batch_size=16, device="cpu", seed=0)
    ddir = str(tmp_path / "dd")
    cam.gen_dagger(st, ddir, "D1", learned_ckpt=st.anchor["path"],
                   learned_exec_prob=0.3, games=2)
    ck, _ = cam.run_ppo_round(st, policy_ckpt=st.anchor["path"], rollout_n=24,
                              seed=9, device="cpu", batch_size=8, ppo_epochs=1)
    # Resume the persisted campaign from the full policy checkpoint.  This
    # exercises optimizer/controller/RNG restore plus the next local merge.
    loaded = cam.CampaignState.load(os.path.join(out, "campaign.json"))
    ck2, resumed_stats = cam.run_ppo_round(
        loaded, policy_ckpt=ck, rollout_n=4, seed=10, device="cpu",
        batch_size=4, ppo_epochs=1)
    assert resumed_stats["policy_version"] == 1
    cam.run_gate(loaded, ck2, level="smoke", device="cpu", required_pairs=8)
    loaded = cam.CampaignState.load(os.path.join(out, "campaign.json"))
    assert loaded.phase == "ppo"
    assert len(loaded.policies) == 2
    assert len(loaded.rollouts) == 2
    assert len(loaded.paired) == 1
