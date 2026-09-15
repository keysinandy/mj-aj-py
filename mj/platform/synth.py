"""引擎自博弈 → 平台格式事件流(离线测试夹具 + 协议 spec-of-record)。

通过继承 Game 挂钩内部转移函数,把一局自博弈转写为平台事件序列
(字段名与 replay.js / 平台指南 v34 对齐;事件 schema 沿用 v22:
type/seat/tile/data/seq)。两种
消费形态:
- 全量流(含四家摸牌 + start_hands 庄家 14 张含首摸)→ replay 校验
  与 mirror 属性测试的 ground truth;
- 单座位视角流(tile_drawn 仅本人)→ mirror/bot_client 的实时姿态
  模拟,附每个决策点的快照 prompt(含合法集真值)。

策略默认随机合法(最大化规则覆盖),可注入启发式 bot。
"""

import random

from ..game import (
    Game, HU, PASS, PONG, KONG_OPEN, CHOW_LOW,
)
from ..win import is_baotou
from .proto import tname, EV_DRAWN, EV_DISCARDED, EV_PASS, EV_CHI, EV_PENG, \
    EV_GANG, EV_HU, EV_TIMEOUT, EV_ROUND_ENDED, EV_GAME_ENDED


class _EmitterGame(Game):
    """挂 internal 钩子发事件;规则语义与 Game 完全一致(只加发射)。"""

    def __init__(self, seed=None, dealer=0, base=1, you_cai_bi_kao=False):
        self.events = []
        self._suppress_draw = True
        super().__init__(seed, dealer, base, you_cai_bi_kao)
        self._suppress_draw = False  # 庄家第 14 张为发牌直抽,不发事件
        # 发牌完成瞬间的手牌(庄家 14 张含首摸)——赛后数据 start_hands
        self.start_counts = [list(h) for h in self.hands]
        self.first_draw = self.drawn[dealer]  # 庄家首摸(终局前捕获)

    def _emit(self, etype, seat, tile=None, data=None):
        self.events.append({
            "type": etype, "seat": seat,
            "tile": tname(tile) if tile is not None else None,
            "data": data or {}, "seq": len(self.events) + 1,
        })

    def _draw(self, seat, kong=False):
        before = len(self.wall)
        super()._draw(seat, kong)
        if len(self.wall) < before and not self._suppress_draw:
            self._emit(EV_DRAWN, seat, self.drawn[seat])

    def _do_discard(self, seat, tile):
        # 先发事件:super 的白板弃牌路径会触发下一家摸牌(_draw 发射),
        # 事件序必须为 [弃牌, 后续摸牌]
        self._emit(EV_DISCARDED, seat, tile)
        super()._do_discard(seat, tile)

    def _do_react(self, seat, action):
        if action == PASS:
            self._emit(EV_PASS, seat)  # 先发:super 的 PASS 可能触发摸牌
            super()._do_react(seat, action)
            return
        owner, tile = self.pending
        if action == PONG:
            self._emit(EV_PENG, seat, tile)
            super()._do_react(seat, action)
        elif action == KONG_OPEN:
            self._emit(EV_GANG, seat, tile, {"kind": "ming"})
            super()._do_react(seat, action)  # 补牌在 super 内发射,序正确
        else:  # CHOW_*
            pos = CHOW_LOW - action
            a = tile - pos
            self._emit(EV_CHI, seat, tile,
                       {"tiles": [tname(a), tname(a + 1), tname(a + 2)]})
            super()._do_react(seat, action)

    def _do_kong_closed(self, seat, t):
        self._emit(EV_GANG, seat, t, {"kind": "an"})
        super()._do_kong_closed(seat, t)  # 补牌在 super 内发射

    def _do_kong_add(self, seat, t):
        self._emit(EV_GANG, seat, t, {"kind": "bu"})
        super()._do_kong_add(seat, t)

    def _do_hu(self, seat):
        super()._do_hu(seat)
        self._emit(EV_HU, seat)


def random_policy(rng):
    """随机合法(最大化规则覆盖);rng 须为独立 Random 实例保证可复现。"""
    def policy(g, seat):
        return rng.choice(g.legal_actions())
    return policy


_MELD_OUT = {"chow": "chi", "pong": "peng",
             "kong_closed": "gang_an", "kong_open": "gang_ming",
             "kong_add": "gang_bu"}


def _melds_out(melds):
    out = []
    for kind, t in melds:
        if kind == "chow":
            tiles = [t, t + 1, t + 2]
        elif kind == "pong":
            tiles = [t] * 3
        else:
            tiles = [t] * 4
        out.append({"kind": _MELD_OUT[kind],
                    "tiles": [tname(x) for x in tiles]})
    return out


