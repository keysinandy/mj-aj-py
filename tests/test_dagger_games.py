"""DAgger 数据生成 (design 5):标签恒来自 T0、provenance、mix executor。"""

import numpy as np
import pytest

from mj.training import dagger_games as dg
from mj.training.distributed_bc import HANDLERS


def _legal_discard(g, seat):
    """一个合法的普通弃牌(取最小合法动作;若含弃牌优先)。"""
    legal = tuple(g.legal_actions())
    for a in legal:
        if 0 <= a <= 33:
            return int(a)
    return int(legal[0])


def _picky_learned(g, seat):
    """故意与 teacher 一般不同:若 0 合法就弃 0,否则取合法作。"""
    legal = tuple(int(a) for a in g.legal_actions())
    return 0 if 0 in legal else int(legal[0])


def test_dagger_deterministic():
    a = dg.generate_dagger_game(9, learned_exec_prob=0.5,
                                learned_policy=_legal_discard)
    b = dg.generate_dagger_game(9, learned_exec_prob=0.5,
                                learned_policy=_legal_discard)
    for key in ("planes", "scalars", "mask", "action", "seat", "score",
                "teacher_action", "executed_action", "disagreement",
                "source_policy"):
        assert (a[key] == b[key]).all(), key


def test_dagger_labels_always_from_teacher():
    d = dg.generate_dagger_game(7, learned_exec_prob=1.0,
                                learned_policy=_picky_learned)
    n = len(d["action"])
    assert n > 0
    assert (d["action"] == d["teacher_action"]).all()
    # action 是合法教师动作
    for i in range(n):
        assert d["mask"][i][d["action"][i]]


def test_dagger_records_mixed_executor_and_disagreement():
    d = dg.generate_dagger_game(11, learned_exec_prob=1.0,
                                learned_policy=_picky_learned)
    src = d["source_policy"]
    assert (src == np.asarray("learned", dtype="U16")).any() or \
        (src == np.asarray("learned_fallback", dtype="U16")).any()
    # 有些状态 teacher 与 learned 不同 → disagreement 存在且>0
    assert d["disagreement"].max() > 0
    assert d["disagreement"].shape == (len(d["action"]),)
    # fallback(非法 learned)会被拒绝并 count 不 into 纯 learned
    bad_learned = lambda g, s: -999  # noqa: E731 永远非法
    d2 = dg.generate_dagger_game(13, learned_exec_prob=1.0,
                                 learned_policy=bad_learned)
    # learned_fallback 时会落到 legacy,disagreement==0
    assert set(np.unique(d2["source_policy"].astype(str))).issubset(
        {"learned_fallback"})
    assert float(d2["disagreement"].max()) == 0.0


def test_no_learned_policy_means_pure_legacy_labels():
    d = dg.generate_dagger_game(5, learned_exec_prob=1.0, learned_policy=None)
    n = len(d["action"])
    if n > 0:
        # 无 learned → 执行者即 teacher,无 disagreement
        assert float(d["disagreement"].max()) == 0.0
        assert (d["action"] == d["executed_action"]).all()


def test_executor_mix_rate_shifts_source_distribution():
    # 低 learned 概率 → 大部分样本由 legacy 执行
    low = dg.generate_dagger_game(3, learned_exec_prob=0.0,
                                  learned_policy=_picky_learned)
    assert (low["source_policy"] == np.asarray("legacy", dtype="U16")).all()
    # 高概率 → 存在 learned 执行样本
    high = dg.generate_dagger_game(4, learned_exec_prob=1.0,
                                   learned_policy=_picky_learned)
    src = set(np.unique(high["source_policy"].astype(str)))
    assert src & {"learned", "learned_fallback"}


def test_execute_dagger_job_writes_shard(tmp_path):
    npz = tmp_path / "out.npz"
    job = {
        "job_id": "j1", "campaign_id": "c1", "kind": "dagger_games",
        "generation": 0,
        "payload": {
            "seed_start": 0, "games": 3, "learned_exec_prob": 0.5,
            "teacher_evaluator": "legacyV2-offline",
        },
    }
    meta = dg.execute_dagger_job(job, cache_dir=str(tmp_path))
    saved = tmp_path / "j1" / "rollout.npz"
    assert saved.exists()
    z = np.load(saved)
    assert z["action"].shape[0] > 0
    assert "source_policy" in z
    assert z["big_hand_shadow"].shape[1] == 10
    assert meta["teacher_policy"] == "legacy"
    assert meta["teacher_fingerprint"]
    assert meta["feature_contract"] == "public-v1"
    assert meta["transition_count"] == int(len(z["action"]))
    assert "dagger_games" in HANDLERS