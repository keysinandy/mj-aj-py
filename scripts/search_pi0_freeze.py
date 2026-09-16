#!/usr/bin/env python3
"""Freeze pi0: strongest fast policy by reference regret, not evaluator name."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from mj.training.distillation_profile import (
    PolicyCandidate,
    SearchDistillationProfile,
    select_strongest_fast_policy,
)
from mj.training.regret_selection import (
    evaluate_heuristic,
    load_reference_rows,
    write_selection_report,
)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--candidates",
                        default="heuristic:legacy,heuristic:shape-v1,heuristic:shape-v2")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--max-latency-ms", type=float, default=36.0)
    parser.add_argument("--p95-limit", type=float, default=None)
    parser.add_argument("--benchmark-samples", type=int, default=64)
    parser.add_argument("--benchmark-repeats", type=int, default=2)
    parser.add_argument("--paired-report", type=Path, default=None,
                        help="paired matrix report; per-matrix CI used as tie-break")
    args = parser.parse_args(argv)

    rows = load_reference_rows(args.reference)
    if not rows:
        raise ValueError("reference context set is empty")
    profile = SearchDistillationProfile()
    paired = (json.loads(args.paired_report.read_text(encoding="utf-8"))
              if args.paired_report else {})
    evaluations, candidates = [], []
    for source in (item.strip() for item in args.candidates.split(",") if item.strip()):
        if not source.startswith("heuristic:"):
            raise ValueError(f"unsupported pi0 candidate source: {source!r}")
        evaluator = source.split(":", 1)[1]
        evaluation = evaluate_heuristic(
            rows, evaluator=evaluator, profile=profile,
            benchmark_samples=args.benchmark_samples,
            benchmark_repeats=args.benchmark_repeats)
        evaluations.append(evaluation)
        matrix = ((paired.get("matrices") or {}).get(evaluator)
                  or paired.get(source))
        ci = tuple(matrix["ci95"]) if matrix and matrix.get("ci95") else None
        candidates.append(PolicyCandidate(
            name=source,
            mean_reference_regret=evaluation.mean_reference_regret,
            p95_reference_regret=evaluation.p95_reference_regret,
            paired_score_ci=ci,
            batch1_latency_ms=(evaluation.latency or {}).get("p95_ms")))
        print(json.dumps({
            "candidate": source, "count": evaluation.count,
            "mean_reference_regret": evaluation.mean_reference_regret,
            "p95_reference_regret": evaluation.p95_reference_regret,
            "top1_action_agreement": evaluation.top1_action_agreement,
            "latency_p95_ms": (evaluation.latency or {}).get("p95_ms"),
            "paired_score_ci": ci, "skipped": evaluation.skipped,
        }, ensure_ascii=False))
    selection = select_strongest_fast_policy(
        candidates, max_latency_ms=args.max_latency_ms,
        max_p95_regret=args.p95_limit)
    report = write_selection_report(
        args.out, evaluations=evaluations, selection=selection)
    print(json.dumps({"selected": selection["selected"],
                      "reason": selection["reason"],
                      "report": str(args.out),
                      "report_fingerprint": report["fingerprint"]},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
