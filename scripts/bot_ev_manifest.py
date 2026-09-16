#!/usr/bin/env python3
"""Assemble and validate the frozen bot-ev-discard evidence manifest.

The manifest is deliberately stricter than a file-presence index.  It binds
calibration, independent Q/regret, score evaluation, the frozen profile and
the current rules/kernel/source fingerprints.  Any contract or source drift
invalidates the affected evidence and leaves ``legacy_default`` true.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from mj.decision.calibration import (
    calibration_evidence_manifest, evidence_contract, fingerprint,
    validate_evidence_fingerprint,
)
from mj.decision.profile import ProfileSpec, profile_from_json
from mj.game import CHOW_LIMIT, DEAD_WALL


SOURCE_FILES = (
    "mj/bot.py", "mj/decision/calibration.py", "mj/decision/context.py",
    "mj/decision/fast_ev.py", "mj/decision/frontier.py",
    "mj/decision/profile.py", "mj/game.py", "mj/hand_eval.py",
    "mj/decision/score_value.py", "mj/rollout/belief.py",
    "mj/rollout/evaluator.py", "mj/rollout/teacher_data.py",
    "mj/rollout/simulator.py", "mj/scoring.py", "mj/shanten.py",
    "rust/src/lib.rs", "docs/ev.md",
)


def _load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_head(root):
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _git_diff_fingerprint(root):
    try:
        value = subprocess.check_output(
            ["git", "diff", "HEAD", "--binary"], cwd=root)
    except (OSError, subprocess.CalledProcessError):
        return None
    return hashlib.sha256(value).hexdigest()


def _profile_from_path(path):
    if not path:
        return ProfileSpec.shape_v2_discard()
    value = _load(path)
    if isinstance(value.get("profile"), dict):
        value = value["profile"]
    return profile_from_json(value)


def _artifact_contract(artifact, expected, *, allow_missing_manifest=False):
    actual = dict(artifact.get("contract", {}))
    if not actual:
        profile = artifact.get("profile")
        if isinstance(profile, dict):
            actual = {
                "profile_fingerprint": profile.get("fingerprint"),
                "rule_version": profile.get("rules_version"),
                "kernel_version": profile.get("kernel_version"),
                "scope": profile.get("scope"),
                "continuation_version": profile.get("continuation_version"),
                "strategy": artifact.get("strategy", "shape-v2"),
            }
    fields = ("profile_fingerprint", "rule_version", "kernel_version",
              "scope", "continuation_version", "strategy",
              "reward_units", "belief_version", "tail_version",
              "horizon", "bound_version", "bound_mode",
              "pairwise_racing_version", "resume_schema",
              "manifest_fingerprint")
    if allow_missing_manifest:
        fields = tuple(field for field in fields
                       if field != "manifest_fingerprint")
    return validate_evidence_fingerprint(actual, expected, fields=fields)


def _q_jsonl(path):
    rows = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def build(*, profile=None, split_manifest, calibration=None, q_records=None,
          q_report=None, score=None, root=None, strict=True):
    root = os.path.abspath(root or os.getcwd())
    profile = profile or ProfileSpec.shape_v2_discard()
    expected = evidence_contract(
        profile, manifest=split_manifest, strategy="shape-v2",
        scope=profile.scope)

    # The report functions use contract-shaped artifact objects.  Q records
    # are kept separate from the aggregate report so the source rows remain
    # independently auditable and the aggregate cannot hide missing groups.
    q_rows = list(q_records or [])
    q_payload = q_report or {}
    q_artifact = None
    if q_rows or q_payload:
        q_artifact = {
            "schema": "bot-ev-discard/independent-q-regret-evidence-v1",
            "contract": expected,
            "records": len(q_rows),
            "independent_world_records": sum(
                row.get("independent_worlds") is True for row in q_rows),
            "complete_records": sum(
                bool(row.get("q_meta", {}).get("action_status")) and
                all(status.get("complete") is True for status in
                    row.get("q_meta", {}).get("action_status", {}).values())
                for row in q_rows),
            "teacher_ambiguous_records": sum(
                row.get("teacher", {}).get("ambiguous") is True
                for row in q_rows),
            "report": q_payload,
            "fingerprint": fingerprint({
                "rows": q_rows, "report": q_payload, "contract": expected},
                24),
            "offline_only": True, "counterfactual_evaluation": True,
            "oracle": False,
        }

    # ``calibration_evidence_manifest`` supplies the common contract checks;
    # the q artifact is used as the teacher slot because its Q labels are
    # generated from the independent rollout worlds.
    common = calibration_evidence_manifest(
        profile, split_manifest=split_manifest, strategy="shape-v2",
        scope=profile.scope, calibration_artifact=calibration,
        teacher_artifact=q_artifact, score_evidence=score,
        strict=bool(strict), require_all=False)

    validation = {
        "present": bool(calibration and
                        (calibration.get("rows", {}).get("validation", 0)
                         if isinstance(calibration.get("rows"), dict) else 0)),
        "rows": ((calibration or {}).get("rows", {}).get("validation", 0)
                 if isinstance((calibration or {}).get("rows"), dict) else 0),
        "metrics": ((calibration or {}).get("metrics", {}).get("global", {})
                    .get("validation") if calibration else None),
    }
    final_test = {
        "present": bool(calibration and
                        (calibration.get("rows", {}).get("final_test", 0)
                         if isinstance(calibration.get("rows"), dict) else 0)),
        "rows": ((calibration or {}).get("rows", {}).get("final_test", 0)
                 if isinstance((calibration or {}).get("rows"), dict) else 0),
        "metrics": ((calibration or {}).get("metrics", {}).get("global", {})
                    .get("final_test") if calibration else None),
    }

    runtime = {
        "profile": profile.as_json(),
        "contract": expected,
        "rules": {"rule_version": profile.rules_version,
                  "dead_wall": DEAD_WALL, "chow_limit": CHOW_LIMIT,
                  "scope": profile.scope},
        "policy": {"production_default": "legacy",
                   "candidate": "shape-v2", "opponent": "legacy",
                   "online_decision": False},
        "repository": {"head": _git_head(root),
                        "worktree_diff_fingerprint": _git_diff_fingerprint(root)},
        "source_sha256": {
            path: _sha256(os.path.join(root, path))
            for path in SOURCE_FILES if os.path.isfile(os.path.join(root, path))
        },
        "offline_only": True,
        "oracle": False,
    }
    runtime["fingerprint"] = fingerprint(runtime, 24)
    drift = {}
    for name, artifact in (("calibration", calibration),
                           ("q_regret", q_artifact), ("score", score)):
        if artifact is None:
            drift[name] = {"present": False, "valid": False,
                           "reason": "missing"}
            continue
        check = _artifact_contract(
            artifact, expected, allow_missing_manifest=(name == "score"))
        drift[name] = {
            "present": True, "valid": bool(check["valid"]),
            "mismatch": check["mismatch"],
            "fingerprint": artifact.get(
                "fingerprint", artifact.get("artifact_fingerprint"))
            or fingerprint(artifact, 24),
        }

    if score is not None:
        final_spec = split_manifest.get("splits", {}).get("final_test", {})
        start = score.get("seed_start")
        games = score.get("games_requested", score.get("games"))
        schedule_valid = (start is not None and games is not None and
                          int(start) >= int(final_spec.get("seed_start", -1)) and
                          int(start) + int(games) <= (
                              int(final_spec.get("seed_start", -1)) +
                              int(final_spec.get("games", 0))))
        drift["score"]["frozen_final_test_schedule"] = {
            "valid": bool(schedule_valid),
            "seed_start": start, "games": games,
            "expected": final_spec,
        }
        drift["score"]["valid"] = bool(
            drift["score"]["valid"] and schedule_valid)

    score_gate = (score or {}).get("release_gate", {})
    q_report_independent = bool(
        q_payload.get("independent_worlds_verified")) if q_payload else False
    required_rows = (calibration is not None and validation["present"] and
                     final_test["present"])
    all_contracts = all(item.get("valid") for item in drift.values()
                        if item.get("present")) and bool(drift)
    release_gate = {
        "pairs": int(score_gate.get("pairs", 0) or 0),
        "required_pairs": int(score_gate.get("required_pairs", 4096) or 4096),
        "score_gate_passed": bool(score_gate.get("passed")),
        "balanced_16_seat_dealer": bool(
            score_gate.get("balance", {}).get("passed")),
        "independent_q_verified": q_report_independent,
        "calibration_splits_present": bool(required_rows),
        "legacy_default": True,
    }
    release_gate["eligible_for_default_switch"] = bool(
        release_gate["score_gate_passed"] and
        release_gate["balanced_16_seat_dealer"] and
        release_gate["independent_q_verified"] and
        release_gate["calibration_splits_present"] and all_contracts)

    value = {
        "schema": "bot-ev-discard/release-evidence-manifest-v1",
        "frozen_at": "2026-09-16",
        "profile": profile.as_json(),
        "contract": expected,
        "split_manifest": split_manifest,
        "calibration": common["artifacts"].get("calibration"),
        "validation": validation,
        "final_test": final_test,
        "teacher": common["artifacts"].get("teacher"),
        "q_regret": {"records": len(q_rows),
                     "complete_records": sum(
                         bool(row.get("q_meta", {}).get("action_status")) and
                         all(status.get("complete") is True for status in
                             row.get("q_meta", {}).get(
                                 "action_status", {}).values())
                         for row in q_rows),
                     "teacher_ambiguous_records": sum(
                         row.get("teacher", {}).get("ambiguous") is True
                         for row in q_rows),
                     "report_fingerprint": q_payload.get("fingerprint"),
                     "independent_worlds_verified": q_report_independent},
        "score_evidence": common["artifacts"].get("score_evidence"),
        "runtime": runtime,
        "drift": drift,
        "release_gate": release_gate,
        "common_contract_valid": common["contract_valid"],
        "invalidated": common["invalidated"],
        "legacy_default": True,
        "offline_only": True,
        "oracle": False,
    }
    value["fingerprint"] = fingerprint(value, 24)
    return value


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile-json")
    parser.add_argument("--split-manifest", required=True)
    parser.add_argument("--calibration")
    parser.add_argument("--q-records")
    parser.add_argument("--q-report")
    parser.add_argument("--score-evidence")
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    profile = _profile_from_path(args.profile_json)
    split_manifest = _load(args.split_manifest)
    calibration = _load(args.calibration) if args.calibration else None
    score = _load(args.score_evidence) if args.score_evidence else None
    q_records = _q_jsonl(args.q_records) if args.q_records else []
    q_report = _load(args.q_report) if args.q_report else None
    value = build(profile=profile, split_manifest=split_manifest,
                  calibration=calibration, q_records=q_records,
                  q_report=q_report, score=score)
    Path(args.output).write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    print(json.dumps({
        "schema": value["schema"],
        "fingerprint": value["fingerprint"],
        "common_contract_valid": value["common_contract_valid"],
        "release_gate": value["release_gate"],
        "legacy_default": value["legacy_default"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
