#!/usr/bin/env python3
"""Paired candidate-vs-baseline games with clustered confidence intervals.

The candidate and baseline see identical seed/seat/dealer/YCBK/opponent
schedules; only the hero policy changes.  Self-play, legacy/shape-v1 and
frozen-population matrices are reported separately.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from mj.training.distillation_profile import OpponentPopulationProfile
from mj.training.paired_eval import (
    OPPONENT_SPLITS,
    PairedSchedule,
    opponent_factory,
    paired_score_report,
    play_pair,
    policy_callable,
)


def _schedule(args):
    variants = {"off": (False,), "on": (True,), "both": (False, True)}[args.ycbk]
    return PairedSchedule(seed_start=args.seed_start, games=args.games,
                          ycbk_variants=variants)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", required=True,
                        help="checkpoint:<path> or heuristic:<evaluator>")
    parser.add_argument("--baseline", required=True,
                        help="checkpoint:<path>, heuristic:<evaluator> or "
                             "'shape-v2'/'shape-v1'")
    parser.add_argument("--opponents", choices=OPPONENT_SPLITS,
                        default="legacy_shape_v1")
    parser.add_argument("--matrices", default=None,
                        help="comma list of opponent splits (default: --opponents)")
    parser.add_argument("--generation", type=int, default=0)
    parser.add_argument("--population", default="legacy=1,shape-v1=1,shape-v2=1")
    parser.add_argument("--seed-start", type=int, default=240000)
    parser.add_argument("--games", type=int, default=2048,
                        help="scheduled games; pairs = games * ycbk variants")
    parser.add_argument("--ycbk", choices=("off", "on", "both"), default="both")
    parser.add_argument("--required-pairs", type=int, default=4096)
    parser.add_argument("--bootstrap-rounds", type=int, default=2000)
    parser.add_argument("--alpha", type=float, default=0.05)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    candidate = policy_callable(args.candidate)
    baseline = policy_callable(args.baseline)
    members = tuple((item.split("=", 1)[0], float(item.split("=", 1)[1]))
                    if "=" in item else (item, 1.0)
                    for item in args.population.split(",") if item)
    population = OpponentPopulationProfile(members=members)
    matrices = ([item.strip() for item in args.matrices.split(",")]
                if args.matrices else [args.opponents])
    schedule = _schedule(args)
    report = {
        "schema": "search-bc-paired-matrix-report-v1",
        "candidate": args.candidate, "baseline": args.baseline,
        "generation": args.generation, "schedule": {
            "seed_start": args.seed_start, "games": args.games,
            "ycbk_variants": list(schedule.ycbk_variants),
            "pairs": schedule.pairs, "seat_dealer": "seat=i%4, dealer=(i//4)%4",
        },
        "matrices": {}, "oracle": False,
    }
    for matrix in matrices:
        factory = opponent_factory(
            matrix, candidate=candidate, population=population,
            generation=args.generation)
        rows = []
        for row in schedule.rows():
            opponents = factory(row["cluster"])
            rows.append(play_pair(row, candidate=candidate, baseline=baseline,
                                  opponents=opponents))
        matrix_report = paired_score_report(
            rows, required_pairs=args.required_pairs,
            rounds=args.bootstrap_rounds, seed=args.seed_start,
            alpha=args.alpha, matrix=matrix)
        report["matrices"][matrix] = matrix_report
        print(json.dumps({key: matrix_report[key] for key in (
            "matrix", "pairs", "mean_delta", "ci95", "verdict")},
            ensure_ascii=False))
    from mj.decision.profile import fingerprint
    report["fingerprint"] = fingerprint(report, 24)
    args.out.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
