#!/usr/bin/env python3
"""Deterministic local timing probe for legacy/shape-v1/shape-v2 decisions."""

from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from mj.bot import choose_action
from mj.game import Game
from mj.hand_eval import warmup

try:
    from mj.shanten import (DISCARD_FRONTIER_KERNEL_VERSION,
                            DISCARD_FRONTIER_BATCH_KERNEL_VERSION)
except ImportError:  # pragma: no cover - rolling deployments
    DISCARD_FRONTIER_KERNEL_VERSION = "unavailable"
    DISCARD_FRONTIER_BATCH_KERNEL_VERSION = None


def _percentile(values, p):
    if not values:
        return None
    values = sorted(values)
    return values[min(len(values) - 1, int((len(values) - 1) * p))]


def run(games=200, seed_start=190000, evaluator="shape-v1"):
    if evaluator == "shape-v1":
        warmup(evaluator)
    samples = {"discard": [], "react": []}
    fallbacks = {}
    levels = {"discard": {}, "react": {}}
    fallback_by_phase = {"discard": {}, "react": {}}
    q_pruned = {"discard": 0, "react": 0}
    nodes = {"discard": [], "react": []}
    kernel_calls = {"discard": [], "react": []}
    decisions = {"discard": 0, "react": 0}
    fallback_decisions = {"discard": 0, "react": 0}
    complete_levels = {"discard": 0, "react": 0}
    t0 = time.perf_counter()
    for i in range(games):
        g = Game(seed=seed_start + i, dealer=i % 4,
                 you_cai_bi_kao=bool(i % 2))
        while not g.done:
            phase = "discard" if g.phase == "discard" else "react"
            start = time.perf_counter()
            result = choose_action(g, g.current_seat(), evaluator=evaluator,
                                   return_evaluation=True)
            # Include the actual JSON-compatible explanation construction in
            # the decision budget accounting; this must not trigger a second
            # search.
            if isinstance(result, tuple) and len(result) == 2:
                json.dumps(result[1], ensure_ascii=False, separators=(",", ":"),
                           default=str)
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            act, ev = result if isinstance(result, tuple) else (result, None)
            if act not in g.legal_actions():
                raise AssertionError(f"illegal {act} phase={g.phase}")
            samples[phase].append(elapsed_ms)
            reason = getattr(ev, "fallback_reason", None)
            if isinstance(ev, dict):
                reason = ev.get("fallback_reason")
                level = ev.get("level")
            else:
                level = getattr(ev, "level", None)
            level = level or "legacy"
            if isinstance(ev, dict):
                if ev.get("nodes") is not None:
                    nodes[phase].append(int(ev["nodes"]))
                if ev.get("kernel_calls") is not None:
                    kernel_calls[phase].append(int(ev["kernel_calls"]))
            elif ev is not None:
                if getattr(ev, "nodes", None) is not None:
                    nodes[phase].append(int(ev.nodes))
                if getattr(ev, "kernel_calls", None) is not None:
                    kernel_calls[phase].append(int(ev.kernel_calls))
            levels[phase][level] = levels[phase].get(level, 0) + 1
            decisions[phase] += 1
            if level in ("V2-EV1", "V2-EV2", "Q"):
                complete_levels[phase] += 1
            if isinstance(ev, dict):
                q_pruned[phase] += sum(
                    1 for item in ev.get("candidates", ())
                    if item.get("q_pruned"))
            elif ev is not None:
                q_pruned[phase] += sum(
                    1 for item in getattr(ev, "candidates", ())
                    if item.get("q_pruned"))
            # ``only_legal_action`` is a normal terminal/forced branch, not a
            # policy fallback.  Keep it in the reason histogram but exclude it
            # from the fallback-rate denominator used by the acceptance gate.
            if reason:
                if reason != "only_legal_action":
                    fallback_decisions[phase] += 1
                fallbacks[reason] = fallbacks.get(reason, 0) + 1
                phase_reasons = fallback_by_phase[phase]
                phase_reasons[reason] = phase_reasons.get(reason, 0) + 1
            g.step(act)
    summary = {"games": games, "seed_start": seed_start,
               "evaluator": evaluator,
               "elapsed_s": time.perf_counter() - t0,
               "decisions": decisions,
               "fallback_decisions": fallback_decisions,
               "complete_levels": complete_levels,
               "fallbacks": fallbacks,
               "fallbacks_by_phase": fallback_by_phase,
               "levels": levels,
               "q_pruned_candidates": q_pruned}
    for phase, values in samples.items():
        summary[phase] = {
            "n": len(values), "mean_ms": statistics.fmean(values) if values else None,
            "p50_ms": _percentile(values, .50),
            "p95_ms": _percentile(values, .95),
            "p99_ms": _percentile(values, .99),
            "max_ms": max(values) if values else None,
        }
        summary[phase]["fallback_rate"] = (
            fallback_decisions[phase] / decisions[phase]
            if decisions[phase] else None)
        summary[phase]["complete_level_rate"] = (
            complete_levels[phase] / decisions[phase]
            if decisions[phase] else None)
        for name, source in (("nodes", nodes[phase]),
                             ("kernel_calls", kernel_calls[phase])):
            summary[phase][name] = {
                "n": len(source),
                "mean": statistics.fmean(source) if source else None,
                "p50": _percentile(source, .50),
                "p95": _percentile(source, .95),
                "p99": _percentile(source, .99),
                "max": max(source) if source else None,
            }
    summary["elapsed_per_game_s"] = (
        summary["elapsed_s"] / games if games else None)
    return summary


def _median(values):
    return statistics.median(values) if values else None


