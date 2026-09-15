"""Safe online explanations and offline decision/regret association.

This module only transforms already-produced values.  It never calls an
evaluator, submits an action, or reads a complete hidden-world object.
"""

from __future__ import annotations

from collections.abc import Mapping
import copy
import math


_DROP_KEYS = frozenset({
    "authorization", "token", "access_token", "bearer", "credential",
    "body", "request_body", "response_body", "url", "wall_order",
    "wall_sequence", "real_wall", "opponent_hand", "opponent_hands",
    "hidden_hands", "hidden_wall", "rng_state", "rng", "secret",
})


def sanitize_public(value, *, _key=""):
    """Copy JSON-like evaluation data while dropping hidden/secrets fields."""
    normalized = str(_key).strip().lower().replace("-", "_")
    if normalized in _DROP_KEYS:
        return None
    if isinstance(value, Mapping):
        result = {}
        for key, item in value.items():
            nk = str(key).strip().lower().replace("-", "_")
            # Do not blanket-drop ``hand``: hero-private input is useful in a
            # local report.  Opponent/hidden names above are always excluded.
            if nk in _DROP_KEYS:
                continue
            cleaned = sanitize_public(item, _key=nk)
            if cleaned is not None:
                result[str(key)] = cleaned
        return result
    if isinstance(value, (list, tuple, set, frozenset)):
        return [sanitize_public(item, _key=_key) for item in value]
    if hasattr(value, "as_json"):
        return sanitize_public(value.as_json(), _key=_key)
    if hasattr(value, "to_json"):
        return sanitize_public(value.to_json(), _key=_key)
    return copy.deepcopy(value)


def compact_evaluation(evaluation, *, limit=3, legacy_action=None):
    """Bound a live explanation without changing any computed values."""
    if evaluation is None:
        return None
    data = sanitize_public(evaluation)
    if not isinstance(data, dict):
        return data
    candidates = data.get("candidates")
    if not isinstance(candidates, list):
        return data
    data["candidate_count"] = len(candidates)
    selected = data.get("selected")
    if isinstance(selected, Mapping):
        selected = selected.get("tile", selected.get("action"))
    legacy = data.get("legacy_best", data.get("legacy_action", legacy_action))
    keep = []
    for index, item in enumerate(candidates):
        if not isinstance(item, Mapping):
            continue
        ident = item.get("tile", item.get("action"))
        if ((ident is not None and ident in (selected, legacy)) or
                item.get("selected")):
            keep.append(index)
    # ``limit`` applies to candidates other than the essential selected and
    # legacy entries.  If both entries are the same action, using a fixed
    # ``limit + 2`` total would accidentally retain one extra candidate.
    max_total = len(keep) + max(0, int(limit))
    for index in range(len(candidates)):
        if index not in keep and len(keep) < max_total:
            keep.append(index)
    if len(keep) < len(candidates):
        data["candidates_truncated"] = True
        data["candidates"] = [candidates[index] for index in keep]
    return data


def _identity(record, *, scope=None):
    evaluation = record.get("evaluation") or {}
    digest = record.get("digest") or {}
    return {
        "gid": record.get("gid"),
        "round_no": record.get("round_no", digest.get("round_no")),
        "seq": record.get("seq", record.get("source_seq")),
        "decision_id": record.get("id", record.get("decision_id",
                                                     record.get("decision"))),
        "input_hash": record.get("input_hash", evaluation.get("context_hash")),
        "scope": record.get("scope", evaluation.get("scope", scope)),
    }


def decision_key(record, *, scope=None):
    """Stable association key; action/tile values are intentionally absent."""
    ident = _identity(record, scope=scope)
    return tuple(ident[name] for name in
                 ("gid", "round_no", "seq", "decision_id", "input_hash", "scope"))


def _candidate_value(evaluation, action):
    if not isinstance(evaluation, Mapping):
        return None
    for item in evaluation.get("candidates") or ():
        if not isinstance(item, Mapping):
            continue
        ident = item.get("tile", item.get("action"))
        if ident == action:
            value = item.get("Q", item.get("fast_ev", item.get("EV")))
            if value is not None:
                try:
                    return float(value)
                except (TypeError, ValueError):
                    return None
    return None


