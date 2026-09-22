"""启发式 bot 弃牌排序的固定牌例回归。

背景(2026-09-11):choose_discard 旧实现按 (向听, 财神, 喂牌, tile编号)
排序,同向听、同风险时由 tile 编号决定——数牌编号 0~26、字牌 27~33,
于是孤张字牌被留着、数牌搭子反被先拆;shanten>1 时更是完全不比较
进张;贴近听牌时先按编号截 top_k=4 再算进张,字牌可能被挤出候选。

现口径(优先级从高到低):向听数(硬约束)→ 财神保护 → 进张数 →
牌型结构损失 → 喂牌风险 → tile 编号(仅稳定排序)。

反应侧(legacy-shape-progress-v1):吃/碰按"副露 + 最佳弃牌后站立
牌面"与 PASS 基准比较;向听下降即做,同向听需爆头/财飘/听牌宽度/
降向听能力显著推进。KONG_OPEN 单独走结构、牌效与杠开硬门。
"""

import random
import unittest
from unittest import mock

from mj import bot as bot_mod
from mj.bot import (
    choose_discard,
    _eval_standing,
    _best_post_claim_discard,
)
from mj.game import (
    CHOW_HIGH,
    CHOW_LOW,
    CHOW_MID,
    Game,
    HU,
    KONG_ADD_BASE,
    KONG_CLOSED_BASE,
    KONG_OPEN,
    PASS,
    PONG,
)
from mj.shanten import shanten
from mj.tiles import W, counts
from mj.win import is_baotou_wait


def _game(spec):
    """构造只有自家手牌的最小对局(无副露、牌河为空)。"""
    g = Game.__new__(Game)
    hand = counts(spec)
    assert sum(hand) == 14, (spec, sum(hand))
    g.hands = [hand, [0] * 34, [0] * 34, [0] * 34]
    g.melds = [[] for _ in range(4)]
    g.discards = [[] for _ in range(4)]
    return g


def _draw_game(spec, drawn, *, you_cai_bi_kao=False, melds=None):
    """Construct a complete draw-phase Game for self-kong policy tests."""
    melds = list(melds or [])
    g = Game.__new__(Game)
    hand = counts(spec)
    assert sum(hand) == 14 - 3 * len(melds), (spec, sum(hand), melds)
    g.hands = [hand, [0] * 34, [0] * 34, [0] * 34]
    g.melds = [melds, [], [], []]
    g.discards = [[] for _ in range(4)]
    g.dealer = 0
    g.base = 1
    g.you_cai_bi_kao = you_cai_bi_kao
    g.wall = [0] * 60
    g.drawn = [None] * 4
    g.drawn[0] = drawn
    g.chows = [0] * 4
    g.turn = 0
    g.phase = "discard"
    g.pending = None
    g.freeze = 0
    g.freezer = None
    g.chain = [0] * 4
    g.chain_piao = [0] * 4
    g.scores = [0] * 4
    g.done = False
    g.result = None
    g._kong_draw = False
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
        """存在非财神最小向听候选时不打财神;唯一例外是弃白后站立手
        仍爆头听(财飘形状,爆头档 tier0——openspec
        baotou-piao-aware-discard 的新语义)。"""
        for hand in self._random_hands(40, 4242, jokers=1):
            g = Game.__new__(Game)
            g.hands = [list(hand), [0] * 34, [0] * 34, [0] * 34]
            g.melds = [[] for _ in range(4)]
            g.discards = [[] for _ in range(4)]
            best = _min_shanten(hand)
            alts = [t for t in range(33) if hand[t] and _shanten_after(hand, t) == best]
            if not alts:
                continue
            pick = choose_discard(g, 0)
            if pick == W:
                after = list(hand)
                after[W] -= 1
                self.assertTrue(is_baotou_wait(after, 0),
                                f"弃白非爆头形状: {hand}")


