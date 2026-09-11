"""启发式 bot 弃牌排序的固定牌例回归。

背景(2026-09-11):choose_discard 旧实现按 (向听, 财神, 喂牌, tile编号)
排序,同向听、同风险时由 tile 编号决定——数牌编号 0~26、字牌 27~33,
于是孤张字牌被留着、数牌搭子反被先拆;shanten>1 时更是完全不比较
进张;贴近听牌时先按编号截 top_k=4 再算进张,字牌可能被挤出候选。

现口径(优先级从高到低):向听数(硬约束)→ 财神保护 → 进张数 →
牌型结构损失 → 喂牌风险 → tile 编号(仅稳定排序)。
"""

import random
import unittest

from mj.bot import choose_discard
from mj.game import Game
from mj.shanten import shanten
from mj.tiles import W, counts


def _game(spec):
    """构造只有自家手牌的最小对局(无副露、牌河为空)。"""
    g = Game.__new__(Game)
    hand = counts(spec)
    assert sum(hand) == 14, (spec, sum(hand))
    g.hands = [hand, [0] * 34, [0] * 34, [0] * 34]
    g.melds = [[] for _ in range(4)]
    g.discards = [[] for _ in range(4)]
    return g


def _shanten_after(hand, t):
    c = list(hand)
    c[t] -= 1
    return shanten(c, 0)


def _min_shanten(hand):
    return min(_shanten_after(hand, t) for t in range(34) if hand[t])


class TestDiscardShape(unittest.TestCase):
    def test_isolated_honor_beats_number_taatsu(self):
        """孤张字牌优先于数牌搭子——旧实现因编号小先拆 3万。"""
        # 3万 44万 567万 1筒5筒 3689条 东 发:打东进张 95,打3万仅 70
        for spec in ("3m44m567m1p5p3689sEF", "3m444m5689m66p7p18sE"):
            self.assertEqual(choose_discard(_game(spec), 0), 27, spec)

    def test_honor_pair_not_broken(self):
        """东是唯一对子(雀头)时不能拆——拆对掉向听/掉进张。"""
        g = _game("28m123459p1568sEE")
        pick = choose_discard(g, 0)
        self.assertNotEqual(pick, 27)
        self.assertEqual(_shanten_after(g.hands[0], pick), _min_shanten(g.hands[0]))

    def test_shanten_first_allows_number_discard(self):
        """字牌全部成对时,最小向听弃牌落在数牌上,不能被"字牌优先"带偏。"""
        g = _game("1488m15p2377sEESS")
        pick = choose_discard(g, 0)
        self.assertEqual(pick, 0)  # 1万
        self.assertEqual(_shanten_after(g.hands[0], pick), _min_shanten(g.hands[0]))

    def test_ukeire_outranks_shape(self):
        """同向听时进张多的牌优先:打东 62 张,其余候选 46~50。"""
        g = _game("299m3p678p577899sE")
        self.assertEqual(choose_discard(g, 0), 27)

    def test_tenpai_keeps_tenpai(self):
        """听牌后打无关牌保持听牌,不为打孤张字牌破听。"""
        g = _game("E11m56m234p678p123s")
        pick = choose_discard(g, 0)
        self.assertEqual(pick, 27)
        self.assertEqual(_shanten_after(g.hands[0], pick), 0)


class TestDiscardInvariants(unittest.TestCase):
    def _random_hands(self, n, seed0, jokers=0):
        rng = random.Random(seed0)
        for _ in range(n):
            hand = [0] * 34
            hand[W] = jokers
            for _ in range(14 - jokers):
                while True:
                    t = rng.randrange(33)  # 0~32,不含财神
                    if hand[t] < 4:
                        hand[t] += 1
                        break
            yield hand

    def test_never_regresses_shanten(self):
        """向听数是硬约束:选中牌必须可达全候选最小向听,且确实在手里。"""
        for hand in self._random_hands(40, 20260911):
            g = Game.__new__(Game)
            g.hands = [list(hand), [0] * 34, [0] * 34, [0] * 34]
            g.melds = [[] for _ in range(4)]
            g.discards = [[] for _ in range(4)]
            pick = choose_discard(g, 0)
            self.assertGreater(hand[pick], 0)
            self.assertEqual(_shanten_after(hand, pick), _min_shanten(hand))

    def test_joker_never_discarded_when_alternative(self):
        """只要存在非财神的最小向听候选,就不该打财神。"""
        for hand in self._random_hands(40, 4242, jokers=1):
            g = Game.__new__(Game)
            g.hands = [list(hand), [0] * 34, [0] * 34, [0] * 34]
            g.melds = [[] for _ in range(4)]
            g.discards = [[] for _ in range(4)]
            best = _min_shanten(hand)
            alts = [t for t in range(33) if hand[t] and _shanten_after(hand, t) == best]
            if not alts:
                continue
            self.assertNotEqual(choose_discard(g, 0), W)


if __name__ == "__main__":
    unittest.main()
