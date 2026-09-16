#!/usr/bin/env python3
"""Evaluate checkpoints against the frozen high-budget search reference."""

from __future__ import annotations

import argparse
from dataclasses import replace
import json
from pathlib import Path
import shutil

from mj.decision.policy_v3 import load_policy_value_model
from mj.training.distillation_profile import SearchDistillationProfile
from mj.training.regret_selection import (
    evaluate_checkpoint,
    load_reference_rows,
    select_best_checkpoint,
    write_selection_report,
)


def _previous_metrics(path):
    if not path:
        return {}
    report = json.loads(Path(path).read_text(encoding="utf-8"))
    selection = report.get("selection") or {}
    selected = selection.get("selected")
    for evaluation in report.get("evaluations", ()):
        if evaluation.get("path") == selected:
            return {"previous_mean": evaluation.get("mean_reference_regret"),
                    "previous_p95": evaluation.get("p95_reference_regret"),
                    "path": selected}
    return {}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, nargs="+", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--best-out", type=Path, default=None,
                        help="copied best-by-regret checkpoint path")
    parser.add_argument("--previous-report", type=Path)
    parser.add_argument("--p95-limit", type=float, default=None)
    parser.add_argument("--p95-regression-factor", type=float, default=1.25)
    parser.add_argument("--catastrophic-limit", type=float, default=0.02)
    parser.add_argument("--special-state-regression", type=float, default=0.0)
    parser.add_argument("--latency-p95-ms", type=float, default=None)
    parser.add_argument("--benchmark-samples", type=int, default=0)
    parser.add_argument("--benchmark-repeats", type=int, default=1)
    parser.add_argument("--value-weight", type=float, default=0.0)
    parser.add_argument("--catastrophic-threshold", type=float, default=None)
    args = parser.parse_args(argv)

    rows = load_reference_rows(args.reference)
    if not rows:
        raise ValueError("reference context set is empty")
    profile = SearchDistillationProfile(value_weight=args.value_weight)
    evaluations = []
    for path in args.checkpoint:
        model = load_policy_value_model(path)
        evaluation = evaluate_checkpoint(
            model, rows, profile=profile,
            catastrophe_threshold=args.catastrophic_threshold,
            benchmark_samples=args.benchmark_samples,
            benchmark_repeats=args.benchmark_repeats)
        evaluation = replace(evaluation, path=str(path))
        evaluations.append(evaluation)
        print(json.dumps({
            "path": str(path),
            "mean_reference_regret": evaluation.mean_reference_regret,
            "p95_reference_regret": evaluation.p95_reference_regret,
            "catastrophic_regret_rate": evaluation.catastrophic_regret_rate,
            "top1_action_agreement": evaluation.top1_action_agreement,
            "count": evaluation.count, "skipped": evaluation.skipped,
            "latency": evaluation.latency,
        }, ensure_ascii=False))
    previous = _previous_metrics(args.previous_report)
    selection = select_best_checkpoint(
        evaluations,
        previous_mean=previous.get("previous_mean"),
        previous_p95=previous.get("previous_p95"),
        p95_limit=args.p95_limit,
        catastrophic_limit=args.catastrophic_limit,
        p95_regression_factor=args.p95_regression_factor,
        latency_p95_ms=args.latency_p95_ms,
        special_state_regression=args.special_state_regression)
    report = write_selection_report(args.out, evaluations=evaluations,
                                    selection=selection)
    best_out = args.best_out or args.out.with_name("best-by-regret.pt")
    if selection["selected"]:
        shutil.copyfile(selection["selected"], best_out)
    summary = {
        "selected": selection["selected"], "promoted": selection["promoted"],
        "reason": selection["reason"], "best_out": str(best_out),
        "report": str(args.out), "report_fingerprint": report["fingerprint"],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
