#!/usr/bin/env python3
"""Measure the legacy/shape-v1/all-root reaction delta on frozen fixtures.

This is an offline gate probe, not an online policy switch.  It uses the same
public reaction fixtures and sampled world for each evaluator, serializes the
explanation inside the timed region, and reports incomplete all-root decisions
separately from the frozen shape-v1 baseline.
"""

from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import json
import os
import platform
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from mj.bot import choose_action
from mj.decision.context import PublicDecisionContext
from mj.decision.profile import ProfileSpec
from mj.decision.root import evaluate_root_context
from mj.rollout.belief import BeliefSampler
from mj.rollout.simulator import build_world_game
from scripts.bot_ev_root_teacher import _context_from_case, load_fixture


DEFAULT_FIXTURE = (Path(__file__).resolve().parent.parent / "tests" /
                   "fixtures" / "bot_ev_root_cases.json")
REACTION_PHASES = {"react", "response_peng", "response_chi"}
MODES = ("legacy", "shape-v1", "all-root")


def _percentile(values, p):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1,
                       int((len(ordered) - 1) * float(p)))]


def _reaction_cases(fixture):
    document = load_fixture(fixture)
    cases = [case for case in document.get("cases", ())
             if case.get("context", {}).get("phase") in REACTION_PHASES]
    if not cases:
        raise ValueError("fixture has no supported reaction cases")
    return cases


def _profile():
    # Keep the production all-root defaults visible in the report.  A caller
    # may lower the budgets for a diagnostic run, but the gate never treats a
    # delegated legacy result as a complete v2 reaction.
    return ProfileSpec.shape_v2_all_root(calibrated=True)


def _decision(context, world, mode):
    game = build_world_game(context, world)
    legal = tuple(game.legal_actions())
    if mode == "legacy":
        action = choose_action(game, context.hero_seat,
                               evaluator="legacy-v1")
        evaluation = {
            "version": "legacy", "profile": "legacy", "scope": "legacy",
            "level": "legacy", "selected": int(action),
            "reason": "legacy_baseline", "complete": True,
        }
    elif mode == "shape-v1":
        action, evaluation = choose_action(
            game, context.hero_seat, evaluator="shape-v1",
            return_evaluation=True)
        if hasattr(evaluation, "as_json"):
            evaluation = evaluation.as_json()
    elif mode == "all-root":
        # Project the actual world Game to the public value object inside the
        # timed call.  Opponent tile identities are never passed to v2.
        public = PublicDecisionContext.from_game(game, context.hero_seat)
        legacy = choose_action(game, context.hero_seat,
                               evaluator="legacy-v1")
        result = evaluate_root_context(
            public, _profile(), legacy_action=legacy)
        action = result.selected if result.selected is not None else legacy
        evaluation = result.as_json()
    else:  # pragma: no cover - guarded by the public run() API
        raise ValueError(f"unknown mode {mode!r}")
    if action not in legal:
        raise AssertionError(
            f"{mode} produced illegal action {action}; legal={legal}")
    # Explanation encoding is part of the measured decision path.
    json.dumps(evaluation, ensure_ascii=False, separators=(",", ":"),
               default=str)
    return int(action), evaluation


def _measure(context, world, mode):
    started = time.perf_counter()
    action, evaluation = _decision(context, world, mode)
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    return {
        "action": action,
        "elapsed_ms": elapsed_ms,
        "level": evaluation.get("level", "legacy"),
        "reason": evaluation.get("reason"),
        "complete": bool(evaluation.get("complete", False)),
        "nodes": evaluation.get("nodes"),
        "kernel_calls": evaluation.get("kernel_calls"),
    }


def _summary(rows, mode):
    elapsed = [float(row["elapsed_ms"]) for row in rows]
    levels = Counter(row["level"] for row in rows)
    reasons = Counter(row["reason"] for row in rows if row.get("reason"))
    node_values = [int(row["nodes"]) for row in rows
                   if row.get("nodes") is not None]
    kernel_values = [int(row["kernel_calls"]) for row in rows
                     if row.get("kernel_calls") is not None]
    complete = sum(1 for row in rows if row["complete"])
    # ``legacy`` and the KONG_OPEN shape-v1 branch are intentional baselines;
    # only an all-root delegation is a v2 fallback for this report.
    fallback = (sum(1 for row in rows if row["level"] == "legacy")
                if mode == "all-root" else 0)

    def numeric(values):
        return {
            "n": len(values),
            "p50": _percentile(values, .50),
            "p95": _percentile(values, .95),
            "p99": _percentile(values, .99),
            "max": max(values) if values else None,
        }

    return {
        "mode": mode, "decisions": len(rows),
        "elapsed_ms": numeric(elapsed), "levels": dict(levels),
        "reasons": dict(reasons), "nodes": numeric(node_values),
        "kernel_calls": numeric(kernel_values),
        "complete_rate": complete / len(rows) if rows else None,
        "fallback_rate": fallback / len(rows) if rows else None,
        "p95_limit_ms": 10.0,
        "reaction_p95_gate": bool(
            rows and _percentile(elapsed, .95) <= 10.0 and
            (mode != "all-root" or complete == len(rows))),
    }


