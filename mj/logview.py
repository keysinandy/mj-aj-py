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
        return (f"{pre} req    seq={rec.get('seq')} "
                f"{rec.get('latency_ms')}ms{att} → {rec.get('status')} "
                f"{res.get('n_events', 0)}ev{snap}{flags}")
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
    print("----- 摘要 -----")
    print("记录:", " ".join(f"{k}×{v}" for k, v in sorted(counts.items())))
    if lat:
        p50 = lat[len(lat) // 2]
        print(f"req 耗时: p50={p50}ms max={lat[-1]}ms "
              f"(n={len(lat)}, 重试{len(retries)}次)")
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


def main(argv=None):
    ap = argparse.ArgumentParser(description="自记对局日志时间线查看")
    ap.add_argument("target", help="gid 或日志文件路径")
    ap.add_argument("--root", default=ROOT, help="日志根目录")
    ap.add_argument("--types", default=None,
                    help="只看这些记录类型(逗号分隔: req,decision,...)")
    ap.add_argument("--full-events", action="store_true",
                    help="展开事件批内每条事件")
    args = ap.parse_args(argv)
    paths = find_logs(args.target, args.root)
    if not paths:
        print(f"未找到日志: {args.target} (root={args.root})", file=sys.stderr)
        return 1
    types = set(args.types.split(",")) if args.types else None
    for p in paths:
        show_file(p, types, args.full_events)
    return 0


if __name__ == "__main__":
    sys.exit(main())
