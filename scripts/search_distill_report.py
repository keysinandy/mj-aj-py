#!/usr/bin/env python3
"""Consolidate one reduced Gen0 run into a single markdown/JSON summary."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from mj.training.run_summary import build_summary, render_markdown


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, default="runs/search_bc/gen0")
    parser.add_argument("--dataset-manifest", type=Path,
                        default="data/distill/dataset0.manifest.json")
    parser.add_argument("--pi0-selection", type=Path,
                        default="runs/search_bc/pi0_selection.json")
    parser.add_argument("--pipeline-log", type=Path,
                        default="data/distill/pipeline_gen0.log")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--json-out", type=Path, default=None)
    args = parser.parse_args(argv)
    summary = build_summary(
        run_dir=args.run_dir, dataset_manifest=args.dataset_manifest,
        pi0_selection=args.pi0_selection, pipeline_log=args.pipeline_log)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render_markdown(summary), encoding="utf-8")
    if args.json_out:
        args.json_out.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2,
                       sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"summary": str(args.out),
                      "paired_matrices": list(summary["paired"]),
                      "offline_selected": summary["offline"]["selected"],
                      "dataset_rows": summary["dataset"]["rows"]},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
