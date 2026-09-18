"""随机 BOT(本地竞技场对手位):随机舍牌 + 必定吃碰杠。

规则:
- 胡合法必胡;
- 出牌回合(有弃牌动作):随机选一张合法弃牌(不主动暗杠/加杠);
- 反应窗:优先级 碰 > 明杠 > 吃(多种吃法随机取一)> 过。

可复现:rng 由 (seed, seat) 派生(``seed * 4 + seat``,整数稳定,不受进程
哈希随机化影响),同一 (game, seat) 决策序列可跨进程复现。
"""

from __future__ import annotations

import random

from ..game import PASS, PONG, KONG_OPEN, HU, CHOW_LOW, CHOW_MID, CHOW_HIGH

__all__ = ["make_random_claim_bot", "bot_seed", "decide"]

_CHOW_ACTIONS = frozenset((CHOW_LOW, CHOW_MID, CHOW_HIGH))


def bot_seed(seed, seat):
    return int(seed) * 4 + int(seat)


def decide(acts, rng):
    """纯决策:输入合法集与 rng,输出所选动作(供单测直接驱动)。"""
    acts = set(int(a) for a in acts)
    if HU in acts:
        return HU
    discards = [a for a in range(34) if a in acts]
    if discards:
        return rng.choice(sorted(discards))
    # 反应窗
    if PONG in acts:
        return PONG
    if KONG_OPEN in acts:
        return KONG_OPEN
    chows = sorted(acts & _CHOW_ACTIONS)
    if chows:
        return rng.choice(chows)
    return PASS


def make_random_claim_bot(seed, seat):
    """构造 (g, seat) -> action 玩家,供 evaluate.run_games/_play 使用。"""
    rng = random.Random(bot_seed(seed, seat))

    def play(g, seat):
        return decide(g.legal_actions(), rng)

    return play