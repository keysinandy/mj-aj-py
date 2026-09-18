"""Task 2.3 验收:批量执行器(seed0+i / 座位庄家轮转 / 取消 / 一致)。

使用确定性策略(legacy + random)保证 X=1 与 X=4 逐动作一致;shape-v2
带预算属机器速度敏感,不在确定性断言范围(这正是记录存动作序列的原因)。
"""

import os
import time

import pytest

from mj.clientd.arena import run_arena, ArenaConfig
from mj.clientd.records import load_game_record, game_path


def _config(n_games, concurrency, seed0, opponents=("legacy", "random",
                                                   "random")):
    return {
        "n_games": n_games, "concurrency": concurrency, "seed0": seed0,
        "seats": [
            {"strategy": "bot", "evaluator": "legacy"},
        ] + [
            ({"strategy": o} if o == "random"
             else {"strategy": "bot", "evaluator": o}) for o in opponents
        ],
    }


@pytest.fixture(scope="module")
def module_tmp(tmp_path_factory):
    return tmp_path_factory.mktemp("arena")


def test_seed_distribution_and_rotation(module_tmp):
    cfg = _config(16, 4, seed0=100)
    out = run_arena(cfg, out_dir=str(module_tmp), batch_id="rot16")
    assert out["completed"] == 16 and out["skipped"] == 0
    assert not out["cancelled"]
    combos = []
    for i in range(16):
        rec = load_game_record(game_path(out["batch_dir"], i))
        main_seat = rec["roles"].index(0)
        dealer = rec["dealer"]
        combos.append((main_seat, dealer, rec["seed"]))
    # 主位 i%4,庄家 (i//4)%4,种子 seed0+i
    assert combos == [(i % 4, (i // 4) % 4, 100 + i) for i in range(16)]
    pair_set = {(m, d) for m, d, _ in combos}
    assert len(pair_set) == 16  # 16 组合均衡


def test_concurrency_consistency(module_tmp):
    cfg = _config(6, 4, seed0=200)
    out4 = run_arena(cfg, out_dir=str(module_tmp), batch_id="conc4")
    cfg1 = _config(6, 1, seed0=200)
    out1 = run_arena(cfg1, out_dir=str(module_tmp), batch_id="conc1")
    for i in range(6):
        r4 = load_game_record(game_path(out4["batch_dir"], i))
        r1 = load_game_record(game_path(out1["batch_dir"], i))
        assert r4["seed"] == r1["seed"]
        assert r4["actions"] == r1["actions"]


def test_cancellation_at_game_boundary(module_tmp):
    stop_flag = {"on": False}
    progress_hits = {"count": 0}

    def progress(completed, total, rate, index):
        progress_hits["count"] += 1
        if completed >= 3:
            stop_flag["on"] = True

    cfg = _config(12, 1, seed0=300)  # 单并发 → 进度按索引 0,1,2,...
    out = run_arena(cfg, out_dir=str(module_tmp), batch_id="cancel12",
                    is_stop=lambda: stop_flag["on"], progress=progress)
    assert out["cancelled"] is True
    # 已完成局全部保留且只有早期局
    assert out["completed"] >= 3
    assert out["completed"] + out["skipped"] == 12
    assert out["indexes"] == list(range(out["completed"]))


def test_seed0_null_randomized(module_tmp):
    cfg = _config(2, 1, seed0=None)
    out = run_arena(cfg, out_dir=str(module_tmp), batch_id="nullseed")
    assert cfg["seed0"] is not None
    assert out["seed0"] == cfg["seed0"]
    rec = load_game_record(out["game_paths"][0])
    assert rec["seed"] == out["seed0"]


def test_validate_requirements():
    with pytest.raises(ValueError):
        ArenaConfig({"n_games": 0, "concurrency": 1,
                     "seats": [{}] * 4}).validate()
    with pytest.raises(ValueError):
        ArenaConfig({"n_games": 4, "concurrency": 0,
                     "seats": [{}] * 4}).validate()
    with pytest.raises(ValueError):
        ArenaConfig({"n_games": 4, "concurrency": 1,
                     "seats": [{}] * 3}).validate()