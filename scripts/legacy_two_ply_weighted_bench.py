"""Benchmark the weighted online two-ply frontier.

The report intentionally separates the raw native call from the complete
``choose_action`` path, because Python root construction and explanation
mapping are part of online latency but not Rust kernel latency.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
import os
import statistics
import sys
import time

from mj.bot import _discard_shape_cost, _feed_risk, choose_action
from mj.game import Game
from mj.legacy_eval import (
    DEFAULT_BOT_EVALUATOR,
    LegacyRootCandidate,
    LegacyTwoPlyProfile,
    _limit_weighted_frontier,
    _native_legal_masks,
    _root_features,
)
from mj.shanten import shanten, weighted_two_ply_frontier


BASELINE_EVALUATOR = "legacy-v2-baseline"
SPEED_BAND_EVALUATOR = "legacy-v2-speed-band"
SHAPE_EVALUATORS = {
    "legacy-v2-shape-phase-a": "root",
    "legacy-v2-shape-phase-b": "full",
}


def _quantile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * fraction))]


def _roots(game, seat, profile):
    hand = game.hands[seat]
    locked = len(game.melds[seat])
    visible = tuple(game.visible_counts(seat))
    frozen = bool(getattr(game, "freeze", 0) > 0 and
                  seat != getattr(game, "freezer", None))
    only = game.drawn[seat] if frozen else None
    best_s = None
    candidates = []
    for tile in range(34):
        if hand[tile] == 0 or (only is not None and tile != only):
            continue
        post = list(hand)
        post[tile] -= 1
        value = shanten(post, locked)
        if best_s is None or value < best_s:
            best_s, candidates = value, []
        if value == best_s:
            candidates.append(LegacyRootCandidate(
                tile=tile, hand=tuple(post), shanten=value,
                shape_loss=_discard_shape_cost(hand, tile),
                feed_risk=_feed_risk(game, seat, tile)))
    enriched, frontier, diagnostics = _root_features(
        candidates, locked, visible,
        shape_quality_enabled=profile.shape_quality_enabled,
        marginal_role_enabled=(
            profile.marginal_structure_guard_enabled or
            profile.speed_band_enabled or profile.pareto_frontier_enabled),
        speed_band_enabled=profile.speed_band_enabled,
        speed_band_min_ratio_by_shanten=(
            profile.speed_band_min_ratio_by_shanten),
        pareto_frontier_enabled=profile.pareto_frontier_enabled,
        max_frontier_candidates=profile.max_frontier_candidates)
    frontier, diagnostics = _limit_weighted_frontier(
        frontier, diagnostics, profile.max_frontier_candidates,
        shape_quality_enabled=profile.shape_quality_enabled)
    return locked, visible, frozen, frontier


def _summary(durations, complete, partial, fallback, search_used,
             phase_counts, coverage, frontiers, metrics, reasons):
    return {
        "states": len(durations),
        "complete": complete,
        "partial_accepted": partial,
        "fallback": fallback,
        "search_used": search_used,
        "search_used_rate": (search_used / max(1, len(durations))),
        "search_phase_counts": dict(phase_counts),
        "workers": max((item["workers"] for item in metrics), default=0),
        "p50_ms": _quantile(durations, 0.50),
        "p90_ms": _quantile(durations, 0.90),
        "p95_ms": _quantile(durations, 0.95),
        "p99_ms": _quantile(durations, 0.99),
        "max_ms": max(durations) if durations else None,
        "coverage_p50": statistics.median(coverage) if coverage else None,
        "frontier_p50": statistics.median(frontiers) if frontiers else None,
        "shanten_calls_total": sum(item["shanten_calls"] for item in metrics),
        "ukeire_calls_total": sum(item["ukeire_calls"] for item in metrics),
        "child_nodes_total": sum(item["child_nodes"] for item in metrics),
        "shanten_cache_hit_rate": (
            sum(item["shanten_cache_hits"] for item in metrics) /
            max(1, sum(item["shanten_calls"] for item in metrics))),
        "fallback_reasons": dict(reasons),
    }


def _measure_one(seed, evaluator, profile):
    game = Game(seed=seed)
    seat = game.current_seat()
    started = time.perf_counter()
    requested_evaluator = (
        DEFAULT_BOT_EVALUATOR
        if evaluator in (BASELINE_EVALUATOR, SPEED_BAND_EVALUATOR)
        else evaluator)
    action, info = choose_action(
        game, seat, evaluator=requested_evaluator, return_evaluation=True,
        speed_band_enabled=profile.speed_band_enabled,
        pareto_frontier_enabled=profile.pareto_frontier_enabled,
        speed_band_min_ratio_by_shanten=(
            profile.speed_band_min_ratio_by_shanten))
    end_ms = (time.perf_counter() - started) * 1000.0
    if action not in tuple(game.legal_actions()):
        raise RuntimeError(f"{evaluator} produced illegal action {action}")
    raw_ms = None
    if info.get("decision_scope") != "baotou_scope":
        locked, visible, frozen, frontier = _roots(game, seat, profile)
        masks = _native_legal_masks(frontier, visible, frozen)
        raw_started = time.perf_counter()
        weighted_two_ply_frontier(
            [list(root.hand) for root in frontier],
            [root.shanten for root in frontier], list(visible), masks,
            locked, frozen, profile.node_budget, profile.soft_budget_ms,
            profile.hard_budget_ms, profile.cache_capacity,
            profile.min_partial_coverage, False, profile.workers, False,
            bool(profile.shape_quality_enabled and
                 profile.shape_quality_stage == "full"))
        raw_ms = (time.perf_counter() - raw_started) * 1000.0
    return end_ms, raw_ms, info


def _summary_for_measurements(measurements, profile):
    end_durations = []
    raw_durations = []
    complete = partial = fallback = 0
    search_used = 0
    phase_counts = Counter()
    coverage = []
    frontiers = []
    metrics = []
    reasons = Counter()
    scopes = Counter()
    stage_b_entered = 0
    baotou_elapsed = []
    baotou_changed = 0
    shape_changed = 0
    for end_ms, raw_ms, info in measurements:
        if info is None:
            reasons["end_to_end_exception"] += 1
            continue
        end_durations.append(float(end_ms))
        if raw_ms is not None:
            raw_durations.append(float(raw_ms))
        complete += int(bool(info.get("complete")))
        partial += int(bool(info.get("partial_accepted")))
        fallback += int(bool(info.get("fallback_reason") or
                             info.get("kernel_fallback_reason")))
        search_used += int(bool(info.get("search_used")))
        phase_counts[str(info.get("search_attempt_phase") or "none")] += 1
        scopes[str(info.get("decision_scope") or "legacy")] += 1
        stage_b_entered += int(bool(info.get("stage_b_entered")))
        shape_changed += int(bool(info.get("shape_changed_winner")))
        if info.get("decision_scope") == "baotou_scope":
            if info.get("baotou_elapsed_ms") is not None:
                baotou_elapsed.append(float(info["baotou_elapsed_ms"]))
            baotou_changed += int(bool(info.get("baotou_shape_used") and
                                        info.get("shape_changed_winner")))
        if info.get("coverage") is not None:
            coverage.append(float(info["coverage"]))
        if info.get("fallback_reason"):
            reasons[str(info["fallback_reason"])] += 1
        search = info.get("search_metrics") or {}
        frontiers.append(int(search.get("root_candidates") or 0))
        metrics.append({
            "shanten_calls": int(search.get("shanten_calls") or 0),
            "ukeire_calls": int(search.get("ukeire_calls") or 0),
            "child_nodes": int(search.get("child_nodes") or 0),
            "shanten_cache_hits": int(search.get("shanten_cache_hits") or 0),
            "workers": int(search.get("workers") or 0),
        })

    return {
        "profile": profile.as_json(),
        "end_to_end": _summary(
            end_durations, complete, partial, fallback, search_used,
            phase_counts, coverage, frontiers, metrics, reasons),
        "raw_native": {
            "states": len(raw_durations),
            "p50_ms": _quantile(raw_durations, 0.50),
            "p95_ms": _quantile(raw_durations, 0.95),
            "p99_ms": _quantile(raw_durations, 0.99),
            "max_ms": max(raw_durations) if raw_durations else None,
        },
        "decision_scope_counts": dict(scopes),
        "stage_b_entered": stage_b_entered,
        "shape_changed_winner": shape_changed,
        "baotou": {
            "count": scopes.get("baotou_scope", 0),
            "elapsed_ms_p50": statistics.median(baotou_elapsed)
            if baotou_elapsed else None,
            "elapsed_ms_p95": _quantile(baotou_elapsed, 0.95),
            "elapsed_ms_p99": _quantile(baotou_elapsed, 0.99),
            "elapsed_ms_max": max(baotou_elapsed)
            if baotou_elapsed else None,
            "shape_changed": baotou_changed,
        },
    }


def run(states, seed0, profile, evaluator=BASELINE_EVALUATOR):
    measurements = []
    errors = []
    for offset in range(states):
        try:
            measurements.append(_measure_one(seed0 + offset, evaluator,
                                             profile))
        except Exception as exc:
            errors.append({"seed": seed0 + offset,
                           "error": f"{type(exc).__name__}:{exc}"})
            measurements.append((None, None, None))
    result = _summary_for_measurements(measurements, profile)
    result.update({"evaluator": evaluator, "states_requested": states,
                   "seed_start": seed0, "errors": errors})
    return result


def run_interleaved(states=1000, repeats=3, seed0=20260926,
                    evaluators=(BASELINE_EVALUATOR,
                                "legacy-v2-shape-phase-a")):
    evaluators = tuple(dict.fromkeys(evaluators))
    if len(evaluators) < 2 or states <= 0 or repeats <= 0:
        raise ValueError("interleaved bench needs 2 evaluators and positive sizes")
    profiles = {}
    for evaluator in evaluators:
        shape_stage = SHAPE_EVALUATORS.get(evaluator)
        if evaluator in (BASELINE_EVALUATOR, SPEED_BAND_EVALUATOR):
            # Both labels run the current canonical legacyV2 route. The
            # candidate changes only speed-band/Pareto flags; shape, marginal,
            # and other current defaults remain identical to production.
            profiles[evaluator] = LegacyTwoPlyProfile.weighted_online(
                kernel="auto",
                speed_band_enabled=(evaluator == SPEED_BAND_EVALUATOR),
                pareto_frontier_enabled=(evaluator == SPEED_BAND_EVALUATOR),
            )
        else:
            profiles[evaluator] = LegacyTwoPlyProfile.weighted_online(
                kernel="auto", big_hand_enabled=False,
                big_hand_same_shanten_enabled=False,
                big_hand_plus_one_enabled=False,
                shape_quality_enabled=shape_stage is not None,
                shape_quality_stage=shape_stage or "diagnostic",
                shape_quality_guard_enabled=shape_stage is not None,
                marginal_structure_guard_enabled=False,
                speed_band_enabled=False,
                pareto_frontier_enabled=False,
            )
    samples = {name: [] for name in evaluators}
    errors = {name: [] for name in evaluators}
    per_repeat = {name: [] for name in evaluators}
    total = int(states) * int(repeats) * len(evaluators)
    completed = 0
    for repeat in range(int(repeats)):
        start_by_eval = {name: len(samples[name]) for name in evaluators}
        for offset in range(int(states)):
            order = list(evaluators)
            if (repeat + offset) % 2:
                order.reverse()
            for evaluator in order:
                try:
                    samples[evaluator].append(_measure_one(
                        int(seed0) + offset, evaluator,
                        profiles[evaluator]))
                except Exception as exc:
                    errors[evaluator].append({
                        "seed": int(seed0) + offset,
                        "error": f"{type(exc).__name__}:{exc}"})
                    samples[evaluator].append((None, None, None))
                completed += 1
                if completed % 100 == 0:
                    print(f"interleaved decisions {completed}/{total}",
                          file=sys.stderr, flush=True)
        for evaluator in evaluators:
            begin = start_by_eval[evaluator]
            per_repeat[evaluator].append(_summary_for_measurements(
                samples[evaluator][begin:], profiles[evaluator]))
    summaries = {}
    for evaluator in evaluators:
        summaries[evaluator] = _summary_for_measurements(
            samples[evaluator], profiles[evaluator])
        summaries[evaluator]["repeats"] = per_repeat[evaluator]
        summaries[evaluator]["errors"] = errors[evaluator]
    return {
        "mode": "interleaved_ordinary_discard",
        "states_per_evaluator_per_repeat": int(states),
        "repeats": int(repeats), "seed_start": int(seed0),
        "interleaved": True,
        "order": "alternate by repeat plus state index parity",
        "evaluators": summaries,
        "comparison": _ordinary_comparison(summaries, evaluators),
    }


def _ordinary_comparison(summaries, evaluators):
    baseline_name = BASELINE_EVALUATOR
    if baseline_name not in summaries:
        return {"baseline_evaluator": None}
    baseline = summaries[baseline_name]["end_to_end"]
    result = {"baseline_evaluator": baseline_name, "candidates": {}}
    for name in evaluators:
        if name == baseline_name:
            continue
        candidate = summaries[name]["end_to_end"]
        result["candidates"][name] = {
            "p95_regression_pct": (
                (candidate["p95_ms"] / baseline["p95_ms"] - 1) * 100
                if baseline["p95_ms"] else None),
            "p99_regression_pct": (
                (candidate["p99_ms"] / baseline["p99_ms"] - 1) * 100
                if baseline["p99_ms"] else None),
            "fallback_rate_delta_percentage_points": (
                100 * (candidate["fallback"] / max(1, candidate["states"]) -
                       baseline["fallback"] / max(1, baseline["states"]))),
            "p95_gate_pass": bool(candidate["p95_ms"] is not None and
                                  baseline["p95_ms"] is not None and
                                  candidate["p95_ms"] <= baseline["p95_ms"] * 1.10),
            "p99_gate_pass": bool(candidate["p99_ms"] is not None and
                                  baseline["p99_ms"] is not None and
                                  candidate["p99_ms"] <= baseline["p99_ms"] * 1.15),
        }
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--states", type=int, default=100)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--evaluator", choices=(
        BASELINE_EVALUATOR, SPEED_BAND_EVALUATOR, *SHAPE_EVALUATORS),
        default=BASELINE_EVALUATOR)
    parser.add_argument("--interleaved", action="store_true")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--evaluators", nargs="+", choices=(
        BASELINE_EVALUATOR, SPEED_BAND_EVALUATOR, *SHAPE_EVALUATORS),
        default=(BASELINE_EVALUATOR, SPEED_BAND_EVALUATOR))
    parser.add_argument("--output")
    parser.add_argument("--workers", type=int, default=0,
                        help=("Stage B worker count (0 = kernel default). "
                              "The end-to-end path is pinned through "
                              "MJ_KERNELS_THREADS because the bot builds its "
                              "own profile."))
    args = parser.parse_args()
    if args.workers > 0:
        os.environ["MJ_KERNELS_THREADS"] = str(args.workers)
    if args.interleaved:
        result = run_interleaved(
            states=args.states, repeats=args.repeats, seed0=args.seed,
            evaluators=tuple(args.evaluators))
    else:
        shape_stage = SHAPE_EVALUATORS.get(args.evaluator)
        if args.evaluator in (BASELINE_EVALUATOR, SPEED_BAND_EVALUATOR):
            profile = LegacyTwoPlyProfile.weighted_online(
                kernel="rust", workers=args.workers,
                speed_band_enabled=(args.evaluator == SPEED_BAND_EVALUATOR),
                pareto_frontier_enabled=(
                    args.evaluator == SPEED_BAND_EVALUATOR))
        else:
            profile = LegacyTwoPlyProfile.weighted_online(
                kernel="rust", workers=args.workers,
                big_hand_enabled=False,
                big_hand_same_shanten_enabled=False,
                big_hand_plus_one_enabled=False,
                shape_quality_enabled=shape_stage is not None,
                shape_quality_stage=shape_stage or "diagnostic",
                shape_quality_guard_enabled=shape_stage is not None,
                marginal_structure_guard_enabled=False,
                speed_band_enabled=False,
                pareto_frontier_enabled=False)
        result = run(args.states, args.seed, profile, args.evaluator)
    encoded = json.dumps(result, ensure_ascii=False, indent=2)
    print(encoded)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(encoded + "\n")
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(encoded + "\n")


if __name__ == "__main__":
    main()
