"""自记对局日志查看器:JSONL → 时间线复盘。

用法:
  python3 -m mj.logview <gid或文件路径>            # 全量时间线 + 摘要
  python3 -m mj.logview <gid> --types decision,action
  python3 -m mj.logview <gid> --full-events         # 展开每条事件

<gid> 在 local/games/ 下递归查找 *_<gid>.jsonl(多文件时全部渲染)。
"""

import argparse
import glob
import json
import os
import sys
import time

from .game import (
    PASS, PONG, KONG_OPEN, KONG_CLOSED_BASE, KONG_ADD_BASE, HU, CHOW_LOW,
)
from .platform.proto import tname

ROOT = "local/games"


def action_name(a):
    """引擎动作 → 可读名。"""
    if a is None:
        return "?"
    if 0 <= a <= 33:
        return f"打 {tname(a)}"
    if a == PASS:
        return "过"
    if CHOW_LOW - 2 <= a <= CHOW_LOW:
        return "吃"
    if a == PONG:
        return "碰"
    if a == KONG_OPEN:
        return "明杠"
    if KONG_CLOSED_BASE - 33 <= a <= KONG_CLOSED_BASE:
        return f"暗杠 {tname(KONG_CLOSED_BASE - a)}"
    if KONG_ADD_BASE - 33 <= a <= KONG_ADD_BASE:
        return f"加杠 {tname(KONG_ADD_BASE - a)}"
    if a == HU:
        return "胡"
    return f"?{a}"


def find_logs(target, root=ROOT):
    """gid → 日志文件列表;路径直接返回单元素。"""
    if os.sep in target or target.endswith(".jsonl"):
        return [target] if os.path.exists(target) else []
    return sorted(glob.glob(os.path.join(root, "**", f"*_{target}.jsonl"),
                            recursive=True))


