import random
import unittest
from unittest.mock import patch

import mj.shanten as shanten_module
from mj.tiles import counts, W
from mj.shanten import (shanten, ukeire, waits, baotou_ukeire,
                        kernel_runtime_diagnostic, format_kernel_diagnostic)
from mj.win import is_baotou_wait


class TestShanten(unittest.TestCase):
    def test_old_weighted_kernel_abi_is_reported_as_degraded(self):
        with patch.object(shanten_module, "_FORCE_PY", False), \
                patch.object(shanten_module, "_rust_shanten", object()), \
                patch.object(shanten_module, "_rust_ukeire", object()), \
                patch.object(shanten_module,
                             "WEIGHTED_TWO_PLY_KERNEL_VERSION",
                             "rust-weighted-two-ply-v2"):
            diagnostic = kernel_runtime_diagnostic()
            message = format_kernel_diagnostic()
        self.assertTrue(diagnostic["degraded"])
        self.assertEqual(diagnostic["reason"],
                         "weighted_kernel_version_mismatch")
        self.assertFalse(diagnostic["weighted_kernel_compatible"])
        self.assertEqual(diagnostic["weighted_kernel_required"],
                         "rust-weighted-two-ply-v4")
        self.assertIn("required=rust-weighted-two-ply-v4", message)

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


class TestBaotouUkeire(unittest.TestCase):
    """爆头进张(openspec baotou-piao-aware-discard):摸 t 后可弃成
    爆头听(听任意)的集合与未见加权和。"""

    def test_baotou_wait_hand_all_tiles(self):
        # 3 面子 + 555p + 财神:弃 0 也听任意 → 全部牌是进张
        hand = counts("123m456m789m555pw")
        acc, u1 = baotou_ukeire(hand, 0, hand)
        self.assertEqual(len(acc), 34)
        self.assertEqual(u1, sum(4 - hand[t] for t in range(34)))

    def test_tier1_hand_known_set(self):
        # 持财神普通听牌:摸 4p/5p/7p/财神 后弃 5p/6p 可成爆头听,
        # 6p(补对子)与 8p(拆不出搭子)不在集合内——与普通胡牌张
        # [4p..8p, W] 交集更窄,度量确实不同。
        hand = counts("123m456m789m5p5p6pw")
        self.assertFalse(is_baotou_wait(hand))
        acc, u1 = baotou_ukeire(hand, 0, hand)
        self.assertEqual(acc, [12, 13, 15, 33])  # 4p/5p/7p/白
        # 4p 未见 4,5p 手持 2 → 2,7p 未见 4,白手持 1 → 3
        self.assertEqual(u1, 4 + 2 + 4 + 3)

    def test_no_wild_prunes_to_same_set(self):
        # 无财神手牌同样有爆头进张概念(如七对向豪华推进的形状),
        # 剪枝口径与有财神一致——此处只验证不抛错且自洽。
        hand = counts("123m456m789m5p5p6p6p")
        acc, u1 = baotou_ukeire(hand, 0, hand)
        self.assertIsInstance(acc, list)

    def test_visible_no_double_subtraction(self):
        # 自持对子的进张(5p)未见 = 2,不是被扣两次的 0
        hand = counts("123m456m789m5p5p6pw")
        vis = list(hand)
        vis[13] += 2  # 牌河再见 2 张 5p → 未见归零,但集合不变
        acc, u1 = baotou_ukeire(hand, 0, vis)
        self.assertEqual(acc, [12, 13, 15, 33])
        self.assertEqual(u1, 4 + 0 + 4 + 3)

    def test_pruning_matches_full_enumeration(self):
        # 随机差分:剪枝候选与全量 34 枚举的爆头进张集合逐一相等
        # (CLAUDE.md 铁律——shanten 类优化必须配随机差分验证)。
        # W<3 剪枝(远离候选集的牌不可能是爆头进张——唯一途径是
        # (W,W,t) 刻子 + 第 3 财神配对,需 W>=3);W>=3 函数走全量
        # 枚举,与参考同路径。Rust 版同口径(scripts/rust_parity.py)。
        rng = random.Random(20260918)
        tested = 0
        while tested < 80:
            locked = rng.randint(0, 3)
            n = 13 - 3 * locked
            wilds = rng.choice([0, 1, 2, 3])
            hand = [0] * 34
            tiles = [rng.randrange(33) for _ in range(n - wilds)]
            for t in tiles:
                hand[t] += 1
            hand[W] = wilds
            if any(c > 4 for c in hand):
                continue  # 物理不可能的手牌
            tested += 1
            full = []
            for t in range(34):
                if hand[t] >= 4:
                    continue
                c = list(hand)
                c[t] += 1
                for d in range(34):
                    if d == t or c[d] == 0:
                        continue
                    c[d] -= 1
                    ok = is_baotou_wait(c, locked)
                    c[d] += 1
                    if ok:
                        full.append(t)
                        break
            acc, _u1 = baotou_ukeire(hand, locked)
            self.assertEqual(acc, full, msg=f"hand={hand} locked={locked}")

    def test_three_wilds_meld_branch_all_wait(self):
        # W=3 面子路径:3 面子 + 5p + WWW——去一财神后 (5p,W,W→555p)
        # 补第 4 面子,听任意,全牌进张。W>=3 的全量枚举分支为保守
        # 冗余(可证:非爆头听的 W=3 手远牌必不进张),差分已覆盖。
        hand = counts("123m456m789m5pwww")
        acc, u1 = baotou_ukeire(hand, 0, hand)
        self.assertEqual(len(acc), 34)
        self.assertEqual(u1, sum(4 - hand[t] for t in range(34)))


if __name__ == "__main__":
    unittest.main()