class TestSelfKongDecision(unittest.TestCase):
    def test_self_kong_uses_public_replacement_expectation(self):
        """v33:暗杠在摸后窗与普通弃牌竞争,不是无条件忽略或强制杠。"""
        g = _draw_game("1111m456m789m11pw5s", 22)
        acts = g.legal_actions()
        self.assertIn(KONG_CLOSED_BASE, acts)

        action, detail = bot_mod._choose_draw_action(g, 0, acts)
        self.assertEqual(action, KONG_CLOSED_BASE)
        self.assertEqual(detail["reason"], "kong_expected_value")
        self.assertGreater(detail["selected_value"], detail["baseline_value"])
        self.assertEqual(detail["wall_left"], 39)

        # shape-v1 shares the same out-of-scope legacy branch, so it also
        # exposes the v33 self-kong choice rather than falling to discard.
        action, evaluation = bot_mod.choose_action(
            g, 0, evaluator="shape-v1", return_evaluation=True)
        self.assertEqual(action, KONG_CLOSED_BASE)
        self.assertEqual(evaluation["reason"], "kong_expected_value")

    def test_self_add_kong_keeps_existing_locked_meld_count(self):
        """v33:补杠替换碰,不能把 locked 错算成新增两个副露。"""
        g = _draw_game("1m456m789m123pw", 0,
                       melds=[("pong", 0)])
        acts = g.legal_actions()
        self.assertIn(KONG_ADD_BASE, acts)

        action, detail = bot_mod._choose_draw_action(g, 0, acts)
        self.assertEqual(action, KONG_ADD_BASE)
        self.assertEqual(detail["reason"], "kong_expected_value")
        self.assertEqual(detail["selected_kind"], "add")

    def test_immediate_hu_beats_lower_value_self_kong(self):
        """杠后期望不足时仍保留确定 HU,避免写成 if-kong-return-kong。"""
        g = _draw_game("1111m22m33m44m55m66m", 5)
        acts = g.legal_actions()
        self.assertIn(HU, acts)
        self.assertIn(KONG_CLOSED_BASE, acts)
        self.assertEqual(bot_mod.choose_action(g, 0), HU)


