"""启发式 bot 弃牌排序的固定牌例回归。

背景(2026-09-11):choose_discard 旧实现按 (向听, 财神, 喂牌, tile编号)
排序,同向听、同风险时由 tile 编号决定——数牌编号 0~26、字牌 27~33,
于是孤张字牌被留着、数牌搭子反被先拆;shanten>1 时更是完全不比较
进张;贴近听牌时先按编号截 top_k=4 再算进张,字牌可能被挤出候选。

现口径(优先级从高到低):向听数(硬约束)→ 财神保护 → 进张数 →
牌型结构损失 → 喂牌风险 → tile 编号(仅稳定排序)。

反应侧(2026-09-11 bot-react-full-eval):吃/碰按"副露 + 最佳弃牌
后站立牌面"与 PASS 基准比较,门槛判据 (shanten, ukeire)——向听
下降即做,等向听需进张增量达标(PONG≥2/CHOW≥4);KONG_OPEN 合法
时整个 claim 窗口(含 PONG)沿用 legacy 决策。
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
    KONG_OPEN,
    PASS,
    PONG,
)
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

    def test_chi_equal_shanten_gain_exactly_at_threshold(self):
        """等向听 + 进张增量恰为 4(= CHOW 门槛)→ 吃;门槛语义是 ≥。

        pending 3p 已入河使 PASS 基准进张 11→10,最优吃法进张 14,
        增量恰为 4:默认门槛下接受;把门槛抬到 5 即拒绝——同牌型
        锁死"恰达门槛仍可接受"的边界。
        """
        spec, owner, tile = "22m345m678m123p45p", 0, 11
        base, claims, act = self._probe(spec, owner, tile)
        self.assertEqual(base[0], 0)
        self.assertEqual(max(r[1] - base[1] for _, r in claims), 4)
        self.assertEqual(act, CHOW_HIGH)
        with mock.patch.object(bot_mod, "CHOW_UKE_GAIN", 5):
            g = _react_game(spec, owner, tile)
            self.assertEqual(bot_mod.choose_action(g, 1), PASS)

    def test_pong_taken_when_shanten_drops(self):
        """碰后向听下降(1→0)→ PONG。"""
        base, claims, act = self._probe("55m678m123p456p9sE", 0, 4, mode="claim")
        self.assertEqual(base[0], 1)
        self.assertEqual(claims[0][1][0], 0)
        self.assertEqual(act, PONG)

    def test_pong_equal_shanten_ukeire_drop_pass(self):
        """等向听 + 进张增量 -1(< PONG 门槛 2)→ PASS。"""
        base, claims, act = self._probe("33m456m789m123p45p", 0, 2, mode="claim")
        self.assertEqual(base[0], 0)
        self.assertEqual(claims[0][1][1] - base[1], -1)
        self.assertEqual(act, PASS)

    def test_pong_equal_shanten_gain_and_threshold_boundary(self):
        """等向听 + 进张增量 +12 → PONG;门槛边界用常量锁定。

        同一局面增量恰为 +12:PONG_UKE_GAIN=12 时可接受(≥),
        =13 时拒绝——直接锁死门槛语义是"≥ 增量"。
        """
        spec, owner, tile = "33m456m789m12p45pE", 0, 2
        base, claims, act = self._probe(spec, owner, tile, mode="claim")
        self.assertEqual(base[0], 1)
        delta = claims[0][1][1] - base[1]
        self.assertEqual(delta, 12)
        self.assertEqual(act, PONG)
        with mock.patch.object(bot_mod, "PONG_UKE_GAIN", 12):
            self.assertEqual(act, PONG)  # 增量恰达门槛仍可接受(上面已验)
        with mock.patch.object(bot_mod, "PONG_UKE_GAIN", 13):
            g = _react_game(spec, owner, tile, mode="claim")
            self.assertEqual(bot_mod.choose_action(g, 1), PASS)

    def test_kong_window_keeps_legacy_behavior(self):
        """KONG_OPEN 合法时整窗走 legacy:KONG 优先于 PONG(等向听),
        PONG 向听严格更优时 PONG——两例与旧实现逐动作一致。
        """
        # legacy: 碰/杠同 key 时 KONG 次项 -1 优先 → KONG_OPEN
        base, claims, act = self._probe("333m456m789m12p45p", 0, 2, mode="claim")
        self.assertEqual(act, KONG_OPEN)
        # legacy: PONG 的副露后向听严格更低 → PONG
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


if __name__ == "__main__":
    unittest.main()