def run_interleaved(games=200, seed_start=190000,
                    evaluators=("shape-v1", "shape-v2"), repetitions=3):
    """Run the frozen timing workload in an alternating evaluator schedule.

    Each evaluator sees the same source seed range in every repetition.  The
    evaluator order is reversed on alternating repetitions to reduce a simple
    warm-up/thermal-order bias.  This is an offline timing artifact only: it
    does not alter the production default or constitute online evidence.
    """
    evaluators = tuple(dict.fromkeys(str(x) for x in evaluators))
    if not evaluators:
        raise ValueError("at least one evaluator is required")
    repetitions = int(repetitions)
    if games <= 0 or repetitions <= 0:
        raise ValueError("games and repetitions must be positive")
    runs = []
    for repetition in range(repetitions):
        order = (evaluators if repetition % 2 == 0
                 else tuple(reversed(evaluators)))
        for order_index, evaluator in enumerate(order):
            value = run(games, seed_start, evaluator)
            value.update({
                "repetition": repetition + 1,
                "order_index": order_index,
                "schedule_order": list(order),
            })
            runs.append(value)

    by_evaluator = {evaluator: [] for evaluator in evaluators}
    for value in runs:
        by_evaluator[value["evaluator"]].append(value)

    aggregate = {}
    for evaluator, values in by_evaluator.items():
        elapsed = [float(value["elapsed_per_game_s"])
                   for value in values]
        discard = [value["discard"] for value in values]
        react = [value["react"] for value in values]
        aggregate[evaluator] = {
            "runs": len(values),
            "elapsed_per_game_s": {
                "values": elapsed,
                "median": _median(elapsed),
                "mean": statistics.fmean(elapsed) if elapsed else None,
            },
            "discard": {
                "p50_ms": [value.get("p50_ms") for value in discard],
                "p95_ms": [value.get("p95_ms") for value in discard],
                "p99_ms": [value.get("p99_ms") for value in discard],
                "max_ms": [value.get("max_ms") for value in discard],
                "fallback_rate": [value.get("fallback_rate")
                                   for value in discard],
                "complete_level_rate": [value.get("complete_level_rate")
                                         for value in discard],
                "nodes": [value.get("nodes") for value in discard],
                "kernel_calls": [value.get("kernel_calls")
                                  for value in discard],
            },
            "react": {
                "p50_ms": [value.get("p50_ms") for value in react],
                "p95_ms": [value.get("p95_ms") for value in react],
                "p99_ms": [value.get("p99_ms") for value in react],
                "max_ms": [value.get("max_ms") for value in react],
                "fallback_rate": [value.get("fallback_rate")
                                   for value in react],
                "complete_level_rate": [value.get("complete_level_rate")
                                         for value in react],
                "nodes": [value.get("nodes") for value in react],
                "kernel_calls": [value.get("kernel_calls")
                                  for value in react],
            },
        }

    baseline = aggregate.get("shape-v1")
    baseline_median = (baseline["elapsed_per_game_s"]["median"]
                       if baseline else None)
    comparison = {}
    for evaluator, value in aggregate.items():
        median = value["elapsed_per_game_s"]["median"]
        increase = (median / baseline_median - 1.0
                    if median is not None and baseline_median else None)
        # A timing result is not a release pass when the purported fast layer
        # is absent.  This field is deliberately diagnostic; the release gate
        # also needs independent score, regret and online evidence.
        complete_rate = (value["discard"]["complete_level_rate"] or [])
        fallback_rate = (value["discard"]["fallback_rate"] or [])
        comparison[evaluator] = {
            "elapsed_per_game_median_increase_vs_shape_v1": increase,
            "within_15_percent": (increase is not None and increase <= .15),
            "complete_discard_rate_median": _median(
                [x for x in complete_rate if x is not None]),
            "fallback_discard_rate_median": _median(
                [x for x in fallback_rate if x is not None]),
            "performance_gate_passed": bool(
                evaluator == "shape-v1" or
                (increase is not None and increase <= .15 and
                 complete_rate and any((x or 0.0) > 0.0
                                        for x in complete_rate))),
        }

    return {
        "schema": "bot-ev-discard/interleaved-performance-v1",
        "workload": {"games": games, "seed_start": seed_start,
                      "repetitions": repetitions,
                      "evaluators": list(evaluators),
                      "include_explanation_serialization": True},
        "runtime": {"python": sys.version,
                     "platform": platform.platform(),
                     "machine": platform.machine(),
                     "kernel": DISCARD_FRONTIER_KERNEL_VERSION,
                     "frontier_batch_kernel":
                     DISCARD_FRONTIER_BATCH_KERNEL_VERSION},
        "runs": runs,
        "aggregate": aggregate,
        "comparison": comparison,
        "offline_only": True,
        "legacy_default_unchanged": True,
    }


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=200)
    ap.add_argument("--seed-start", type=int, default=190000)
    ap.add_argument("--evaluator", choices=("legacy", "shape-v1", "shape-v2"),
                    default="shape-v1")
    ap.add_argument("--interleaved", action="store_true",
                    help="run the alternating multi-evaluator benchmark")
    ap.add_argument("--evaluators", default="shape-v1,shape-v2",
                    help="comma-separated evaluator order for --interleaved")
    ap.add_argument("--repetitions", type=int, default=3)
    ap.add_argument("--output")
    args = ap.parse_args(argv)
    if args.interleaved:
        value = run_interleaved(
            args.games, args.seed_start,
            tuple(x.strip() for x in args.evaluators.split(",") if x.strip()),
            args.repetitions)
    else:
        value = run(args.games, args.seed_start, args.evaluator)
    encoded = json.dumps(value, ensure_ascii=False, indent=2)
    print(encoded)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(encoded + "\n")


if __name__ == "__main__":
    main()