class TestBaotouDiscardTier(unittest.TestCase):
    """持财神听牌态的爆头档排序(openspec baotou-piao-aware-discard)。"""

    def test_tier0_baotou_wait_candidate_wins(self):
        # 弃 5p 后 567p+财神 = 听任意(tier0);其余候选均为普通听牌。
        g = _game("123m456m789m55p67pw")
        pick, info = choose_discard(g, 0, return_info=True)
        self.assertEqual(pick, 13)
        self.assertEqual(info["reason"], "discard_baotou")
        self.assertEqual(info["baotou_tier"], 0)
        after = list(g.hands[0])
        after[13] -= 1
        self.assertTrue(is_baotou_wait(after, 0))

    def test_tier1_baotou_ukeire_outranks_plain_ukeire(self):
        # 旧键按普通进张选 4p(disc12, uke=22);新排序按爆头进张选
        # 9s(disc26, baotou_u1=9 > disc12 的 5)——有财必拷响下 4p
        # 路径的胡牌张一张都提交不了,爆头推进才是真实进度。
        g = _game("456m224p444s6789sw")
        pick, info = choose_discard(g, 0, return_info=True)
        self.assertEqual(pick, 26)
        self.assertEqual(info["reason"], "discard_baotou")
        self.assertEqual(info["baotou_tier"], 1)

    def test_budget_fallback_returns_legacy_key(self):
        # 节点预算超限:整局回退 legacy 键(普通进张排序),可归因、不混用。
        # 预算只按节点数判定(墙钟仅诊断)——同种子决策不随负载漂移。
        g = _game("456m224p444s6789sw")
        with mock.patch.object(bot_mod, "BAOTOU_UKE_BUDGET_NODES", 0):
            pick, info = choose_discard(g, 0, return_info=True)
        self.assertEqual(pick, 12)  # 旧键 pick(4p)
        self.assertEqual(info["reason"], "discard_legacy")
        self.assertEqual(info["fallback_reason"], "baotou_budget_exceeded")

    def test_no_rust_kernel_skips_tier(self):
        # 无 Rust 内核(纯 Python 枚举不可用):整档跳过,行为回退旧排序。
        with mock.patch("mj.shanten.BAOTOU_UKEIRE_RUST", False):
            pick, info = choose_discard(_game("456m224p444s6789sw"), 0,
                                        return_info=True)
        self.assertEqual(pick, 12)
        self.assertEqual(info["reason"], "discard_legacy")
        self.assertNotIn("fallback_reason", info)

    def test_freeze_restricts_to_drawn_tile(self):
        # 抓打圈冻结态只能弃刚摸的牌(legal_actions 唯一真源)。
        # 旧实现冻结盲:持 8p×4 时 legacy 键/爆头档都会偏爱拆杠
        # 第 4 张(8p)而非刚摸牌,自博弈 A/B 实弹暴露非法动作。
        hand = counts("123m456m789m8888pw")
        g = Game.__new__(Game)
        g.hands = [list(hand), [0] * 34, [0] * 34, [0] * 34]
        g.melds = [[] for _ in range(4)]
        g.discards = [[] for _ in range(4)]
        g.drawn = [0, None, None, None]  # 刚摸 1m
        g.freeze = 2
        g.freezer = 2
        pick, info = choose_discard(g, 0, return_info=True)
        self.assertEqual(pick, 0)
        # 冻结解除后恢复自由排序(弃 8p 成 4 面子+W 爆头听为 tier0);
        # 收紧 X 避免“轮数收手”掩盖档位断言
        g.freeze = 0
        g.freezer = None
        with mock.patch.object(bot_mod, "BAOTOU_PUSH_MAX_ROUNDS", 99):
            pick2, info2 = choose_discard(g, 0, return_info=True)
        self.assertEqual((pick2, info2["baotou_tier"]), (16, 0))  # 8p=16

    def test_push_abort_rounds_reverts_to_speed(self):
        # X 轮未转化 → 收手回速度线(旧键 pick 4p),可归因
        g = _game("456m224p444s6789sw")
        with mock.patch.object(bot_mod, "BAOTOU_PUSH_MAX_ROUNDS", 2):
            pick1, info1 = choose_discard(g, 0, return_info=True)
            self.assertEqual(info1.get("reason"), "discard_baotou")
            self.assertNotIn("push_abort", info1)
            pick2, info2 = choose_discard(g, 0, return_info=True)
            self.assertEqual(pick2, 12)
            self.assertEqual(info2["push_abort"], "rounds")
            self.assertEqual(info2["push_rounds"], 2)

    def test_push_abort_rounds_resets_on_state_exit(self):
        # 离开推进态(手牌失去财神)轮数清零,重新进入重新计轮
        g = _game("456m224p444s6789sw")
        with mock.patch.object(bot_mod, "BAOTOU_PUSH_MAX_ROUNDS", 2):
            choose_discard(g, 0, return_info=True)   # rounds=1,仍推进
            _pick, info2 = choose_discard(g, 0, return_info=True)
            self.assertEqual(info2["push_abort"], "rounds")  # rounds=2 → 收手
            g.hands[0][33] = 0                       # 换入 1s:失去财神离开推进态
            g.hands[0][18] = 1
            choose_discard(g, 0)
            g.hands[0][33] = 1                       # 换回财神重新进入推进态
            g.hands[0][18] = 0
            pick, info = choose_discard(g, 0, return_info=True)
            # 轮数从 1 重新起算,不再处于收手态
            self.assertEqual(info.get("push_rounds"), 1)
            self.assertEqual(info.get("reason"), "discard_baotou")

    def test_push_abort_opp_melds(self):
        # 任意对手副露 ≥ Y → 收手
        g = _game("456m224p444s6789sw")
        g.melds[1] = [("pong", 0)] * 3
        pick, info = choose_discard(g, 0, return_info=True)
        self.assertEqual(pick, 12)
        self.assertEqual(info["push_abort"], "opp_melds")

    def test_push_abort_live_wall(self):
        # 活墙 < Z → 收手(需要 live_wall_left 可读的对局)
        g = _draw_game("456m224p444s6789sw", 33)
        g.wall = [0] * (20 + 10)  # 活墙 10 < 16
        pick, info = choose_discard(g, 0, return_info=True)
        self.assertEqual(info.get("push_abort"), "live_wall")

    def test_piao_suppressed_on_opp_melds(self):
        # 软收手(对手副露 ≥ Y)时弃胡打白飘同样落袋为安
        g = _draw_game("123m456m789m5pwwww", W)
        g.wall = [0] * (20 + 40)
        g.melds[1] = [("pong", 0)] * 3
        action, _ = bot_mod._choose_draw_action(g, 0, g.legal_actions())
        self.assertEqual(action, HU)

    def test_no_wild_hand_unchanged(self):
        # 不持财神:排序与旧冻结基线一致(既有固定牌例回归)。
        self.assertEqual(choose_discard(_game("3m44m567m1p5p3689sEF"), 0), 27)

    def test_ycbk_flag_does_not_change_discard(self):
        # 策略无条件生效:门禁开关只影响 HU 合法性,不影响弃牌选择。
        g1 = _game("456m224p444s6789sw")
        g2 = _game("456m224p444s6789sw")
        g1.you_cai_bi_kao = False
        g2.you_cai_bi_kao = True
        self.assertEqual(choose_discard(g1, 0), choose_discard(g2, 0))


