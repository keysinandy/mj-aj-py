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
    calibration_evidence_manifest, evidence_contract, freeze_split_manifest,
    split_calibration_artifact, validate_evidence_fingerprint,
)
from mj.decision.profile import ProfileSpec, fingerprint, profile_from_json


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
        shanten_upper=None, split_manifest=None, require_all_splits=False,
        profile=None, require_contract=False, manifest_output=None,
        strategy="shape-v2", strict_evidence=False):
    rows = load_rows(input_path)
    targets = [float(row["target"]) for row in rows]
    constraints = ({"shanten": (None, float(shanten_upper))}
                   if shanten_upper is not None else None)
    profile = profile or ProfileSpec.shape_v2_discard()
    has_split_metadata = any(
        row.get("split") is not None or row.get("seed") is not None or
        row.get("source_seed") is not None for row in rows)
    if has_split_metadata or split_manifest is not None:
        manifest = split_manifest or freeze_split_manifest(
            profile_fingerprint=profile.fingerprint,
            rule_version=profile.rules_version,
            kernel_version=profile.kernel_version,
            scope=profile.scope,
            continuation_version=profile.continuation_version,
            strategy=strategy)
        expected_contract = evidence_contract(
            profile, manifest=manifest, strategy=strategy,
            scope=profile.scope)
        contract = manifest.get("contract", {})
        contract_check = validate_evidence_fingerprint(
            contract, expected_contract, fields=(
                "profile_fingerprint", "rule_version", "kernel_version",
                "scope", "continuation_version", "strategy"))
        if require_contract and not contract_check["valid"]:
            raise ValueError("split manifest contract mismatch: " +
                             json.dumps(contract_check["mismatch"],
                                        ensure_ascii=False, sort_keys=True))
        artifact = split_calibration_artifact(
            rows, manifest, constraints=constraints,
            min_bucket_samples=min_bucket_samples,
            require_all_splits=require_all_splits)
        artifact["input"] = os.path.abspath(input_path)
        artifact["input_fingerprint"] = fingerprint(rows, 24)
        artifact["profile"] = profile.as_json()
        artifact["contract"] = expected_contract
        artifact["teacher_only_offline"] = True
        artifact["input_mode"] = "frozen_source_splits"
        artifact["fingerprint"] = fingerprint(artifact, 24)
        encoded = json.dumps(artifact, ensure_ascii=False, indent=2)
        if output_path:
            with open(output_path, "w", encoding="utf-8") as handle:
                handle.write(encoded + "\n")
        if manifest_output:
            evidence = calibration_evidence_manifest(
                profile, split_manifest=manifest, strategy=strategy,
                scope=profile.scope, calibration_artifact=artifact,
                strict=strict_evidence)
            with open(manifest_output, "w", encoding="utf-8") as handle:
                json.dump(evidence, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
        return artifact
    global_model = fit_global_linear(
        rows, targets, feature_names=FEATURE_NAMES, constraints=constraints)
    lut = fit_sparse_lut(
        rows, targets, feature_names=FEATURE_NAMES, constraints=constraints,
        min_bucket_samples=min_bucket_samples)
    artifact = {
        "schema": "bot-ev-discard/calibration-artifact-v1",
        "input": os.path.abspath(input_path),
        "input_fingerprint": fingerprint(rows, 24),
        "split_manifest": freeze_split_manifest(
            profile_fingerprint=profile.fingerprint,
            rule_version=profile.rules_version,
            kernel_version=profile.kernel_version,
            scope=profile.scope,
            continuation_version=profile.continuation_version,
            strategy=strategy),
        "profile": profile.as_json(),
        "feature_names": list(FEATURE_NAMES),
        "constraints": constraints or {},
        "global_model": global_model.as_json(),
        "sparse_lut": lut.as_json(),
        "ablations": ablation_report(rows, targets, constraints=constraints),
        "source_groups": len(set(row["source_group"] for row in rows)),
        "oracle": False,
        "teacher_only_offline": True,
        "input_mode": "unsplit_diagnostic",
    }
    artifact["contract"] = evidence_contract(
        profile, manifest=artifact["split_manifest"], strategy=strategy,
        scope=profile.scope)
    artifact["fingerprint"] = fingerprint(artifact, 24)
    encoded = json.dumps(artifact, ensure_ascii=False, indent=2)
    if output_path:
        with open(output_path, "w", encoding="utf-8") as handle:
            handle.write(encoded + "\n")
    if manifest_output:
        evidence = calibration_evidence_manifest(
            profile, split_manifest=artifact["split_manifest"],
            strategy=strategy, scope=profile.scope,
            calibration_artifact=artifact, strict=strict_evidence)
        with open(manifest_output, "w", encoding="utf-8") as handle:
            json.dump(evidence, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    return artifact


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output")
    parser.add_argument("--min-bucket-samples", type=int, default=64)
    parser.add_argument("--shanten-upper", type=float)
    parser.add_argument("--split-manifest",
                        help="JSON manifest with frozen train/validation/final_test ranges")
    parser.add_argument("--require-all-splits", action="store_true",
                        help="fail unless every frozen split has at least one row")
    parser.add_argument("--profile-json",
                        help="frozen ProfileSpec JSON, including fingerprint")
    parser.add_argument("--require-contract", action="store_true",
                        help="reject a split manifest without a matching contract")
    parser.add_argument("--manifest-output",
                        help="write a calibration evidence manifest")
    parser.add_argument("--strict-evidence", action="store_true",
                        help="require full contract and self-consistent split fingerprints")
    parser.add_argument("--strategy", default="shape-v2")
    args = parser.parse_args(argv)
    manifest = None
    if args.split_manifest:
        with open(args.split_manifest, encoding="utf-8") as handle:
            manifest = json.load(handle)
    profile = None
    if args.profile_json:
        with open(args.profile_json, encoding="utf-8") as handle:
            profile = profile_from_json(json.load(handle))
    value = run(args.input, args.output, args.min_bucket_samples,
                args.shanten_upper, manifest, args.require_all_splits,
                profile, args.require_contract, args.manifest_output,
                args.strategy, args.strict_evidence)
    print(json.dumps({key: value[key] for key in (
        "schema", "input_fingerprint", "source_groups", "fingerprint")},
        ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
