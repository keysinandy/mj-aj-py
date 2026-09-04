"""features.py:平面语义精确值测试 + 对局中逐决策点性质对拍。"""

import random
import unittest

from mj.game import (
    Game, DEAD_WALL, HU, PASS, PONG, KONG_OPEN,
    KONG_CLOSED_BASE, KONG_ADD_BASE, CHOW_LOW, CHOW_MID, CHOW_HIGH,
)
from mj.features import (
    N_ACTIONS, N_PLANES, N_PLANES_ORACLE, N_SCALARS,
    PLANE_GROUPS, ORACLE_GROUPS,
    extract, legal_mask, action_to_flat, flat_to_action,
)
from mj.shanten import shanten
from mj.tiles import W, counts


def _group_offsets(groups):
    off, out = 0, {}
    for name, n in groups:
        out[name] = off
        off += n
    return out


OFF = _group_offsets(PLANE_GROUPS + ORACLE_GROUPS)


def _sub(counts, t):
    c = list(counts)
    c[t] -= 1
    return c


def bot_states(n_games=3, seed=7):
    """推进 bot 对局,逐决策点 yield (g, seat)。"""
    import mj.bot as bot
    rng = random.Random(seed)
    for _ in range(n_games):
        g = Game(seed=rng.randrange(10 ** 9))
        while not g.done:
            yield g, g.current_seat()
            acts = g.legal_actions()
            act = bot.choose_action(g, g.current_seat())
            g.step(act if act in acts else rng.choice(acts))


class TestLayout(unittest.TestCase):
    def test_counts(self):
        self.assertEqual(N_PLANES, 75)
        self.assertEqual(N_PLANES_ORACLE, 91)
        self.assertEqual(N_SCALARS, 8)
        self.assertEqual(sum(n for _, n in PLANE_GROUPS), N_PLANES)
        self.assertEqual(sum(n for _, n in ORACLE_GROUPS), 16)

    def test_shapes(self):
        g = Game(seed=1)
        p, s = extract(g, g.current_seat())
        self.assertEqual(p.shape, (N_PLANES, 34))
        self.assertEqual(s.shape, (N_SCALARS,))
        p, _ = extract(g, g.current_seat(), oracle=True)
        self.assertEqual(p.shape, (N_PLANES_ORACLE, 34))


class TestActionSpace(unittest.TestCase):
    def test_roundtrip(self):
        for i in range(N_ACTIONS):
            self.assertEqual(action_to_flat(flat_to_action(i)), i)

    def test_known_codes(self):
        self.assertEqual(action_to_flat(5), 5)          # 弃 6m
        self.assertEqual(action_to_flat(PASS), 34)
        self.assertEqual(action_to_flat(CHOW_LOW), 35)
        self.assertEqual(action_to_flat(CHOW_MID), 36)
        self.assertEqual(action_to_flat(CHOW_HIGH), 37)
        self.assertEqual(action_to_flat(PONG), 38)
        self.assertEqual(action_to_flat(KONG_OPEN), 39)
        for t in range(34):
            self.assertEqual(action_to_flat(KONG_CLOSED_BASE - t), 40 + t)
            self.assertEqual(action_to_flat(KONG_ADD_BASE - t), 74 + t)
        self.assertEqual(action_to_flat(HU), 108)

    def test_mask_matches_legal(self):
        for g, _ in bot_states():
            acts = g.legal_actions()
            mask = legal_mask(g)
            self.assertEqual(
                {action_to_flat(a) for a in acts},
                {i for i, v in enumerate(mask) if v},
            )