class TestBaotouPiaoWallGuard(unittest.TestCase):
    """墙量守卫:活墙可摸(已扣死墙)< 6 落袋为安直接胡。"""

    def _piao_game(self, live):
        g = _draw_game("123m456m789m5pwwww", W)
        g.wall = [0] * (20 + live)
        return g

    def test_wall_below_guard_hu_directly(self):
        g = self._piao_game(5)
        acts = g.legal_actions()
        self.assertIn(HU, acts)
        action, detail = bot_mod._choose_draw_action(g, 0, acts)
        self.assertEqual(action, HU)
        self.assertEqual(detail["reason"], "hu_wall_guard_legacy")
        self.assertFalse(bot_mod._should_piao(g, 0))

    def test_wall_at_guard_still_piao(self):
        g = self._piao_game(6)
        self.assertTrue(bot_mod._should_piao(g, 0))
        action, detail = bot_mod._choose_draw_action(g, 0, g.legal_actions())
        self.assertEqual(action, W)
        self.assertEqual(detail["reason"], "hu_or_piao_legacy")

    def test_freeze_piao_requires_drawn_wild(self):
        # 抓打圈冻结态只能弃刚摸牌:弃胡打白飘仅在刚摸财神时合法,
        # 否则必须直接胡或弃刚摸牌(旧 _should_piao 冻结盲)。
        g = _draw_game("123m456m789m5pwwww", 13)  # 摸 5p 成胡,非财神
        g.wall = [0] * (20 + 40)
        g.freeze = 2
        g.freezer = 2
        action, detail = bot_mod._choose_draw_action(g, 0, g.legal_actions())
        self.assertEqual(action, HU)  # 冻结 + 摸的不是白 → 不飘
        # 未冻结 + 刚摸财神 → 弃白飘仍被选择
        g2 = _draw_game("123m456m789m5pwwww", W)
        g2.wall = [0] * (20 + 40)
        action2, _ = bot_mod._choose_draw_action(g2, 0, g2.legal_actions())
        self.assertEqual(action2, W)

    def test_guard_beats_kong_comparison(self):
        # HU 与暗杠同时合法、墙 < 6:守卫短路直接胡,杠期望比较不参与。
        g = _draw_game("1111m456m789m5pwww", W)
        g.wall = [0] * (20 + 5)
        acts = g.legal_actions()
        self.assertIn(HU, acts)
        self.assertIn(KONG_CLOSED_BASE, acts)
        self.assertEqual(bot_mod.choose_action(g, 0), HU)


def _react_game(spec, owner, tile, mode="claim", melds=None, river=None, seat=1):
    """构造 react 阶段的最小对局:seat 手牌 + owner 打出的 pending 牌。

    mode="claim" 为碰/明杠窗,"chow" 为吃窗(引擎两窗互斥)。
    melds 为 seat 的既有副露 [(kind, tile), ...];river 为 {座位: [tile]}
    的既有牌河(不含 pending——pending 已由引擎先行入河)。
    """
    g = Game.__new__(Game)
    hand = counts(spec)
    need = 13 - 3 * len(melds or [])
    assert sum(hand) == need, (spec, sum(hand), need)
    g.hands = [[0] * 34 for _ in range(4)]
    g.hands[seat] = hand
    g.melds = [[] for _ in range(4)]
    if melds:
        g.melds[seat] = list(melds)
    g.discards = [[] for _ in range(4)]
    if river:
        for s_, ts in river.items():
            g.discards[s_] = list(ts)
    g.chows = [0] * 4
    g.drawn = [None] * 4
    g.turn = seat
    g.phase = "react"
    g.pending = (owner, tile)
    g.wall = [0] * 60  # live_wall_left = 40 > 0,不影响吃/碰
    g.done = False
    g._n_claim = 1
    g.react_idx = 0 if mode == "claim" else 1
    # 引擎口径:_do_discard 先把 pending 牌入 owner 牌河再进 react,
    # claim 时才弹出(_pop_pending_discard 断言其为牌河顶牌)。
    g.discards[owner].append(tile)
    return g


