"""Task 2.4 验收:对局记录读写 + 无 torch 确定性重放。"""

import sys

import pytest

from mj.game import Game
from mj.bot import choose_action
from mj.clientd.records import (
    write_game_record, load_game_record, replay_game_from_record,
    result_from_game, game_path,
)


def _played_record(seed=42, dealer=1):
    """用 legacy bot 四人跑一局,返回 {actions, result} + 结束状态。"""
    players = [lambda g, s: choose_action(g, s)] * 4
    g = Game(seed=seed, dealer=dealer)
    actions = []
    guard = 0
    while not g.done and guard < 5000:
        seat = g.current_seat()
        act = players[seat](g, seat)
        actions.append(int(act))
        g.step(act)
        guard += 1
    return {"actions": actions, "result": result_from_game(g),
            "final_scores": list(g.scores), "done": g.done}


def test_write_and_load_roundtrip(tmp_path):
    base = _played_record(seed=1)
    seats = [{"strategy": "bot", "evaluator": "legacy"} for _ in range(4)]
    roles = [0, 1, 2, 3]
    path = write_game_record(tmp_path, 3, seed=42, dealer=1, base=1,
                             you_cai_bi_kao=False, seats=seats, roles=roles,
                             actions=base["actions"], result=base["result"])
    assert path == game_path(tmp_path, 3)
    rec = load_game_record(path)
    assert rec["seed"] == 42 and rec["dealer"] == 1
    assert rec["actions"] == base["actions"]
    assert rec["result"] == base["result"]
    assert rec["roles"] == roles
    ind = load_game_record(path)
    assert ind["you_cai_bi_kao"] is False and ind["base"] == 1


def test_replay_matches_recorded_game(tmp_path):
    base = _played_record(seed=7)
    seats = [{"strategy": "bot", "evaluator": "legacy"}] * 4
    roles = [0, 1, 2, 3]
    path = write_game_record(tmp_path, 0, seed=7, dealer=1, base=1,
                             you_cai_bi_kao=False, seats=seats, roles=roles,
                             actions=base["actions"], result=base["result"])
    rec = load_game_record(path)
    g = replay_game_from_record(rec)
    assert g.done
    assert list(g.scores) == base["final_scores"]


def test_replay_no_torch(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "torch", None)
    seed = 99
    base = _played_record(seed=seed, dealer=1)
    actions = base["actions"]
    seats = [{"strategy": "bot", "evaluator": "legacy"}] * 4
    roles = [0, 1, 2, 3]
    path = write_game_record(tmp_path, 1, seed=seed, dealer=1, base=1,
                             you_cai_bi_kao=False, seats=seats, roles=roles,
                             actions=actions, result=base["result"])
    g = replay_game_from_record(load_game_record(path))
    assert g.done


def test_illegal_action_detected_in_replay(tmp_path):
    # 手工构造一个含非法动作的记录 → replay 显式报错
    seats = [{"strategy": "bot", "evaluator": "legacy"}] * 4
    roles = [0, 1, 2, 3]
    # 合法一局,篡改第一个动作为非法 HU(-75)
    good = _played_record(seed=5)["actions"]
    bad_actions = list(good)
    bad_actions[0] = -75
    path = write_game_record(tmp_path, 2, seed=5, dealer=0, base=1,
                             you_cai_bi_kao=False, seats=seats, roles=roles,
                             actions=bad_actions, result=None)
    with pytest.raises(ValueError):
        replay_game_from_record(load_game_record(path))