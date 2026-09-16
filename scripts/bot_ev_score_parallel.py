#!/usr/bin/env python3
"""Run a frozen paired score study in independent source-seed shards.

Each worker invokes the existing score runner for a disjoint range of the
same frozen source schedule.  The parent process merges the rows and recomputes
all confidence intervals and the release gate over the complete source-group
set, so sharding changes wall time only, not the statistical unit.
"""

from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
import json
import os
from pathlib import Path
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from mj.decision.calibration import (
    cluster_bootstrap_simultaneous, evidence_contract, release_gate,
)
from mj.decision.profile import ProfileSpec, profile_from_json
from scripts.bot_ev_score_eval import (
    _merge_eval_stats, _score_interval, run as run_shard,
)


METRICS = (
    "legacy_score", "candidate_score", "score_delta",
    "legacy_win_rate", "candidate_win_rate", "legacy_draw_rate",
    "candidate_draw_rate", "legacy_realized_multiplier",
    "candidate_realized_multiplier",
)


def _worker(payload):
    """Run one shard; the payload is pickle-safe for process workers."""
    (games, seed_start, ycbk, evaluator, profile_payload, required_pairs,
     index_start, shard_id) = payload
    profile = ProfileSpec(**profile_payload)
    result = run_shard(
        games=games, seed_start=seed_start, ycbk=ycbk,
        evaluator=evaluator, profile=profile,
        required_pairs=required_pairs, bootstrap_rounds=1,
        index_start=index_start,
    )
    result["shard_id"] = int(shard_id)
    result["shard_index_start"] = int(index_start)
    return result


def _winning_multiplier(strategy, valid):
    values = [float(row[strategy]["mult"])
              for row in valid if row[strategy]["mult"] is not None]
    return {"n": len(values),
            "mean": statistics.fmean(values) if values else None}


def _aggregate(shards, *, games, seed_start, ycbk, evaluator, profile,
               required_pairs, bootstrap_rounds, alpha, bootstrap_seed,
               started, worker_count, shard_games):
    rows = []
    errors = []
    for shard in shards:
        rows.extend(shard.get("rows", []))
        errors.extend(shard.get("errors", []))
    rows.sort(key=lambda row: int(row.get("index", 0)))
    errors.sort(key=lambda row: int(row.get("index", 0)))
    valid = [row for row in rows if row.get("score_delta") is not None]
    groups = [row["source_group"] for row in valid]
    delta = [float(row["score_delta"]) for row in valid]
    legacy_scores = [float(row["legacy"]["score"]) for row in valid]
    candidate_scores = [float(row["candidate"]["score"]) for row in valid]
    combinations = Counter((row["seat"], row["dealer"]) for row in rows)
    expected_per_combination = int(games) // 16 if int(games) % 16 == 0 else None
    balanced = (
        len(rows) == int(games) and len(combinations) == 16 and
        expected_per_combination is not None and
        set(combinations.values()) == {expected_per_combination}
    )
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
        "legacy_realized_multiplier": [
            float(row["legacy"]["mult"] or 0.0) for row in valid],
        "candidate_realized_multiplier": [
            float(row["candidate"]["mult"] or 0.0) for row in valid],
    }
    simultaneous = (cluster_bootstrap_simultaneous(
        metric_series, groups, rounds=bootstrap_rounds, seed=bootstrap_seed,
        alpha=alpha) if delta else {})
    gate = (release_gate(
        delta, groups, required_pairs=required_pairs, ci_lower=0.0,
        rounds=bootstrap_rounds, seed=bootstrap_seed, invalid=len(errors),
        alpha=alpha, simultaneous_comparisons=len(metric_series)) if delta else {
            "passed": False, "legacy_default": True, "pairs": 0,
            "required_pairs": int(required_pairs), "invalid": len(errors),
            "interval": {"n": 0, "clusters": 0, "mean": None,
                         "low": None, "high": None},
        })
    gate["passed"] = bool(gate["passed"] and balanced)
    gate["legacy_default"] = not gate["passed"]
    gate["balance"] = {
        "passed": balanced, "combinations": len(combinations),
        "expected_per_combination": expected_per_combination,
        "counts": {f"{seat}/{dealer}": count
                    for (seat, dealer), count in sorted(combinations.items())},
    }
    candidate_eval = _merge_eval_stats(
        [row["candidate"] for row in valid])

    outcomes = {
        "legacy": {
            "wins": sum(row["legacy"]["win"] for row in valid),
            "draws": sum(row["legacy"]["draw"] for row in valid),
            "win_rate": simultaneous.get("legacy_win_rate"),
            "draw_rate": simultaneous.get("legacy_draw_rate"),
            "realized_multiplier": simultaneous.get(
                "legacy_realized_multiplier"),
            "winning_multiplier": _winning_multiplier("legacy", valid),
        },
        "candidate": {
            "wins": sum(row["candidate"]["win"] for row in valid),
            "draws": sum(row["candidate"]["draw"] for row in valid),
            "win_rate": simultaneous.get("candidate_win_rate"),
            "draw_rate": simultaneous.get("candidate_draw_rate"),
            "realized_multiplier": simultaneous.get(
                "candidate_realized_multiplier"),
            "winning_multiplier": _winning_multiplier("candidate", valid),
        },
    }
    contract = (evidence_contract(profile, strategy=evaluator,
                                  scope=profile.scope)
                if evaluator == "shape-v2" else None)
    return {
        "schema": "bot-ev-discard/paired-score-evidence-v1",
        "runner": "parallel-sharded-v1",
        "evaluator": evaluator, "games_requested": int(games),
        "games_completed": len(rows), "seed_start": int(seed_start),
        "ycbk": bool(ycbk), "primary_metric": "hero_round_score_points",
        "higher_is_better": True,
        "profile": profile.as_json() if evaluator == "shape-v2" else None,
        "contract": contract,
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
            "games_requested": int(games), "games_completed": len(rows),
            "valid_pairs": len(valid), "invalid_games": len(errors),
            "pair_coverage": len(valid) / int(games) if games else None,
            "ordinary_discard_calls": candidate_eval[
                "ordinary_discard_calls"],
            "ev2_complete": candidate_eval["ev2_complete"],
            "ev2_coverage": candidate_eval["ev2_coverage"],
            "q0_fallback": candidate_eval["q0_fallback"],
        },
        "candidate_wins": outcomes["candidate"]["wins"],
        "legacy_wins": outcomes["legacy"]["wins"],
        "candidate_draws": outcomes["candidate"]["draws"],
        "legacy_draws": outcomes["legacy"]["draws"],
        "candidate_evaluation": candidate_eval,
        "errors": errors,
        "release_gate": gate,
        "rows": rows,
        "parallel": {
            "workers": int(worker_count), "shard_games": int(shard_games),
            "shards": len(shards), "bootstrap_rounds_per_shard": 1,
            "aggregate_bootstrap_rounds": int(bootstrap_rounds),
            "source_group_unit": "seed",
        },
        "elapsed_s": time.perf_counter() - started,
        "offline_only": True, "counterfactual_evaluation": True,
        "online_decision": False, "oracle": False,
        "default_strategy_unchanged": True,
    }


