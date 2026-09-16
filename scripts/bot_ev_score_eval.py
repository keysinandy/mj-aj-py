#!/usr/bin/env python3
"""Run paired score evidence for a frozen bot evaluator profile.

The source game is rebuilt independently for legacy and the candidate
evaluator.  Only the hero's round settlement score is compared.  Evaluation
metadata is aggregated separately so a high fallback rate cannot masquerade
as a complete v2 result.  This is an offline evidence runner; it does not
change the production/default strategy.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from mj.bot import choose_action
from mj.decision.calibration import (
    cluster_bootstrap, cluster_bootstrap_simultaneous, evidence_contract,
    release_gate,
)
from mj.decision.fast_ev import choose_game_action
from mj.decision.profile import ProfileSpec, profile_from_json
from mj.game import Game


def _percentile(values, p):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * p))]


def _timing(values):
    return {
        "n": len(values),
        "mean_ms": statistics.fmean(values) if values else None,
        "p50_ms": _percentile(values, 0.50),
        "p95_ms": _percentile(values, 0.95),
        "p99_ms": _percentile(values, 0.99),
        "max_ms": max(values) if values else None,
    }


def _new_eval_stats():
    return {"calls": 0, "ordinary_discard_calls": 0,
            "ev2_complete": 0, "q0_fallback": 0,
            "levels": Counter(), "fallbacks": Counter(), "timing_ms": [],
            "ordinary_timing_ms": []}


def _note_evaluation(stats, evaluation, elapsed_ms):
    if not isinstance(evaluation, dict):
        return
    stats["calls"] += 1
    level = str(evaluation.get("level") or "unknown")
    fallback = evaluation.get("fallback_reason")
    delegated = evaluation.get("delegated_reason")
    stats["levels"][level] += 1
    ordinary = level.startswith("V2-") or str(fallback).startswith("q0_")
    stats["timing_ms"].append(float(elapsed_ms))
    if ordinary:
        stats["ordinary_discard_calls"] += 1
        stats["ordinary_timing_ms"].append(float(elapsed_ms))
    if evaluation.get("ev2_complete") or level == "V2-EV2":
        stats["ev2_complete"] += 1
    if level == "V2-Q0" or str(fallback).startswith("q0_"):
        stats["q0_fallback"] += 1
    reason = fallback or delegated
    if reason:
        stats["fallbacks"][str(reason)] += 1


def _finish_eval_stats(stats, *, include_samples=False):
    value = {
        "calls": stats["calls"],
        "ordinary_discard_calls": stats["ordinary_discard_calls"],
        "ev2_complete": stats["ev2_complete"],
        "q0_fallback": stats["q0_fallback"],
        "ev2_coverage": (
            stats["ev2_complete"] / stats["ordinary_discard_calls"]
            if stats["ordinary_discard_calls"] else None),
        "levels": dict(sorted(stats["levels"].items())),
        "fallbacks": dict(sorted(stats["fallbacks"].items())),
        "timing_ms": _timing(stats["timing_ms"]),
        "ordinary_timing_ms": _timing(stats["ordinary_timing_ms"]),
    }
    if include_samples:
        # These are internal aggregation inputs.  ``run`` removes them from
        # the final source rows after computing the exact cross-game timing
        # summary, so published artifacts do not grow with every decision.
        value["_timing_samples_ms"] = list(stats["timing_ms"])
        value["_ordinary_timing_samples_ms"] = list(
            stats["ordinary_timing_ms"])
    return value


def _play(seed, seat, dealer, ycbk, evaluator, *, profile=None):
    """Play one source game and aggregate only observable evaluator facts."""
    game = Game(seed=int(seed), dealer=int(dealer),
                you_cai_bi_kao=bool(ycbk))
    eval_stats = _new_eval_stats()
    while not game.done:
        current = game.current_seat()
        if current != seat or evaluator == "legacy":
            action = choose_action(game, current, evaluator="legacy")
        elif evaluator == "shape-v2":
            started = time.perf_counter()
            action, evaluation = choose_game_action(game, current, profile)
            encoded = json.dumps(evaluation, ensure_ascii=False,
                                 separators=(",", ":"), default=str)
            del encoded
            _note_evaluation(
                eval_stats, evaluation,
                (time.perf_counter() - started) * 1000.0)
        else:
            started = time.perf_counter()
            result = choose_action(game, current, evaluator=evaluator,
                                   return_evaluation=True)
            action, evaluation = result if isinstance(result, tuple) else (
                result, None)
            encoded = json.dumps(evaluation, ensure_ascii=False,
                                 separators=(",", ":"), default=str)
            del encoded
            _note_evaluation(
                eval_stats, evaluation,
                (time.perf_counter() - started) * 1000.0)
        legal = tuple(game.legal_actions())
        if action not in legal:
            raise RuntimeError(
                f"{evaluator} produced illegal action {action}: seed={seed} "
                f"seat={seat} dealer={dealer} current={current} "
                f"phase={game.phase} legal={legal}")
        game.step(action)
    winner = game.result[0] if game.result else None
    return {
        "score": float(game.scores[seat]),
        "win": bool(winner == seat),
        "mult": game.result[1] if winner == seat else None,
        "draw": game.result is None,
        "evaluation": _finish_eval_stats(eval_stats, include_samples=True),
    }


def _score_interval(values, groups, *, rounds, seed, alpha):
    if not values:
        return {"n": 0, "clusters": 0, "mean": None, "low": None,
                "high": None, "alpha": float(alpha),
                "method": "source-group-bootstrap"}
    return cluster_bootstrap(values, groups, rounds=rounds, seed=seed,
                             alpha=alpha)


def _merge_eval_stats(rows):
    result = _new_eval_stats()
    for row in rows:
        data = row.get("evaluation") or {}
        result["calls"] += int(data.get("calls", 0))
        result["ordinary_discard_calls"] += int(
            data.get("ordinary_discard_calls", 0))
        result["ev2_complete"] += int(data.get("ev2_complete", 0))
        result["q0_fallback"] += int(data.get("q0_fallback", 0))
        result["levels"].update(data.get("levels") or {})
        result["fallbacks"].update(data.get("fallbacks") or {})
        timing_samples = data.get("_timing_samples_ms")
        if timing_samples is not None:
            result["timing_ms"].extend(float(x) for x in timing_samples)
        else:
            # Backward-compatible input artifacts may contain only per-game
            # summaries.  Keep the old approximation explicit for those rows;
            # current _play always supplies raw samples.
            timing = data.get("timing_ms") or {}
            if timing.get("mean_ms") is not None:
                result["timing_ms"].extend(
                    [float(timing["mean_ms"])] * int(timing.get("n", 0)))
        ordinary_samples = data.get("_ordinary_timing_samples_ms")
        if ordinary_samples is not None:
            result["ordinary_timing_ms"].extend(
                float(x) for x in ordinary_samples)
        else:
            ordinary_timing = data.get("ordinary_timing_ms") or {}
            if ordinary_timing.get("mean_ms") is not None:
                result["ordinary_timing_ms"].extend(
                    [float(ordinary_timing["mean_ms"])] *
                    int(ordinary_timing.get("n", 0)))
    return _finish_eval_stats(result)


def run(games=16, seed_start=240000, ycbk=False, evaluator="shape-v2",
        *, profile=None, required_pairs=4096, bootstrap_rounds=2000,
        alpha=0.05, bootstrap_seed=20260915, index_start=0):
    """Run a paired source-seed study and return a JSON-safe artifact."""
    if int(games) <= 0:
        raise ValueError("games must be positive")
    if evaluator not in ("legacy", "shape-v1", "shape-v2"):
        raise ValueError(f"unsupported evaluator: {evaluator}")
    profile = profile or ProfileSpec.shape_v2_discard()
    if evaluator == "shape-v2" and profile.scope != "discard":
        raise ValueError("paired score evaluator requires discard scope")
    rows = []
    errors = []
    started = time.perf_counter()
    for offset in range(int(games)):
        # ``index_start`` is used by the sharded offline runner.  Keeping the
        # global index in the source row preserves the frozen 16-way
        # seat/dealer schedule when independent shards are merged.
        index = int(index_start) + offset
        seed = int(seed_start) + index
        seat = index % 4
        dealer = (index // 4) % 4
        try:
            legacy = _play(seed, seat, dealer, ycbk, "legacy")
            candidate = _play(seed, seat, dealer, ycbk, evaluator,
                              profile=profile)
            rows.append({
                "source_group": f"seed:{seed}", "seed": seed,
                "index": index, "seat": seat, "dealer": dealer,
                "legacy": legacy, "candidate": candidate,
                "score_delta": candidate["score"] - legacy["score"],
            })
        except Exception as exc:
            errors.append({"source_group": f"seed:{seed}", "seed": seed,
                           "index": index, "error": f"{type(exc).__name__}:{exc}"})
    valid = [row for row in rows if row.get("score_delta") is not None]
    groups = [row["source_group"] for row in valid]
    delta = [row["score_delta"] for row in valid]
    legacy_scores = [row["legacy"]["score"] for row in valid]
    candidate_scores = [row["candidate"]["score"] for row in valid]
    combinations = Counter((row["seat"], row["dealer"]) for row in rows)
    balanced = (len(rows) == int(games) and len(combinations) == 16 and
                len(set(combinations.values())) == 1)
    metric_series = {
        "legacy_score": legacy_scores,
        "candidate_score": candidate_scores,
        "score_delta": delta,
        "legacy_win_rate": [float(row["legacy"]["win"]) for row in valid],
        "candidate_win_rate": [float(row["candidate"]["win"])
                               for row in valid],
        "legacy_draw_rate": [float(row["legacy"]["draw"]) for row in valid],
        "candidate_draw_rate": [float(row["candidate"]["draw"])
                                for row in valid],
        # A non-winning game contributes zero to the realised multiplier.  It
        # is reported separately from the conditional mean over wins below.
        "legacy_realized_multiplier": [
            float(row["legacy"]["mult"] or 0.0) for row in valid],
        "candidate_realized_multiplier": [
            float(row["candidate"]["mult"] or 0.0) for row in valid],
    }
    simultaneous = (cluster_bootstrap_simultaneous(
        metric_series, groups, rounds=bootstrap_rounds, seed=bootstrap_seed,
        alpha=alpha) if delta else {})
    gate = release_gate(delta, groups, required_pairs=required_pairs,
                        ci_lower=0.0, rounds=bootstrap_rounds,
                        seed=bootstrap_seed, invalid=len(errors),
                        alpha=alpha,
                        simultaneous_comparisons=len(metric_series)) if delta else {
                            "passed": False, "legacy_default": True,
                            "pairs": 0, "required_pairs": int(required_pairs),
                            "invalid": len(errors), "interval": {
                                "n": 0, "clusters": 0, "mean": None,
                                "low": None, "high": None},
                        }
    gate["passed"] = bool(gate["passed"] and balanced)
    gate["legacy_default"] = not gate["passed"]
    gate["balance"] = {"passed": balanced, "combinations": len(combinations),
                        "counts": {f"{seat}/{dealer}": count
                                   for (seat, dealer), count
                                   in sorted(combinations.items())}}
    candidate_eval = _merge_eval_stats([row["candidate"] for row in valid])

    def _winning_multiplier(strategy):
        values = [float(row[strategy]["mult"])
                  for row in valid if row[strategy]["mult"] is not None]
        return {"n": len(values),
                "mean": statistics.fmean(values) if values else None}

    outcomes = {
        "legacy": {
            "wins": sum(row["legacy"]["win"] for row in valid),
            "draws": sum(row["legacy"]["draw"] for row in valid),
            "win_rate": simultaneous.get("legacy_win_rate"),
            "draw_rate": simultaneous.get("legacy_draw_rate"),
            "realized_multiplier": simultaneous.get(
                "legacy_realized_multiplier"),
            "winning_multiplier": _winning_multiplier("legacy"),
        },
        "candidate": {
            "wins": sum(row["candidate"]["win"] for row in valid),
            "draws": sum(row["candidate"]["draw"] for row in valid),
            "win_rate": simultaneous.get("candidate_win_rate"),
            "draw_rate": simultaneous.get("candidate_draw_rate"),
            "realized_multiplier": simultaneous.get(
                "candidate_realized_multiplier"),
            "winning_multiplier": _winning_multiplier("candidate"),
        },
    }
    # Raw per-decision timings are useful while aggregating but are not part
    # of the stable paired-game schema.  The exact aggregate above has already
    # consumed them.
    for row in rows:
        evaluation = row.get("candidate", {}).get("evaluation", {})
        evaluation.pop("_timing_samples_ms", None)
        evaluation.pop("_ordinary_timing_samples_ms", None)
    return {
        "schema": "bot-ev-discard/paired-score-evidence-v1",
        "evaluator": evaluator, "games_requested": int(games),
        "games_completed": len(rows), "seed_start": int(seed_start),
        "ycbk": bool(ycbk), "primary_metric": "hero_round_score_points",
        "higher_is_better": True,
        "profile": profile.as_json() if evaluator == "shape-v2" else None,
        "contract": (evidence_contract(profile, strategy=evaluator,
                                        scope=profile.scope)
                     if evaluator == "shape-v2" else None),
        "legacy_score": simultaneous.get("legacy_score") or _score_interval(
            legacy_scores, groups, rounds=bootstrap_rounds,
            seed=bootstrap_seed, alpha=alpha),
        "candidate_score": simultaneous.get("candidate_score") or _score_interval(
            candidate_scores, groups, rounds=bootstrap_rounds,
            seed=bootstrap_seed, alpha=alpha),
        "score_delta": simultaneous.get("score_delta") or _score_interval(
            delta, groups, rounds=bootstrap_rounds,
            seed=bootstrap_seed, alpha=alpha),
        "simultaneous_intervals": simultaneous,
        "outcomes": outcomes,
        "coverage": {
            "games_requested": int(games),
            "games_completed": len(rows),
            "valid_pairs": len(valid),
            "invalid_games": len(errors),
            "pair_coverage": len(valid) / int(games) if games else None,
            "ordinary_discard_calls": candidate_eval[
                "ordinary_discard_calls"],
            "ev2_complete": candidate_eval["ev2_complete"],
            "ev2_coverage": candidate_eval["ev2_coverage"],
            "q0_fallback": candidate_eval["q0_fallback"],
        },
        "candidate_wins": sum(row["candidate"]["win"] for row in valid),
        "legacy_wins": sum(row["legacy"]["win"] for row in valid),
        "candidate_draws": sum(row["candidate"]["draw"] for row in valid),
        "legacy_draws": sum(row["legacy"]["draw"] for row in valid),
        "candidate_evaluation": candidate_eval,
        "errors": errors,
        "release_gate": gate,
        "rows": rows,
        "elapsed_s": time.perf_counter() - started,
        "offline_only": True, "counterfactual_evaluation": True,
        "online_decision": False, "oracle": False,
        "default_strategy_unchanged": True,
    }


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--games", type=int, default=16)
    parser.add_argument("--seed-start", type=int, default=240000)
    parser.add_argument("--you-cai-bi-kao", action="store_true")
    parser.add_argument("--evaluator", choices=("legacy", "shape-v1", "shape-v2"),
                        default="shape-v2")
    parser.add_argument("--node-budget", type=int)
    parser.add_argument("--time-budget-ms", type=float)
    parser.add_argument("--full-ev2", action="store_true",
                        help="use a large offline budget so EV2 can finish; never an online profile")
    parser.add_argument("--profile-json")
    parser.add_argument("--required-pairs", type=int, default=4096)
    parser.add_argument("--bootstrap-rounds", type=int, default=2000)
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    profile = ProfileSpec.shape_v2_discard()
    if args.profile_json:
        with open(args.profile_json, encoding="utf-8") as handle:
            profile = profile_from_json(json.load(handle))
    changes = {}
    if args.full_ev2:
        changes.update({"node_budget": 10_000_000,
                        "time_budget_ms": 30_000.0})
    if args.node_budget is not None:
        changes["node_budget"] = args.node_budget
    if args.time_budget_ms is not None:
        changes["time_budget_ms"] = args.time_budget_ms
    if changes:
        profile = ProfileSpec(**{**profile.payload(), **changes})
    value = run(args.games, args.seed_start, args.you_cai_bi_kao,
                args.evaluator, profile=profile,
                required_pairs=args.required_pairs,
                bootstrap_rounds=args.bootstrap_rounds)
    encoded = json.dumps(value, ensure_ascii=False, indent=2)
    print(encoded)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(encoded + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
