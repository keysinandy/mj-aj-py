#!/usr/bin/env python3
"""形状护栏冻结状态成对 A/B(离线)。

对同一批确定性状态(seed 局的当前弃牌决策),在**同一预算**下分别以
``shape_guard_enabled=False/True`` 决策,报告:

* 护栏触发率与触发样例();
* 决策变化数 + 逐条差异(selected/level/admitted/dropped);
* future 指标与自身延迟(raw 端到端);
* 参数网格 ``slack ∈ {0,1,2} × delta ∈ {6,8,10}`` 的对照;
* 延迟验收:护栏开启后 p95 是否仍在预算内。

用法:
    PYTHONPATH=/tmp/wheel-unpacked:. python3 scripts/shape_guard_ab.py \
        --states 800 --budget-ms 50 --json evidence/shape-guard-ab.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mj.bot import choose_discard  # noqa: E402
from mj.game import Game  # noqa: E402
from mj.legacy_eval import LegacyTwoPlyProfile  # noqa: E402


def _profile(budget_ms, **overrides):
    values = {
        "kernel": "rust",
        "time_budget_ms": float(budget_ms),
        "soft_budget_ms": float(budget_ms) * 0.8,
        "hard_budget_ms": float(budget_ms),
        "shape_quality_enabled": False,
        "shape_quality_guard_enabled": False,
    }
    values.update(overrides)
    return LegacyTwoPlyProfile.weighted_online(**values)


def _decide(seed, profile):
    game = Game(seed=seed)
    seat = game.current_seat()
    started = time.perf_counter()
    try:
        action, info = choose_discard(game, seat, return_info=True,
                                      profile=profile)
    except Exception as exc:  # 状态本身非法时记录而不中断扫描
        return None, {"error": type(exc).__name__, "detail": str(exc)}
    info = dict(info)
    info["_ms"] = (time.perf_counter() - started) * 1000.0
    info["_action"] = action
    return action, info


def _quantile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * fraction))]


def _compare(seeds, budget_ms, slack, delta, limit_examples=5):
    off_profile = _profile(budget_ms, shape_guard_enabled=False,
                           shape_guard_ukeire_slack=slack,
                           shape_guard_shape_delta=delta)
    on_profile = _profile(budget_ms, shape_guard_enabled=True,
                          shape_guard_ukeire_slack=slack,
                          shape_guard_shape_delta=delta)
    fired, changed, changed_guard, changed_noisy = 0, 0, 0, 0
    examples = []
    ms_off, ms_on = [], []
    skipped = {}
    for seed in seeds:
        off_action, off_info = _decide(seed, off_profile)
        if off_action is None:
            skipped[off_info["error"]] = skipped.get(off_info["error"], 0) + 1
            continue
        on_action, on_info = _decide(seed, on_profile)
        if on_action is None:
            skipped[on_info["error"]] = skipped.get(on_info["error"], 0) + 1
            continue
        ms_off.append(off_info["_ms"])
        ms_on.append(on_info["_ms"])
        guard = on_info.get("frontier_guard") or {}
        admitted = bool(guard.get("admitted_tiles"))
        if admitted:
            fired += 1
        if (off_action != on_action or off_info.get("level")
                != on_info.get("level")):
            changed += 1
            # 预算边界本身会随负载漂移:任一侧回退且护栏未生效的差异记为噪声
            fallback = bool(off_info.get("fallback_reason")
                            or on_info.get("fallback_reason"))
            if admitted:
                changed_guard += 1
            elif fallback:
                changed_noisy += 1
            if len(examples) < limit_examples:
                examples.append({
                    "seed": seed,
                    "off": {"action": off_action,
                            "level": off_info.get("level")},
                    "on": {"action": on_action,
                           "level": on_info.get("level")},
                    "admitted": guard.get("admitted_tiles"),
                    "dropped": guard.get("dropped_tiles"),
                    "guard_effect": admitted,
                    "fallback": {"off": off_info.get("fallback_reason"),
                                 "on": on_info.get("fallback_reason")},
                })
    return {
        "slack": slack,
        "delta": delta,
        "states": len(ms_off),
        "guard_fired": fired,
        "guard_fire_rate": fired / max(1, len(ms_off)),
        "decision_changed": changed,
        "decision_changed_by_guard": changed_guard,
        "decision_changed_timing_noise": changed_noisy,
        "examples": examples,
        "latency_ms": {
            "off_p50": _quantile(ms_off, 0.50),
            "off_p95": _quantile(ms_off, 0.95),
            "on_p50": _quantile(ms_on, 0.50),
            "on_p95": _quantile(ms_on, 0.95),
            "on_max": max(ms_on) if ms_on else None,
        },
        "skipped": skipped,
    }


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--states", type=int, default=800)
    parser.add_argument("--seed-start", type=int, default=0)
    parser.add_argument("--budget-ms", type=float, default=50.0)
    parser.add_argument("--grid", default=None,
                        help="逗号分隔的 slack:delta 列表;默认用声明网格")
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args(argv)
    seeds = list(range(args.seed_start, args.seed_start + args.states))
    grid = ([(0, 8), (1, 6), (1, 8), (1, 10), (2, 6), (2, 8), (2, 10)]
            if not args.grid else
            [tuple(int(v) for v in item.split(":")) for item in args.grid.split(",")])
    results = []
    for slack, delta in grid:
        row = _compare(seeds, args.budget_ms, slack, delta)
        results.append(row)
        lat = row["latency_ms"]
        print("slack=%d delta=%2d states=%d fired=%d(%.2f%%) changed=%d "
              "on p50=%.2f p95=%.2f max=%.2f | off p50=%.2f p95=%.2f" % (
                  slack, delta, row["states"], row["guard_fired"],
                  row["guard_fire_rate"] * 100, row["decision_changed"],
                  lat["on_p50"] or 0, lat["on_p95"] or 0, lat["on_max"] or 0,
                  lat["off_p50"] or 0, lat["off_p95"] or 0))
    baseline = next(row for row in results if row["slack"] == 1
                    and row["delta"] == 8)
    latency_pass = (baseline["latency_ms"]["on_p95"] or 0) <= args.budget_ms
    outcome_evidence = False  # 离线决策级 A/B 不含收益指标
    payload = {
        "budget_ms": args.budget_ms,
        "states": args.states,
        "seed_start": args.seed_start,
        "grid": results,
        "baseline": {"slack": 1, "delta": 8},
        "latency_pass": latency_pass,
        "outcome_evidence_available": outcome_evidence,
        "enable_by_default": bool(latency_pass and outcome_evidence),
    }
    print("\n延迟验收(p95 <= %.0fms): %s" % (
        args.budget_ms, "PASS" if latency_pass else "FAIL"))
    print("收益证据: %s" % ("有" if outcome_evidence else "无(离线无对局结果)"))
    print("结论: %s" % ("可考虑切换默认" if payload["enable_by_default"]
                        else "保持默认关闭(证据不足)"))
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                             encoding="utf-8")
        print("已写入 %s" % args.json)
    return 0 if payload["enable_by_default"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
