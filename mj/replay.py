"""回放校验器:平台赛后事件流 → 引擎逐步重放(P1.5 引擎-平台行为对齐)。

数据源:GET /api/test-rooms/{room}/games/{batch}/events(免认证,含
四家开局手牌 start_hands——庄家 14 张含首摸,发牌直抽无歧义)+ rounds
局结果。逐事件重放:
- 真实摸牌序入墙(引擎从墙尾弹出 = 事件时间序);
- 每个日志动作断言 ∈ 当点引擎 legal_actions() 再 step();
- round_ended 时引擎结算结果 vs 服务端 fan/detail/scores 逐项对账。

CLI:python3 -m mj.replay --room <id> [--batch N]... [--base 1] [--out report.json]

容忍模式:pass 事件缺失时按引擎反应序自动代过(计 auto_pass);
round_ended 为胡而事件流无 hu 时(服务端超时自动胡)补试 HU。
"""

import argparse
import json
import random
import sys

from .game import Game, HU, PASS
from .platform.proto import (
    parse_event, tidx, tname, ProtocolError,
    EV_DRAWN, EV_DISCARDED, EV_PASS, EV_CHI, EV_PENG, EV_GANG, EV_HU,
    EV_TIMEOUT, EV_ROUND_ENDED, EV_GAME_ENDED,
)
from .platform.actions import payload_to_engine_action
from .platform import api as api_mod

DEAD = 20


# ---------- 文档结构解析 ----------

def extract_rounds(doc):
    """赛后事件流 doc → [(start_hands, events, round_meta), ...]。

    实测格式(2026-09-08 探针):blocks 按 ≤128 事件分块,每块带
    seq_start/seq_end;每局首块含 start_hands(庄家 14 张含首摸)/
    dealer/round_no,续块 start_hands=null;rounds[i] 局结果
    {dealer, is_draw, multiplier, winner, scores, round_no}。
    兼容顶层 start_hands+events 的单局合成流形态。
    """
    rounds, cur = [], None
    blocks = doc.get("blocks")
    if blocks is not None:
        for b in blocks:
            if not isinstance(b, dict):
                continue
            sh = b.get("start_hands")
            if sh and any(h is not None for h in sh):
                # 每局首块:start_hands 实值(庄家 14 含首摸)
                if cur is not None:
                    rounds.append(cur)
                cur = {"start_hands": sh,
                       "events": list(b.get("events") or [])}
            elif cur is not None:
                # 续块:start_hands 为 null 或 [null]*4,事件拼接
                cur["events"].extend(b.get("events") or [])
    elif doc.get("events") is not None:
        cur = {"start_hands": doc.get("start_hands"),
               "events": list(doc["events"])}
    if cur is not None:
        rounds.append(cur)
    metas = doc.get("rounds") or []
    out = []
    for i, r in enumerate(rounds):
        out.append((r["start_hands"], r["events"],
                    metas[i] if i < len(metas) else {}))
    return out


# ---------- 单局重放 ----------

def _build_game(start_hands, draws, dealer, base, you_cai_bi_kao, drawn_guess):
    g = Game.__new__(Game)
    g.dealer = dealer
    g.base = base
    g.you_cai_bi_kao = you_cai_bi_kao
    g._kong_draw = False
    g.rng = random.Random(0)
    g.hands = []
    for names in start_hands:
        h = [0] * 34
        for n in names:
            h[tidx(n)] += 1
        g.hands.append(h)
    g.wall = [0] * DEAD + list(reversed(draws))  # 引擎从尾部弹 = 时间序
    g.melds = [[] for _ in range(4)]
    g.discards = [[] for _ in range(4)]
    g.drawn = [None] * 4
    g.drawn[dealer] = drawn_guess
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
    return g


def _dealer_draw_guess(start_hands, dealer, events):
    """庄家首摸身份推断:其首个弃牌,否则手中任一张。

    仅影响有财必拷响的爆头门禁判定(胡牌合法性),弃牌/杠不受影响。
    """
    for e in events:
        if e.get("type") == EV_DISCARDED and e.get("seat") == dealer:
            return tidx(e["tile"])
    for t in range(34):
        if sum(1 for n in start_hands[dealer] if tidx(n) == t):
            return t
    return None


