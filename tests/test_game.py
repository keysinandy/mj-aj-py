import random
import unittest

from mj.tiles import counts, W
from mj.game import (
    Game, PASS, PONG, KONG_OPEN, CHOW_LOW, KONG_CLOSED_BASE, HU,
)

# 三个互不冲突的 13 张填充手牌:无对子无杠,且任意单张摸牌都不能成胡
# (牌面全孤立或结构缺口无法由一张补齐)。
FA = "2p3p4p5p6p7p8p9p1s2s3s4s6s"
FB = "2p3p4p5p6p7p8p9p1s3s5s7s9s"
FC = "2p3p4p5p6p7p8p9p2s4s6s8sE"


def tile_conservation(g):
    c = [0] * 34
    for h in g.hands:
        for t, n in enumerate(h):
            c[t] += n
    for ms in g.melds:
        for kind, t in ms:
            if kind == "chow":
                c[t] += 1
                c[t + 1] += 1
                c[t + 2] += 1
            elif kind.startswith("kong"):
                c[t] += 4
            else:  # pong
                c[t] += 3
    for d in g.discards:
        for t in d:
            c[t] += 1
    for t in g.wall:
        c[t] += 1
    return c


def setup(hands_spec, first_draws, dealer=0, seed=0, you_cai_bi_kao=False):
    """构造指定手牌与摸牌序列的对局。

    hands_spec: 4 家各 13 张手牌牌串;first_draws: 摸牌序列(首张先摸,
    由庄家摸)。其余牌洗匀入墙,总牌数守恒。
    """
    g = Game.__new__(Game)
    g.dealer = dealer
    g.base = 1
    g.you_cai_bi_kao = you_cai_bi_kao
    g._kong_draw = False
    g.rng = random.Random(seed)
    g.hands = [counts(s) for s in hands_spec]
    total = [sum(h[t] for h in g.hands) for t in range(34)]
    assert all(x <= 4 for x in total), "手牌中同种牌超过 4 张"
    rest = []
    for t in range(34):
        rest += [t] * (4 - total[t])
    for t in first_draws:
        if t not in rest:
            raise ValueError(f"牌 {t} 不在余牌中")
        rest.remove(t)
    random.Random(seed).shuffle(rest)
    for t in reversed(first_draws):
        rest.append(t)
    g.wall = rest
    g.melds = [[] for _ in range(4)]
    g.discards = [[] for _ in range(4)]
    g.drawn = [None] * 4
    g.chows = [0] * 4
    g.turn = dealer
    g.phase = "discard"
    g.pending = None
    g.freeze = 0
    g.freezer = None
    g.chain = [0] * 4
    g.chain_piao = [0] * 4
    g.scores = [0] * 4
    g.done = False
    g.result = None
    g._draw(dealer)
    return g


def auto_hu_step(g, rng):
    """模拟平台超时兜底的随机玩家:可胡必胡,其余随机。"""
    acts = g.legal_actions()
    g.step(HU if HU in acts else rng.choice(acts))


def run_freeze_round(g):
    """推进抓打圈至圈末:三家只能打刚摸的牌,其余反应全过。

    圈内前两次弃牌仅打财神者有反应权;末家弃牌后 freeze 归零,
    恢复正常反应窗口,过完即轮到打财神者摸牌。
    """
    for _ in range(3):
        assert g.legal_actions() == [g.drawn[g.turn]], "冻结家只能打刚摸的牌"
        g.step(g.drawn[g.turn])
        while g.phase == "react":
            g.step(PASS)