def associate_counterfactual(decisions, counterfactuals, *, scope=None):
    """Join offline evaluations to actual records only on the full identity.

    An absent decision, missing teacher coverage, unknown identity, or missing
    valid Q leaves regret as ``None``.  The output keeps actual transport and
    offline counterfactual facts in separate fields.
    """
    actual = {}
    for record in decisions:
        key = decision_key(record, scope=scope)
        # Duplicate identities are unsafe to guess through; retain the first
        # and mark the association ambiguous below.
        if key in actual:
            actual[key] = None
        else:
            actual[key] = record
    output = []
    for offline in counterfactuals:
        key = decision_key(offline, scope=scope)
        decision = actual.get(key)
        teacher_eval = offline.get("evaluation")
        teacher_action = offline.get("action")
        teacher_q = _candidate_value(teacher_eval, teacher_action)
        if teacher_q is None and isinstance(teacher_eval, Mapping):
            selected = teacher_eval.get("selected_candidate") or {}
            if isinstance(selected, Mapping):
                teacher_q = selected.get("Q", selected.get("fast_ev"))
        actual_eval = decision.get("evaluation") if decision else None
        actual_action = decision.get("action") if decision else None
        actual_q = _candidate_value(actual_eval, actual_action)
        status = ((decision or {}).get("identity_status") or
                  (offline.get("identity_status")) or "unknown")
        submit = None
        if decision is not None:
            submit = {"decision_id": decision.get("id"),
                      "action": decision.get("action"),
                      "status": "decision_recorded"}
        regret = (float(teacher_q) - float(actual_q)
                  if decision is not None and status not in
                  ("identity_unknown", "unknown") and
                  teacher_q is not None and actual_q is not None else None)
        output.append({
            "counterfactual": True, "online_decision": False,
            "identity": _identity(offline, scope=scope),
            "actual": {
                "present": decision is not None,
                "action": actual_action,
                "identity_status": status,
                "submit": submit,
                "regret": regret,
            },
            "teacher": {"action": teacher_action, "Q": teacher_q,
                        "coverage": teacher_q is not None},
        })
    return output


def build_offline_report(records, *, teacher_by_key=None, scope=None):
    """Build a complete-candidate report without manufacturing live actions."""
    decisions = [r for r in records if r.get("type") == "decision"]
    # Decision ids are local to a gid/round.  The gid is the minimum identity
    # needed here because action records historically do not carry the full
    # input hash; never let a reused id from another game satisfy a submit.
    actions_by_decision = {}
    for record in records:
        if record.get("type") != "action" or record.get("decision") is None:
            continue
        key = (record.get("gid"), record.get("decision"))
        if key in actions_by_decision:
            actions_by_decision[key] = None
        else:
            actions_by_decision[key] = record
    rows = []
    for decision in decisions:
        evaluation = sanitize_public(decision.get("evaluation"))
        action = decision.get("action")
        submit = actions_by_decision.get((decision.get("gid"),
                                          decision.get("id")))
        row = {
            "identity": _identity(decision, scope=scope),
            "actual_action": action,
            "legal": list(decision.get("legal") or []),
            "evaluation": evaluation,
            "submitted": (None if submit is None else {
                "ok": bool(submit.get("ok")),
                "status": submit.get("status"),
                "code": submit.get("code"),
            }),
            "server_auto_discard": False,
            "strategy_pass": action in (-1, 34),
            "identity_status": decision.get("identity_status", "unknown"),
            "regret": None,
        }
        if teacher_by_key:
            teacher = teacher_by_key.get(decision_key(decision, scope=scope))
            if teacher is not None:
                t_action = teacher.get("action", teacher.get("best_action"))
                t_q = _candidate_value(teacher.get("evaluation", teacher), t_action)
                a_q = _candidate_value(evaluation, action)
                if t_q is not None and a_q is not None:
                    row["regret"] = float(t_q) - float(a_q)
                row["teacher_coverage"] = t_q is not None
        rows.append(row)
    # A timeout/auto-discard record without a strategy decision is kept as its
    # own fact.  It is never converted into an actual action row.
    for record in records:
        kind = str(record.get("type", ""))
        if kind in ("timeout", "server_auto_discard") or (
                kind == "action" and record.get("decision") is None and
                record.get("phase") in ("draw", "discard")):
            rows.append({"identity": _identity(record, scope=scope),
                         "actual_action": None, "legal": None,
                         "evaluation": None,
                         "submitted": {"ok": bool(record.get("ok")),
                                       "status": record.get("status")},
                         "server_auto_discard": True,
                         "strategy_pass": False,
                         "identity_status": record.get("identity_status",
                                                        "unknown"),
                         "regret": None})
    return {"schema": "bot-ev-discard/offline-report-v1",
            "counterfactual": True, "online_decision": False,
            "rows": rows, "candidate_reports": [
                row for row in rows if row.get("evaluation") is not None
            ]}


def render_report_summary(report):
    """Small stable summary for CLI/logview callers."""
    rows = report.get("rows") or ()
    return {"rows": len(rows),
            "decisions": sum(r.get("actual_action") is not None for r in rows),
            "server_auto_discard": sum(bool(r.get("server_auto_discard"))
                                      for r in rows),
            "regret_observed": sum(r.get("regret") is not None for r in rows),
            "identity_unknown": sum(r.get("identity_status") in
                                     (None, "unknown", "identity_unknown")
                                     for r in rows)}
