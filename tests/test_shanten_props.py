"""向听数性质测试(随机手牌,防止剪枝/缓存引入回归)。"""

import random
import unittest

from mj.tiles import W, counts
from mj.shanten import shanten, shanten_py, waits, ukeire, ukeire_py
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

    def test_shanten_minus1_iff_win_wild_rich(self):
        """财神加密采样的同构性质(2026-09-11):均匀随机几乎采不到
        多财神顺子前置形态,win.py 漏 [财,t,t+1] 分支因此逃过
        test_shanten_minus1_iff_win;强制 1~3 财神后高密度覆盖。"""
        rng = random.Random(20260914)
        from mj.tiles import counts as _counts  # noqa: F401(与 _hand 的墙采样区分)
        for _ in range(1500):
            wilds = rng.randrange(1, 4)
            wall = [t for t in range(34) for _ in range(4)]
            c = [0] * 34
            c[W] = wilds
            for t in rng.sample(wall, 14 - wilds):
                c[t] += 1
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


def _add(counts, t):
    c = list(counts)
    c[t] += 1
    return c


def _ukeire_full(counts, locked=0, visible=None):
    """剪枝前的原始实现(全量 34 枚举,纯 Python),作为差分 ground truth。"""
    from mj.win import is_win
    s = shanten_py(counts, locked)
    vis = counts if visible is None else visible
    if s <= 0:
        if s == 0:
            acc = [t for t in range(34) if is_win(_add(counts, t), locked)]
            return s, acc, sum(max(0, 4 - vis[t]) for t in acc)
        return s, [], 0
    acc = [t for t in range(34)
           if counts[t] < 4 and shanten_py(_add(counts, t), locked) < s]
    return s, acc, sum(max(0, 4 - vis[t]) for t in acc)


class TestUkeirePruning(unittest.TestCase):
    """ukeire 候选剪枝的差分与性质验证(2026-09-11)。

    剪枝:无财神且向听数 > 0 时,不在手且与任何手数牌同花色距离 >2
    的牌不可能是进张。安全性前提:有财神必须全量 fallback(财神可
    配任意新单张成对/补结构);七对在 13 张奇数手必有单张。

    默认路径已切 Rust 内核(mj/shanten.py 调度器);随机差分同时
    断言调度器(默认=Rust,未装扩展=Python)与 ukeire_py 两条路径
    均与全量枚举一致,monkeypatch/性质断言则针对 ukeire_py。
    """

    def _no_wild_hand(self, rng, n):
        wall = [t for t in range(33) for _ in range(4)]  # 0~32,无财神
        c = [0] * 34
        for t in rng.sample(wall, n):
            c[t] += 1
        return c

    def _assert_all_paths(self, c, locked, vis):
        want = _ukeire_full(c, locked, vis)
        for fn in (ukeire, ukeire_py):
            got = fn(c, locked, vis)
            self.assertEqual(
                got, want, f"{fn.__name__} 不符: {c} locked={locked} vis={vis}\n"
                f"  got ={got}\n  want={want}")
            self.assertEqual(got[1], sorted(got[1]), f"acc 非升序: {got}")

    def test_random_differential_no_wilds(self):
        """无财神随机手(locked 0..4):两条路径都与全量版三元组逐位相等。"""
        rng = random.Random(20260911)
        checked = 0
        for locked in range(5):
            n = 13 - 3 * locked
            for _ in range(120):
                c = self._no_wild_hand(rng, n)
                if shanten_py(c, locked) <= 0:
                    continue  # s<=0 走 is_win 分支,与剪枝无关
                vis = list(c)
                for t in rng.sample(range(34), 6):
                    vis[t] += rng.randrange(3)  # 随机已见,验证 _left 折算
                self._assert_all_paths(c, locked, vis)
                checked += 1
        self.assertGreater(checked, 100)

    def test_non_candidate_never_improves(self):
        """性质断言:被剪掉的牌摸到后向听数必不降(剪枝的数学命题)。"""
        import mj.shanten as sh
        rng = random.Random(20260912)
        checked = 0
        for locked in range(5):
            n = 13 - 3 * locked
            for _ in range(80):
                c = self._no_wild_hand(rng, n)
                s = shanten_py(c, locked)
                if s <= 0:
                    continue
                cands = set(sh._ukeire_candidates(c))
                for t in range(34):
                    if t in cands or c[t] >= 4:
                        continue
                    self.assertGreaterEqual(
                        shanten_py(_add(c, t), locked), s,
                        f"非候选 {t} 却降了向听: {c} locked={locked}")
                    checked += 1
        self.assertGreater(checked, 100)

    def test_wilds_full_fallback(self):
        """有财神时不得走剪枝路径,且结果与全量枚举一致。"""
        import mj.shanten as sh

        def _boom(counts):
            raise AssertionError("有财神时调用了候选剪枝")

        orig = sh._ukeire_candidates
        sh._ukeire_candidates = _boom
        try:
            cases = [
                # 6 对 + 1 财神:摸任意新牌财神配对成七对(最凶的反例)
                (counts("11m22m33m44m55m66m w"), 0),
                # 财神 + 多孤张
                (counts("147m258p1369sEEw"), 0),
                # 财神 + 搭子/对子
                (counts("123m456p67s99sEEw"), 0),
                # 多财神
                (counts("1479m258p3366sww"), 0),
                # locked=1 + 财神(10 张暗牌)
                (counts("123m456p78sEw"), 1),
            ]
            for c, locked in cases:
                self.assertEqual(sum(c), 13 - 3 * locked, f"张数错: {c}")
                self.assertEqual(
                    ukeire_py(c, locked), _ukeire_full(c, locked), f"hand={c}")
        finally:
            sh._ukeire_candidates = orig

    def test_wild_random_differential(self):
        """有财神随机手(locked 0..3):两条路径都与全量枚举一致。"""
        rng = random.Random(20260913)
        checked = 0
        for locked in range(4):
            n = 13 - 3 * locked
            for _ in range(60):
                c = [0] * 34
                c[W] = rng.randrange(1, 3)
                pool = [t for t in range(33) for _ in range(4)]
                for t in rng.sample(pool, n - c[W]):
                    c[t] += 1
                if shanten_py(c, locked) <= 0:
                    continue
                self._assert_all_paths(c, locked, None)
                checked += 1
        self.assertGreater(checked, 50)


if __name__ == "__main__":
    unittest.main()
