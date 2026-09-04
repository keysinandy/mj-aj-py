import unittest

from mj.tiles import counts, W
from mj.shanten import shanten, ukeire, waits


class TestShanten(unittest.TestCase):
    def test_tenpai(self):
        self.assertEqual(shanten(counts("123m456m789m123p5p")), 0)

    def test_one_shanten(self):
        # 3 面子 + 56s 搭子 + 2 孤张:打孤张后听 47s
        self.assertEqual(shanten(counts("123m456m789m56s9m2p")), 1)

    def test_two_shanten(self):
        # 3 面子 + 搭子 + 3 孤张
        self.assertEqual(shanten(counts("123m456m789m56s9m2p8s")), 1)
        self.assertEqual(shanten(counts("123m456m789m5s9m2p8s6p")), 2)

    def test_wild_completes_win(self):
        # 14 张:财神与 5p 成雀头 → 和牌
        self.assertEqual(shanten(counts("123m456m789m123p5sw")), -1)

    def test_two_wilds_are_meld(self):
        self.assertEqual(shanten(counts("123m456m789m123pww")), -1)

    def test_three_wilds_win(self):
        # 13 张:3 面子 + 5p + 3 财神 → 爆头式听牌(摸任意牌都胡)
        self.assertEqual(shanten(counts("123m456m789m5pwww")), 0)

    def test_wild_as_tenpai_wait(self):
        # 13 张:3 面子 + 46p 坎张搭子 + 9p 单张 + 财神
        # 财神补坎张成面子,9p 单张作雀头候选 → 听牌
        self.assertEqual(shanten(counts("123m456m789m4p6p9pw")), 0)

    def test_chiitoi_tenpai(self):
        self.assertEqual(shanten(counts("1122m3344p5566s7p")), 0)

    def test_chiitoi_one_shanten(self):
        self.assertEqual(shanten(counts("1122m3344p5567s8p")), 1)

    def test_chiitoi_six_pairs_single(self):
        # 6 对 + 1 单:七对听牌路线优于标准形
        self.assertEqual(shanten(counts("1199m1199p1199s1m")), 0)

    def test_locked_meld(self):
        self.assertEqual(shanten(counts("234m567m234p5s6s"), locked=1), 0)
        self.assertEqual(shanten(counts("234m567m234p5s"), locked=1), 0)
        self.assertEqual(shanten(counts("234m567m234p5s5s"), locked=1), -1)

    def test_pure_nine_gates(self):
        # 纯正九莲宝灯:听牌(听所有万子)
        self.assertEqual(shanten(counts("1112345678999m")), 0)

    def test_leftover_pairs_as_wait(self):
        # 多余对子可作搭子(摸第三张成刻)
        self.assertEqual(shanten(counts("22m33m4m7m2p3p6p7p9p4sE")), 3)

    def test_invalid_count(self):
        with self.assertRaises(ValueError):
            shanten(counts("123m456m789m5p"))  # 11 张


class TestWaits(unittest.TestCase):
    def test_shanpon(self):
        self.assertEqual(waits(counts("123m456m789m11p5s5s")), [9, 22, W])

    def test_wild_wait_anything(self):
        # 财神单吊:听全部 34 种
        self.assertEqual(len(waits(counts("123m456m789m123pw"))), 34)

    def test_kanchan(self):
        self.assertEqual(waits(counts("123m456m789m13p55s")), [10, W])


class TestUkeire(unittest.TestCase):
    def test_tenpai_counts(self):
        s, acc, total = ukeire(counts("123m456m789m11p5s5s"))
        self.assertEqual(s, 0)
        self.assertEqual(acc, [9, 22, W])  # 摸财神也能胡
        # 1p 剩 2,5s 剩 2,财神剩 4
        self.assertEqual(total, 8)
    def test_one_shanten_ukeire(self):
        s, acc, total = ukeire(counts("123m456m789m56s9m2p"))
        self.assertEqual(s, 1)
        self.assertIn(21, acc)  # 4s
        self.assertIn(24, acc)  # 7s
        self.assertIn(W, acc)  # 财神是进张
        c = counts("123m456m789m56s9m2p")
        self.assertEqual(total, sum(4 - c[t] for t in acc))

    def test_visible_no_double_subtraction(self):
        # visible 含手牌:持有 2 张的进张(7m 待第三张)未见 = 2,
        # 而非手牌被扣两次得 0(旧 bug 系统性贬低对子待刻结构)
        hand = counts("123m56m77m123p456p")
        s, acc, total = ukeire(hand, 0, hand)
        self.assertEqual((s, acc), (0, [3, 6, 33]))  # 4m / 7m / 白
        self.assertEqual(total, 4 + 2 + 4)

    def test_visible_subtracts_seen(self):
        hand = counts("123m56m77m123p456p")
        vis = list(hand)
        vis[3] += 1  # 4m 已见 1 张(牌河)
        vis[33] += 3  # 白已见 3 张(他家打出/副露)
        _, _, total = ukeire(hand, 0, vis)
        self.assertEqual(total, 3 + 2 + 1)

    def test_visible_prediscard_hand(self):
        # choose_discard 场景:vis = 弃牌前完整手牌(含将打出的候选);
        # 弃牌只是手→牌河,4-vis 即弃后的精确未见数
        full = counts("123m456m789m123p55p")  # 14 张,摸 5p
        c = list(full)
        c[13] -= 1  # 拟弃 5p
        s, acc, total = ukeire(c, 0, full)
        self.assertEqual((s, acc), (0, [13, 33]))  # 5p / 白
        # 真实未见 5p:4 - 手 1 - 牌河 1 = 2
        self.assertEqual(total, 2 + 4)


if __name__ == "__main__":
    unittest.main()