def replay_round(start_hands, events, dealer, base=1, you_cai_bi_kao=False,
                 meta=None):
    """重放一局,返回 report dict(illegal/warnings/auto_pass/对账)。

    meta:rounds[] 局结果(实测键 is_draw/multiplier/winner/scores;
    round_ended 事件 data 另含 dealer/draw/round_no/scores,胡局或有
    fan/detail——两者取并集对账)。
    """
    meta = meta or {}
    rep = {"round_no": None, "illegal": [], "warnings": [],
           "auto_pass": 0, "actions_checked": 0,
           "settle": None, "engine_result": None}
    norm = []
    for raw in events:
        try:
            norm.append((raw, parse_event(raw)))
        except ProtocolError as e:
            rep["warnings"].append(f"事件解析失败: {e}")
    if not start_hands or len(start_hands) != 4:
        rep["warnings"].append("缺 start_hands,无法重放")
        return rep
    draws = [e["tile"] for _, e in norm if e["type"] == EV_DRAWN]
    guess = _dealer_draw_guess(start_hands, dealer, events)
    g = _build_game(start_hands, draws, dealer, base, you_cai_bi_kao, guess)

    def fail(msg, raw=None):
        rep["illegal"].append({"seq": raw.get("seq") if raw else None,
                               "msg": msg})

    def has_open_pong(seat):
        return lambda x: any(m[0] == "pong" and m[1] == x
                            for m in g.melds[seat])

    def ensure_actor(seat, mode=None):
        """react 序自动代过(pass 事件缺失容忍),直到当前行动者=seat
        且 react 模式匹配(同一座位可能先 claim 后 chow 两次被询问)。"""
        guard = 0
        while (not g.done and g.phase == "react" and guard < 8
               and (g.turn != seat or (mode is not None
                                       and g.react_mode() != mode))):
            g.step(PASS)
            rep["auto_pass"] += 1
            guard += 1
        return (g.phase == "react" and g.turn == seat
                and (mode is None or g.react_mode() == mode))

    def drain_react():
        """反应窗整体代过(事件流缺全部 pass 时推进到下一摸牌)。"""
        guard = 0
        while not g.done and g.phase == "react" and guard < 8:
            g.step(PASS)
            rep["auto_pass"] += 1
            guard += 1

    for raw, e in norm:
        t, seat = e["type"], e["seat"]
        if g.done and t not in (EV_ROUND_ENDED, EV_GAME_ENDED):
            continue
        if t in (EV_DRAWN, EV_DISCARDED, EV_ROUND_ENDED) or (
                t == EV_GANG and (e.get("kind") or "ming") != "ming") \
                or t == EV_HU:
            drain_react()  # 缺 pass 事件时先推进反应窗(含流局尾窗)
        if t == EV_DRAWN:
            if g.phase != "discard" or g.turn != seat \
                    or g.drawn[seat] != e["tile"]:
                fail(f"摸牌不符: 事件 seat={seat} {e['tile']},"
                     f"引擎 turn={g.turn} drawn={g.drawn[seat]}", raw)
        elif t == EV_DISCARDED:
            if g.phase != "discard" or g.turn != seat:
                fail(f"出牌时机不符: seat={seat} 引擎 turn={g.turn}"
                     f" phase={g.phase}", raw)
                continue
            if e["tile"] not in g.legal_actions():
                fail(f"非法弃牌 {tname(e['tile'])}: "
                     f"合法集 {g.legal_actions()}", raw)
                continue
            g.step(e["tile"])
            rep["actions_checked"] += 1
        elif t == EV_PASS:
            if g.phase == "react" and g.turn == seat:
                if PASS not in g.legal_actions():
                    fail("过: 合法集无 PASS", raw)
                    continue
                g.step(PASS)
        elif t in (EV_CHI, EV_PENG):
            if not ensure_actor(seat, "chow" if t == EV_CHI else "claim"):
                fail(f"{t} 时机不符: seat={seat} 引擎 turn={g.turn}"
                     f" phase={g.phase}", raw)
                continue
            payload = {"action": "chi" if t == EV_CHI else "peng",
                       "tile": tname(e["tile"])}
            if t == EV_CHI:
                payload["tiles"] = [tname(x) for x in (e["tiles"] or [])]
            try:
                pending = g.pending[1] if g.phase == "react" else None
                act = payload_to_engine_action(
                    payload, g.hands[seat], pending,
                    has_open_pong=has_open_pong(seat))
            except (ValueError, KeyError, TypeError) as ex:
                fail(f"{t} 动作映射失败: {ex}", raw)
                continue
            if act not in g.legal_actions():
                fail(f"非法 {t}: {act} 不在 {g.legal_actions()}", raw)
                continue
            g.step(act)
            rep["actions_checked"] += 1
        elif t == EV_GANG:
            kind = e.get("kind") or "ming"
            if kind == "ming":
                if not ensure_actor(seat, "claim"):
                    fail(f"明杠时机不符: seat={seat} turn={g.turn}", raw)
                    continue
                pending = g.pending[1] if g.phase == "react" else None
            else:
                if g.phase != "discard" or g.turn != seat:
                    fail(f"{'暗' if kind == 'an' else '加'}杠时机不符: "
                         f"seat={seat} turn={g.turn} phase={g.phase}", raw)
                    continue
                pending = None
            payload = {"action": "gang", "tile": tname(e["tile"])}
            try:
                act = payload_to_engine_action(
                    payload, g.hands[seat], pending,
                    has_open_pong=has_open_pong(seat))
            except (ValueError, KeyError, TypeError) as ex:
                fail(f"gang 动作映射失败: {ex}", raw)
                continue
            if act not in g.legal_actions():
                fail(f"非法 {kind}杠 {tname(e['tile'])}: "
                     f"合法集 {g.legal_actions()}", raw)
                continue
            g.step(act)
            rep["actions_checked"] += 1
        elif t == EV_HU:
            if g.phase != "discard" or g.turn != seat:
                fail(f"胡牌时机不符: seat={seat} turn={g.turn}", raw)
                continue
            if HU not in g.legal_actions():
                fail(f"非法胡: seat={seat} 合法集 {g.legal_actions()}", raw)
                continue
            g.step(HU)
            rep["actions_checked"] += 1
        elif t == EV_TIMEOUT:
            pass  # 超时代打的 tile_discarded 已先行应用
        elif t == EV_ROUND_ENDED:
            data = e.get("data") or {}
            rep["round_no"] = data.get("round_no") or meta.get("round_no")
            merged = dict(meta)
            merged.update({k: v for k, v in data.items() if v is not None})
            merged.setdefault("draw", bool(meta.get("is_draw")))
            _settle_compare(g, merged, rep, fail)
        elif t == EV_GAME_ENDED:
            break
    return rep