def load_records(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


# ---------- 单条渲染 ----------

def _t(rec, t0):
    return f"{rec.get('ts', t0) - t0:7.1f}s"


def render(rec, t0):
    t = rec["type"]
    pre = _t(rec, t0)
    if t == "meta":
        return (f"{pre} meta   令牌={rec.get('name')} tid={rec.get('tid')} "
                f"YCBK={int(bool(rec.get('you_cai_bi_kao')))} "
                f"base={rec.get('base')}")
    if t == "req":
        res = rec.get("res") or {}
        flags = "".join(
            f"[{k}]" for k in ("pending", "gap", "finished") if res.get(k))
        snap = "[快照]" if res.get("snapshot") else ""
        att = f" ×{rec['attempts']}" if (rec.get("attempts") or 1) != 1 else ""
        tr = rec.get("transport") or {}
        th = rec.get("throttle") or {}
        retry = "/".join(str(tr.get(k, 0)) for k in
                          ("retry_429", "retry_gateway", "retry_network"))
        retry = f" retry={retry}" if retry != "0/0/0" else ""
        backoff = f" backoff={tr['backoff_ms']}ms" if tr.get("backoff_ms") else ""
        queued = f" queue={th['queue_wait_ms']}ms" if th.get("queue_wait_ms") else ""
        missed = "[截止已失]" if th.get("deadline_missed") else ""
        return (f"{pre} req    seq={rec.get('seq')} "
                f"{rec.get('latency_ms')}ms{att}{retry}{backoff}{queued} {missed}→ "
                f"{rec.get('status')} {res.get('n_events', 0)}ev{snap}{flags}")
    if t == "snapshot":
        snap = rec.get("snap") or {}
        return (f"{pre} snap   seq={rec.get('seq')} "
                f"seat={snap.get('seat')} round={snap.get('round_no')} "
                f"phase={snap.get('phase')} "
                f"wall={snap.get('wall_remaining')}")
    if t == "events":
        evs = rec.get("events") or []
        span = f"seq {evs[0].get('seq')}..{evs[-1].get('seq')}" if evs else "空"
        return f"{pre} events {span} 共{len(evs)}条"
    if t == "decision":
        dg = rec.get("digest") or {}
        legal = rec.get("legal") or []
        lsum = (f"{len(legal)}项" if len(legal) > 8
                else "[" + ",".join(action_name(a) for a in legal) + "]")
        return (f"{pre} DECIDE #{rec.get('id')} {rec.get('phase')} "
                f"seq={rec.get('seq')} 合法={lsum} → "
                f"{action_name(rec.get('action'))} ({rec.get('latency_ms')}ms"
                f",手{dg.get('hand')}张/墙{dg.get('wall')})")
    if t == "action":
        p = rec.get("payload") or {}
        if rec.get("ok"):
            return (f"{pre} ACT    {p.get('action')} "
                    f"{p.get('tile', '')} {p.get('tiles', '')}".rstrip()
                    + f" ok ({rec.get('latency_ms')}ms)")
        return (f"{pre} ACT    {p.get('action')} {p.get('tile', '')} "
                f"✗ {rec.get('status')} {rec.get('code')}")
    if t == "reset":
        return f"{pre} !!RESET {rec.get('reason')}"
    if t == "end":
        return (f"{pre} ==END  {rec.get('reason')} "
                f"scores={rec.get('scores')}")
    return f"{pre} ?{t} {rec}"


def render_event_lines(rec):
    for ev in rec.get("events") or []:
        tile = f" {ev.get('tile')}" if ev.get("tile") else ""
        data = ev.get("data") or {}
        extra = f" ({data})" if data else ""
        yield (f"          ev seq={ev.get('seq')} {ev.get('type')} "
               f"seat={ev.get('seat')}{tile}{extra}")


# ---------- 时间线与摘要 ----------

def show_file(path, types=None, full_events=False):
    recs = load_records(path)
    if not recs:
        print(f"(空日志: {path})")
        return
    t0 = recs[0].get("ts", time.time())
    print(f"\n===== {path} =====")
    for rec in recs:
        if types and rec["type"] not in types:
            continue
        print(render(rec, t0))
        if full_events and rec["type"] == "events":
            for line in render_event_lines(rec):
                print(line)
    summarize(recs)


def summarize(recs):
    counts = {}
    for r in recs:
        counts[r["type"]] = counts.get(r["type"], 0) + 1
    lat = sorted(r["latency_ms"] for r in recs
                 if r["type"] == "req" and r.get("latency_ms") is not None)
    fails = [r for r in recs if r["type"] == "action" and not r.get("ok")]
    retries = [r for r in recs
               if r["type"] == "req" and (r.get("attempts") or 1) > 1]
    reqs = [r for r in recs if r["type"] == "req"]
    physical = sum(r.get("attempts") or 1 for r in reqs)
    retry_429 = sum((r.get("transport") or {}).get("retry_429", 0)
                    for r in reqs)
    queued = sorted((r.get("throttle") or {}).get("queue_wait_ms", 0)
                    for r in reqs)
    missed = sum(bool((r.get("throttle") or {}).get("deadline_missed"))
                 for r in reqs)
    print("----- 摘要 -----")
    print("记录:", " ".join(f"{k}×{v}" for k, v in sorted(counts.items())))
    if lat:
        p50 = lat[len(lat) // 2]
        print(f"req 耗时: p50={p50}ms max={lat[-1]}ms "
              f"(逻辑{len(reqs)}/物理{physical}, 重试{len(retries)}, 429={retry_429})")
    if queued and any(queued):
        print(f"调度等待: p50={queued[len(queued) // 2]}ms max={queued[-1]}ms "
              f"截止已失={missed}")
    if fails:
        codes = {}
        for f in fails:
            codes[f.get("code") or f.get("status")] = \
                codes.get(f.get("code") or f.get("status"), 0) + 1
        print(f"失败动作: {len(fails)} 次 {codes}")
    dec = [r for r in recs if r["type"] == "decision"]
    if dec:
        dl = sorted(r["latency_ms"] for r in dec
                    if r.get("latency_ms") is not None)
        print(f"决策: {len(dec)} 次, decide p50="
              f"{dl[len(dl) // 2] if dl else '?'}ms max={dl[-1] if dl else '?'}ms")
    end = next((r for r in reversed(recs) if r["type"] == "end"), None)
    if end:
        print(f"终局: {end.get('reason')} scores={end.get('scores')}")


# ---------- 动作窗口时间线 ----------

WINDOW_SPAN = {"draw": 3.0, "response_peng": 1.0, "response_chi": 2.0}
KIND_DESC = {"draw": "自家摸/吃碰→弃牌窗[3s]",
             "response_peng": "碰窗[T,T+1]",
             "response_chi": "吃窗[T+1,T+2]"}


def window_timeline(recs):
    """每个动作窗口的完整时间链:服务端 T → 我方观测 → 决策 → 提交落点。

    行格式: 观测迟到 / 提交=T+x / 距窗口关闭余量 / 结果。供优化归因:
    迟到来自排队还是服务端刷新、提交是否掐在窗内、错过的是哪一段。
    """
    seat = None
    t0 = recs[0].get("ts", time.time())
    win = None
    rows = []

    def close(reason):
        nonlocal win
        if win is not None:
            rows.append((win, None, None, reason))
            win = None

    for rec in recs:
        t = rec["type"]
        if t == "snapshot" and seat is None:
            seat = (rec.get("snap") or {}).get("seat")
        elif t == "events":
            for ev in rec.get("events") or []:
                et, es = ev.get("type"), ev.get("seat")
                if et == "tile_drawn" and es == seat:
                    close("被新触发覆盖")
                    win = {"kind": "draw", "T": ev.get("ts"),
                           "arrival": rec["ts"]}
                elif et in ("chi", "peng") and es == seat:
                    close("被新触发覆盖")
                    win = {"kind": "draw", "T": ev.get("ts"),
                           "arrival": rec["ts"]}
                elif et == "tile_discarded" and es != seat:
                    close("被新触发覆盖")
                    win = {"kind": "window", "T": ev.get("ts"),
                           "arrival": rec["ts"], "tile": ev.get("tile"),
                           "seat": es}
                elif et == "timeout" and es == seat \
                        and win is not None and win["kind"] == "window":
                    data = ev.get("data") or {}
                    if data.get("kind") == "response":
                        close(f"未响应(无碰/吃选项或窗已关,"
                              f"{data.get('window')})")
        elif t == "decision" and win is not None:
            win["dec_ms"] = rec.get("latency_ms")
        elif t == "action" and win is not None:
            phase = rec.get("phase") or ""
            kind = phase if phase in WINDOW_SPAN else win["kind"]
            rows.append((win, rec, kind,
                         "ok" if rec.get("ok")
                         else f"✗{rec.get('status')}"))
            win = None
    close("局终未决")

    print("----- 动作窗口时间线 -----")
    obs_l, subs, ok_cnt, miss_cnt = [], [], 0, 0
    for win, act, kind, status in rows:
        T, arr = win.get("T"), win["arrival"]
        if T is None:
            continue
        obs = arr - T
        obs_l.append(obs)
        if win["kind"] == "draw":
            desc = "自家回合 "
        else:
            desc = f"他{win.get('seat')}弃{win.get('tile')} "
        if act is None:
            miss_cnt += 1
            print(f"{_t({'ts': arr}, t0)} {desc}{KIND_DESC.get(win['kind'], '')} "
                  f"观测迟到={obs:.2f}s → {status}")
            continue
        span = WINDOW_SPAN.get(kind, 1.0)
        # New logs record the send start explicitly. Older logs only have
        # response completion and elapsed time: their start is an estimate.
        # Compare server epoch T with the separately recorded local epoch.
        # started_at is monotonic and must never be subtracted from T.
        started_at = act.get("started_epoch")
        estimated = started_at is None
        sub = (started_at - T) if started_at is not None else None
        if sub is not None:
            subs.append((sub, span, kind))
        if status == "ok":
            ok_cnt += 1
        # Only the server's result proves acceptance. Local clocks and event
        # timestamps cannot establish the instant the server accepted a POST.
        mark = "✓" if status == "ok" else "✗"
        dec = f" 决策{win.get('dec_ms', '?')}ms" if win.get("dec_ms") else ""
        position = (f"发送{'估计' if estimated else ''}=T+{sub:.2f}s "
                    f"估计余{span - sub:+.2f}s "
                    if sub is not None else "发送落点=不可推断 ")
        print(f"{_t({'ts': arr}, t0)} {desc}{KIND_DESC.get(kind, '')} "
              f"观测迟到={obs:.2f}s{dec} {position}"
              f"HTTP={act.get('latency_ms', '?')}ms "
              f"{mark}{'' if status == 'ok' else status}")
    if obs_l:
        obs_l.sort()
        n = len(obs_l)
        print(f"\n观测迟到: n={n} p50={obs_l[n // 2]:.2f}s "
              f"p90={obs_l[int(n * 0.9)]:.2f}s max={obs_l[-1]:.2f}s "
              f">1s={sum(1 for x in obs_l if x > 1)}")
    if subs:
        inwin = [s for s, span, _ in subs if s <= span]
        print(f"发送时刻估计: {len(inwin)}/{len(subs)} 不晚于事件推算截止; "
              f"未提交(过/代过)={miss_cnt}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="自记对局日志时间线查看")
    ap.add_argument("target", help="gid 或日志文件路径")
    ap.add_argument("--root", default=ROOT, help="日志根目录")
    ap.add_argument("--types", default=None,
                    help="只看这些记录类型(逗号分隔: req,decision,...)")
    ap.add_argument("--full-events", action="store_true",
                    help="展开事件批内每条事件")
    ap.add_argument("--windows", action="store_true",
                    help="打印动作窗口时间线(观测迟到/提交落点/结果)")
    args = ap.parse_args(argv)
    paths = find_logs(args.target, args.root)
    if not paths:
        print(f"未找到日志: {args.target} (root={args.root})", file=sys.stderr)
        return 1
    for p in paths:
        if args.windows:
            recs = load_records(p)
            if recs:
                print(f"\n===== {p} =====")
                window_timeline(recs)
            continue
        show_file(p, args.types, args.full_events)
    return 0


if __name__ == "__main__":
    sys.exit(main())
