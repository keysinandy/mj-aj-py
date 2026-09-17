"""事件源公共状态镜像:平台事件 → 引擎 Game(决策点构建)。

实时事件流只含本人的摸牌、他家手牌不可见,因此不能用引擎 step() 推进
全局;Mirror 增量消费公开事件,维护「自有手牌(精确)+ 公共信息(四家
牌河/副露/吃摊数/冻结/墙数)+ 自有私有标志」,在本人决策点用
Game.__new__ 模式(tests/test_game.py setup() 同款)构建 Game,供
legal_actions()/features.extract()/policy 决策。

关键不变量:
- 合法性自洽:legal_actions() 只读自有手牌/公共牌河副露/墙长/冻结/
  自有吃摊数/自有标志——全部由 Mirror 精确维护,他家暗手不参与
  (hands 其他位置填零向量,永不读取);
- 墙长精确:live_wall_left = 64 − _pops;_pops 按事件计(自家摸牌事件
  +1;他家摸牌不可见,在其打牌时按 expects_draw 状态机补计;他家杠
  补牌在 gang 事件计、自家杠补牌在自家 tile_drawn 计);
- 失步恢复:任何断言失败(MirrorInconsistent)→ 上层 seq=0 重建。
"""

import random

from ..game import Game, CHOW_LOW
from ..tiles import W
from ..win import is_baotou
from .proto import (
    tidx, parse_event, EV_DRAWN, EV_DISCARDED, EV_PASS, EV_CHI, EV_PENG,
    EV_GANG, EV_HU, EV_TIMEOUT, EV_ROUND_ENDED, EV_GAME_ENDED,
)


class MirrorInconsistent(Exception):
    """事件流与镜像状态不符——上层应 seq=0 重建并告警。"""


# The current v34 snapshot does not expose this field, but keeping the origin
# explicit prevents a FULL/gap rebuild from silently turning a kong
# replacement draw into an ordinary draw. ``kong_draw`` remains the
# compatibility projection consumed by Game and older callers.
DRAW_ORIGIN_NORMAL = "NORMAL"
DRAW_ORIGIN_KONG_REPLACEMENT = "KONG_REPLACEMENT"


