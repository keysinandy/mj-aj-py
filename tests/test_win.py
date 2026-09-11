import unittest

from mj.tiles import counts
from mj.win import is_win, is_chiitoi, waiting_tiles, is_baotou_wait, is_baotou
from mj.scoring import hand_multiplier, settle


class TestWin(unittest.TestCase):
    def test_pinhu(self):
        self.assertTrue(is_win(counts("123m456m789m123p55p")))

    def test_wrong_count(self):
        self.assertFalse(is_win(counts("123m456m789m123p5p")))

    def test_not_win_two_singles(self):
        self.assertFalse(is_win(counts("123m456m789m123p56p")))

    def test_wild_fills_chow(self):
        self.assertTrue(is_win(counts("12m456m789m123p55pw")))

    def test_wild_before_smallest_in_chow(self):
        """最小自然牌位于顺子第 2/3 位、前置位用财神(2026-09-11 修复)。

        旧版 _melds 只枚举 t 作顺子首位,漏掉 [财,t,t+1]/[财,财,t],
        shanten=-1 而 is_win=False,合法自摸胡被 game.py 拒绝。
        """
        # [白(1万) 2万 3万][456万][789万][567筒][东东]
        self.assertTrue(is_win(counts("23m456m789m567pEE w")))
        # [8筒9筒白(7筒)][6条6条白][789万][456筒][5万5万]
        self.assertTrue(is_win(counts("55m789m456p89p66s w w")))
        # 同形听牌(13 张双财神):摸 9筒 成胡——旧版 waiting_tiles 漏 9筒
        self.assertIn(17, waiting_tiles(counts("55m789m456p8p66s w w")))

    def test_wild_fills_pong(self):
        self.assertTrue(is_win(counts("1mww456m789m123p55p")))

    def test_wild_pair(self):
        self.assertTrue(is_win(counts("123m456m789m123p5pw")))

    def test_locked_meld_reduces_need(self):
        self.assertTrue(is_win(counts("234m567m234p55p"), locked=1))
        self.assertFalse(is_win(counts("234m567m234p55p"), locked=0))

    def test_chiitoi(self):
        self.assertEqual(is_chiitoi(counts("11m22m33m44m55m66m77p")), (True, 0))

    def test_luxury_chiitoi_quad(self):
        self.assertEqual(is_chiitoi(counts("1111m22m33m44m55m66m")), (True, 1))

    def test_luxury_chiitoi_wild(self):
        # 财神补齐的四张不算豪华组(平台 fan-calc 口径)
        self.assertEqual(is_chiitoi(counts("111m22m33m44m55m66mw")), (True, 0))

    def test_chiitoi_win(self):
        self.assertTrue(is_win(counts("11m22m33m44m55m66m77p")))

    def test_waiting_tiles(self):
        # 345p6p:听 3p/6p 做雀头;财神百搭,摸财神也成
        self.assertEqual(waiting_tiles(counts("123m456m789m345p6p")), [11, 14, 33])

    def test_waiting_tiles_kanchan(self):
        # 13p 坎张听 2p,另含财神
        self.assertEqual(waiting_tiles(counts("123m456m789m13p55p")), [10, 33])

    def test_baotou_wait_positive(self):
        self.assertTrue(is_baotou_wait(counts("123m456m789m123pw")))

    def test_baotou_wait_three_wild(self):
        # 3 个财神时任意牌也能胡:雀头(5p+财)+刻子(X+财+财)
        self.assertTrue(is_baotou_wait(counts("123m456m789m5pwww")))

    def test_baotou_wait_negative(self):
        self.assertFalse(is_baotou_wait(counts("123m456m789m123p5p")))

    def test_is_baotou(self):
        self.assertTrue(is_baotou(counts("123m456m789m123pw")))  # 听任意牌
        self.assertFalse(is_baotou(counts("123m456m789m123p5p")))  # 只听 5p
        # 恰持 4 张白板听任意:同样计爆头(v21 裁定,2026-09-08 fan-calc 对拍)
        self.assertTrue(is_baotou(counts("123m456m789m wwww")))

    def test_is_baotou_with_melds(self):
        # 副露 3 副后暗牌 4 张:1p2p + 双财,摸任意即胡
        self.assertTrue(is_baotou(counts("1p2pww"), 3))

    def test_baotou_waiting_all_34(self):
        self.assertEqual(len(waiting_tiles(counts("123m456m789m123pw"))), 34)


