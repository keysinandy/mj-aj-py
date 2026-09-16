#!/usr/bin/env python3
"""Run a small local score evaluation with exhaustive Fast-EV2 decisions.

This is an offline diagnostic runner.  The hero uses a discard profile with
large node/time budgets and score-only EV2 ordering; the other three seats
remain legacy so the result is a paired policy comparison.  HU/KONG/reaction
branches are still reported as delegated because this change only completes
the ordinary-discard EV2 path.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from mj.bot import choose_action
from mj.decision.fast_ev import choose_game_action
from mj.decision.profile import ProfileSpec
from mj.game import Game


def _percentile(values, p):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * p))]


def _bootstrap(values, *, rounds=2000, seed=20260915):
    values = [float(value) for value in values]
    if not values:
        return {"n": 0, "mean": None, "low": None, "high": None}
    rng = random.Random(seed)
    n = len(values)
    means = [sum(values[rng.randrange(n)] for _ in range(n)) / n
             for _ in range(max(1, int(rounds)))]
    means.sort()
    return {
        "n": n,
        "mean": statistics.fmean(values),
        "low": means[int(0.025 * (len(means) - 1))],
        "high": means[int(0.975 * (len(means) - 1))],
        "method": "source-game-bootstrap-diagnostic",
    }


def _new_stats():
    return {
        "discard_evaluations": 0,
        "ev2_complete": 0,
        "q0_fallback": 0,
        "delegated": 0,
        "levels": {},
        "fallbacks": {},
        "all_elapsed_ms": [],
        "ordinary_elapsed_ms": [],
        "all_nodes": [],
        "ordinary_nodes": [],
        "all_kernel_calls": [],
        "ordinary_kernel_calls": [],
    }


def _note_evaluation(stats, evaluation):
    if not isinstance(evaluation, dict):
        return
    level = evaluation.get("level") or "unknown"
    fallback_reason = evaluation.get("fallback_reason")
    delegated_reason = evaluation.get("delegated_reason")
    reason = fallback_reason or delegated_reason
    # ``choose_game_action`` returns a discard-scoped legacy explanation for
    # only-legal, reaction, and HU/KONG delegation too.  Those calls must not
    # inflate the ordinary-discard EV2 denominator.
    ordinary_attempt = level.startswith("V2-") or str(fallback_reason).startswith("q0_")
    if ordinary_attempt:
        stats["discard_evaluations"] += 1
    levels = stats["levels"]
    levels[level] = levels.get(level, 0) + 1
    if evaluation.get("ev2_complete") or level == "V2-EV2":
        stats["ev2_complete"] += 1
    if level == "V2-Q0" or str(reason).startswith("q0_"):
        stats["q0_fallback"] += 1
    if level == "legacy":
        stats["delegated"] += 1
    if reason:
        fallbacks = stats["fallbacks"]
        fallbacks[reason] = fallbacks.get(reason, 0) + 1
    if evaluation.get("elapsed_ms") is not None:
        elapsed = float(evaluation["elapsed_ms"])
        stats["all_elapsed_ms"].append(elapsed)
        if ordinary_attempt:
            stats["ordinary_elapsed_ms"].append(elapsed)
    if evaluation.get("nodes") is not None:
        nodes = int(evaluation["nodes"])
        stats["all_nodes"].append(nodes)
        if ordinary_attempt:
            stats["ordinary_nodes"].append(nodes)
    if evaluation.get("kernel_calls") is not None:
        kernel_calls = int(evaluation["kernel_calls"])
        stats["all_kernel_calls"].append(kernel_calls)
        if ordinary_attempt:
            stats["ordinary_kernel_calls"].append(kernel_calls)


def _finish_stats(stats):
    all_elapsed = stats.pop("all_elapsed_ms")
    elapsed = stats.pop("ordinary_elapsed_ms")
    all_nodes = stats.pop("all_nodes")
    nodes = stats.pop("ordinary_nodes")
    all_kernels = stats.pop("all_kernel_calls")
    kernels = stats.pop("ordinary_kernel_calls")
    stats["ev2_coverage"] = (
        stats["ev2_complete"] / stats["discard_evaluations"]
        if stats["discard_evaluations"] else None)
    stats["timing_ms"] = {
        "n": len(elapsed),
        "mean": statistics.fmean(elapsed) if elapsed else None,
        "p50": _percentile(elapsed, 0.50),
        "p95": _percentile(elapsed, 0.95),
        "p99": _percentile(elapsed, 0.99),
        "max": max(elapsed) if elapsed else None,
    }
    stats["all_calls_timing_ms"] = {
        "n": len(all_elapsed),
        "mean": statistics.fmean(all_elapsed) if all_elapsed else None,
        "p50": _percentile(all_elapsed, 0.50),
        "p95": _percentile(all_elapsed, 0.95),
        "p99": _percentile(all_elapsed, 0.99),
        "max": max(all_elapsed) if all_elapsed else None,
    }
    stats["nodes_summary"] = {
        "mean": statistics.fmean(nodes) if nodes else None,
        "max": max(nodes) if nodes else None,
    }
    stats["all_calls_nodes_summary"] = {
        "mean": statistics.fmean(all_nodes) if all_nodes else None,
        "max": max(all_nodes) if all_nodes else None,
    }
    stats["kernel_calls_summary"] = {
        "mean": statistics.fmean(kernels) if kernels else None,
        "max": max(kernels) if kernels else None,
    }
    stats["all_calls_kernel_calls_summary"] = {
        "mean": statistics.fmean(all_kernels) if all_kernels else None,
        "max": max(all_kernels) if all_kernels else None,
    }
    return stats


def _play(seed, seat, dealer, ycbk, *, evaluator, profile=None):
    game = Game(seed=seed, dealer=dealer, you_cai_bi_kao=ycbk)
    stats = _new_stats()
    while not game.done:
        current = game.current_seat()
        if current == seat and evaluator == "full-ev2":
            action, evaluation = choose_game_action(game, current, profile)
            # Keep this outside the strategy implementation so the measured
            # timing includes only the local decision, not JSON serialization.
            _note_evaluation(stats, evaluation)
        else:
            action = choose_action(game, current, evaluator="legacy")
        legal = tuple(game.legal_actions())
        if action not in legal:
            raise RuntimeError(
                f"{evaluator} produced illegal action {action}: "
                f"seed={seed} seat={seat} dealer={dealer} current={current} "
                f"phase={game.phase} legal={legal}")
        game.step(action)
    winner = game.result[0] if game.result else None
    multiplier = game.result[1] if game.result else None
    return {
        "score": float(game.scores[seat]),
        "win": bool(winner == seat),
        "mult": multiplier if winner == seat else None,
        "draw": game.result is None,
        "evaluation": _finish_stats(stats),
    }


def _profile(node_budget, time_budget_ms):
    # An uncalibrated profile must not mix heuristic Q0 units with score
    # points.  Zeroing the structural terms makes this diagnostic runner's
    # complete layer order by the raw EV2 score itself.  It remains opt-in and
    # is not a release/calibrated profile.
    return ProfileSpec.shape_v2_discard(
        node_budget=int(node_budget), time_budget_ms=float(time_budget_ms),
        q0_shanten_weight=0.0, q0_u1_weight=0.0,
        q0_ev1_weight=0.0, q0_ev2_weight=1.0,
        q0_fan_weight=0.0, q0_risk_weight=0.0,
        explanation=False)


def run(games=1, seed_start=190000, ycbk=False, *, node_budget=10_000_000,
        time_budget_ms=30_000):
    profile = _profile(node_budget, time_budget_ms)
    rows = []
    started = time.perf_counter()
    for i in range(int(games)):
        seat, dealer = i % 4, (i // 4) % 4
        seed = int(seed_start) + i
        legacy = _play(seed, seat, dealer, ycbk, evaluator="legacy")
        full = _play(seed, seat, dealer, ycbk, evaluator="full-ev2",
                     profile=profile)
        rows.append({
            "seed": seed, "seat": seat, "dealer": dealer,
            "legacy": legacy, "full_ev2": full,
            "score_delta": full["score"] - legacy["score"],
        })
    deltas = [row["score_delta"] for row in rows]
    full_scores = [row["full_ev2"]["score"] for row in rows]
    legacy_scores = [row["legacy"]["score"] for row in rows]
    return {
        "schema": "bot-ev-discard/full-local-score-eval-v1",
        "games": int(games), "seed_start": int(seed_start),
        "ycbk": bool(ycbk), "profile": profile.as_json(),
        "primary_metric": "hero_round_score_points",
        "legacy_score": _bootstrap(legacy_scores),
        "full_ev2_score": _bootstrap(full_scores),
        "score_delta": _bootstrap(deltas),
        "full_ev2_wins": sum(row["full_ev2"]["win"] for row in rows),
        "legacy_wins": sum(row["legacy"]["win"] for row in rows),
        "full_ev2_draws": sum(row["full_ev2"]["draw"] for row in rows),
        "legacy_draws": sum(row["legacy"]["draw"] for row in rows),
        "rows": rows,
        "elapsed_s": time.perf_counter() - started,
        "offline_only": True,
        "default_strategy_unchanged": True,
    }


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--games", type=int, default=1)
    parser.add_argument("--seed-start", type=int, default=190000)
    parser.add_argument("--you-cai-bi-kao", action="store_true")
    parser.add_argument("--node-budget", type=int, default=10_000_000)
    parser.add_argument("--time-budget-ms", type=float, default=30_000)
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    value = run(args.games, args.seed_start, args.you_cai_bi_kao,
                node_budget=args.node_budget,
                time_budget_ms=args.time_budget_ms)
    encoded = json.dumps(value, ensure_ascii=False, indent=2)
    print(encoded)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(encoded + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
