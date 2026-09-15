"""Business diffs, request classification, and evidence-bounded diagnostics."""

from __future__ import annotations

import copy
import json
from typing import Any, Iterable, Mapping

from .model import (
    Avoidability,
    Diagnostic,
    DiagnosticType,
    DiffResult,
    EvidenceStrength,
    EventType,
    LocalStep,
    LocalStepType,
    Request,
    RequestClassification,
    stable_id,
)
from .state import business_projection


def _is_knowledge(value: Any) -> bool:
    return isinstance(value, Mapping) and "status" in value and "evidence" in value


def _walk_diff(left: Any, right: Any, path: str, result: DiffResult):
    if _is_knowledge(left) or _is_knowledge(right):
        lstatus = left.get("status") if isinstance(left, Mapping) else "UNKNOWN"
        rstatus = right.get("status") if isinstance(right, Mapping) else "UNKNOWN"
        if lstatus != "KNOWN" or rstatus != "KNOWN":
            if lstatus == rstatus and lstatus in ("UNKNOWN", "HIDDEN", "NOT_APPLICABLE"):
                result.unknown_fields.append(path) if lstatus in ("UNKNOWN", "HIDDEN") else result.unavailable_fields.append(path)
                return
            result.unknown_fields.append(path)
            return
        result.compared_fields.append(path)
        lvalue = left.get("value")
        rvalue = right.get("value")
        if lvalue != rvalue:
            result.changes.append({"field": path, "before": copy.deepcopy(lvalue),
                                   "after": copy.deepcopy(rvalue)})
        return
    if isinstance(left, Mapping) or isinstance(right, Mapping):
        lmap = left if isinstance(left, Mapping) else {}
        rmap = right if isinstance(right, Mapping) else {}
        for key in sorted(set(lmap) | set(rmap), key=str):
            if key in ("rawRefs", "evidence", "source"):
                continue
            _walk_diff(lmap.get(key), rmap.get(key), f"{path}.{key}" if path else str(key), result)
        return
    if isinstance(left, list) or isinstance(right, list):
        result.compared_fields.append(path)
        if left != right:
            result.changes.append({"field": path, "before": copy.deepcopy(left),
                                   "after": copy.deepcopy(right)})
        return
    result.compared_fields.append(path)
    if left != right:
        result.changes.append({"field": path, "before": left, "after": right})


def compare_states(left: Any, right: Any, *, reason: str | None = None) -> DiffResult:
    """Compare only common business fields and explicitly report coverage."""
    left = business_projection(left)
    right = business_projection(right)
    result = DiffResult(reason=reason)
    if left is None or right is None:
        result.reason = reason or "one state is unavailable"
        result.unknown_fields.append("state")
        result.complete = False
        return result
    _walk_diff(left, right, "", result)
    result.compared_fields = sorted(set(x for x in result.compared_fields if x))
    result.unknown_fields = sorted(set(x for x in result.unknown_fields if x))
    result.unavailable_fields = sorted(set(x for x in result.unavailable_fields if x))
    result.complete = not result.unknown_fields and not result.unavailable_fields
    return result


def request_to_response_diff(state_before, response_state) -> DiffResult:
    return compare_states(state_before, response_state,
                          reason="response payload compared with request start")


def effective_merge_diff(state_before_merge, state_after_merge) -> DiffResult:
    return compare_states(state_before_merge, state_after_merge,
                          reason="actual pre-merge and post-merge states")


def expected_observed_diff(expected, observed) -> DiffResult:
    return compare_states(expected, observed,
                          reason="same local processing boundary")


def _has_new_information(request: Request) -> bool:
    response = request.response or {}
    return bool(response.get("events") or response.get("snapshot") or
                response.get("finished") or response.get("round_ended"))


def classify_request(request: Request, *, has_conflict=False,
                     local_transition_recovered=False,
                     reconnect_recovered=False,
                     missed_event_recovered=False,
                     validation_needed=False,
                     response_progress=False,
                     complete_comparison=False,
                     effective_change=False,
                     unknown_reason=False) -> Request:
    """Apply the deterministic business-necessity priority from the spec."""
    causes = []
    if local_transition_recovered:
        causes.append(RequestClassification.RECOVERY_CAUSED_BY_LOCAL_TRANSITION.value)
    if reconnect_recovered:
        causes.append(RequestClassification.RECOVERY_CAUSED_BY_RECONNECT.value)
    if missed_event_recovered:
        causes.append(RequestClassification.RECOVERY_CAUSED_BY_MISSED_EVENT.value)
    if has_conflict:
        request.classification = RequestClassification.SUSPICIOUS
        request.avoidability = Avoidability.UNKNOWN
        request.evidence = EvidenceStrength.UNKNOWN
        request.contributing_causes = causes
        return request
    if local_transition_recovered:
        request.classification = RequestClassification.RECOVERY_CAUSED_BY_LOCAL_TRANSITION
        request.avoidability = Avoidability.AVOIDABLE
    elif reconnect_recovered:
        request.classification = RequestClassification.RECOVERY_CAUSED_BY_RECONNECT
        request.avoidability = Avoidability.NECESSARY
    elif missed_event_recovered:
        request.classification = RequestClassification.RECOVERY_CAUSED_BY_MISSED_EVENT
        request.avoidability = Avoidability.UNKNOWN
    elif causes:
        request.classification = RequestClassification.RECOVERY_REQUIRED
        request.avoidability = Avoidability.UNKNOWN
    elif validation_needed:
        request.classification = RequestClassification.VALIDATION_ONLY
        request.avoidability = Avoidability.NECESSARY
    elif response_progress:
        request.classification = RequestClassification.PROGRESS_UPDATE
        request.avoidability = Avoidability.NECESSARY
    elif complete_comparison and not effective_change and not unknown_reason:
        request.classification = RequestClassification.REDUNDANT
        request.avoidability = Avoidability.AVOIDABLE
    else:
        request.classification = RequestClassification.UNCLASSIFIED
        request.avoidability = Avoidability.UNKNOWN
    request.contributing_causes = causes
    request.evidence = (EvidenceStrength.RECORDED if complete_comparison
                        else EvidenceStrength.UNKNOWN)
    return request


