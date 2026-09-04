"""杭州麻将对局状态机。

面向自博弈/RL 的接口:Game(seed) 开始一局,legal_actions() 给当前行动者
的合法动作,step(action) 推进;done 后 scores 为四家本局得分。

规则要点(平台指南 v2,2026-09-02;有财必拷响为门户 v6+ config 项):
- 胡牌是显式动作(不强制报胡):摸牌/杠后补牌成胡由玩家提交 HU,也可
  弃胡继续打牌(飘/杠链前提);碰/吃后未摸牌不可胡(刚摸牌门禁)。
- 有财必拷响(you_cai_bi_kao,锦标赛 config.YouCaiBiKao):开启时手中
  有财神则平胡被门禁——须爆头(摸前站立手听任意牌)或杠开(杠后
  补牌成胡)才可胡;仅门禁合法性,不改番型计算。
- 动作链:杠与飘每个动作番数 ×2,可连续可组合;飘 = 爆头状态打出
  财神(打出后站立手牌仍听任意牌);打出其他牌(含非爆头态打白板)
  断链清零;链内飘出的白板计入 4 白板番。
- 财神(白板)不能被吃/碰/杠;可主动打出。打出财神触发抓打圈:
  其余玩家一圈内不能吃碰明杠(仅暗杠与自摸胡),出牌只能打刚摸的牌。
- 碰(含明杠)窗口先于吃窗口;吃最多 2 摊;碰杠不限。
- 最后 10 墩(20 张)保留不摸,之内禁止杠牌;摸完无人胡则流局。
- 庄家 ×8 直上;和牌或流局的连庄由上层锦标赛处理。

动作编码:0..33 弃牌;负数为特殊动作。杠动作内嵌牌种:
KONG_CLOSED = -7-t (t=0..33), KONG_ADD = -41-t。
"""

import random

from .tiles import W
from .win import is_win, is_baotou
from .scoring import hand_multiplier, settle

FULL_DECK = [t for t in range(34) for _ in range(4)]
DEAD_WALL = 20  # 最后 10 墩
CHOW_LIMIT = 2

PASS = -1
CHOW_LOW = -2  # 吃的牌在顺子中位置:低
CHOW_MID = -3  # 中
CHOW_HIGH = -4  # 高
PONG = -5
KONG_OPEN = -6
KONG_CLOSED_BASE = -7  # -7-t, t=0..33
KONG_ADD_BASE = -41  # -41-t
HU = -75  # 自摸胡(要求刚摸牌)

_KONG_KINDS = ("kong_closed", "kong_open", "kong_add")