class TestExtractProperty(unittest.TestCase):
    """对局中逐决策点:平面/标量与游戏状态逐项对拍。"""

    def test_planes_match_state(self):
        checked = 0
        for g, seat in bot_states(n_games=4):
            p, s = extract(g, seat, oracle=True)
            self.assertTrue(float(p.min()) >= 0.0 and float(p.max()) <= 1.0)
            self.assertTrue(float(s.min()) >= 0.0 and float(s.max()) <= 1.0)
            # 手牌多重性
            for k in range(4):
                expect = [1.0 if c > k else 0.0 for c in g.hands[seat]]
                self.assertEqual(list(p[OFF["hand"] + k]), expect)
            # 三家 oracle 手牌
            for r, rel in ((1, "next"), (2, "across"), (3, "prev")):
                for k in range(4):
                    expect = [1.0 if c > k else 0.0
                              for c in g.hands[(seat + r) % 4]]
                    self.assertEqual(
                        list(p[OFF[f"oracle_hand_{rel}"] + k]), expect)
            # 活墙组成
            wall = [0] * 34
            for t in g.wall[DEAD_WALL:]:
                wall[t] += 1
            for k in range(4):
                expect = [1.0 if c > k else 0.0 for c in wall]
                self.assertEqual(list(p[OFF["oracle_wall"] + k]), expect)
            # 刚摸标记
            d = g.drawn[seat]
            self.assertEqual(
                list(p[OFF["drawn"]]),
                [1.0 if t == d else 0.0 for t in range(34)],
            )
            # 财神
            w_riv = sum(dd.count(W) for dd in g.discards)
            self.assertEqual(set(p[OFF["god"]].tolist()), {g.hands[seat][W] / 4.0})
            self.assertEqual(set(p[OFF["god"] + 1].tolist()), {w_riv / 4.0})
            # 向听数广播与进张图与 shanten 一致(float32 舍入容差)。
            # 站立手基准:react 阶段=手牌;摸牌后=去刚摸牌;
            # 吃碰后弃牌=最优弃张后的手
            hand = g.hands[seat]
            locked = len(g.melds[seat])
            base = list(hand)
            if sum(hand) == 13 - 3 * locked + 1:
                d = g.drawn[seat]
                if d is not None:
                    base[d] -= 1
                else:
                    base[min(
                        (t for t in range(34) if hand[t]),
                        key=lambda t: shanten(_sub(hand, t), locked),
                    )] -= 1
            sh = shanten(base, locked)
            self.assertAlmostEqual(float(p[OFF["eng"]][0]), (sh + 1) / 9.0,
                                   places=6)
            for t in range(34):
                c = list(base)
                c[t] += 1
                want = 1.0 if shanten(c, locked) < sh else 0.0
                self.assertEqual(float(p[OFF["eng"] + 3][t]), want)
            # 标量
            self.assertEqual(float(s[0]), 1.0 if seat == g.dealer else 0.0)
            self.assertAlmostEqual(float(s[1]), g.live_wall_left() / 64.0)
            self.assertEqual(float(s[3]), 1.0 if g.in_freeze(seat) else 0.0)
            self.assertAlmostEqual(float(s[4]), g.chows[seat] / 2.0)
            checked += 1
        self.assertGreater(checked, 100)


