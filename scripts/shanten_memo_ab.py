#!/usr/bin/env python3
"""LegacyV2 向听内核 A/B:花色表(memo) vs 逐牌 DFS。

用途:在**安静机器**上决定 `MJ_KERNELS_SHANTEN=memo` 是否值得默认开启。
内核每次调用都会重新读环境变量,所以可以在同一进程内按轮次交替切换两种
模式,避免跨会话负载漂移;每轮同时跑 workers=1 与 workers=0(auto),记录
complete/fallback、raw/E2E p50/p95/p99 与每决策 CPU。

前置:
    maturin build --release --manifest-path rust/Cargo.toml -o /tmp/wheel
    unzip -q -o /tmp/wheel/mj_kernels-*.whl -d /tmp/wheel-unpacked
    PYTHONPATH=/tmp/wheel-unpacked:. python3 scripts/shanten_memo_ab.py --rounds 3

判定规则(预先声明,避免事后挑指标;三条同时满足才建议默认开启 memo):
  1. 每个 worker 配置下,p95/p99 的中位数 memo 均不差于 DFS,且 complete 不低于 DFS;
  2. p50 中位数回退不超过 5%;
  3. 采样期间 load average < 1.0(否则结论不可用,只能看方向)。

退出码:0 = 建议默认开启 memo;1 = 保持 opt-in(默认 DFS);2 = 内核不可用。
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from legacy_two_ply_weighted_bench import run as bench_run  # noqa: E402
from mj.legacy_eval import LegacyTwoPlyProfile  # noqa: E402
from mj.shanten import WEIGHTED_TWO_PLY_KERNEL_VERSION  # noqa: E402

MEMO_ENV = "MJ_KERNELS_SHANTEN"
THREADS_ENV = "MJ_KERNELS_THREADS"
LOAD_LIMIT = 1.0
P50_TOLERANCE = 1.05


def _set_mode(memo: bool) -> None:
    if memo:
        os.environ[MEMO_ENV] = "memo"
    else:
        os.environ.pop(MEMO_ENV, None)


def _measure(memo: bool, workers: int, states: int, seed: int) -> dict:
    _set_mode(memo)
    os.environ[THREADS_ENV] = str(workers)
    profile = LegacyTwoPlyProfile.weighted_online(kernel="rust", workers=workers)
    load_before = os.getloadavg()[0]
    cpu_before = time.process_time()
    summary = bench_run(states, seed, profile)
    cpu_ms = (time.process_time() - cpu_before) * 1000.0
    end, raw = summary["end_to_end"], summary["raw_native"]
    return {
        "mode": "memo" if memo else "dfs",
        "workers": workers,
        "workers_used": end["workers"],
        "complete": end["complete"],
        "fallback": end["fallback"],
        "search_used_rate": end["search_used_rate"],
        "raw_p50_ms": raw["p50_ms"],
        "raw_p95_ms": raw["p95_ms"],
        "raw_p99_ms": raw["p99_ms"],
        "e2e_p50_ms": end["p50_ms"],
        "e2e_p95_ms": end["p95_ms"],
        "e2e_p99_ms": end["p99_ms"],
        "cpu_ms_per_call": cpu_ms / max(1, 2 * states),
        "load_before": load_before,
        "load_after": os.getloadavg()[0],
    }


def _median(rows, key):
    return statistics.median(row[key] for row in rows)


def _verdict(records, workers: int) -> dict:
    dfs = [row for row in records
           if row["workers"] == workers and row["mode"] == "dfs"]
    memo = [row for row in records
            if row["workers"] == workers and row["mode"] == "memo"]
    checks = {
        "p95_not_worse": _median(memo, "raw_p95_ms") <= _median(dfs, "raw_p95_ms"),
        "p99_not_worse": _median(memo, "raw_p99_ms") <= _median(dfs, "raw_p99_ms"),
        "complete_not_worse": _median(memo, "complete") >= _median(dfs, "complete"),
        "p50_within_5pct": (_median(memo, "raw_p50_ms")
                            <= _median(dfs, "raw_p50_ms") * P50_TOLERANCE),
    }
    return {
        "workers": workers,
        "checks": checks,
        "pass": all(checks.values()),
        "dfs": {key: _median(dfs, key) for key in (
            "raw_p50_ms", "raw_p95_ms", "raw_p99_ms", "complete",
            "cpu_ms_per_call")},
        "memo": {key: _median(memo, key) for key in (
            "raw_p50_ms", "raw_p95_ms", "raw_p99_ms", "complete",
            "cpu_ms_per_call")},
    }


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--states", type=int, default=100)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--workers", default="1,0",
                        help="逗号分隔的 worker 数,0=auto")
    parser.add_argument("--json", type=Path, default=None,
                        help="把完整结果写入该 JSON 文件")
    args = parser.parse_args(argv)
    workers_list = [int(value) for value in args.workers.split(",") if value]
    if WEIGHTED_TWO_PLY_KERNEL_VERSION is None:
        print("mj_kernels weighted kernel unavailable; build rust/ first")
        return 2

    records = []
    for round_index in range(args.rounds):
        order = (False, True) if round_index % 2 == 0 else (True, False)
        for workers in workers_list:
            for memo in order:
                row = _measure(memo, workers, args.states, args.seed)
                row["round"] = round_index + 1
                records.append(row)
                print("round %d workers=%d %-4s complete=%3d fallback=%3d "
                      "raw p50/p95/p99=%6.2f/%6.2f/%6.2f cpu/call=%6.2fms "
                      "load=%.2f" % (
                          row["round"], workers, row["mode"], row["complete"],
                          row["fallback"], row["raw_p50_ms"], row["raw_p95_ms"],
                          row["raw_p99_ms"], row["cpu_ms_per_call"],
                          row["load_after"]))

    verdicts = [_verdict(records, workers) for workers in workers_list]
    loads = [row["load_after"] for row in records]
    quiet = max(loads) < LOAD_LIMIT
    enable = quiet and all(item["pass"] for item in verdicts)
    result = {
        "kernel_version": WEIGHTED_TWO_PLY_KERNEL_VERSION,
        "states": args.states,
        "seed": args.seed,
        "rounds": args.rounds,
        "rule": {
            "p50_tolerance": P50_TOLERANCE,
            "load_limit": LOAD_LIMIT,
            "summary": "p95/p99/complete 不差于 DFS 且 p50 回退 <=5%,并在 load<1 下测得",
        },
        "max_load": max(loads),
        "quiet": quiet,
        "records": records,
        "verdicts": verdicts,
        "enable_memo_by_default": enable,
    }
    print("\n判定(最大 load %.2f,需要 < %.1f):" % (max(loads), LOAD_LIMIT))
    for item in verdicts:
        print("  workers=%d %s %s" % (
            item["workers"], "PASS" if item["pass"] else "FAIL",
            json.dumps(item["checks"], ensure_ascii=False)))
    print("结论:%s" % ("建议默认开启 memo" if enable else "保持 opt-in(默认 DFS)"))
    if args.json:
        args.json.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                             encoding="utf-8")
        print("完整结果已写入 %s" % args.json)
    return 0 if enable else 1


if __name__ == "__main__":
    raise SystemExit(main())
