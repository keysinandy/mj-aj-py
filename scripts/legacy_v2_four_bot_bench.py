#!/usr/bin/env python3
"""Measure complete local games with the same legacyV2 bot in all seats."""

from __future__ import annotations

import argparse
from collections import Counter
import json
import statistics
import sys
import time

from mj.bot import choose_action
from mj.game import Game
from mj.legacy_eval import LegacyTwoPlyProfile
from mj.shanten import kernel_runtime_diagnostic


BASELINE = "legacy-v2-baseline"
SHAPE_EVALUATORS = ("legacy-v2-shape-phase-a",
                    "legacy-v2-shape-phase-b")


def _quantile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * fraction))]


def run(games, seed_start, evaluator="legacyV2"):
    elapsed = []
    decisions = []
    score_totals = [0.0] * 4
    draws = wins = 0
    for offset in range(games):
        seed = seed_start + offset
        game = Game(seed=seed, dealer=offset % 4)
        decision_count = 0
        started = time.perf_counter()
        while not game.done:
            seat = game.current_seat()
            action = choose_action(game, seat, evaluator=evaluator)
            game.step(action)
            decision_count += 1
        elapsed.append((time.perf_counter() - started) * 1000.0)
        decisions.append(decision_count)
        for seat, score in enumerate(game.scores):
            score_totals[seat] += float(score)
        draws += int(game.result is None)
        wins += int(game.result is not None)
    return {
        "games": games,
        "seed_start": seed_start,
        "evaluator": evaluator,
        "dealer_schedule": "(game_index % 4)",
        "elapsed_ms_per_game": {
            "p50": _quantile(elapsed, 0.50),
            "p95": _quantile(elapsed, 0.95),
            "p99": _quantile(elapsed, 0.99),
            "max": max(elapsed) if elapsed else None,
            "mean": statistics.fmean(elapsed) if elapsed else None,
        },
        "decisions_per_game_mean": (
            statistics.fmean(decisions) if decisions else None),
        "decisions_total": sum(decisions),
        "wins": wins,
        "draws": draws,
        "settlement_score_totals_by_seat": score_totals,
        "kernel": kernel_runtime_diagnostic(),
        "profile": LegacyTwoPlyProfile.weighted_online().as_json(),
    }


def run_interleaved(games=200, repeats=3, seed_start=560000,
                    evaluators=(BASELINE, *SHAPE_EVALUATORS)):
    evaluators = tuple(dict.fromkeys(str(item) for item in evaluators))
    if len(evaluators) < 2 or int(games) <= 0 or int(repeats) <= 0:
        raise ValueError("interleaved 4-bot benchmark needs 2 evaluators and positive sizes")
    elapsed = {name: [] for name in evaluators}
    decisions = {name: [] for name in evaluators}
    outcomes = {name: {"wins": 0, "draws": 0} for name in evaluators}
    execution_counts = Counter()
    total = int(games) * int(repeats) * len(evaluators)
    completed = 0
    started = time.perf_counter()
    for repeat in range(int(repeats)):
        for offset in range(int(games)):
            seed = int(seed_start) + offset
            dealer = (repeat * int(games) + offset) % 4
            order = list(evaluators)
            if (repeat + offset) % 2:
                order.reverse()
            for evaluator in order:
                game = Game(seed=seed, dealer=dealer)
                decision_count = 0
                game_started = time.perf_counter()
                while not game.done:
                    seat = game.current_seat()
                    action = choose_action(game, seat, evaluator=evaluator)
                    if action not in tuple(game.legal_actions()):
                        raise RuntimeError(
                            f"{evaluator} produced illegal action={action}: "
                            f"seed={seed} dealer={dealer} seat={seat} "
                            f"phase={game.phase}")
                    game.step(action)
                    decision_count += 1
                game_elapsed = (time.perf_counter() - game_started) * 1000
                if sum(game.scores) != 0:
                    raise AssertionError(f"score conservation failed: {game.scores}")
                elapsed[evaluator].append(game_elapsed)
                decisions[evaluator].append(decision_count)
                outcomes[evaluator]["draws"] += int(game.result is None)
                outcomes[evaluator]["wins"] += int(game.result is not None)
                execution_counts["baseline_first" if order[0] == BASELINE
                                 else "candidate_first"] += 1
                completed += 1
                if completed % 50 == 0:
                    print(f"interleaved 4-bot games {completed}/{total}",
                          file=sys.stderr, flush=True)
    summaries = {}
    for name in evaluators:
        values = elapsed[name]
        counts = decisions[name]
        summaries[name] = {
            "games": len(values),
            "elapsed_ms_per_game": {
                "p50": _quantile(values, 0.50),
                "p95": _quantile(values, 0.95),
                "p99": _quantile(values, 0.99),
                "max": max(values) if values else None,
                "mean": statistics.fmean(values) if values else None,
            },
            "decisions_per_game_mean": (
                statistics.fmean(counts) if counts else None),
            "wins": outcomes[name]["wins"],
            "draws": outcomes[name]["draws"],
        }
    comparisons = {}
    if BASELINE in summaries:
        baseline_p50 = summaries[BASELINE]["elapsed_ms_per_game"]["p50"]
        for name in evaluators:
            if name == BASELINE:
                continue
            candidate_p50 = summaries[name]["elapsed_ms_per_game"]["p50"]
            regression = ((candidate_p50 / baseline_p50 - 1) * 100
                          if baseline_p50 else None)
            comparisons[name] = {
                "p50_regression_pct": regression,
                "elapsed_game_gate_pass": (
                    regression is not None and regression <= 10.0),
            }
    elapsed_s = time.perf_counter() - started
    return {
        "schema": "legacy-v2-shape-aware-two-ply/interleaved-four-bot-v1",
        "mode": "interleaved_four_bot",
        "games_per_evaluator_per_repeat": int(games),
        "repeats": int(repeats), "seed_start": int(seed_start),
        "dealer_schedule": "(repeat * games + game_index) % 4",
        "all_four_seats_same_evaluator": True,
        "interleaved": True,
        "alternating_order_counts": dict(execution_counts),
        "evaluators": summaries, "comparison": comparisons,
        "kernel": kernel_runtime_diagnostic(),
        "runtime_s": elapsed_s,
        "total_matches": total,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--games", type=int, default=10)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--seed-start", type=int, default=20260926)
    parser.add_argument("--evaluator", default="legacyV2")
    parser.add_argument("--interleaved", action="store_true")
    parser.add_argument("--evaluators", nargs="+", default=(
        BASELINE, *SHAPE_EVALUATORS))
    parser.add_argument("--output")
    args = parser.parse_args()
    if args.games <= 0:
        parser.error("--games must be positive")
    if args.interleaved:
        value = run_interleaved(
            args.games, args.repeats, args.seed_start,
            tuple(args.evaluators))
    else:
        value = run(args.games, args.seed_start, args.evaluator)
    encoded = json.dumps(value, ensure_ascii=False, indent=2)
    print(encoded)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(encoded + "\n")


if __name__ == "__main__":
    main()