def _one_probe(args):
    case, context, seed, sample_id, mode = args
    world = BeliefSampler(context, seed=seed).sample(sample_id)
    row = _measure(context, world, mode)
    row.update({"fixture_id": case["id"], "sample_id": sample_id})
    return row


def run(*, fixture=DEFAULT_FIXTURE, repetitions=3, seed=20260916,
        concurrent_games=10):
    cases = _reaction_cases(fixture)
    contexts = {case["id"]: _context_from_case(case) for case in cases}
    modes = list(MODES)
    rows = {mode: [] for mode in modes}
    repetitions = int(repetitions)
    if repetitions <= 0:
        raise ValueError("repetitions must be positive")
    for repetition in range(repetitions):
        order = modes if repetition % 2 == 0 else list(reversed(modes))
        for mode in order:
            for case_index, case in enumerate(cases):
                context = contexts[case["id"]]
                context.validate_for("rollout")
                sample_id = repetition * len(cases) + case_index
                world = BeliefSampler(context, seed=seed).sample(sample_id)
                row = _measure(context, world, mode)
                row.update({"fixture_id": case["id"],
                            "sample_id": sample_id})
                rows[mode].append(row)

    concurrent = {}
    concurrent_games = max(0, int(concurrent_games))
    if concurrent_games:
        # Ten independent windows provide a small contention/recovery probe;
        # this is intentionally reported apart from the serial p95 gate.
        for mode in modes:
            jobs = []
            for index in range(concurrent_games):
                case = cases[index % len(cases)]
                jobs.append((case, contexts[case["id"]], seed,
                             repetitions * len(cases) + index, mode))
            with ThreadPoolExecutor(max_workers=min(10, concurrent_games)) as pool:
                concurrent_rows = list(pool.map(_one_probe, jobs))
            summary = _summary(concurrent_rows, mode)
            summary["workers"] = min(10, concurrent_games)
            summary["concurrent_games"] = concurrent_games
            concurrent[mode] = summary

    summaries = {mode: _summary(rows[mode], mode) for mode in modes}
    baseline = summaries["shape-v1"]["elapsed_ms"]["p50"]
    comparison = {}
    for mode, summary in summaries.items():
        median = summary["elapsed_ms"]["p50"]
        increase = (median / baseline - 1.0
                    if median is not None and baseline else None)
        comparison[mode] = {
            "p50_increase_vs_shape_v1": increase,
            "within_15_percent": increase is not None and increase <= .15,
            "reaction_p95_gate": summary["reaction_p95_gate"],
            "complete_rate": summary["complete_rate"],
            "fallback_rate": summary["fallback_rate"],
            "performance_gate_passed": bool(
                mode != "all-root" or
                (summary["reaction_p95_gate"] and
                 increase is not None and increase <= .15 and
                 summary["fallback_rate"] == 0.0)),
        }
    return {
        "schema": "bot-react-decision/reaction-performance-v1",
        "fixture": str(fixture), "fixture_cases": [case["id"] for case in cases],
        "repetitions": repetitions, "seed": seed,
        "profile": _profile().as_json(),
        "runtime": {"python": sys.version,
                     "platform": platform.platform(),
                     "machine": platform.machine()},
        "summaries": summaries, "comparison": comparison,
        "concurrent_probe": concurrent, "offline_only": True,
        "legacy_default_unchanged": True,
        "explanation_serialization_in_timing": True,
    }


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--seed", type=int, default=20260916)
    parser.add_argument("--concurrent-games", type=int, default=10)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    value = run(fixture=args.fixture, repetitions=args.repetitions,
                seed=args.seed, concurrent_games=args.concurrent_games)
    encoded = json.dumps(value, ensure_ascii=False, indent=2)
    print(encoded)
    if args.output:
        args.output.write_text(encoded + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