def _event_key(event):
    return (event.seq_no, str(event.type), event.seat, event.tile, tuple(event.tiles))


def detect_missing_transitions(events: Iterable[Any], steps: Iterable[LocalStep],
                               step_states: Mapping[str, Mapping[str, Any]],
                               *, game_id=None) -> list[Diagnostic]:
    """Only diagnose a missing claim at a completed trace boundary.

    A legal action, an SSE watermark, or an absent trace record is deliberately
    insufficient.  ``step_states`` is expected to contain expected/observed
    state snapshots produced by the compiler.
    """
    events = list(events)
    event_by_id = {getattr(e, "event_id", None): e for e in events}
    out: list[Diagnostic] = []
    claim_types = {EventType.CHI.value, EventType.PON.value,
                   EventType.KAN_OPEN.value, EventType.KAN_CLOSED.value,
                   EventType.KAN_ADDED.value}
    for step in steps:
        if step.type not in (LocalStepType.LOCAL_TRANSITION,
                             LocalStepType.PROCESSING_COMPLETE):
            continue
        payload = step.payload or {}
        outcome = str(step.outcome or payload.get("outcome") or "").upper()
        complete = bool(payload.get("processingComplete") or
                        payload.get("executionComplete") or
                        step.type == LocalStepType.PROCESSING_COMPLETE or
                        outcome in ("COMPLETED", "FAILED", "SKIPPED", "ERROR"))
        if not complete:
            continue
        event = event_by_id.get(step.related_event_id)
        event_type = str(getattr(event, "type", payload.get("eventType", ""))).upper()
        if event_type not in claim_types:
            continue
        states = step_states.get(step.step_id) or {}
        expected = states.get("expectedAfter") or step.state_after
        observed = states.get("observedAfter") or step.state_after
        if expected is None or observed is None:
            continue
        expected_melds = [m for p in expected.get("players", [])
                          for m in p.get("melds", [])
                          if m.get("type") == event_type and
                          (getattr(event, "seat", None) is None or p.get("seat") == event.seat)]
        observed_melds = [m for p in observed.get("players", [])
                          for m in p.get("melds", [])
                          if m.get("type") == event_type and
                          (getattr(event, "seat", None) is None or p.get("seat") == event.seat)]
        if not expected_melds or observed_melds:
            continue
        did = stable_id("diagnostic", "missing-transition", step.step_id, event_type)
        out.append(Diagnostic(
            diagnostic_id=did, type=DiagnosticType.MISSING_LOCAL_TRANSITION,
            severity="ERROR", round_no=step.round_no,
            seq_no=step.related_seq_no, local_step_index=step.index,
            local_ordinal=step.local_ordinal,
            message=f"{event_type} input completed without an observed local transition",
            status="PROCESSED_INCORRECTLY", expected=expected, actual=observed,
            affected_fields=[f"players[{getattr(event, 'seat', '?')}].melds"],
            probable_cause=f"LOCAL_{event_type}_NOT_APPLIED",
            caused_by_event_id=getattr(event, "event_id", None),
            caused_by_step_id=step.step_id,
            evidence=EvidenceStrength.RECORDED,
            raw_refs=list(step.raw_refs) + list(getattr(event, "raw_refs", [])),
            navigation_target={"localStepIndex": step.index,
                               "seqNo": step.related_seq_no}))
    return out


def timing_delta(start: Mapping[str, Any] | None, end: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not start or not end:
        return None
    a, b = start.get("timestamp"), end.get("timestamp")
    if not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
        return None
    domain_a, domain_b = start.get("clockDomain"), end.get("clockDomain")
    result = {"valueMs": round((b - a) * 1000, 3),
              "clockDomain": domain_a if domain_a == domain_b else "cross-clock",
              "precision": start.get("precision") or end.get("precision") or "recorded"}
    if domain_a != domain_b:
        result["exact"] = False
        result["label"] = "cross-clock observation delta"
    else:
        result["exact"] = True
    return result

