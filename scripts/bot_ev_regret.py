#!/usr/bin/env python3
"""Render clustered offline paired-Q/regret evidence from JSONL.

Each source group must contain either one ``q`` mapping or one row per
strategy.  This command is counterfactual/offline evidence only; it never
creates or modifies an online decision.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from mj.decision.calibration import (
    evidence_contract, fingerprint, paired_q_regret_report,
)
from mj.decision.profile import ProfileSpec, profile_from_json


def load(path):
    with open(path, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("input")
    parser.add_argument("--output")
    parser.add_argument("--baseline", default="legacy")
    parser.add_argument("--teacher", default="teacher")
    parser.add_argument("--strategies", default="legacy,shape-v1,shape-v2")
    parser.add_argument("--rounds", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--alpha", type=float, default=0.05)
    parser.add_argument("--require-independent-worlds", action="store_true",
                        help="reject source groups without explicit independent-world provenance")
    parser.add_argument("--split-manifest",
                        help="bind the report to a frozen source split manifest")
    parser.add_argument("--profile-json",
                        help="frozen profile JSON used by the Q evidence")
    args = parser.parse_args(argv)
    strategies = tuple(x.strip() for x in args.strategies.split(",") if x.strip())
    value = paired_q_regret_report(
        load(args.input), baseline=args.baseline, teacher=args.teacher,
        strategies=strategies, rounds=args.rounds, seed=args.seed,
        alpha=args.alpha,
        require_independent_worlds=args.require_independent_worlds)
    if args.split_manifest:
        with open(args.split_manifest, encoding="utf-8") as handle:
            manifest = json.load(handle)
        if args.profile_json:
            with open(args.profile_json, encoding="utf-8") as handle:
                profile_data = json.load(handle)
            if isinstance(profile_data.get("profile"), dict):
                profile_data = profile_data["profile"]
            profile = profile_from_json(profile_data)
        else:
            profile = ProfileSpec.shape_v2_discard()
        value["artifact_schema"] = (
            "bot-ev-discard/paired-q-regret-evidence-v1")
        value["profile"] = profile.as_json()
        value["contract"] = evidence_contract(
            profile, manifest=manifest, strategy="shape-v2",
            scope=profile.scope)
        value["offline_only"] = True
        value["oracle"] = False
        value["fingerprint"] = fingerprint(value, 24)
    encoded = json.dumps(value, ensure_ascii=False, indent=2)
    print(encoded)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(encoded + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
