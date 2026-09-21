"""Strict validation for policy-version-locked rollout merges."""

from __future__ import annotations

import json
import os
from typing import Mapping

from .artifact_store import verify_result
from .minisuphx_manifest import (
    ACTION_SCOPE_DISCARD,
    FEATURE_CONTRACT,
    VALUE_CONTRACT,
)

__all__ = ["load_result_manifests", "validate_merge"]


def load_result_manifests(result_root: str, campaign_id: str) -> list[dict]:
    """Load only committed manifests, retaining artifact errors for rejection."""
    base = os.path.join(result_root, campaign_id)
    if not os.path.isdir(base):
        return []
    results = []
    for name in sorted(os.listdir(base)):
        manifest_path = os.path.join(base, name, "manifest.json")
        if not os.path.exists(manifest_path):
            continue
        with open(manifest_path, "r", encoding="utf-8") as stream:
            manifest = json.load(stream)
        ok, missing = verify_result(manifest_path)
        if not ok:
            manifest["_artifact_error"] = (
                f"artifact missing or checksum mismatch: {missing}")
        results.append(manifest)
    return results


def validate_merge(manifests: list[Mapping], *,
                   expected_policy_fingerprint: str,
                   expected_policy_version: int | None = None,
                   expected_campaign_id: str | None = None,
                   expected_generation: int | None = None,
                   expected_value_contract: str = VALUE_CONTRACT,
                   expected_feature_contract: str = FEATURE_CONTRACT,
                   expected_git: str = "",
                   expected_action_scope: str = ACTION_SCOPE_DISCARD):
    """Validate complete provenance for one on-policy update.

    A missing field is an error.  The function never silently drops stale or
    malformed shards, so callers can fail the update and retry the job.
    """
    errors: list[str] = []
    if not manifests:
        return False, ["no rollout results to merge"]
    seen_jobs: set[str] = set()
    for index, manifest in enumerate(manifests):
        prefix = f"manifest[{index}]"
        if manifest.get("_artifact_error"):
            errors.append(f"{prefix} {manifest['_artifact_error']}")
        for field in ("campaign_id", "job_id", "worker_id", "git_commit",
                      "policy_version", "policy_fingerprint",
                      "value_contract", "feature_contract", "action_scope"):
            if manifest.get(field) in (None, ""):
                errors.append(f"{prefix} missing {field}")
        job_id = manifest.get("job_id")
        if job_id and job_id in seen_jobs:
            errors.append(f"{prefix} duplicate job_id {job_id!r}")
        if job_id:
            seen_jobs.add(str(job_id))

        if manifest.get("status") not in (None, "SUCCEEDED"):
            errors.append(f"{prefix} status {manifest.get('status')!r} is not SUCCEEDED")
        if manifest.get("policy_fingerprint") != expected_policy_fingerprint:
            errors.append(
                f"{prefix} stale policy fingerprint "
                f"{manifest.get('policy_fingerprint')!r} != "
                f"expected {expected_policy_fingerprint!r}")
        if expected_policy_version is not None and manifest.get("policy_version") != expected_policy_version:
            errors.append(
                f"{prefix} policy version {manifest.get('policy_version')!r} != "
                f"expected {expected_policy_version!r}")
        if expected_campaign_id is not None and manifest.get("campaign_id") != expected_campaign_id:
            errors.append(
                f"{prefix} campaign {manifest.get('campaign_id')!r} != "
                f"expected {expected_campaign_id!r}")
        if expected_generation is not None and manifest.get("generation") != expected_generation:
            errors.append(
                f"{prefix} generation {manifest.get('generation')!r} != "
                f"expected {expected_generation!r}")
        if manifest.get("value_contract") != expected_value_contract:
            errors.append(
                f"{prefix} value contract {manifest.get('value_contract')!r} != "
                f"{expected_value_contract!r}")
        if manifest.get("feature_contract") != expected_feature_contract:
            errors.append(
                f"{prefix} feature contract {manifest.get('feature_contract')!r} != "
                f"{expected_feature_contract!r}")
        if expected_git and manifest.get("git_commit") != expected_git:
            errors.append(
                f"{prefix} git commit {manifest.get('git_commit')!r} != "
                f"{expected_git!r}")
        if manifest.get("action_scope") != expected_action_scope:
            errors.append(
                f"{prefix} action scope {manifest.get('action_scope')!r} != "
                f"{expected_action_scope!r}")
        if manifest.get("artifact_relpath") and not manifest.get("artifact_sha256"):
            errors.append(f"{prefix} artifact_relpath has no checksum")
    return not errors, errors
