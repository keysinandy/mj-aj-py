#!/usr/bin/env python3
"""Render additive offline candidate/regret evidence from JSONL logs."""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from mj.decision.report import build_offline_report, render_report_summary


def load(path):
    with open(path, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="+")
    parser.add_argument("--output")
    parser.add_argument("--scope")
    args = parser.parse_args(argv)
    reports = []
    for path in args.paths:
        report = build_offline_report(load(path), scope=args.scope)
        report["source_path"] = path
        report["summary"] = render_report_summary(report)
        reports.append(report)
    value = {"schema": "bot-ev-discard/offline-report-batch-v1",
             "counterfactual": True, "online_decision": False,
             "reports": reports}
    encoded = json.dumps(value, ensure_ascii=False, indent=2)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(encoded + "\n")
    print(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