def run_parallel(*, games, seed_start, ycbk=False, evaluator="shape-v2",
                 profile=None, required_pairs=4096, bootstrap_rounds=2000,
                 alpha=0.05, bootstrap_seed=20260915, workers=8,
                 shard_games=256):
    if int(games) <= 0 or int(shard_games) <= 0:
        raise ValueError("games and shard_games must be positive")
    if int(games) % int(shard_games):
        raise ValueError("games must be divisible by shard_games")
    profile = profile or ProfileSpec.shape_v2_discard()
    started = time.perf_counter()
    payloads = []
    for shard_id, index_start in enumerate(range(0, int(games),
                                                 int(shard_games))):
        payloads.append((int(shard_games), int(seed_start), bool(ycbk),
                         evaluator, profile.payload(), int(required_pairs),
                         int(index_start), int(shard_id)))
    shards = []
    with ProcessPoolExecutor(max_workers=int(workers)) as pool:
        futures = [pool.submit(_worker, payload) for payload in payloads]
        for future in as_completed(futures):
            result = future.result()
            shards.append(result)
            print(json.dumps({
                "shard_id": result["shard_id"],
                "index_start": result["shard_index_start"],
                "games_completed": result["games_completed"],
                "errors": len(result.get("errors", [])),
            }, ensure_ascii=False), flush=True)
    return _aggregate(
        shards, games=games, seed_start=seed_start, ycbk=ycbk,
        evaluator=evaluator, profile=profile, required_pairs=required_pairs,
        bootstrap_rounds=bootstrap_rounds, alpha=alpha,
        bootstrap_seed=bootstrap_seed, started=started, worker_count=workers,
        shard_games=shard_games)


def _profile_from_path(path):
    if not path:
        return ProfileSpec.shape_v2_discard()
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(value.get("profile"), dict):
        value = value["profile"]
    return profile_from_json(value)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--games", type=int, default=4096)
    parser.add_argument("--seed-start", type=int, default=242048)
    parser.add_argument("--you-cai-bi-kao", action="store_true")
    parser.add_argument("--evaluator", choices=("legacy", "shape-v1",
                                                  "shape-v2"),
                        default="shape-v2")
    parser.add_argument("--profile-json")
    parser.add_argument("--required-pairs", type=int, default=4096)
    parser.add_argument("--bootstrap-rounds", type=int, default=2000)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--shard-games", type=int, default=256)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    profile = _profile_from_path(args.profile_json)
    value = run_parallel(
        games=args.games, seed_start=args.seed_start,
        ycbk=args.you_cai_bi_kao, evaluator=args.evaluator,
        profile=profile, required_pairs=args.required_pairs,
        bootstrap_rounds=args.bootstrap_rounds, workers=args.workers,
        shard_games=args.shard_games)
    encoded = json.dumps(value, ensure_ascii=False, indent=2)
    Path(args.output).write_text(encoded + "\n", encoding="utf-8")
    print(json.dumps({
        "schema": value["schema"], "games_completed": value["games_completed"],
        "release_gate": value["release_gate"],
        "output": args.output,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