class Mirror:
    """单局(round)、单座位的事件源镜像。一局一个实例。"""

    def __init__(self, my_seat, dealer, base=1, you_cai_bi_kao=False,
                 round_no=1):
        self.me = my_seat
        self.dealer = dealer
        self.base = base
        self.you_cai_bi_kao = you_cai_bi_kao
        self.round_no = round_no
        self.my_hand = [0] * 34
        self.melds = [[] for _ in range(4)]
        self.discards = [[] for _ in range(4)]
        self.drawn = None            # 自家刚摸牌(牌河阶段前)
        self.pending = None          # (owner, tile) 反应窗
        self.chows = [0] * 4
        self.freeze = 0
        self.freezer = None
        self.chain = 0               # 自家动作链(其他家不跟踪,决策无关)
        self.chain_piao = 0
        self.baotou = None            # 当前 standing hand 是否爆头
        self.draw_origin = None       # NORMAL | KONG_REPLACEMENT | None
        self.kong_draw = False       # draw_origin 的兼容布尔投影
        self.round_ended = None      # round_ended 事件 data(结算对账)
        self.game_ended = False
        # 墙长:_pops 含庄家直抽(=1 起步)
        self._pops = 1
        self._expects_draw = [True] * 4
        self._expects_draw[dealer] = False
        self._own_gang_replacement = False
        # 已响应窗口:(round_no, 弃牌事件序号, 窗口种类)
        self._responded = set()
        self._n_discard_events = 0
        # Optional authoritative response cursor.  ``responding_seats`` is
        # only an authorization projection and is deliberately not enough to
        # reconstruct priority order or completed responses.
        self.response_order = ()
        self.response_index = None
        self.response_claim_count = None
        self.response_source = None
        # 四家公开暗手张数：只保存计数，不保存对手牌面。全量快照是
        # 唯一的权威锚点；事件只能在变化可证明时推进，否则失效等待重锚。
        self.public_hand_counts = None
        self.public_hand_counts_source = None
        self.public_hand_counts_status = "unknown"

    # ---------- 锚点:全量快照(实测字段,2026-09-08 探针) ----------

    def _set_public_hand_counts(self, snap):
        """校验快照公开张数并建立新的物料锚点。"""
        raw = snap.get("hand_counts")
        if raw is None:
            self.public_hand_counts = None
            self.public_hand_counts_source = "unknown"
            self.public_hand_counts_status = "unknown"
            return
        try:
            raw_counts = tuple(raw)
        except TypeError:
            raw_counts = ()
        valid = (len(raw_counts) == 4
                 and all(type(x) is int and x >= 0 for x in raw_counts))
        counts = tuple(raw_counts) if valid else ()
        if valid:
            hand_count = sum(self.my_hand)
            valid = counts[self.me] == hand_count
        meld_counts = [len(row) for row in self.melds]
        phase = snap.get("phase")
        turn = snap.get("turn")
        if valid:
            for seat, count in enumerate(counts):
                base = max(0, 13 - 3 * meld_counts[seat])
                # 快照可能落在任一家的摸后弃牌前；除当前 draw actor
                # 外，平台没有公开摸牌事件，故允许两种站牌长度，随后由
                # 事件状态机决定是否还能继续使用该锚点。
                allowed = {base, base + 1}
                if count not in allowed:
                    valid = False
                    break
        self.public_hand_counts = counts if valid else None
        self.public_hand_counts_source = "snapshot" if valid else "unknown"
        self.public_hand_counts_status = "verified" if valid else "malformed"

    def _advance_public_hand_count(self, seat, delta):
        """推进可证明的计数；不确定或越界时整组标为 unknown。"""
        if self.public_hand_counts_status != "verified":
            return
        if not 0 <= int(seat) < 4:
            self.public_hand_counts = None
            self.public_hand_counts_source = "unknown"
            self.public_hand_counts_status = "unknown"
            return
        counts = list(self.public_hand_counts)
        counts[seat] += int(delta)
        if counts[seat] < 0:
            self.public_hand_counts = None
            self.public_hand_counts_source = "unknown"
            self.public_hand_counts_status = "malformed"
            return
        self.public_hand_counts = tuple(counts)
        self.public_hand_counts_source = "event_advanced"

    def _invalidate_public_hand_counts(self, status="unknown"):
        self.public_hand_counts = None
        self.public_hand_counts_source = status
        self.public_hand_counts_status = status

    def public_material_projection(self):
        """返回不含牌面身份的公开计数投影。"""
        return (self.public_hand_counts, self.public_hand_counts_source,
                self.public_hand_counts_status)

    # ---------- 锚点:全量快照(实测字段,2026-09-08 探针) ----------

    _MELD_KIND = {
        "chi": "chow", "peng": "pong",
        "gang_ming": "kong_open", "ming": "kong_open",
        "gang_an": "kong_closed", "an": "kong_closed",
        "gang_bu": "kong_add", "bu": "kong_add",
    }

    def apply_snapshot(self, snap):
        """seq=0/gap/轮边界的全量快照 → 镜像重建(快照为规范真相)。

        实测快照字段:seat/phase/turn/responding_seats/drawn_tile/my_hand/
        god{baotou,chain_count,catch_play}/round_no/dealer/wall_remaining/
        discards(四家牌河,含 pending 牌)/melds[{kind,tiles}]/hand_counts/
        last_discard/scores。公共状态按快照全量重建(自愈);draw_origin 优先
        使用未来显式字段,当前 v34 无该字段时按规则安全推导;响应阶段
        turn = 出牌者(实测),pending = (turn, last_discard)。
        """
        if snap.get("round_no") is not None:
            self.round_no = snap["round_no"]
        if snap.get("dealer") is not None:
            self.dealer = snap["dealer"]
        hand = snap.get("my_hand")
        if hand is not None:
            h = [0] * 34
            for name in hand:
                h[tidx(name)] += 1
            self.my_hand = h
        d = snap.get("drawn_tile")
        self.drawn = tidx(d) if d else None
        god = snap.get("god") or {}
        if "chain_count" in god:
            self.chain = god["chain_count"]
        # 公共状态全量重建
        if snap.get("discards") is not None:
            self.discards = [[tidx(n) for n in river]
                             for river in snap["discards"]]
        if snap.get("melds") is not None:
            self.melds = [self._parse_melds(ms) for ms in snap["melds"]]
            self.chows = [sum(1 for kind, _ in ms if kind == "chow")
                          for ms in self.melds]
        self._set_public_hand_counts(snap)
        if snap.get("wall_remaining") is not None:
            # wall_remaining 含死墙(开局 83);_pops 含庄家直抽(=1)
            self._pops = 84 - snap["wall_remaining"]
        # 响应窗:pending = (出牌者, last_discard);turn 实测为出牌者
        phase = snap.get("phase")
        self._restore_response_cursor(snap, phase)
        self._restore_draw_origin(snap, god, phase)
        ld = snap.get("last_discard")
        if phase in ("react", "response_peng", "response_chi") and ld:
            owner = snap.get("turn")
            if owner is not None and 0 <= owner <= 3:
                self.pending = (owner, tidx(ld))
            else:
                self.pending = None
        else:
            # A FULL snapshot is authoritative. Do not let a previous
            # response window survive a settled/draw rebuild.
            self.pending = None
        # 抓打圈:gap 快照必须恢复发起者与剩余冻结弃牌数。catch_play 是
        # 本人视角（发起者自己会是 false），故以 god_discarder_seat 为准。
        freezer = god.get("god_discarder_seat")
        if isinstance(freezer, int) and 0 <= freezer <= 3:
            self._rebuild_freeze(snap, freezer)
        elif god.get("catch_play"):
            if self.freeze > 0 and self.freezer != self.me:
                # 增量事件已知发起者的旧快照（synth/早期协议不带字段）：
                # 保留精确状态，不能用保守值覆盖它。
                pass
            else:
                # 新建镜像且旧快照没有发起者时宁可保守冻结整圈，不能放宽
                # 出牌/反应合法集造成 409；下一份 catch_play=false 快照清除。
                self.freeze, self.freezer = 3, -1
        elif self.freezer == -1 and self.freeze > 0:
            self.freeze = 0

    def _restore_response_cursor(self, snap, phase):
        """Restore an explicit ordered response cursor when the protocol has one.

        A snapshot's ``responding_seats`` may omit seats that already passed,
        so deriving ``react_seq`` from it would manufacture a different
        continuation.  Unknown/partial response metadata remains unsupported
        for v2 teacher evaluation and keeps the legacy placeholder path.
        """
        if phase not in ("react", "response_peng", "response_chi"):
            self.response_order = ()
            self.response_index = None
            self.response_claim_count = None
            self.response_source = None
            return
        nested = snap.get("response")
        nested = nested if isinstance(nested, dict) else {}
        missing = object()

        def pick(*names):
            for name in names:
                if name in snap:
                    return snap[name]
                if name in nested:
                    return nested[name]
            return missing

        order = pick("response_order", "responseOrder", "react_seq",
                     "reactSeq", "order")
        index = pick("response_index", "responseIndex", "react_index",
                     "reactIndex", "react_idx", "reactIdx", "index")
        claim_count = pick("response_claim_count", "responseClaimCount",
                           "react_claim_count", "reactClaimCount",
                           "claim_count", "claimCount", "n_claim", "nClaim")
        if order is missing or index is missing or claim_count is missing:
            self.response_order = ()
            self.response_index = None
            self.response_claim_count = None
            self.response_source = None
            return
        try:
            order = tuple(int(x) for x in order)
            index = int(index)
            claim_count = int(claim_count)
        except (TypeError, ValueError) as exc:
            raise MirrorInconsistent(
                f"响应游标字段格式错误: order={order!r}, index={index!r}, "
                f"claim_count={claim_count!r}") from exc
        if (not order or any(x < 0 or x >= 4 for x in order) or
                not 0 <= index < len(order) or
                not 0 <= claim_count <= len(order)):
            raise MirrorInconsistent(
                f"响应游标越界: order={order!r}, index={index}, "
                f"claim_count={claim_count}")
        self.response_order = order
        self.response_index = index
        self.response_claim_count = claim_count
        self.response_source = "explicit_snapshot"

    def _restore_draw_origin(self, snap, god, phase):
        """Restore the current draw source across FULL/gap snapshots.

        v34 snapshots observed in the platform do not carry a dedicated
        ``kong_draw`` field. For a snapshot at our actionable draw, a
        positive action chain plus a non-baotou standing hand is the safe
        rule-backed inference for a kong replacement. A piao chain keeps a
        baotou standing hand and therefore does not get misclassified. If a
        future protocol exposes an explicit origin, it wins over inference.
        """
        self._own_gang_replacement = False
        self.draw_origin = None
        self.kong_draw = False

        baotou = god.get("baotou")
        if isinstance(baotou, bool):
            self.baotou = baotou
        elif self.drawn is not None and self.drawn < len(self.my_hand):
            standing = list(self.my_hand)
            if standing[self.drawn] > 0:
                standing[self.drawn] -= 1
            self.baotou = is_baotou(standing, len(self.melds[self.me]))
        else:
            self.baotou = None

        missing = object()
        raw = snap.get("draw_origin", missing)
        if raw is missing:
            raw = god.get("draw_origin", missing)
        if raw is missing:
            raw = snap.get("kong_draw", missing)
        if raw is missing:
            raw = god.get("kong_draw", missing)

        origin = None
        if raw is not missing:
            if isinstance(raw, bool):
                origin = (DRAW_ORIGIN_KONG_REPLACEMENT if raw
                          else DRAW_ORIGIN_NORMAL)
            elif isinstance(raw, str):
                normalized = raw.strip().upper().replace("-", "_")
                if normalized in ("NORMAL", "NORMAL_DRAW"):
                    origin = DRAW_ORIGIN_NORMAL
                elif normalized in ("KONG_REPLACEMENT", "KONG_DRAW",
                                    "GANG_DRAW"):
                    origin = DRAW_ORIGIN_KONG_REPLACEMENT

        actionable_draw = (self.drawn is not None and phase == "draw"
                           and snap.get("turn") == self.me)
        if origin is None and actionable_draw and self.chain > 0 \
                and self.baotou is False:
            # v33/v34 rule path: non-baotou chain draw is the kong
            # replacement window, including after an seq=0/gap rebuild.
            origin = DRAW_ORIGIN_KONG_REPLACEMENT
        elif origin is None and self.drawn is not None:
            origin = DRAW_ORIGIN_NORMAL

        self.draw_origin = origin
        self.kong_draw = origin == DRAW_ORIGIN_KONG_REPLACEMENT

    def _rebuild_freeze(self, snap, freezer):
        """用快照牌河+phase 精确还原抓打圈，失败时保守降级。

        白板弃牌后 freeze=3；每张后续弃牌先消耗一次。发起者若在圈内
        认领后再次弃牌，牌河中白板后的额外一张需要纳入计数。
        """
        river = self.discards[freezer]
        last_white = max((i for i, t in enumerate(river) if t == W), default=-1)
        turn = snap.get("turn")
        phase = snap.get("phase")
        consumed = -1
        if last_white >= 0 and isinstance(turn, int) and 0 <= turn <= 3:
            post_white = len(river) - last_white - 1
            if phase == "draw":
                consumed = ((turn - freezer - 1) % 4) + 2 * post_white
            elif phase in ("response_peng", "response_chi") and post_white == 0:
                consumed = (turn - freezer) % 4
        if 0 <= consumed <= 2:
            self.freeze, self.freezer = 3 - consumed, freezer
        else:
            # 同一坏快照重拉会循环，不能抛失步；保守近似保证不放宽动作。
            self.freeze, self.freezer = 1, -1

    @classmethod
    def _parse_melds(cls, raw_melds):
        out = []
        for m in raw_melds or []:
            kind_raw = m.get("kind", "")
            tiles = [tidx(n) for n in (m.get("tiles") or [])]
            kind = cls._MELD_KIND.get(kind_raw)
            if kind is None:
                if kind_raw.startswith("gang"):
                    kind = {"an": "kong_closed", "ming": "kong_open",
                            "bu": "kong_add"}.get(
                        kind_raw.replace("gang_", ""), "kong_open")
                elif kind_raw == "pong":
                    kind = "pong"
                else:
                    raise MirrorInconsistent(f"未知副露类型 {m}")
            if kind == "chow":
                out.append(("chow", min(tiles)))
            else:
                out.append((kind, tiles[0]))
        return out

    # ---------- 事件应用 ----------

    def apply_event(self, ev):
        """平台原始事件 dict → 状态推进;违规抛 MirrorInconsistent。"""
        e = parse_event(ev)
        t = e["type"]
        if t == EV_DRAWN:
            self._on_tile_drawn(e)
        elif t == EV_DISCARDED:
            self._on_tile_discarded(e)
        elif t == EV_PASS:
            pass  # 标注事件;窗口防重由快照层负责
        elif t == EV_CHI:
            self._on_claim(e, "chi")
        elif t == EV_PENG:
            self._on_claim(e, "peng")
        elif t == EV_GANG:
            self._on_gang(e)
        elif t == EV_HU:
            pass  # 结算在 round_ended
        elif t == EV_TIMEOUT:
            pass  # 超时代打的 tile_discarded 已先行应用
        elif t == EV_ROUND_ENDED:
            self.round_ended = e.get("data") or {}
        elif t == EV_GAME_ENDED:
            self.game_ended = True

    def _on_tile_drawn(self, e):
        if e["seat"] != self.me:
            # 他家摸牌不可见，不能据此把公开计数推进到某个确定值。
            self._invalidate_public_hand_counts("unknown")
            return  # 墙数在其打牌时补计
        t = e["tile"]
        if t is None:
            raise MirrorInconsistent(f"tile_drawn 缺牌: {e}")
        self.my_hand[t] += 1
        self._advance_public_hand_count(self.me, 1)
        self.drawn = t
        self._pops += 1
        self.draw_origin = (DRAW_ORIGIN_KONG_REPLACEMENT
                            if self._own_gang_replacement
                            else DRAW_ORIGIN_NORMAL)
        self.kong_draw = self.draw_origin == DRAW_ORIGIN_KONG_REPLACEMENT
        standing = list(self.my_hand)
        standing[t] -= 1
        self.baotou = is_baotou(standing, len(self.melds[self.me]))
        self._own_gang_replacement = False

    def _on_tile_discarded(self, e):
        s, t = e["seat"], e["tile"]
        if t is None:
            raise MirrorInconsistent(f"tile_discarded 缺牌: {e}")
        if s == self.me:
            self._check(self.my_hand[t] > 0, f"自家打牌不在手: {e}")
            self.my_hand[t] -= 1
            self._advance_public_hand_count(s, -1)
            self.drawn = None
            self.draw_origin = None
            self.kong_draw = False
            self.baotou = None
            # 自家断链/飘(chain 供 god 对账;决策不依赖)
            if t == W:
                after = list(self.my_hand)
                if is_baotou(after, len(self.melds[self.me])):
                    self.chain += 1
                    self.chain_piao += 1
                else:
                    self.chain = 0
                    self.chain_piao = 0
            else:
                self.chain = 0
                self.chain_piao = 0
        else:
            self._advance_public_hand_count(s, -1)
            if self._expects_draw[s]:
                self._pops += 1  # 他家摸牌不可见,打牌时补计
            self._invalidate_public_hand_counts("unknown")
        self._expects_draw[s] = True
        # 冻结语义与引擎 _do_discard 同序:先减后设
        if self.freeze > 0:
            self.freeze -= 1
        if t == W:
            self.freezer = s
            self.freeze = 3
        self.discards[s].append(t)
        self.pending = (s, t)
        self._n_discard_events += 1

    def _on_claim(self, e, kind):
        s, t = e["seat"], e["tile"]
        if self.pending is None:
            raise MirrorInconsistent(f"{kind} 无被反应牌: {e}")
        owner, ptile = self.pending
        self._check(ptile == t, f"{kind} 牌与 pending 不符: {e} vs {ptile}")
        d = self.discards[owner]
        self._check(bool(d) and d[-1] == t, f"{kind} 牌河顶不匹配: {e}")
        d.pop()
        if kind == "chi":
            tiles = e.get("tiles")
            if not tiles or len(tiles) != 2:
                raise MirrorInconsistent(f"chi 缺两张手牌: {e}")
            a = min(min(tiles), t)
            self.melds[s].append(("chow", a))
            self.chows[s] += 1
            if s == self.me:
                for x in tiles:
                    self._check(self.my_hand[x] > 0, f"chi 手牌不足: {e}")
                    self.my_hand[x] -= 1
                self._advance_public_hand_count(s, -2)
        else:
            self.melds[s].append(("pong", t))
            if s == self.me:
                self._check(self.my_hand[t] >= 2, f"peng 手牌不足: {e}")
                self.my_hand[t] -= 2
                self._advance_public_hand_count(s, -2)
        if s == self.me:
            self.drawn = None
            self.draw_origin = None
            self.kong_draw = False
            self.baotou = None
        self.pending = None
        self._expects_draw[s] = False

    def _on_gang(self, e):
        s, t = e["seat"], e["tile"]
        kind = e.get("kind") or "ming"
        if t is None:
            raise MirrorInconsistent(f"gang 缺牌: {e}")
        if kind == "ming":
            if self.pending is None:
                raise MirrorInconsistent(f"明杠无被反应牌: {e}")
            owner, ptile = self.pending
            self._check(ptile == t, f"明杠牌与 pending 不符: {e}")
            d = self.discards[owner]
            self._check(bool(d) and d[-1] == t, f"明杠牌河顶不匹配: {e}")
            d.pop()
            self.melds[s].append(("kong_open", t))
            if s == self.me:
                self._check(self.my_hand[t] >= 3, f"明杠手牌不足: {e}")
                self.my_hand[t] -= 3
                self._advance_public_hand_count(s, -3)
            self.pending = None
        elif kind == "an":
            self.melds[s].append(("kong_closed", t))
            if s == self.me:
                self._check(self.my_hand[t] == 4, f"暗杠手牌不足: {e}")
                self.my_hand[t] -= 4
                self._advance_public_hand_count(s, -4)
        else:  # bu 加杠
            found = False
            for i, m in enumerate(self.melds[s]):
                if m[0] == "pong" and m[1] == t:
                    self.melds[s][i] = ("kong_add", t)
                    found = True
                    break
            self._check(found, f"加杠无既有碰副露: {e}")
            if s == self.me:
                self._check(self.my_hand[t] >= 1, f"加杠手牌不足: {e}")
                self.my_hand[t] -= 1
                self._advance_public_hand_count(s, -1)
        if s == self.me:
            self.chain += 1
            self._own_gang_replacement = True  # 补牌将以自家 tile_drawn 到达
            self.drawn = None
            self.draw_origin = None
            self.kong_draw = False
            self.baotou = None
        else:
            if kind in ("an", "bu") and self._expects_draw[s]:
                # 暗杠/加杠前必有一次原始摸牌(drawn 门禁),该回合
                # 实际消耗 2 pop:原摸在此计,补牌在下面计
                self._pops += 1
            self._pops += 1  # 他家杠补牌:在 gang 事件计
        self._expects_draw[s] = False

    # ---------- 决策点构建 ----------

    def live_wall_left(self):
        return max(64 - self._pops, 0)

    def n_discards(self):
        return sum(len(d) for d in self.discards)

    def window_key(self, phase):
        """反应窗防重键:(局号, 弃牌事件序号, 窗口种类)。

        弃牌事件序号单调递增(claim 会从牌河弹牌,不能按牌河长度计)。
        """
        kind = "peng" if phase == "response_peng" else "chi"
        return (self.round_no, self._n_discard_events, kind)

    def mark_responded(self, phase):
        self._responded.add(self.window_key(phase))

    def already_responded(self, phase):
        return self.window_key(phase) in self._responded

    def hand_count_ok(self, phase):
        """决策前手牌张数与副露/阶段是否自洽(不自洽 → False)。

        吃碰后 timeout kind=hu_failed 服务端跳过弃牌,手牌自此比引擎
        预期多 1 张(match 实测 2026-09-08,5/1405 次认领);张数异常时
        客户端交服务端代打,轮边界快照重建后自愈。
        """
        need = 13 - 3 * len(self.melds[self.me])
        n = sum(self.my_hand)
        if phase == "draw":
            return n in (need, need + 1)  # 吃碰后 / 刚摸
        return n == need

    def build_game(self, phase):
        """按决策阶段构建引擎 Game(仅当前决策所需字段,他家暗手置零)。

        phase ∈ {"draw", "react", "response_peng", "response_chi"}。
        """
        g = Game.__new__(Game)
        g.dealer = self.dealer
        g.base = self.base
        g.you_cai_bi_kao = self.you_cai_bi_kao
        g.rng = random.Random(0)
        g.hands = [[0] * 34 for _ in range(4)]
        g.hands[self.me] = list(self.my_hand)
        # 仅传递公开张数及其状态；对手牌面仍保持全零占位。
        counts, source, status = self.public_material_projection()
        g.public_hand_counts = counts
        g.public_hand_counts_source = source
        g.public_hand_counts_status = status
        g.wall = [0] * (20 + self.live_wall_left())
        g.melds = [list(m) for m in self.melds]
        g.discards = [list(d) for d in self.discards]
        g.drawn = [None] * 4
        g.drawn[self.me] = self.drawn
        g.chows = list(self.chows)
        g.freeze = self.freeze
        g.freezer = self.freezer
        g.chain = [0] * 4
        g.chain[self.me] = self.chain
        g.chain_piao = [0] * 4
        g.chain_piao[self.me] = self.chain_piao
        g.scores = [0] * 4
        g.done = False
        g.result = None
        g._kong_draw = self.kong_draw
        g.turn = self.me
        if phase == "draw":
            g.phase = "discard"
            g.pending = None
            g.react_seq = [self.me]
            g.react_idx = 0
            g._n_claim = 0
        elif phase in ("react", "response_peng", "response_chi"):
            if self.pending is None:
                raise MirrorInconsistent(f"{phase} 无 pending")
            if self.pending[0] == self.me:
                # 自家打出的牌没有自家反应窗(引擎 _begin_react 只让他家
                # 进 react;镜像 build 捷径会绕过该保证——防陈旧窗口
                # 状态构建出"吃自己弃牌"的假合法集)
                raise MirrorInconsistent(
                    f"{phase} pending 属于自家(陈旧/失效窗口)")
            if (phase == "response_chi"
                    and (self.pending[0] + 1) % 4 != self.me):
                # 吃牌只属于出牌者的下家。  A stale/full snapshot can
                # still carry the previous discard while the protocol has
                # already advanced to another seat; constructing a local
                # Game for that phase would manufacture a false legal chi
                # opportunity and inflate claim-miss/timeout diagnostics.
                raise MirrorInconsistent(
                    f"{phase} pending 非下家(陈旧/失效窗口)")
            g.phase = "react"
            g.pending = self.pending
            if (self.response_order and self.response_index is not None and
                    self.response_claim_count is not None):
                g.react_seq = list(self.response_order)
                g.react_idx = int(self.response_index)
                g._n_claim = int(self.response_claim_count)
            else:
                if phase == "react":
                    # Unlike the platform-specific response_peng/chi phases,
                    # the generic engine phase carries no claim/chow boundary
                    # by itself.  Do not guess one for a public adapter.
                    raise MirrorInconsistent(
                        "react 缺少显式响应游标/碰吃边界")
                # Legacy placeholder retained for online legality checks. It
                # is not a complete rollout cursor; the public context
                # adapter marks the response order unsupported in this case.
                g.react_seq = [self.me]
                g.react_idx = 0
                g._n_claim = 1 if phase == "response_peng" else 0
        else:
            raise ValueError(f"未知决策阶段 {phase}")
        return g

    @staticmethod
    def _check(ok, msg):
        if not ok:
            raise MirrorInconsistent(msg)