class TestRandomPlay(unittest.TestCase):
    def test_random_games(self):
        wins = draws = 0
        for seed in range(300):
            g = Game(seed=seed)
            rng = random.Random(seed)
            steps = 0
            while not g.done:
                acts = g.legal_actions()
                self.assertTrue(acts, "无合法动作")
                auto_hu_step(g, rng)
                steps += 1
                self.assertLess(steps, 1500, "对局未终止")
                self.assertEqual(tile_conservation(g), [4] * 34, "牌数不守恒")
            if g.result:
                wins += 1
                self.assertEqual(sum(g.scores), 0, "得分不守恒")
            else:
                draws += 1
                self.assertEqual(g.scores, [0] * 4)
        self.assertGreaterEqual(wins, 5, "和牌数异常少")
        self.assertGreaterEqual(draws, 5, "流局数异常少")

    def test_freeze_discard_constraint(self):
        """抓打圈内其余玩家只能打刚摸的牌。"""
        checked = 0
        for seed in range(300):
            g = Game(seed=seed)
            rng = random.Random(seed)
            while not g.done:
                acts = g.legal_actions()
                if g.phase == "discard" and g.in_freeze(g.turn):
                    discards = [a for a in acts if a >= 0]
                    self.assertEqual(discards, [g.drawn[g.turn]])
                    checked += 1
                g.step(HU if HU in acts else rng.choice(acts))
        self.assertGreater(checked, 50, "未覆盖到抓打圈场景")

    def test_win_result_wellformed(self):
        """随机对局里每一次报胡,结果均带回倍率与番型。"""
        for seed in range(100):
            g = Game(seed=seed)
            rng = random.Random(seed)
            while not g.done:
                auto_hu_step(g, rng)
            if g.result:
                seat, mult, parts = g.result
                self.assertGreaterEqual(mult, 1)
                self.assertTrue(parts)
                self.assertEqual(g.scores[seat], max(g.scores))