class TestExtractExact(unittest.TestCase):
    """构造状态验证副露/牌河帧/阶段标量/弃后地图的精确语义。"""

    def _game(self):
        g = Game(seed=5)
        g.hands[0] = counts("123m456m789m123p55p")  # 14 张,含摸的 5p
        g.drawn[0] = 13
        return g

    def test_meld_planes(self):
        g = self._game()
        g.melds[1] = [("chow", 0), ("chow", 0)]   # 两摊 1m2m3m
        g.melds[2] = [("pong", 5), ("kong_closed", 9), ("kong_add", 33)]
        g.melds[3] = [("pong", 5)]
        p, _ = extract(g, 0)
        o = OFF["meld_next"]  # 座位 1 = 下家;组内:吃 4 / 碰 4 / 杠 4
        for k in range(4):
            plane = p[o + k]
            self.assertEqual([plane[0], plane[1], plane[2]],
                             [1.0 if k < 2 else 0.0] * 3)
        o = OFF["meld_across"]  # 座位 2 = 对家
        self.assertEqual([p[o + 4 + k][5] for k in range(4)],
                         [1.0, 1.0, 1.0, 0.0])  # 碰 = 3 张
        for t in (9, 33):
            self.assertEqual([p[o + 8 + k][t] for k in range(4)],
                             [1.0] * 4)  # 杠 = 4 张
        o = OFF["meld_prev"]  # 座位 3 = 上家
        self.assertEqual([p[o + 4 + k][5] for k in range(4)],
                         [1.0, 1.0, 1.0, 0.0])

    def test_river_frames(self):
        g = self._game()
        g.discards[2] = [3, 3, 4, 7, W]  # 5 张 → 帧 1,1,1,2
        p, _ = extract(g, 0)
        o = OFF["river_across"]
        self.assertEqual(p[o][3], 1.0)             # 帧0: 4m? 不,tile 3=4m
        self.assertEqual(p[o + 1][3], 1.0)         # 帧1: 第二张 4m
        self.assertEqual(p[o + 2][4], 1.0)         # 帧2: 5m
        # 帧3: 7m + 白
        self.assertEqual(p[o + 3][7], 1.0)
        self.assertEqual(p[o + 3][W], 1.0)
        self.assertEqual(sum(p[o + 3]), 2.0)

    def test_god_and_scalars(self):
        g = self._game()
        g.hands[0] = counts("123m456m789m123p5p w")  # 14 张含财神,刚摸白
        g.drawn[0] = W
        g.freeze, g.freezer = 2, 1
        g.chows[0] = 1
        p, s = extract(g, 0)
        self.assertEqual(set(p[OFF["god"]].tolist()), {0.25})
        self.assertAlmostEqual(float(s[2]), 2 / 3)
        self.assertEqual(float(s[3]), 1.0)  # seat 0 在抓打圈内
        self.assertAlmostEqual(float(s[4]), 0.5)
        self.assertEqual(list(s[5:]), [1.0, 0.0, 0.0])  # 摸打阶段

    def test_react_phase(self):
        g = self._game()
        g.phase = "react"
        g.pending = (1, 5)
        g.react_seq = [2, 3, 0, 0]
        g._n_claim, g.react_idx = 3, 0
        g.turn = 2
        g.hands[2] = counts("123m456m789m123p5p")  # 反应者 13 张无摸牌
        g.drawn[2] = None
        p, s = extract(g, 2)
        self.assertEqual(list(s[5:]), [0.0, 1.0, 0.0])  # 碰杠窗
        self.assertEqual(set(p[OFF["drawn"]].tolist()), {0.0})
        self.assertEqual(set(p[OFF["eng"] + 1].tolist()), {1.0})  # 无弃后地图
        g.react_idx = 3
        _, s = extract(g, 2)
        self.assertEqual(list(s[5:]), [0.0, 0.0, 1.0])  # 吃窗

    def test_eng_after_claim_discard(self):
        # 吃碰后的弃牌决策:drawn=None 但手牌 need+1 → 弃后地图照常,
        # 站立手取最优弃张后基准。碰 5m 后 11 张:123m789m11p22s3s
        g = self._game()
        g.hands[0] = counts("123m789m11p22s3s")
        g.melds[0] = [("pong", 4)]
        g.drawn[0] = None
        p, _ = extract(g, 0)
        post = p[OFF["eng"] + 1]
        # 打 2s/3s 听牌;打 1m/1p 退一向听;手中无 5p → 填充 1.0
        self.assertAlmostEqual(float(post[19]), 1 / 9)
        self.assertAlmostEqual(float(post[20]), 1 / 9)
        self.assertAlmostEqual(float(post[0]), 2 / 9)
        self.assertAlmostEqual(float(post[9]), 2 / 9)
        self.assertEqual(float(post[13]), 1.0)
        # 最优弃张(2s)后站立手听牌
        self.assertAlmostEqual(float(p[OFF["eng"]][0]), 1 / 9)

    def test_eng_post_discard_map(self):
        g = self._game()  # 摸 5p,base13 = 123m456m789m123p5p 听牌
        p, _ = extract(g, 0)
        post = p[OFF["eng"] + 1]
        # 弃 5p → base13 向听 0;弃 1m → 23m456m789m123p55p 也听牌
        self.assertAlmostEqual(float(post[13]), 1 / 9)
        self.assertAlmostEqual(float(post[0]), 1 / 9)
        self.assertEqual(float(post[33]), 1.0)  # 无白板 → 填充值
        # 进张图:base13 听 5p/白
        acc = p[OFF["eng"] + 3]
        self.assertEqual([acc[13], acc[W]], [1.0, 1.0])
        self.assertEqual(sum(acc), 2.0)
        # 进张未见张数:5p 手 2 张 + 白 0 → (4-2) + 4 = 6
        self.assertAlmostEqual(float(p[OFF["eng"] + 2][0]), 6 / 34)


if __name__ == "__main__":
    unittest.main()