def _prompt(g, viewer, actor, mode):
    """viewer 视角的平台快照 prompt(实测字段全集,2026-09-08 探针)。

    phase/turn 是全局的;响应窗 turn = 出牌者(实测);my_hand/drawn_tile/
    god 是 viewer 自己的;discards/melds/wall_remaining/hand_counts 为
    全量公共状态。
    """
    if mode == "draw":
        phase, responding = "draw", []
        turn = actor
    elif mode == "claim":
        phase, responding = "response_peng", [actor]
        turn = g.pending[0]  # 实测:响应窗 turn = 出牌者
    else:
        phase, responding = "response_chi", [actor]
        turn = g.pending[0]
    standing = None
    drawn = g.drawn[viewer]
    if drawn is not None:
        standing = list(g.hands[viewer])
        standing[drawn] -= 1
    last_disc = None
    if g.pending is not None:
        last_disc = tname(g.pending[1])
    else:
        for d in g.discards:
            if d:
                last_disc = tname(d[-1])
    return {
        "seat": viewer,
        "phase": phase,
        "turn": turn,
        "responding_seats": responding,
        "drawn_tile": tname(drawn) if drawn is not None else "",
        "my_hand": [tname(t) for t in range(34)
                    for _ in range(g.hands[viewer][t])],
        "god": {
            "baotou": bool(standing is not None
                           and is_baotou(standing, len(g.melds[viewer]))),
            "chain_count": g.chain[viewer],
            "catch_play": bool(g.in_freeze(viewer)),
        },
        "round_no": 1,
        "dealer": g.dealer,
        "discards": [[tname(t) for t in d] for d in g.discards],
        "melds": [_melds_out(ms) for ms in g.melds],
        "wall_remaining": len(g.wall),
        "hand_counts": [sum(h) for h in g.hands],
        "last_discard": last_disc,
        "scores": list(g.scores),
    }


def synth_game(seed=None, dealer=None, you_cai_bi_kao=False, policy=None):
    """跑一局自博弈,返回 Synthesis(全量事件流 + 逐决策点真值)。

    decisions 逐决策点记录(按发生序):{seat, mode(draw|claim|chow),
    legal(引擎合法集), action(实际执行), prompt(该座位的快照),
    events_before(该决策点时的事件条数——视角流的切分游标)}。
    """
    if dealer is None:
        dealer = random.Random(seed if seed is not None else 0).randrange(4)
    if policy is None:
        policy = random_policy(random.Random((seed or 0) + 1))
    g = _EmitterGame(seed, dealer, you_cai_bi_kao=you_cai_bi_kao)
    decisions = []
    while not g.done:
        seat = g.current_seat()
        mode = "draw" if g.phase == "discard" else g.react_mode()
        decisions.append({
            "seat": seat,
            "mode": mode,
            "legal": sorted(g.legal_actions()),
            "action": None,  # 下方回填
            "prompt": _prompt(g, seat, seat, mode),
            # 四座位视角快照(FakeApi 离线驱动 bot_client 用):phase/turn
            # 是当前行动者的,my_hand/god 是各座位自己的
            "all_prompts": [_prompt(g, s, seat, mode) for s in range(4)],
            "events_before": len(g.events),
            # 公共状态真值(mirror 属性测试逐项断言)
            "live_wall": g.live_wall_left(),
            "freeze": g.freeze,
            "chows": list(g.chows),
            "melds": [list(m) for m in g.melds],
            "discards": [list(d) for d in g.discards],
        })
        act = (policy or random_policy)(g, seat)
        decisions[-1]["action"] = act
        g.step(act)
    g._emit(EV_ROUND_ENDED, g.result[0] if g.result else None, None, {
        "draw": g.result is None,
        "fan": g.result[1] if g.result else 0,
        "detail": g.result[2] if g.result else [],
        "scores": g.scores,
        "round_no": 1,
        "dealer": g.dealer,
    })
    g._emit(EV_GAME_ENDED, None)
    return {
        "game": g,
        "dealer_first_draw": tname(g.first_draw),
        "config": {
            "M": 1, "Rounds": 1, "BaseScore": 1,
            "YouCaiBiKao": you_cai_bi_kao,
            "PengTimeoutSec": 1, "ChiTimeoutSec": 1, "DiscardTimeoutSec": 3,
        },
        "start_hands": [
            [tname(t) for t in range(34) for _ in range(c[t])]
            for c in g.start_counts
        ],
        "events": g.events,
        "rounds": [{
            "dealer": g.dealer,
            "draw": g.result is None,
            "winner": g.result[0] if g.result else None,
            "fan": g.result[1] if g.result else 0,
            "detail": g.result[2] if g.result else [],
            "scores": g.scores,
        }],
        "decisions": decisions,
    }


def view_for(res, seat):
    """seat 的实时视角:tile_drawn 仅本人 + 该座位的决策 prompt 序列。

    返回 {events, prompts}——prompt 的 events_before 已换算为**过滤后
    流的游标**(seq <= 决策点全量游标的视角事件数),mirror 属性测试按
    它切流、prompt 驱动决策,逐点对合法集。
    """
    from bisect import bisect_right
    own = []
    for ev in res["events"]:
        if ev["type"] == EV_DRAWN and ev["seat"] != seat:
            continue
        own.append(ev)
    seqs = [e["seq"] for e in own]
    prompts = []
    for d in res["decisions"]:
        if d["seat"] != seat:
            continue
        p = dict(d)
        p["events_before"] = bisect_right(seqs, d["events_before"])
        prompts.append(p)
    return {"events": own, "prompts": prompts}


def start_hands_with_dealer14(res):
    """赛后数据形态的 start_hands(= res["start_hands"],庄家 14 张含首摸)。"""
    return res["start_hands"]


__all__ = ["synth_game", "view_for", "start_hands_with_dealer14",
           "random_policy", "EV_TIMEOUT"]
