"""Teacher artifact serialization with explicit provenance and no secrets."""

from __future__ import annotations

import json
from pathlib import Path

from ..decision.profile import canonical_json, fingerprint
from ..decision.report import sanitize_public


def teacher_artifact(result, *, context=None, profile=None, metadata=None):
    """Create a JSON-compatible, replayable teacher report."""
    value = sanitize_public(result.as_json())
    value["artifact_schema"] = "rollout-ev-teacher-v2"
    value["counterfactual"] = True
    value["counterfactual_evaluation"] = True
    value["online_decision"] = False
    value["oracle"] = False
    value["q_object"] = "Q^pi(s,a)|public_context,belief,continuation"
    value["finite_sample_estimate"] = True
    value["real_wall_optimal"] = False
    value["model_error_free"] = False
    value["bound_version"] = value.get("bound_version", "reward-envelope-v1")
    value["bound_mode"] = value.get("bound_mode", "unknown")
    value["pairwise_racing_version"] = value.get(
        "pairwise_racing_version", "paired-racing-v3")
    value["resume_schema"] = value.get(
        "resume_schema", "rollout-teacher-resume-v2")
    # Keep candidate bounds as a separate public section so a report reader
    # does not need to infer the statistical support from observed rewards.
    value["reward_bounds"] = sanitize_public(value.get("reward_bounds", {}))
    value["candidate_envelopes"] = sanitize_public(
        value.get("candidate_envelopes", ()))
    value["metadata"] = sanitize_public(dict(metadata or {}))
    if context is not None:
        value["context"] = sanitize_public(context.as_json())
    if profile is not None:
        value["profile"] = sanitize_public(profile.as_json())
        value["contract"] = {
            "profile_fingerprint": profile.fingerprint,
            "rule_version": profile.rules_version,
            "kernel_version": profile.kernel_version,
            "scope": profile.scope,
            "continuation_version": profile.continuation_version,
            "strategy": str((metadata or {}).get("strategy", profile.name)),
            "reward_units": profile.reward_units,
            "belief_version": profile.belief_version,
            "tail_version": profile.tail_version,
            "horizon": profile.horizon,
            "bound_version": value.get("bound_version"),
            "bound_mode": value.get("bound_mode"),
            "pairwise_racing_version": value.get("pairwise_racing_version"),
            "resume_schema": value.get("resume_schema"),
        }
    # A context contains public material only; rows contain world hashes and
    # rewards, not hidden hands or wall order.
    value["artifact_fingerprint"] = fingerprint({
        "result": value.get("fingerprint"),
        "context_hash": value.get("context_hash"),
        "profile": value.get("profile_fingerprint"),
        "rows": value.get("rows", ()),
        "stop_reason": value.get("stop_reason"),
        "elimination_history": value.get("elimination_history", ()),
        "pairwise_deltas": value.get("pairwise_deltas", ()),
        "bound_version": value.get("bound_version"),
        "bound_mode": value.get("bound_mode"),
        "pairwise_racing_version": value.get("pairwise_racing_version"),
        "resume_schema": value.get("resume_schema"),
        "reward_bounds": value.get("reward_bounds", {}),
        "candidate_envelopes": value.get("candidate_envelopes", ()),
    }, 24)
    return value


def write_teacher_artifact(path, result, *, context=None, profile=None,
                           metadata=None):
    """Write one explicit artifact path; callers own overwrite policy."""
    value = teacher_artifact(result, context=context, profile=profile,
                             metadata=metadata)
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8")
    return value
