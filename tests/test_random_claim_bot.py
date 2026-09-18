"""Task 2.1 验收:随机 BOT(随机舍牌 + 必定吃碰杠)。

单测直接驱动 decide();集成测:全随机四人对局跑通且每步合法 + 跨进程
可复现(同 seed+seat → 同动作序列)。
"""

import random

import pytest

from mj.game import Game, PASS, PONG, KONG_OPEN, HU, CHOW_LOW, CHOW_MID
from mj.clientd.random_claim import (
    decide, make_random_claim_bot, bot_seed,
)


def test_hu_first_when_legal():
    assert decide([HU, 5], random.Random(0)) == HU


def test_peng_over_chow_and_pass():
    rng = random.Random(1)
    acts = [PONG, CHOW_LOW, PASS]
    assert decide(acts, rng) == PONG


def test_open_kong_over_chow():
    rng = random.Random(2)
    acts = [KONG_OPEN, CHOW_MID, PASS]
    assert decide(acts, rng) == KONG_OPEN


def test_random_chow_across_variants():
    rng = random.Random(3)
    acts = [CHOW_LOW, CHOW_MID, PASS]
    a = decide(acts, rng)
    assert a in (CHOW_LOW, CHOW_MID)
    # 可复现:同 seed 同输入重试一致
    assert decide(acts, random.Random(3)) == a


def test_pass_when_nothing_to_claim():
    assert decide([PASS], random.Random(4)) == PASS


def test_random_discard_deterministic():
    acts = [1, 2, 3, PASS]
    a1 = decide(acts, random.Random(5))
    assert a1 in (1, 2, 3)
    assert decide(acts, random.Random(5)) == a1


def test_bot_seed_stable_and_unique_by_seat():
    assert bot_seed(7, 0) != bot_seed(7, 1)
    assert bot_seed(7, 0) == bot_seed(7, 0)
    assert isinstance(bot_seed(7, 0), int)


def test_full_game_self_play_smoke():
    seed = 1234
    players = [make_random_claim_bot(seed, s) for s in range(4)]
    g = Game(seed=seed, dealer=0)
    exhausted = 0
    while not g.done and exhausted < 5000:
        seat = g.current_seat()
        act = players[seat](g, seat)
        legal = list(g.legal_actions())
        assert act in legal, (_seat_fail := f"illegal act {act} not in {legal}")
        g.step(act)
        exhausted += 1
    assert g.done, "对局应在耗尽前结束"


def test_full_game_reproducible_across_runs():
    def trajectory(seed):
        players = [make_random_claim_bot(seed, s) for s in range(4)]
        g = Game(seed=seed, dealer=0)
        acts = []
        while not g.done and len(acts) < 5000:
            seat = g.current_seat()
            act = players[seat](g, seat)
            acts.append((seat, act))
            g.step(act)
        return acts

    t1 = trajectory(2024)
    t2 = trajectory(2024)
    assert t1 == t2


def test_mixed_with_legacy_runs_clean():
    """随机 bot 与 legacy bot 混打不产生非法动作。"""
    from mj.bot import choose_action
    players = [make_random_claim_bot(9, s) for s in range(4)]
    players[1] = lambda g, seat: choose_action(g, seat)
    players[2] = lambda g, seat: choose_action(g, seat)
    g = Game(seed=9, dealer=1)
    guard = 0
    while not g.done and guard < 5000:
        seat = g.current_seat()
        act = players[seat](g, seat)
        legal = list(g.legal_actions())
        assert act in legal
        g.step(act)
        guard += 1