class TestScoring(unittest.TestCase):
    def test_pinhu_mult(self):
        m, parts = hand_multiplier(
            counts("123m456m789m123p55p"), counts("123m456m789m123p5p"), 0
        )
        self.assertEqual(m, 1)
        self.assertEqual(parts, ["平胡"])

    def test_baotou_mult(self):
        m, parts = hand_multiplier(
            counts("123m456m789m123p5pw"), counts("123m456m789m123pw"), 0
        )
        self.assertEqual(m, 2)
        self.assertEqual(parts, ["平胡", "爆头"])

    def test_gangkai_mult(self):
        m, parts = hand_multiplier(
            counts("123m456m789m123p55p"), counts("123m456m789m123p5p"), 0, 1
        )
        self.assertEqual(m, 2)
        self.assertEqual(parts, ["平胡", "杠开"])

    def test_double_kong_mult(self):
        m, parts = hand_multiplier(
            counts("123m456m789m123p55p"), counts("123m456m789m123p5p"), 0, 2
        )
        self.assertEqual(m, 4)
        self.assertEqual(parts, ["平胡", "连杠×2"])

    def test_gangbao_mult(self):
        m, parts = hand_multiplier(
            counts("123m456m789m123p5pw"), counts("123m456m789m123pw"), 0, 1
        )
        self.assertEqual(m, 4)
        self.assertEqual(parts, ["平胡", "杠开", "爆头"])

    def test_gang_piao_chain_mult(self):
        # 一杠一飘组合链:杠飘 ×4 叠加爆头 = ×8
        m, parts = hand_multiplier(
            counts("123m456m789m123p5pw"), counts("123m456m789m123pw"), 0, 2, 1
        )
        self.assertEqual(m, 8)
        self.assertEqual(parts, ["平胡", "杠飘链×2", "爆头"])

    def test_locked_gangbao(self):
        # 爆头听牌时明杠,补牌必胡:locked=1,暗牌 10 张等待、11 张成牌
        m, parts = hand_multiplier(
            counts("234m567m234pw5p"), counts("234m567m234pw"), 1, 1
        )
        self.assertEqual(m, 4)
        self.assertEqual(parts, ["平胡", "杠开", "爆头"])

    def test_chiitoi_baotou_mult(self):
        # 六对 + 财神单吊:七对 ×2 × 爆头 ×2
        m, parts = hand_multiplier(
            counts("11m22m33m44m55m66mw7p"), counts("11m22m33m44m55m66mw"), 0
        )
        self.assertEqual(m, 4)
        self.assertEqual(parts, ["七对", "爆头"])

    def test_four_wilds_mult(self):
        # 摸牌前持 3 白 + 摸白:4 白板 ×2(持 3 白不排除爆头)
        m, parts = hand_multiplier(
            counts("123m456m789m5pwwww"), counts("123m456m789m5pwww"), 0
        )
        self.assertEqual(m, 4)
        self.assertEqual(parts, ["平胡", "4个白板", "爆头"])

    def test_four_whites_held_is_baotou(self):
        # 站立手恰持 4 白听任意:爆头 ×2 与 4 个白板 ×2 叠加
        # (v21 裁定,撤销旧「持 4 白非爆头」口径)
        m, parts = hand_multiplier(
            counts("123m456m789m5pwwww"), counts("123m456m789mwwww"), 0
        )
        self.assertEqual(m, 4)
        self.assertEqual(parts, ["平胡", "4个白板", "爆头"])

    def test_four_whites_luxury_self_paired(self):
        # 5 自然对 + 4 白两两自配:白板仍计 1 豪华组(v21 裁定)
        m, parts = hand_multiplier(
            counts("1122334455m wwww"), counts("1122334455m www"), 0
        )
        self.assertEqual(m, 16)
        self.assertEqual(parts, ["豪华七对×1", "4个白板", "爆头"])

    def test_four_whites_luxury_filling_singles(self):
        # 白板补配落单成对:不重复计豪华,仅七对(v21 裁定)
        m, parts = hand_multiplier(
            counts("1122334456m wwww"), counts("112233456m wwww"), 0
        )
        self.assertEqual(m, 8)
        self.assertEqual(parts, ["七对", "4个白板", "爆头"])

    def test_piao_counts_four_whites_mult(self):
        # 持 3 白 + 链内飘 1 = 4 白板(指南口径),财飘 × 爆头
        m, parts = hand_multiplier(
            counts("1m123m456m789m5pwww"), counts("123m456m789m5pwww"), 0, 1, 1
        )
        self.assertEqual(m, 8)
        self.assertEqual(parts, ["平胡", "财飘", "4个白板", "爆头"])

    def test_luxury_chiitoi_mult(self):
        m, parts = hand_multiplier(
            counts("1111m22m33m44m55m66m"), counts("1111m22m33m44m55m6m"), 0
        )
        self.assertEqual(m, 4)
        self.assertEqual(parts, ["豪华七对×1"])

    def test_settle_dealer_wins(self):
        self.assertEqual(settle(0, 0, 1), [24, -8, -8, -8])

    def test_settle_learner_wins(self):
        self.assertEqual(settle(1, 0, 2), [-16, 20, -2, -2])

    def test_settle_conservation(self):
        for winner in range(4):
            for dealer in range(4):
                self.assertEqual(sum(settle(winner, dealer, 3)), 0)


if __name__ == "__main__":
    unittest.main()