def _settle_compare(g, data, rep, fail):
    draw = bool(data.get("draw"))
    if not draw and not g.done:
        # 服务端超时自动胡等未发 hu 事件的容忍路径
        if g.phase == "discard" and HU in g.legal_actions():
            g.step(HU)
            rep["warnings"].append("round_ended 无 hu 事件,补试 HU 通过")
        else:
            fail(f"终局不符: 引擎未终局(turn={g.turn} phase={g.phase}"
                 f"),平台判胡")
            return
    if not g.done:
        fail(f"终局不符: 引擎未终局,平台 draw={draw}")
        return
    rep["engine_result"] = (list(g.scores),
                            None if g.result is None else list(g.result))
    if draw:
        if g.result is not None:
            fail(f"流局不符: 引擎判胡 {g.result} vs 平台流局")
        return
    if g.result is None:
        fail("终局不符: 引擎流局 vs 平台判胡")
        return
    _, mult, parts = g.result
    fan = data.get("fan", data.get("multiplier"))
    if fan is not None and fan != mult:
        fail(f"倍率不符: 引擎 {mult} vs 平台 {fan}")
    detail = data.get("detail")
    if detail is not None and list(detail) != list(parts):
        fail(f"番型明细不符: 引擎 {parts} vs 平台 {detail}")
    scores = data.get("scores")
    if scores is not None and g.scores != list(scores):
        fail(f"结算不符: 引擎 {g.scores} vs 平台 {scores}")


