"""Task 3.1 验收:本地记录 → 帧数组预计算(不重跑决策/任取帧对账/非法报错)。"""

import pytest

from mj.game import Game
from mj.bot import choose_action
from mj.clientd.records import write_game_record, result_from_game
from mj.clientd.replay import local_frames


def _make_record(tmp_path, seed=55, dealer=3):
    players = [lambda g, s: choose_action(g, s)] * 4
    g = Game(seed=seed, dealer=dealer)
    actions = []
    guard = 0
    while not g.done and guard < 5000:
        seat = g.current_seat()
        a = players[seat](g, seat)
        actions.append(int(a))
        g.step(a)
        guard += 1
    seats = [{"strategy": "bot", "evaluator": "legacy"} for _ in range(4)]
    roles = [0, 1, 2, 3]
    path = write_game_record(tmp_path, 0, seed=seed, dealer=dealer, base=1,
                             you_cai_bi_kao=False, seats=seats, roles=roles,
                             actions=actions, result=result_from_game(g))
    return path, actions


def test_frames_length_and_no_decision_rerun(tmp_path):
    path, actions = _make_record(tmp_path, seed=1)
    frames = local_frames(path)
    assert len(frames) == len(actions) + 1  # 初始帧 + 每步一帧
    assert frames[0]["label"] == "初始"


def test_frames_match_manual_step(tmp_path):
    path, actions = _make_record(tmp_path, seed=7)
    frames = local_frames(path)
    # 手工从头推进,任取三步对比帧字段
    g = Game(seed=7, dealer=3)
    expected = []
    expected.append((list([list(v) for v in g.hands]),
                     [list(r) for r in g.discards],
                     g.live_wall_left()))
    for k, a in enumerate(actions, start=1):
        g.step(a)
        expected.append(([list(v) for v in g.hands],
                         [list(r) for r in g.discards],
                         g.live_wall_left()))
    for k in (0, 1, len(actions) // 2, len(actions)):
        fr = frames[k]
        want_hands, want_disc, want_wall = expected[k]
        assert fr["hands"] == want_hands
        assert fr["discards"] == want_disc
        assert fr["wall_remaining"] == want_wall
        assert fr["step"] == k


def test_illegal_action_raises(tmp_path):
    path, actions = _make_record(tmp_path, seed=5)
    rec = None
    import json as _json
    with open(path, encoding="utf-8") as f:
        rec = _json.load(f)
    rec["actions"] = list(rec["actions"])
    rec["actions"][0] = -75  # HU 非法处
    with pytest.raises(ValueError):
        local_frames(rec)


def test_full_info_hands_and_scores(tmp_path):
    path, _actions = _make_record(tmp_path, seed=9)
    frames = local_frames(path)
    end = frames[-1]
    assert end["info_kind"] == "local"
    assert end["hands"] is not None  # 本地全知:四家手牌可见
    assert len(end["scores"]) == 4
    assert end["current"]["phase"] == "done"