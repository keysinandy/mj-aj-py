#!/usr/bin/env python3
"""Fit the offline shape-v2 global/LUT calibration artifact.

Input is JSONL with ``features`` (the named FEATURE_NAMES), ``target`` and a
source-group id.  The command never reads game hidden state and does not alter
the runtime profile unless its caller explicitly consumes the output.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from mj.decision.calibration import (
    FEATURE_NAMES, ablation_report, fit_global_linear, fit_sparse_lut,
    freeze_split_manifest,
)
from mj.decision.profile import ProfileSpec, fingerprint


def load_rows(path):
    rows = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    if not rows:
        raise ValueError("calibration input is empty")
    for row in rows:
        if not isinstance(row.get("features"), dict):
            raise ValueError("each row needs named features")
        if row.get("source_group") is None:
            raise ValueError("each row needs source_group")
        missing = [name for name in FEATURE_NAMES
                   if name not in row["features"]]
        if missing:
            raise ValueError("feature fields missing: " + ",".join(missing))
    return rows


def run(input_path, output_path=None, min_bucket_samples=64,
        shanten_upper=None):
    rows = load_rows(input_path)
    targets = [float(row["target"]) for row in rows]
    constraints = ({"shanten": (None, float(shanten_upper))}
                   if shanten_upper is not None else None)
    global_model = fit_global_linear(
        rows, targets, feature_names=FEATURE_NAMES, constraints=constraints)
    lut = fit_sparse_lut(
        rows, targets, feature_names=FEATURE_NAMES, constraints=constraints,
        min_bucket_samples=min_bucket_samples)
    artifact = {
        "schema": "bot-ev-discard/calibration-artifact-v1",
        "input": os.path.abspath(input_path),
        "input_fingerprint": fingerprint(rows, 24),
        "split_manifest": freeze_split_manifest(),
        "profile": ProfileSpec.shape_v2_discard().as_json(),
        "feature_names": list(FEATURE_NAMES),
        "constraints": constraints or {},
        "global_model": global_model.as_json(),
        "sparse_lut": lut.as_json(),
        "ablations": ablation_report(rows, targets, constraints=constraints),
        "source_groups": len(set(row["source_group"] for row in rows)),
        "oracle": False,
        "teacher_only_offline": True,
    }
    artifact["fingerprint"] = fingerprint(artifact, 24)
    encoded = json.dumps(artifact, ensure_ascii=False, indent=2)
    if output_path:
        with open(output_path, "w", encoding="utf-8") as handle:
            handle.write(encoded + "\n")
    return artifact


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output")
    parser.add_argument("--min-bucket-samples", type=int, default=64)
    parser.add_argument("--shanten-upper", type=float)
    args = parser.parse_args(argv)
    value = run(args.input, args.output, args.min_bucket_samples,
                args.shanten_upper)
    print(json.dumps({key: value[key] for key in (
        "schema", "input_fingerprint", "source_groups", "fingerprint")},
        ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