def replay_doc(doc, base=1, you_cai_bi_kao=False):
    """整个 events doc → [round_report, ...];dealer 逐局取 rounds[].dealer。"""
    out = []
    last_dealer = 0
    for start_hands, events, meta in extract_rounds(doc):
        dealer = meta.get("dealer", last_dealer)
        last_dealer = dealer
        out.append(replay_round(start_hands, events, dealer,
                                 base=base, you_cai_bi_kao=you_cai_bi_kao,
                                 meta=meta))
    return out


# ---------- 房间级校验(内网) ----------

def validate_room(server, room_id, batches=None, base=1,
                  you_cai_bi_kao=False):
    """拉取房间各局并对账。

    注意:免费认证数据端点按 batch 号拉取,而每轮批次从 0 重号——
    跨轮房间同号 batch 返回**最新轮**的局。因此默认只对最大轮次的
    局做校验(历史轮走门户 GET /portal/api/games/{id}/events,需
    登录态);--batch 显式指定时按号拉(取最新轮)。
    """
    resp = api_mod.room_games(server, room_id)
    games = resp.get("games") if isinstance(resp, dict) else resp
    if batches is not None:
        wanted = set(batches)
        games = [g for g in games if g.get("batch") in wanted]
        seen = set()
        dedup = []
        for g in games:
            if g["batch"] not in seen:
                seen.add(g["batch"])
                dedup.append(g)
        games = dedup
    else:
        max_round = max((g.get("round") or 1) for g in games) if games else 1
        games = [g for g in games if (g.get("round") or 1) == max_round]
    summary = {"room": room_id, "games": [], "total_illegal": 0,
               "total_actions": 0}
    for ginfo in games:
        doc = api_mod.room_events(server, room_id, ginfo["batch"])
        reps = replay_doc(doc, base=base, you_cai_bi_kao=you_cai_bi_kao)
        for r in reps:
            summary["total_illegal"] += len(r["illegal"])
            summary["total_actions"] += r["actions_checked"]
        summary["games"].append({"batch": ginfo["batch"],
                                 "round": ginfo.get("round"),
                                 "game_id": ginfo.get("game_id"),
                                 "rounds": reps})
    return summary


def main(argv=None):
    ap = argparse.ArgumentParser(description="回放校验器(引擎-平台对账)")
    ap.add_argument("--room", required=True)
    ap.add_argument("--server", default="https://10.240.169.190:18080")
    ap.add_argument("--batch", type=int, action="append", default=None)
    ap.add_argument("--base", type=int, default=1)
    ap.add_argument("--you-cai-bi-kao", action="store_true")
    ap.add_argument("--out", default=None, help="报告 JSON 输出路径")
    args = ap.parse_args(argv)
    s = validate_room(args.server, args.room, batches=args.batch,
                      base=args.base, you_cai_bi_kao=args.you_cai_bi_kao)
    for g in s["games"]:
        for r in g["rounds"]:
            status = "OK" if not r["illegal"] else f"{len(r['illegal'])} ILLEGAL"
            print(f"batch {g['batch']} round {r['round_no']}: {status} "
                  f"({r['actions_checked']} 动作, auto_pass {r['auto_pass']})")
            for ill in r["illegal"]:
                print(f"  [seq {ill['seq']}] {ill['msg']}")
            for w in r["warnings"]:
                print(f"  [warn] {w}")
    print(f"合计: {s['total_actions']} 动作, {s['total_illegal']} 非法")
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(s, f, ensure_ascii=False, indent=1)
        print("报告已写入", args.out)
    return 1 if s["total_illegal"] else 0


if __name__ == "__main__":
    sys.exit(main())
