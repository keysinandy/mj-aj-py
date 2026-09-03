"""向听数性质测试(随机手牌,防止剪枝/缓存引入回归)。"""

import random
import unittest

from mj.tiles import W
from mj.shanten import shanten, waits
from mj.win import is_win


def _hand(rng, n):
    wall = [t for t in range(34) for _ in range(4)]
    c = [0] * 34
    for t in rng.sample(wall, n):
        c[t] += 1
    return c


class TestShantenProperties(unittest.TestCase):
    N = 300

    def test_shanten_minus1_iff_win(self):
        rng = random.Random(20260903)
        for _ in range(self.N):
            c = _hand(rng, 14)
            s = shanten(c)
            if s == -1:
                self.assertTrue(is_win(c), f"shanten=-1 但非和牌: {c}")
            else:
                self.assertFalse(is_win(c), f"shanten={s} 却成和牌: {c}")

    def test_monotonicity_add_tile(self):
        """加任意一张牌,向听数最多降 1。"""
        rng = random.Random(20260904)
        for _ in range(self.N):
            c = _hand(rng, 13)
            s = shanten(c)
            for t in range(34):
                if c[t] >= 4:
                    continue
                c2 = list(c)
                c2[t] += 1
                self.assertGreaterEqual(
                    shanten(c2), s - 1,
                    f"加 {t} 后向听数下降超过 1: {c} → {c2}")

    def test_tenpai_has_waits(self):
        """向听 0 的 13 张手必有进张。"""
        rng = random.Random(20260905)
        for _ in range(self.N):
            c = _hand(rng, 13)
            if shanten(c) == 0:
                self.assertTrue(waits(c), f"听牌无进张: {c}")

    def test_locked_hand_not_inflated(self):
        """有副露的手牌向听数不超过无副露同结构(回归:旧剪枝界把
        locked>1 的手牌向听数算高,2026-09-03 修复)。"""
        # locked=3: 5 张暗牌 1m1m 8m8m + 财神 → 和牌
        c = [0] * 34
        c[0] = 2
        c[7] = 2
        c[W] = 1
        self.assertEqual(shanten(c, locked=3), -1)
        # locked=2: 2m3m3m 1p2p 4p5p + 财神 → 听牌(旧实现误算 1)
        c = [0] * 34
        c[1] = 1
        c[2] = 2
        c[9] = 1
        c[10] = 1
        c[12] = 1
        c[13] = 1
        c[W] = 1
        self.assertEqual(shanten(c, locked=2), 0)

    def test_cache_roundtrip(self):
        """缓存命中路径与首次计算结果一致。"""
        rng = random.Random(20260906)
        hands = []
        for _ in range(50):
            locked = rng.randrange(4)
            hands.append((_hand(rng, 13 - 3 * locked), locked))
        first = [shanten(c, k) for c, k in hands]
        second = [shanten(c, k) for c, k in hands]
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
