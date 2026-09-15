"""Teacher artifact serialization with explicit provenance and no secrets."""

from __future__ import annotations

import json
from pathlib import Path

from ..decision.profile import canonical_json, fingerprint
from ..decision.report import sanitize_public


def teacher_artifact(result, *, context=None, profile=None, metadata=None):
    """Create a JSON-compatible, replayable teacher report."""
    value = sanitize_public(result.as_json())
    value["artifact_schema"] = "rollout-ev-teacher-v1"
    value["counterfactual"] = True
    value["online_decision"] = False
    value["q_object"] = "Q^pi(s,a)|public_context,belief,continuation"
    value["finite_sample_estimate"] = True
    value["real_wall_optimal"] = False
    value["model_error_free"] = False
    value["metadata"] = sanitize_public(dict(metadata or {}))
    if context is not None:
        value["context"] = sanitize_public(context.as_json())
    if profile is not None:
        value["profile"] = sanitize_public(profile.as_json())
    # A context contains public material only; rows contain world hashes and
    # rewards, not hidden hands or wall order.
    value["artifact_fingerprint"] = fingerprint({
        "result": value.get("fingerprint"),
        "context_hash": value.get("context_hash"),
        "profile": value.get("profile_fingerprint"),
        "rows": value.get("rows", ()),
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
