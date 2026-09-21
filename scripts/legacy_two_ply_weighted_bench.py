"""Benchmark the weighted online two-ply frontier.

The report intentionally separates the raw native call from the complete
``choose_action`` path, because Python root construction and explanation
mapping are part of online latency but not Rust kernel latency.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
import statistics
import time

from mj.bot import _discard_shape_cost, _feed_risk, choose_action
from mj.game import Game
from mj.legacy_eval import (
    LegacyRootCandidate,
    LegacyTwoPlyProfile,
    _limit_weighted_frontier,
    _native_legal_masks,
    _root_features,
)
from mj.shanten import shanten, weighted_two_ply_frontier


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
        candidates, locked, visible)
    frontier, diagnostics = _limit_weighted_frontier(
        frontier, diagnostics, profile.max_frontier_candidates)
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
        "p50_ms": _quantile(durations, 0.50),
        "p90_ms": _quantile(durations, 0.90),
        "p95_ms": _quantile(durations, 0.95),
        "p99_ms": _quantile(durations, 0.99),
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


def run(states, seed0, profile):
    end_durations = []
    raw_durations = []
    complete = partial = fallback = 0
    search_used = 0
    phase_counts = Counter()
    coverage = []
    frontiers = []
    metrics = []
    reasons = Counter()
    for offset in range(states):
        game = Game(seed=seed0 + offset)
        seat = game.current_seat()
        started = time.perf_counter()
        try:
            _action, info = choose_action(
                game, seat, evaluator="legacyV2",
                return_evaluation=True)
        except Exception as exc:  # benchmark should report malformed states
            reasons[type(exc).__name__] += 1
            continue
        end_durations.append((time.perf_counter() - started) * 1000.0)
        complete += int(bool(info.get("complete")))
        partial += int(bool(info.get("partial_accepted")))
        fallback += int(not info.get("complete") and
                        not info.get("partial_accepted"))
        search_used += int(bool(info.get("search_used")))
        phase_counts[str(info.get("search_attempt_phase") or "none")] += 1
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
        })

        try:
            locked, visible, frozen, frontier = _roots(game, seat, profile)
            masks = _native_legal_masks(frontier, visible, frozen)
            raw_started = time.perf_counter()
            weighted_two_ply_frontier(
                [list(root.hand) for root in frontier],
                [root.shanten for root in frontier], list(visible), masks,
                locked, frozen, profile.node_budget, profile.soft_budget_ms,
                profile.hard_budget_ms, profile.cache_capacity,
                profile.min_partial_coverage, False)
            raw_durations.append((time.perf_counter() - raw_started) * 1000.0)
        except Exception as exc:
            reasons[f"raw_{type(exc).__name__}"] += 1

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
        },
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--states", type=int, default=100)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()
    profile = LegacyTwoPlyProfile.weighted_online(kernel="rust")
    print(json.dumps(run(args.states, args.seed, profile),
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
