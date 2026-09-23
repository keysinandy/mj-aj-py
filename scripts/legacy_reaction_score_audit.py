#!/usr/bin/env python3
"""Offline paired total-score and latency audit for legacy reaction v1/v2.

The two strategies play the same four-seat source games independently.  The
report keeps the final four-seat settlement vector, score deltas, action-path
divergence, and both game-level and decision-level latency summaries.  The v1
arm is pinned to the frozen rollback alias; production aliases use online v2,
with per-window transactional fallback to v1 on incomplete evaluation.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from mj import bot
from mj.game import Game, KONG_OPEN, PONG
from mj.legacy_react import LegacyReactionProfile
from scripts.legacy_reaction_audit import _public_reaction_context


def _percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    index = min(len(ordered) - 1,
                int((len(ordered) - 1) * float(fraction)))
    return ordered[index]


def _timing_summary(values):
    values = [float(value) for value in values]
    return {
        "n": len(values),
        "mean_ms": statistics.fmean(values) if values else None,
        "p50_ms": _percentile(values, 0.50),
        "p90_ms": _percentile(values, 0.90),
        "p95_ms": _percentile(values, 0.95),
        "p99_ms": _percentile(values, 0.99),
        "max_ms": max(values) if values else None,
    }


def _distribution_summary(values):
    values = [float(value) for value in values]
    return {
        "n": len(values),
        "mean": statistics.fmean(values) if values else None,
        "p50": _percentile(values, 0.50),
        "p90": _percentile(values, 0.90),
        "p95": _percentile(values, 0.95),
        "max": max(values) if values else None,
    }


def _choose_v2_candidate(game, seat, profile):
    actions = game.legal_actions()
    if len(actions) == 1:
        return actions[0], {"reason": "only_legal_action"}
    if game.phase == "discard":
        return bot._choose_draw_action(
            game, seat, actions, reaction_profile=profile)
    return bot._choose_react_evaluated(
        game, seat, actions, return_evaluation=True,
        reaction_profile=profile)


def _kong_evaluations(evaluation):
    """Yield every public KONG candidate, including fallback details."""
    if not isinstance(evaluation, dict):
        return ()
    values = []
    for key in ("kong", "kong_evaluation"):
        value = evaluation.get(key)
        if isinstance(value, dict):
            values.append(value)
    candidates = evaluation.get("kong_candidates")
    if isinstance(candidates, (list, tuple)):
        values.extend(value for value in candidates
                      if isinstance(value, dict))
    # Keep the audit counters stable if a caller exposes the same candidate
    # through both the scoped and top-level compatibility fields.
    unique = []
    seen = set()
    for value in values:
        marker = id(value)
        if marker not in seen:
            seen.add(marker)
            unique.append(value)
    return tuple(unique)


def _play(seed, index, mode, profile, *, capture_pong_kong_flips=False):
    game = Game(seed=int(seed), dealer=int(index) % 4,
                you_cai_bi_kao=bool(int(index) % 2))
    action_history = []
    decision_elapsed = []
    decisions = Counter()
    u2_eligible = 0
    u2_covered = 0
    u2_fallbacks = Counter()
    kong_elapsed = []
    kong_fallbacks = Counter()
    same_unit_flips = []
    started = time.perf_counter()
    while not game.done:
        seat = game.current_seat()
        phase = game.phase
        decision_started = time.perf_counter()
        if mode == "v1":
            result = bot.choose_action(
                game, seat, evaluator="legacy-v1", return_evaluation=True)
            action, evaluation = (result if isinstance(result, tuple)
                                  else (result, None))
        else:
            action, evaluation = _choose_v2_candidate(
                game, seat, profile)
        decision_elapsed.append(
            (time.perf_counter() - decision_started) * 1000.0)
        legal = tuple(game.legal_actions())
        if action not in legal:
            raise RuntimeError(
                f"{mode} produced illegal action {action}: seed={seed} "
                f"index={index} seat={seat} phase={phase} legal={legal}")
        action_history.append(int(action))
        decisions[phase] += 1
        if isinstance(evaluation, dict):
            if evaluation.get("u2_eligible"):
                u2_eligible += 1
                if evaluation.get("u2_complete_or_safe_partial"):
                    u2_covered += 1
                u2_fallbacks[
                    evaluation.get("u2_fallback_reason") or "none"] += 1
            kong_values = _kong_evaluations(evaluation)
            for kong in kong_values:
                elapsed = kong.get("continuation_elapsed_ms")
                if elapsed is not None:
                    kong_elapsed.append(float(elapsed))
                    kong_fallbacks[
                        kong.get("continuation_fallback_reason") or "none"
                    ] += 1
            # Self-KONG online fallback deliberately returns the v1 detail
            # shape, so retain its top-level reason even when the candidate
            # timing is not exposed by the compatibility wrapper.
            fallback = evaluation.get("continuation_fallback_reason")
            candidate_fallbacks = {
                kong.get("continuation_fallback_reason")
                for kong in kong_values
                if kong.get("continuation_fallback_reason")
            }
            if fallback and fallback not in candidate_fallbacks:
                kong_fallbacks[str(fallback)] += 1
        if (capture_pong_kong_flips and mode != "v1"
                and phase == "react" and action in (PONG, KONG_OPEN)
                and PONG in legal and KONG_OPEN in legal
                and isinstance(evaluation, dict)):
            slow_path = evaluation.get("pong_kong_slow_path") or {}
            if (slow_path.get("entered")
                    and slow_path.get("q_pong") is not None
                    and slow_path.get("q_kong") is not None):
                v1_action, _v1_evaluation = bot._choose_react_evaluated(
                    game, seat, legal, return_evaluation=True)
                if (v1_action in (PONG, KONG_OPEN)
                        and v1_action != action):
                    same_unit_flips.append({
                        "seed": int(seed),
                        "index": int(index),
                        "seat": int(seat),
                        "v1_action": int(v1_action),
                        "v2_action": int(action),
                        "q_pong": slow_path["q_pong"],
                        "q_kong": slow_path["q_kong"],
                        "delta": slow_path.get("delta"),
                        "public_context": _public_reaction_context(
                            game, seat),
                    })
        game.step(action)
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    scores = [int(score) for score in game.scores]
    if sum(scores) != 0:
        raise AssertionError(f"score conservation failed: {scores}")
    winner = game.result[0] if game.result else None
    return {
        "scores": scores,
        "winner": winner,
        "draw": game.result is None,
        "actions": action_history,
        "decisions": dict(decisions),
        "elapsed_ms": elapsed_ms,
        "decision_elapsed_ms": decision_elapsed,
        "u2_eligible": u2_eligible,
        "u2_covered": u2_covered,
        "u2_fallbacks": dict(u2_fallbacks),
        "kong_elapsed_ms": kong_elapsed,
        "kong_fallbacks": dict(kong_fallbacks),
        "same_unit_flips": same_unit_flips,
    }


def _aggregate(values):
    totals = [0, 0, 0, 0]
    for scores in values:
        for seat, score in enumerate(scores):
            totals[seat] += int(score)
    count = len(values)
    return {
        "games": count,
        "total_by_seat": totals,
        "mean_per_game_by_seat": [
            (total / count if count else None) for total in totals],
        "conserved_total": sum(totals) == 0,
    }


def _counter_add(target, source):
    target.update(source)


def run(*, games=200, seed_start=190000, repeats=1, v2_first=False,
        capture_pong_kong_flips=False):
    if int(games) <= 0 or int(repeats) <= 0:
        raise ValueError("games and repeats must be positive")
    profile = LegacyReactionProfile.v2_online(enabled=True)
    rows = []
    v1_game_elapsed = []
    v2_game_elapsed = []
    v1_decision_elapsed = []
    v2_decision_elapsed = []
    v1_scores = []
    v2_scores = []
    score_deltas = []
    winners = {"v1": Counter(), "v2": Counter()}
    v2_u2_eligible = 0
    v2_u2_covered = 0
    v2_u2_fallbacks = Counter()
    v2_kong_elapsed = []
    v2_kong_fallbacks = Counter()
    same_unit_flips = []
    started = time.perf_counter()
    for repeat in range(int(repeats)):
        for offset in range(int(games)):
            index = repeat * int(games) + offset
            seed = int(seed_start) + index
            if v2_first:
                candidate = _play(
                    seed, index, "v2-candidate", profile,
                    capture_pong_kong_flips=capture_pong_kong_flips)
                baseline = _play(seed, index, "v1", None)
            else:
                baseline = _play(seed, index, "v1", None)
                candidate = _play(
                    seed, index, "v2-candidate", profile,
                    capture_pong_kong_flips=capture_pong_kong_flips)
            v1 = baseline["scores"]
            v2 = candidate["scores"]
            delta = [right - left for left, right in zip(v1, v2)]
            v1_scores.append(v1)
            v2_scores.append(v2)
            score_deltas.append(delta)
            v1_game_elapsed.append(baseline["elapsed_ms"])
            v2_game_elapsed.append(candidate["elapsed_ms"])
            v1_decision_elapsed.extend(baseline["decision_elapsed_ms"])
            v2_decision_elapsed.extend(candidate["decision_elapsed_ms"])
            winners["v1"][str(baseline["winner"])
                           if baseline["winner"] is not None else "draw"] += 1
            winners["v2"][str(candidate["winner"])
                           if candidate["winner"] is not None else "draw"] += 1
            v2_u2_eligible += candidate["u2_eligible"]
            v2_u2_covered += candidate["u2_covered"]
            _counter_add(v2_u2_fallbacks, candidate["u2_fallbacks"])
            v2_kong_elapsed.extend(candidate["kong_elapsed_ms"])
            _counter_add(v2_kong_fallbacks, candidate["kong_fallbacks"])
            same_unit_flips.extend(candidate["same_unit_flips"])
            actions_a = baseline["actions"]
            actions_b = candidate["actions"]
            first_diff = next(
                (pos for pos, pair in enumerate(zip(actions_a, actions_b))
                 if pair[0] != pair[1]), None)
            if first_diff is None and len(actions_a) != len(actions_b):
                first_diff = min(len(actions_a), len(actions_b))
            rows.append({
                "repeat": repeat,
                "index": index,
                "seed": seed,
                "dealer": index % 4,
                "you_cai_bi_kao": bool(index % 2),
                "v1_scores": v1,
                "v2_scores": v2,
                "score_delta_v2_minus_v1": delta,
                "v1_winner": baseline["winner"],
                "v2_winner": candidate["winner"],
                "v1_draw": baseline["draw"],
                "v2_draw": candidate["draw"],
                "v1_elapsed_ms": baseline["elapsed_ms"],
                "v2_elapsed_ms": candidate["elapsed_ms"],
                "v1_decision_count": len(actions_a),
                "v2_decision_count": len(actions_b),
                "first_action_diff_index": first_diff,
                "action_sequences_equal": actions_a == actions_b,
            })

    delta_flat = [score for row in score_deltas for score in row]
    changed_rows = [row for row in rows
                    if not row["action_sequences_equal"]]
    score_changed_rows = [row for row in rows
                          if any(row["score_delta_v2_minus_v1"])]
    return {
        "schema": "legacy-reaction-total-score-audit-v1",
        "games_requested_per_repeat": int(games),
        "repeats": int(repeats),
        "games_completed": len(rows),
        "seed_start": int(seed_start),
        "pair_order": "v2_first" if v2_first else "v1_first",
        "profile": profile.as_json(),
        "score_units": "Game.scores settlement points",
        "strategy_scope": "all_four_seats",
        "v1": {
            "aggregate": _aggregate(v1_scores),
            "winner_counts": dict(winners["v1"]),
            "game_elapsed_ms": _timing_summary(v1_game_elapsed),
            "decision_elapsed_ms": _timing_summary(v1_decision_elapsed),
        },
        "v2_candidate": {
            "aggregate": _aggregate(v2_scores),
            "winner_counts": dict(winners["v2"]),
            "game_elapsed_ms": _timing_summary(v2_game_elapsed),
            "decision_elapsed_ms": _timing_summary(v2_decision_elapsed),
            "u2": {
                "eligible": v2_u2_eligible,
                "complete_or_safe_partial": v2_u2_covered,
                "coverage": (v2_u2_covered / v2_u2_eligible
                             if v2_u2_eligible else None),
                "fallbacks": dict(v2_u2_fallbacks),
            },
            "kong_continuation": {
                **_timing_summary(v2_kong_elapsed),
                "fallbacks": dict(v2_kong_fallbacks),
            },
        },
        "paired_score_delta_v2_minus_v1": {
            "total_by_seat": [sum(row[seat] for row in score_deltas)
                               for seat in range(4)],
            "mean_per_game_by_seat": [
                (sum(row[seat] for row in score_deltas) / len(rows)
                 if rows else None) for seat in range(4)],
            "distribution_all_seat_points": _distribution_summary(delta_flat),
            "games_with_action_difference": len(changed_rows),
            "games_with_score_difference": len(score_changed_rows),
            "max_absolute_game_vector_sum": max(
                (sum(abs(value) for value in row)
                 for row in score_deltas), default=0),
            "score_conserved_per_game": all(sum(row) == 0
                                             for row in score_deltas),
        },
        "elapsed_s": time.perf_counter() - started,
        "offline_only": True,
        "production_default_unchanged": True,
        "regression_fixtures": {
            "pong_kong_same_unit_flip": same_unit_flips,
        },
        "rows": rows,
    }


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--games", type=int, default=200)
    parser.add_argument("--seed-start", type=int, default=190000)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--v2-first", action="store_true",
                        help="run v2 before v1 to expose cache/order effects")
    parser.add_argument(
        "--capture-pong-kong-flips", action="store_true",
        help="capture public-state fixtures for complete same-unit PONG/KONG flips")
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    report = run(games=args.games, seed_start=args.seed_start,
                 repeats=args.repeats, v2_first=args.v2_first,
                 capture_pong_kong_flips=args.capture_pong_kong_flips)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8")
    print(json.dumps({
        "games_completed": report["games_completed"],
        "v1_total_by_seat": report["v1"]["aggregate"]["total_by_seat"],
        "v2_total_by_seat": report["v2_candidate"]["aggregate"][
            "total_by_seat"],
        "score_delta": report["paired_score_delta_v2_minus_v1"],
        "v1_game_elapsed_ms": report["v1"]["game_elapsed_ms"],
        "v2_game_elapsed_ms": report["v2_candidate"]["game_elapsed_ms"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    raise SystemExit(main())
