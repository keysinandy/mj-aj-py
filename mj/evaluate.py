"""自博弈评估:启发式 bot vs 随机 bot。"""

import random
import sys
import time

from mj.game import Game, HU
from mj.bot import choose_action


def random_action(g):
    # 模拟平台超时兜底:可胡时自动胡,其余动作随机
    acts = g.legal_actions()
    if HU in acts:
        return HU
    return random.choice(acts)


def run_games(bot_seats, n=200, seed0=0):
    """bot_seats: 长度 4 的列表,True=启发式 bot,False=随机。"""
    stats = {
        "wins": [0] * 4,
        "score": [0] * 4,
        "mults": {},
        "draws": 0,
    }
    t0 = time.time()
    for i in range(n):
        g = Game(seed=seed0 + i)
        while not g.done:
            seat = g.current_seat()
            act = choose_action(g, seat) if bot_seats[seat] else random_action(g)
            acts = g.legal_actions()
            if act not in acts:
                act = random.choice(acts)
            g.step(act)
        if g.result:
            seat, mult, _parts = g.result
            stats["wins"][seat] += 1
            stats["mults"][mult] = stats["mults"].get(mult, 0) + 1
        else:
            stats["draws"] += 1
        for s in range(4):
            stats["score"][s] += g.scores[s]
    stats["elapsed"] = time.time() - t0
    return stats


def report(name, stats, n):
    print(f"== {name} ({n} 局, {stats['elapsed']:.0f}s) ==")
    print(f"和牌分布: {stats['wins']}  流局: {stats['draws']}")
    print(f"总得分: {stats['score']}  平均: {[round(x / n, 2) for x in stats['score']]}")
    print(f"倍率分布: {dict(sorted(stats['mults'].items()))}")
    print()


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    # 座位 0 = 启发式 bot,其余随机
    s1 = run_games([True, False, False, False], n=n)
    report("1 bot vs 3 random", s1, n)
    # 四家全 bot(自博弈基线,和牌率应显著高于随机)
    s2 = run_games([True, True, True, True], n=n)
    report("4 bots", s2, n)
