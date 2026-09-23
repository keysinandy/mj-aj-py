#!/usr/bin/env python3
"""Deterministic 4-bot throughput probe for legacy reaction v1/v2.

``v1`` is pinned to the frozen rollback alias, while ``v2-candidate`` uses the
online v2 profile. This keeps the A/B baseline stable as production aliases
route to v2.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import platform
import statistics
import sys
import time


SOURCE_ROOT = os.environ.get(
    "LEGACY_REACTION_SOURCE_ROOT",
    os.path.dirname(os.path.dirname(__file__)),
)
sys.path.insert(0, SOURCE_ROOT)

from mj import bot
from mj.game import Game
from mj.shanten import (
    DISCARD_FRONTIER_BATCH_KERNEL_VERSION,
    DISCARD_FRONTIER_KERNEL_VERSION,
)

try:
    from mj.legacy_react import LegacyReactionProfile
except ImportError:  # frozen v1 baseline predates the v2 module
    LegacyReactionProfile = None


def _percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    return ordered[min(len(ordered) - 1,
                       int((len(ordered) - 1) * fraction))]


def _summary(values):
    return {
        "n": len(values),
        "p50_ms": _percentile(values, 0.50),
        "p95_ms": _percentile(values, 0.95),
        "p99_ms": _percentile(values, 0.99),
        "mean_ms": statistics.fmean(values) if values else None,
        "max_ms": max(values) if values else None,
    }


def _choose_v2_candidate(game, seat, profile):
    acts = game.legal_actions()
    if len(acts) == 1:
        return acts[0], {"reason": "only_legal_action"}
    if game.phase == "discard":
        return bot._choose_draw_action(
            game, seat, acts, reaction_profile=profile)
    return bot._choose_react_evaluated(
        game, seat, acts, return_evaluation=True,
        reaction_profile=profile)


def run(games, seed_start, mode, source_label):
    if mode == "v2-candidate" and LegacyReactionProfile is None:
        raise RuntimeError("v2 candidate is unavailable in this source tree")
    profile = (LegacyReactionProfile.v2_online(enabled=True)
               if mode == "v2-candidate" else None)
    decisions = Counter()
    u2_elapsed = []
    u2_eligible = 0
    u2_covered = 0
    u2_fallbacks = Counter()
    kong_elapsed = []
    kong_fallbacks = Counter()
    started = time.perf_counter()
    for index in range(games):
        game = Game(seed=seed_start + index, dealer=index % 4,
                    you_cai_bi_kao=bool(index % 2))
        while not game.done:
            seat = game.current_seat()
            phase = game.phase
            if mode == "v1":
                result = bot.choose_action(
                    game, seat, evaluator="legacy-v1",
                    return_evaluation=True)
                action, evaluation = (result if isinstance(result, tuple)
                                      else (result, None))
            else:
                action, evaluation = _choose_v2_candidate(
                    game, seat, profile)
            if action not in game.legal_actions():
                raise AssertionError(f"illegal action {action} in {phase}")
            decisions[phase] += 1
            if isinstance(evaluation, dict):
                if evaluation.get("u2_eligible"):
                    u2_eligible += 1
                    if evaluation.get("u2_complete_or_safe_partial"):
                        u2_covered += 1
                    if evaluation.get("u2_extra_elapsed_ms") is not None:
                        u2_elapsed.append(evaluation["u2_extra_elapsed_ms"])
                    u2_fallbacks[
                        evaluation.get("u2_fallback_reason") or "none"] += 1
                kong = evaluation.get("kong") or evaluation.get(
                    "kong_evaluation") or {}
                elapsed = kong.get("continuation_elapsed_ms")
                if elapsed is not None:
                    kong_elapsed.append(elapsed)
                    kong_fallbacks[
                        kong.get("continuation_fallback_reason") or "none"
                    ] += 1
            game.step(action)
    elapsed = time.perf_counter() - started
    return {
        "schema": "legacy-reaction-performance-v1",
        "source_label": source_label,
        "source_root": str(Path(SOURCE_ROOT).resolve()),
        "mode": mode,
        "games": games,
        "seed_start": seed_start,
        "elapsed_s": elapsed,
        "elapsed_per_game_s": elapsed / games,
        "decisions": dict(decisions),
        "reaction_u2": {
            **_summary(u2_elapsed),
            "eligible_count": u2_eligible,
            "complete_or_safe_partial_count": u2_covered,
            "complete_or_safe_partial_coverage": (
                u2_covered / u2_eligible if u2_eligible else None),
            "fallback_reasons": dict(u2_fallbacks),
        },
        "kong_continuation": {
            **_summary(kong_elapsed),
            "fallback_reasons": dict(kong_fallbacks),
        },
        "runtime": {
            "python": sys.version,
            "platform": platform.platform(),
            "machine": platform.machine(),
            "kernel": DISCARD_FRONTIER_KERNEL_VERSION,
            "frontier_batch_kernel":
                DISCARD_FRONTIER_BATCH_KERNEL_VERSION,
        },
        "production_default_unchanged": True,
    }


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--games", type=int, default=200)
    parser.add_argument("--seed-start", type=int, default=190000)
    parser.add_argument("--mode", choices=("v1", "v2-candidate"),
                        required=True)
    parser.add_argument("--source-label", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    report = run(args.games, args.seed_start, args.mode, args.source_label)
    encoded = json.dumps(report, ensure_ascii=False, indent=2,
                         allow_nan=False)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(encoded + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in (
        "source_label", "mode", "games", "elapsed_s",
        "elapsed_per_game_s")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