class Game:
    def __init__(self, seed=None, dealer=0, base=1, you_cai_bi_kao=False):
        self.dealer = dealer
        self.base = base
        self.you_cai_bi_kao = you_cai_bi_kao
        self._kong_draw = False  # 当前 drawn 是否为杠后补牌(有财必拷响用)
        self.rng = random.Random(seed)
        wall = FULL_DECK[:]
        self.rng.shuffle(wall)
        self.hands = [[0] * 34 for _ in range(4)]
        for _ in range(13):
            for s in range(4):
                self.hands[s][wall.pop()] += 1
        self.wall = wall
        self.melds = [[] for _ in range(4)]
        self.discards = [[] for _ in range(4)]
        self.drawn = [None] * 4
        self.chows = [0] * 4
        self.turn = dealer
        self.phase = "discard"
        self.pending = None  # react 中:(出牌者, 牌)
        self.freeze = 0  # 抓打圈剩余回合数
        self.freezer = None  # 打出财神触发抓打圈的座位
        self.chain = [0] * 4  # 动作链次数:飘/杠累计,断链清零
        self.chain_piao = [0] * 4  # 当前链内飘出的白板数
        self.scores = [0] * 4
        self.done = False
        self.result = None  # (winner, mult, parts) 或 None=流局
        self._draw(dealer)

    # ---------- 查询 ----------

    def live_wall_left(self):
        return len(self.wall) - DEAD_WALL

    def in_freeze(self, seat):
        return self.freeze > 0 and seat != self.freezer

    def visible_counts(self, seat):
        """seat 视角的可见牌(自己手牌+四家牌河+全部副露,杠计 4 张)。

        即 ukeire(visible=...) 的口径:含手牌本身,进张按 4-vis 折算。
        """
        vis = [0] * 34
        for t, n in enumerate(self.hands[seat]):
            vis[t] += n
        for d in self.discards:
            for t in d:
                vis[t] += 1
        for ms in self.melds:
            for kind, t in ms:
                if kind == "chow":
                    vis[t] += 1
                    vis[t + 1] += 1
                    vis[t + 2] += 1
                elif kind.startswith("kong"):
                    vis[t] += 4
                else:
                    vis[t] += 3
        return vis

    def current_seat(self):
        return self.turn

    def react_mode(self):
        """react 阶段当前询问的模式:"claim"(碰/明杠) 或 "chow"。"""
        return "claim" if self.react_idx < self._n_claim else "chow"

    # ---------- 合法动作 ----------

    def legal_actions(self):
        if self.done:
            return []
        if self.phase == "discard":
            acts = self._legal_discards() + self._legal_kongs()
            if self._can_hu(self.turn):
                acts.insert(0, HU)
            return acts
        return self._legal_reacts()

    def _can_hu(self, seat):
        """刚摸牌门禁:碰/吃/杠后未摸牌不可胡;摸到的牌成胡即可提交。

        有财必拷响开启时,手中有财神(白板)还须爆头(摸前站立手听
        任意牌)或杠开(当前 drawn 为杠后补牌)才可胡——平胡/普通
        七对被门禁。爆头/杠开胡的倍率由原番型公式自然给出。
        """
        if (
            self.drawn[seat] is None
            or not is_win(self.hands[seat], len(self.melds[seat]))
        ):
            return False
        if self.you_cai_bi_kao and self.hands[seat][W] > 0:
            if self._kong_draw:
                return True
            standing = list(self.hands[seat])
            standing[self.drawn[seat]] -= 1
            return is_baotou(standing, len(self.melds[seat]))
        return True

    def _legal_discards(self):
        seat, h = self.turn, self.hands[self.turn]
        if self.in_freeze(seat):
            return [self.drawn[seat]]
        return [t for t in range(34) if h[t] > 0]

    def _legal_kongs(self):
        seat = self.turn
        if self.drawn[seat] is None or self.live_wall_left() <= 0:
            return []
        acts, h, frozen = [], self.hands[seat], self.in_freeze(seat)
        for t in range(33):  # 财神不可杠
            if h[t] == 4:
                acts.append(KONG_CLOSED_BASE - t)
            elif h[t] == 1 and not frozen and self._has_open_pong(t):
                acts.append(KONG_ADD_BASE - t)
        return acts

    def _has_open_pong(self, tile):
        return any(m[0] == "pong" and m[1] == tile for m in self.melds[self.turn])

    def _legal_reacts(self):
        seat = self.turn
        owner, tile = self.pending
        acts = [PASS]
        if tile == W:
            return acts
        h = self.hands[seat]
        if self.react_mode() == "claim":
            if h[tile] >= 2:
                acts.append(PONG)
            if h[tile] == 3 and self.live_wall_left() > 0:
                acts.append(KONG_OPEN)
        else:
            if tile < 27 and self.chows[seat] < CHOW_LIMIT:
                lo = tile - tile % 9
                for pos in range(3):
                    a = tile - pos
                    if a >= lo and a + 2 < lo + 9:
                        if all(h[x] > 0 for x in (a, a + 1, a + 2) if x != tile):
                            acts.append(CHOW_LOW - pos)
        return acts

    # ---------- 推进 ----------

    def step(self, action):
        assert not self.done, "对局已结束"
        if self.phase == "discard":
            if action == HU:
                self._do_hu(self.turn)
            elif action >= 0:
                self._do_discard(self.turn, action)
            elif KONG_CLOSED_BASE - 33 <= action <= KONG_CLOSED_BASE:
                self._do_kong_closed(self.turn, KONG_CLOSED_BASE - action)
            elif KONG_ADD_BASE - 33 <= action <= KONG_ADD_BASE:
                self._do_kong_add(self.turn, KONG_ADD_BASE - action)
            else:
                raise ValueError(f"非法动作 {action}")
        else:
            self._do_react(self.turn, action)

    def _do_discard(self, seat, tile):
        h = self.hands[seat]
        if h[tile] <= 0:
            raise ValueError(f"手中无牌 {tile}")
        # 飘(财飘):爆头状态打出财神,打出后站立手牌仍听任意牌
        # ("继续听任意牌胡")。判定自带弃胡语义:打白前的 14 张
        # (站立+财神)必为成胡牌型,打出财神即弃胡。
        piao = False
        if tile == W:
            after = list(h)
            after[W] -= 1
            piao = is_baotou(after, len(self.melds[seat]))
        h[tile] -= 1
        self.discards[seat].append(tile)
        self.drawn[seat] = None
        if piao:
            self.chain[seat] += 1
            self.chain_piao[seat] += 1
        else:
            self.chain[seat] = 0
            self.chain_piao[seat] = 0
        if self.freeze > 0:
            self.freeze -= 1
        if tile == W:
            self.freezer = seat
            self.freeze = 3
            self._next_draw(seat)  # 财神无人可反应
        else:
            self._begin_react(seat, tile)

    def _do_hu(self, seat):
        if not self._can_hu(seat):
            raise ValueError("胡牌要求刚摸牌且成牌")
        self._win(seat)

    def _do_kong_closed(self, seat, t):
        if self.drawn[seat] is None or self.hands[seat][t] != 4:
            raise ValueError("非暗杠牌型")
        if self.live_wall_left() <= 0:
            raise ValueError("最后 10 墩内禁止杠牌")
        self.hands[seat][t] -= 4
        self.melds[seat].append(("kong_closed", t))
        self.chain[seat] += 1
        self._draw(seat, kong=True)

    def _do_kong_add(self, seat, t):
        if self.drawn[seat] is None or self.hands[seat][t] != 1 or not self._has_open_pong(t):
            raise ValueError("非加杠牌型")
        if self.live_wall_left() <= 0:
            raise ValueError("最后 10 墩内禁止杠牌")
        self.hands[seat][t] -= 1
        for i, m in enumerate(self.melds[seat]):
            if m[0] == "pong" and m[1] == t:
                self.melds[seat][i] = ("kong_add", t)
                break
        self.chain[seat] += 1
        self._draw(seat, kong=True)

    # ---------- 反应阶段 ----------

    def _begin_react(self, owner, tile):
        self.pending = (owner, tile)
        claims = [(owner + k) % 4 for k in (1, 2, 3) if not self.in_freeze((owner + k) % 4)]
        chow_seat = (owner + 1) % 4
        has_chow = not self.in_freeze(chow_seat)
        self.react_seq = claims + ([chow_seat] if has_chow else [])
        self._n_claim = len(claims)
        self.react_idx = 0
        self.phase = "react"
        if not self.react_seq:
            self._next_draw(owner)
        else:
            self.turn = self.react_seq[0]

    def _do_react(self, seat, action):
        owner, tile = self.pending
        if action == PASS:
            self.react_idx += 1
            if self.react_idx >= len(self.react_seq):
                self.pending = None
                self._next_draw(owner)
            else:
                self.turn = self.react_seq[self.react_idx]
            return
        if tile == W:
            raise ValueError("财神不可吃碰杠")
        h = self.hands[seat]
        mode = self.react_mode()
        if action == PONG and mode == "claim" and h[tile] >= 2:
            h[tile] -= 2
            self._pop_pending_discard(owner, tile)
            self.melds[seat].append(("pong", tile))
            self._after_claim(seat)
        elif action == KONG_OPEN and mode == "claim" and h[tile] == 3 and self.live_wall_left() > 0:
            h[tile] -= 3
            self._pop_pending_discard(owner, tile)
            self.melds[seat].append(("kong_open", tile))
            self.chain[seat] += 1
            self._after_claim(seat, replacement=True)
        elif action in (CHOW_LOW, CHOW_MID, CHOW_HIGH) and mode == "chow":
            if self.chows[seat] >= CHOW_LIMIT:
                raise ValueError("吃牌已达上限")
            pos = CHOW_LOW - action
            a = tile - pos
            for x in (a, a + 1, a + 2):
                if x != tile:
                    h[x] -= 1
            self._pop_pending_discard(owner, tile)
            self.melds[seat].append(("chow", a))
            self.chows[seat] += 1
            self._after_claim(seat)
        else:
            raise ValueError(f"非法反应动作 {action}")

    def _pop_pending_discard(self, owner, tile):
        d = self.discards[owner]
        assert d and d[-1] == tile, "牌河顶牌不匹配"
        d.pop()

    def _after_claim(self, seat, replacement=False):
        self.pending = None
        self.drawn[seat] = None
        self.turn = seat
        self.phase = "discard"
        if replacement:
            self._draw(seat, kong=True)  # 明杠补牌 = 杠开判定依据

    def _next_draw(self, owner):
        self._draw((owner + 1) % 4)

    # ---------- 摸牌与终局 ----------

    def _draw(self, seat, kong=False):
        if len(self.wall) <= DEAD_WALL:
            self._end_draw()
            return
        t = self.wall.pop()
        self.drawn[seat] = t
        self.hands[seat][t] += 1
        self._kong_draw = kong
        self.turn = seat
        self.phase = "discard"

    def _win(self, seat):
        locked = len(self.melds[seat])
        before = list(self.hands[seat])
        before[self.drawn[seat]] -= 1
        mult, parts = hand_multiplier(
            self.hands[seat], before, locked,
            self.chain[seat], self.chain_piao[seat],
        )
        self.scores = settle(seat, self.dealer, mult, self.base)
        self.result = (seat, mult, parts)
        self.done = True

    def _end_draw(self):
        self.done = True
        self.result = None