class TestReactDecision(unittest.TestCase):
    """吃/碰"副露 + 最佳弃牌"完整评价的固定牌例。

    期望动作均经实现前数值探针验证(pass/claim 的 (shanten, ukeire)
    由引擎算出,断言锁定决策与数值的一致性)。
    """

    def _probe(self, spec, owner, tile, mode="chow", melds=None, river=None):
        """返回 (pass 基准, claim 评分列表, choose_action 结果)。"""
        g = _react_game(spec, owner, tile, mode, melds, river)
        seat = 1
        hand = g.hands[seat]
        locked = len(g.melds[seat])
        vis = g.visible_counts(seat)
        base = _eval_standing(hand, locked, vis)
        claims = []
        if mode == "claim" and hand[tile] >= 2:
            c = list(hand)
            c[tile] -= 2
            claims.append((PONG, _best_post_claim_discard(c, locked + 1, vis)))
        else:
            for a in (CHOW_LOW, CHOW_MID, CHOW_HIGH):
                pos = CHOW_LOW - a
                start = tile - pos
                need = [(x, 1) for x in (start, start + 1, start + 2) if x != tile]
                if all(hand[x] >= n for x, n in need):
                    c = list(hand)
                    for x, n in need:
                        c[x] -= n
                    claims.append((a, _best_post_claim_discard(c, locked + 1, vis)))
        from mj.bot import choose_action
        return base, claims, choose_action(g, seat)

    def test_hand_size_conventions_locked_0_1_2(self):
        """张数口径:副露 0/1/2 三档吃牌评价不触发 shanten 张数断言。"""
        cases = [
            (None, "23m456m789m12p45p9s"),
            ([("pong", 27)], "23m456m789m12p"),
            ([("pong", 27), ("pong", 28)], "23m456m78p"),
        ]
        for melds, spec in cases:
            g = _react_game(spec, 0, 0, mode="chow", melds=melds)
            self.assertEqual(bot_mod.choose_action(g, 1), CHOW_LOW, spec)

    def test_vis_snapshot_invariant_on_claim(self):
        """不变量:claim 前 vis == claim 后、弃牌前 vis。

        pending 牌已在牌河,claim 时从牌河移入自家副露;自家手牌两张
        移入副露——可见总量均不变(碰/吃各验一次)。
        """
        g = _react_game("33m456m789m12p45pE", 0, 2, mode="claim")
        vis = g.visible_counts(1)
        g.discards[0].pop()  # 碰:牌河弹出 pending
        g.hands[1][2] -= 2
        g.melds[1].append(("pong", 2))
        self.assertEqual(g.visible_counts(1), vis)

        g = _react_game("23m456m789m12p45p9s", 0, 0, mode="chow")
        vis = g.visible_counts(1)
        g.discards[0].pop()  # 吃 123m:牌河弹出 pending,手牌移两张入副露
        g.hands[1][1] -= 1
        g.hands[1][2] -= 1
        g.melds[1].append(("chow", 0))
        self.assertEqual(g.visible_counts(1), vis)

    def test_chi_taken_when_shanten_drops(self):
        """吃后向听下降(2→1)→ 吃 123m。"""
        base, claims, act = self._probe("23m456m789m12p45p9s", 0, 0)
        self.assertEqual(base[0], 2)
        self.assertEqual(claims[0][1][0], 1)
        self.assertEqual(act, CHOW_LOW)

    def test_two_chow_variants_pick_better_final_position(self):
        """两种吃法同向听(都 1→0)时选吃完+最佳弃牌后进张更高者。

        河里压一张 2m:567m 吃法等 2m(3)/5m(3)=6 上下,345m 吃法等
        5m(3)/8m(4) 更高 → 选 345m(CHOW_HIGH);更高向听的第三种
        吃法(1 向听、进张 36)不参与同向听比较。
        """
        base, claims, act = self._probe("34m67m123p456p99sE", 0, 4, river={2: [1]})
        self.assertEqual(base[0], 1)
        best_s = min(r[0] for _, r in claims)
        self.assertEqual(best_s, 0)
        top = {a: r[1] for a, r in claims if r[0] == best_s}
        self.assertEqual(top[CHOW_HIGH], max(top.values()))
        self.assertEqual(act, CHOW_HIGH)

    def test_chi_equal_shanten_small_gain_passes(self):
        """等向听 + 进张增量恰为 4(旧门槛)→ PASS。

        pending 3p 已入河使 PASS 基准进张 11→10,最优吃法进张 14,
        增量恰为 4:未达到 CHOW 绝对门槛 6,也无类别升级。
        """
        spec, owner, tile = "22m345m678m123p45p", 0, 11
        base, claims, act = self._probe(spec, owner, tile)
        self.assertEqual(base[0], 0)
        self.assertEqual(max(r[1] - base[1] for _, r in claims), 4)
        self.assertEqual(act, PASS)

    def test_pong_taken_when_shanten_drops(self):
        """碰后向听下降(1→0)→ PONG。"""
        base, claims, act = self._probe("55m678m123p456p9sE", 0, 4, mode="claim")
        self.assertEqual(base[0], 1)
        self.assertEqual(claims[0][1][0], 0)
        self.assertEqual(act, PONG)

    def test_pong_equal_shanten_baotou_progress(self):
        """等向听普通进张 -1,但爆头进张 0→4 → PONG。"""
        base, claims, act = self._probe("33m456m789m123p45p", 0, 2, mode="claim")
        self.assertEqual(base[0], 0)
        self.assertEqual(claims[0][1][1] - base[1], -1)
        self.assertEqual(act, PONG)

    def test_pong_equal_shanten_gain_and_threshold_boundary(self):
        """等向听 + 进张增量 +12 → PONG;门槛边界用常量锁定。

        同一普通进张 +12,同时满足 PONG 绝对与 1.5 倍门槛。
        """
        spec, owner, tile = "33m456m789m12p45pE", 0, 2
        base, claims, act = self._probe(spec, owner, tile, mode="claim")
        self.assertEqual(base[0], 1)
        delta = claims[0][1][1] - base[1]
        self.assertEqual(delta, 12)
        self.assertEqual(act, PONG)

    def test_kong_window_no_longer_freezes_legacy_claim(self):
        """KONG_OPEN 单独过硬门;PONG 不再被整窗旧逻辑覆盖。
        """
        # KONG 结构安全但 post-KONG 仍 1 向听,PONG 同向听不显著
        # → 两者都不推进,PASS。
        base, claims, act = self._probe("333m456m789m12p45p", 0, 2, mode="claim")
        self.assertEqual(act, PASS)
        # PONG 的副露后向听严格更低,KONG 未过杠开门 → PONG。
        act = self._probe("444m567m89m12p45pE", 0, 3, mode="claim")[2]
        self.assertEqual(act, PONG)

    def test_pass_protects_chiitoi_branch(self):
        """七对为更优分支的局面,吃与碰窗口均 PASS。

        1122334455667m 六对半:七对听 7m;副露后评价只剩标准形,
        PASS 基准天然压过副露评分(自然偏向保护,非绝对 guard)。
        """
        base, claims, act = self._probe("1122334455667m", 0, 2, mode="claim")
        self.assertEqual(base[0], 0)  # 七对听牌
        self.assertEqual(act, PASS)
        base, claims, act = self._probe("1122334455667m", 0, 2, mode="chow")
        self.assertTrue(claims)  # 吃窗确有合法吃法(123m/234m)
        self.assertEqual(act, PASS)

    def test_post_claim_discard_joker_rule_matches_choose_discard(self):
        """claim 后舍牌的财神口径与 choose_discard 一致。

        财神参与最小向听比较(不为保护财神而错失降向听的舍牌——
        百搭语义下实证不可达,随机 5000 手无反例,但口径必须显式
        一致防语义漂移);同向听候选内部用 (d==W, -uke, shape, d)
        保护:存在非财神候选时最优舍牌绝不是财神。
        """
        rng = random.Random(20260911)
        for _ in range(40):
            hand = [0] * 34
            hand[W] = rng.randint(1, 2)
            while sum(hand) < 11:  # need+1(locked=1 的吃/碰后手牌)
                t = rng.randrange(33)
                if hand[t] < 4:
                    hand[t] += 1
            best_s, cands = bot_mod._post_claim_min_shanten(hand, 1)
            self.assertIsNotNone(best_s)
            self.assertTrue(any(hand[d] > 0 for d, _ in cands))
            if any(d != W for d, _ in cands):
                uke, shape, d = bot_mod._best_standing(cands, 1, hand, hand)
                self.assertNotEqual(d, W)


if __name__ == "__main__":
    unittest.main()