class TestScenarios(unittest.TestCase):
    def test_win_requires_explicit_hu(self):
        """摸牌成胡不再强制:显式提交 HU 才结算(双碰听 1p/5s)。"""
        g = setup(
            ["123m456m789m11p55s", FA, FB, FC],
            [9],  # 摸 1p → 111p 面子 + 55s 雀头
        )
        self.assertFalse(g.done, "不再自动报胡")
        self.assertIn(HU, g.legal_actions())
        g.step(HU)
        self.assertTrue(g.done)
        self.assertEqual(g.result[0], 0)
        self.assertEqual(g.result[1], 1)  # 平胡
        self.assertEqual(g.scores, [24, -8, -8, -8])  # 庄家自摸 ×8

    def test_fold_win(self):
        """弃胡:摸牌成胡后照常打牌,对局继续。"""
        g = setup(
            ["123m456m789m11p55s", FA, FB, FC],
            [9],
        )
        g.step(9)  # 弃胡,打出刚摸的 1p
        self.assertFalse(g.done)
        self.assertEqual(g.discards[0], [9])
        self.assertEqual(g.phase, "react")

    def test_no_hu_after_pong(self):
        """刚摸牌门禁:碰后未摸牌提交 HU 报错。"""
        g = setup(
            ["123m456m789m11p55s", "55m" + FA[:22], FB, FC],
            [21],
        )
        g.step(4)  # 打 5m
        g.step(PONG)
        self.assertNotIn(HU, g.legal_actions())
        with self.assertRaises(ValueError):
            g.step(HU)

    def test_baotou_win_multiplier(self):
        """4 面子 + 财神单吊:摸任意牌都胡,爆头 ×2。"""
        g = setup(
            ["123m456m789m123pw", FA, FB, FC],
            [1],  # 摸 2m:与财神成雀头
        )
        self.assertIn(HU, g.legal_actions())
        g.step(HU)
        self.assertTrue(g.done)
        self.assertEqual(g.result[0], 0)
        self.assertEqual(g.result[1], 2)
        self.assertEqual(g.result[2], ["平胡", "爆头"])

    def test_piao_chain_win(self):
        """爆头态摸财神弃胡打白飘,下轮任意牌胡:财飘 × 爆头 = ×4。"""
        g = setup(
            ["123m456m789m123pw", FA, FB, FC],
            [33, 27, 28, 29],  # 摸白弃胡飘 → 三家冻结摸打 东/南/西
        )
        self.assertIn(HU, g.legal_actions())
        g.step(W)  # 弃胡打白飘
        self.assertEqual(g.chain[0], 1)
        self.assertEqual(g.chain_piao[0], 1)
        self.assertFalse(g.done)
        run_freeze_round(g)
        self.assertEqual(g.freeze, 0)
        self.assertEqual(g.turn, 0)
        self.assertIn(HU, g.legal_actions())  # 站立手听任意牌
        g.step(HU)
        self.assertTrue(g.done)
        self.assertEqual(g.result[0], 0)
        self.assertEqual(g.result[1], 4)
        self.assertEqual(g.result[2], ["平胡", "财飘", "爆头"])

    def test_double_piao(self):
        """连续两轮爆头态摸白弃胡飘:双财飘 ×8。"""
        g = setup(
            ["123m456m789m123pw", FA, FB, FC],
            [33, 27, 28, 29, 33, 27, 28, 29, 0],
        )
        for _ in range(2):
            self.assertIn(HU, g.legal_actions())
            g.step(W)  # 弃胡打白飘
            run_freeze_round(g)
        self.assertEqual(g.chain[0], 2)
        self.assertEqual(g.chain_piao[0], 2)
        self.assertIn(HU, g.legal_actions())
        g.step(HU)
        self.assertTrue(g.done)
        self.assertEqual(g.result[1], 8)
        self.assertEqual(g.result[2], ["平胡", "双财飘", "爆头"])

    def test_piao_counts_toward_four_whites(self):
        """持 3 白 + 飘 1:链内飘出白板计入 4 个白板(指南口径)×8。"""
        g = setup(
            ["123m456m789m5pwww", FA, FB, FC],
            [33, 27, 28, 29],
        )
        self.assertIn(HU, g.legal_actions())
        g.step(W)  # 弃胡打白飘(留 3 白在手)
        run_freeze_round(g)
        self.assertIn(HU, g.legal_actions())
        g.step(HU)
        self.assertTrue(g.done)
        self.assertEqual(g.result[1], 8)
        self.assertEqual(g.result[2], ["平胡", "财飘", "4个白板", "爆头"])

    def test_four_whites_not_baotou(self):
        """站立手恰持 4 白板:计 4 个白板 ×2,不视为爆头。"""
        g = setup(
            ["123m456m789mwwww", FA, FB, FC],
            [0],  # 摸 1m
        )
        self.assertIn(HU, g.legal_actions())
        g.step(HU)
        self.assertTrue(g.done)
        self.assertEqual(g.result[1], 2)
        self.assertEqual(g.result[2], ["平胡", "4个白板"])

    def test_chain_breaks_on_normal_discard(self):
        """飘后打出非飘非杠牌:动作链断,重新计数。"""
        g = setup(
            ["123m456m789m123pw", FA, FB, FC],
            [33, 27, 28, 29, 0],  # 飘一轮后摸 1m
        )
        g.step(W)  # 财飘,链 = 1
        run_freeze_round(g)
        self.assertEqual(g.chain[0], 1)
        g.step(0)  # 弃胡打 1m(非飘):断链
        self.assertEqual(g.chain[0], 0)
        self.assertEqual(g.chain_piao[0], 0)
        self.assertFalse(g.done)

    def test_discard_wild_triggers_freeze(self):
        """打财神后其余三家只能打刚摸的牌;打出者不受限且保留反应权。"""
        g = setup(
            ["123m456m789m12p5sw", FA, FB, FC],
            [21],  # 庄家摸 3s(不成胡)
        )
        g.step(W)  # 打出财神
        self.assertEqual(g.freezer, 0)
        self.assertEqual(g.freeze, 3)
        self.assertFalse(g.in_freeze(0))
        self.assertEqual(g.phase, "discard")
        self.assertEqual(g.turn, 1)
        self.assertTrue(g.in_freeze(1))
        self.assertEqual(g.legal_actions(), [g.drawn[1]])
        g.step(g.drawn[1])
        self.assertEqual(g.freeze, 2)
        # 圈内其他两家冻结,只有打财神者(0)保留反应权
        self.assertEqual(g.phase, "react")
        self.assertEqual(g.turn, 0)
        self.assertEqual(g.legal_actions(), [PASS])
        g.step(PASS)
        self.assertEqual(g.turn, 2)
        self.assertTrue(g.in_freeze(2))
        self.assertEqual(g.legal_actions(), [g.drawn[2]])
        g.step(g.drawn[2])
        self.assertEqual(g.freeze, 1)
        g.step(PASS)  # 打财神者过
        self.assertEqual(g.turn, 3)
        self.assertTrue(g.in_freeze(3))
        g.step(g.drawn[3])
        self.assertEqual(g.freeze, 0)
        # 圈结束:正常反应窗口后回到庄家
        for _ in range(4):
            if not g.done:
                g.step(PASS)
        if not g.done:
            self.assertEqual(g.turn, 0)
            self.assertFalse(g.in_freeze(0))
            self.assertEqual(g.phase, "discard")

    def test_wild_discard_skips_react(self):
        """打出的财神不能被碰(即使他家持有白板),直接轮到下家摸牌。"""
        g = setup(
            ["123m456m789m12p5sw", "ww" + FA[:22], FB, FC],
            [21],
        )
        g.step(W)
        self.assertEqual(g.phase, "discard")
        self.assertEqual(g.turn, 1)

    def test_pong_claim(self):
        g = setup(
            ["123m456m789m11p55s", "55m" + FA[:22], FB, FC],
            [21],
        )
        g.step(4)  # 打 5m
        self.assertEqual(g.phase, "react")
        self.assertEqual(g.turn, 1)
        self.assertIn(PONG, g.legal_actions())
        self.assertNotIn(CHOW_LOW, g.legal_actions())  # claim 窗口不能吃
        g.step(PONG)
        self.assertEqual(g.melds[1], [("pong", 4)])
        self.assertEqual(g.turn, 1)
        self.assertEqual(g.phase, "discard")
        self.assertEqual(g.discards[0], [])  # 被碰的牌离开牌河
        self.assertIn(10, g.legal_actions())  # 碰后无摸牌,直接从手牌出(2p)
        self.assertNotIn(4, g.legal_actions())  # 5m 已碰出

    def test_pong_window_before_chow_window(self):
        """碰窗口先于吃窗口:对家的碰优先于下家的吃。"""
        g = setup(
            ["123m456m789m11p55s", "67m" + FA[:22],
             "55m" + FB[:22], FC],
            [21],
        )
        g.step(4)  # 打 5m;下家有 67m 可吃,对家有 55m 可碰
        acts = g.legal_actions()
        self.assertNotIn(CHOW_LOW, acts)  # 下家在 claim 窗口,不能吃
        self.assertNotIn(PONG, acts)  # 下家无 5m
        g.step(PASS)  # 下家过(claim 窗口)
        self.assertEqual(g.turn, 2)
        self.assertIn(PONG, g.legal_actions())
        g.step(PONG)
        self.assertEqual(g.melds[2], [("pong", 4)])

    def test_chow_claim(self):
        g = setup(
            ["123m456m789m11p55s", "67m" + FA[:22], FB, FC],
            [21],
        )
        g.step(4)  # 打 5m
        g.step(PASS)  # 下家 claim 过
        g.step(PASS)  # 对家过
        g.step(PASS)  # 上家过
        self.assertEqual(g.turn, 1)  # 吃窗口回到下家
        acts = g.legal_actions()
        self.assertEqual([a for a in acts if a != PASS], [CHOW_LOW])  # 仅 567m
        g.step(CHOW_LOW)
        self.assertEqual(g.melds[1], [("chow", 4)])  # 5m6m7m
        self.assertEqual(g.chows[1], 1)
        self.assertEqual(g.turn, 1)
        self.assertEqual(g.phase, "discard")
        self.assertNotIn(5, g.legal_actions())  # 6m 已用于吃
        self.assertNotIn(6, g.legal_actions())  # 7m 已用于吃

    def test_chow_limit(self):
        g = setup(
            ["123m456m789m11p55s", "67m" + FA[:22], FB, FC],
            [21],
        )
        g.chows[1] = 2
        g.step(4)
        for _ in range(3):
            g.step(PASS)
        self.assertEqual(g.legal_actions(), [PASS])  # 吃满 2 摊

    def test_kong_closed_gangkai(self):
        """暗杠后补牌成胡 = 杠开 ×2(补牌成胡需显式提交 HU)。"""
        g = setup(
            ["111m456m789m11p55s", FA, FB, FC],
            [0, 9],  # 摸第 4 张 1m 暗杠,补摸 1p 成胡
        )
        acts = g.legal_actions()
        self.assertIn(KONG_CLOSED_BASE, acts)  # 暗杠 1m
        self.assertIn(0, acts)  # 也可直接打出
        g.step(KONG_CLOSED_BASE)
        self.assertEqual(g.melds[0], [("kong_closed", 0)])
        self.assertEqual(g.chain[0], 1)  # 杠计入动作链
        self.assertFalse(g.done, "不再自动胡")
        self.assertIn(HU, g.legal_actions())
        g.step(HU)
        self.assertTrue(g.done)
        self.assertEqual(g.result[0], 0)
        self.assertEqual(g.result[1], 2)
        self.assertEqual(g.result[2], ["平胡", "杠开"])

    def test_no_kong_in_dead_wall(self):
        g = setup(
            ["111m456m789m11p55s", FA, FB, FC],
            [0],
        )
        self.assertIn(KONG_CLOSED_BASE, g.legal_actions())
        g.wall = g.wall[-20:]  # 只剩 10 墩死牌
        self.assertEqual(g.live_wall_left(), 0)
        self.assertNotIn(KONG_CLOSED_BASE, g.legal_actions())
        self.assertIn(0, g.legal_actions())  # 弃牌不受影响

    def test_kong_open(self):
        """明杠:碰窗口内用手中 3 张杠他家打出的牌,杠后补牌。"""
        g = setup(
            ["123m456m789m11p55s", "555m" + FA[:20], FB, FC],
            [21],
        )
        g.step(4)  # 打 5m
        self.assertIn(KONG_OPEN, g.legal_actions())
        g.step(KONG_OPEN)
        self.assertEqual(g.melds[1], [("kong_open", 4)])
        self.assertEqual(g.chain[1], 1)  # 明杠计入动作链
        self.assertEqual(g.phase, "discard")
        self.assertEqual(g.turn, 1)
        self.assertIsNotNone(g.drawn[1])  # 杠后补牌
        g.step(g.drawn[1])  # 弃补牌:非杠动作断链
        self.assertEqual(g.chain[1], 0)

    def test_flow_when_wall_exhausted(self):
        """活牌墙摸完无人胡则流局,得分全 0。"""
        saw_draw = False
        for seed in range(100):
            g = Game(seed=seed)
            rng = random.Random(seed)
            while not g.done:
                auto_hu_step(g, rng)
            if g.result is None:
                self.assertEqual(g.scores, [0] * 4)
                saw_draw = True
        self.assertTrue(saw_draw, "100 局内未出现流局")


class TestYouCaiBiKao(unittest.TestCase):
    """有财必拷响:手中有财神时,须爆头或杠开才可胡。"""

    # 站立手 3 面子 + 11p 雀头候选 + 5s + 财神:非爆头
    # (胡牌进张仅 1p/5s/3s/4s/6s/7s,非任意牌)
    HAND_W = "123m456m789m11p5sw"
    # 站立手 3 面子 + 11p + 双财神:爆头(任意 t + WW = 刻子)
    HAND_BAOTOU = "123m456m789m11pww"

    def test_plain_win_with_god_blocked(self):
        """普通摸牌、手有财神、非爆头 → HU 被门禁,弃牌照常。"""
        g = setup([self.HAND_W, FA, FB, FC], [24], you_cai_bi_kao=True)
        # 5s + W(6s) + 7s = 567s 面子 → 成胡牌型,但非爆头
        self.assertNotIn(HU, g.legal_actions())
        self.assertIn(24, g.legal_actions())  # 摸的 7s 可打
        self.assertIn(W, g.legal_actions())
        g.step(24)  # 弃胡打 7s,对局继续
        self.assertFalse(g.done)
        while not g.done:
            auto_hu_step(g, random.Random(0))

    def test_baotou_win_with_god_allowed(self):
        """爆头态(任意牌即胡)手有财神 → 可胡,且番含爆头 ×2。"""
        g = setup([self.HAND_BAOTOU, FA, FB, FC], [8],
                  you_cai_bi_kao=True)
        self.assertIn(HU, g.legal_actions())
        g.step(HU)
        self.assertTrue(g.done)
        self.assertIn("爆头", g.result[2])
        self.assertEqual(g.result[1], 2)  # 平胡 × 爆头

    def test_kong_draw_win_with_god_allowed(self):
        """杠后补牌成胡(杠开):非爆头但手有财神 → 可胡。"""
        # 1111m 可暗杠;补牌 7s → 5s W(6s) 7s 成面子 = 杠开胡
        g = setup(["1111m456m789m11pw", FA, FB, FC], [22, 24],
                  you_cai_bi_kao=True)
        g.step(KONG_CLOSED_BASE)  # 暗杠 1m → 补牌 7s
        self.assertIn(HU, g.legal_actions())
        g.step(HU)
        self.assertTrue(g.done)
        self.assertIn("杠开", g.result[2])
        self.assertEqual(g.result[1], 2)  # 平胡 × 杠开

    def test_no_god_unaffected(self):
        """手无财神 → 平胡不受门禁影响。"""
        g = setup(["123m456m789m11p55s", FA, FB, FC], [9],
                  you_cai_bi_kao=True)
        self.assertIn(HU, g.legal_actions())
        g.step(HU)
        self.assertEqual(g.result[1], 1)

    def test_off_by_default(self):
        """开关关闭(默认):手有财神非爆头也可平胡(向后兼容)。"""
        g = setup([self.HAND_W, FA, FB, FC], [24])
        self.assertIn(HU, g.legal_actions())
        g.step(HU)
        self.assertEqual(g.result[1], 1)

    def test_random_games_property(self):
        """开关开启的随机对局:守恒/终局不变量保持,且每一个手有
        财神的胡牌者必带爆头或杠/飘链番(门禁未被绕过)。"""
        wins_with_god = 0
        for seed in range(300):
            g = Game(seed=seed, you_cai_bi_kao=True)
            rng = random.Random(seed)
            steps = 0
            while not g.done:
                acts = g.legal_actions()
                self.assertTrue(acts, "无合法动作")
                auto_hu_step(g, rng)
                steps += 1
                self.assertLess(steps, 1500, "对局未终止")
                self.assertEqual(tile_conservation(g), [4] * 34)
            if g.result:
                seat, mult, parts = g.result
                self.assertEqual(sum(g.scores), 0)
                if g.hands[seat][W] > 0:
                    wins_with_god += 1
                    self.assertTrue(
                        "爆头" in parts or g.chain[seat] > 0,
                        f"手有财神的平胡未被门禁: {parts}")
        self.assertGreaterEqual(wins_with_god, 1, "未覆盖手有财神胡牌场景")


if __name__ == "__main__":
    unittest.main()
