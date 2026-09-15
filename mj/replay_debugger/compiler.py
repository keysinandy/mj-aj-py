"""Compilation pipeline from evidence bundle to deterministic ReplaySession."""

from __future__ import annotations

import copy
from collections import defaultdict
from typing import Any, Mapping

from .adapters import EvidenceBundle, normalize_event
from .diagnostics import (
    classify_request,
    compare_states,
    detect_missing_transitions,
    effective_merge_diff,
    expected_observed_diff,
    request_to_response_diff,
)
from .model import (
    Boundary,
    Checkpoint,
    Cursor,
    Diagnostic,
    DiagnosticType,
    EvidenceStrength,
    EventType,
    LocalStepType,
    ReplaySession,
    RequestClassification,
    SourceRole,
    stable_id,
)
from .state import ReferenceReducer, state_from_dict, validate_state
from .timeline import (
    TimelineIndex,
    add_snapshot_anchors,
    attach_steps,
    build_server_frames,
    deduplicate_events,
    establish_causality,
)


def _snapshot(reducer):
    """Serialize to a detached dict without cloning the reducer first."""
    return reducer.state.as_dict()


def _event_dict(event):
    return event.as_dict()


def _same_event(a, b):
    return (a.seq_no, str(a.type), a.seat, a.from_seat, a.tile,
            tuple(a.tiles)) == (b.seq_no, str(b.type), b.seat, b.from_seat,
                                b.tile, tuple(b.tiles))


def _event_key(event):
    return (event.seq_no, str(event.type), event.seat, event.tile,
            tuple(event.tiles))


def _state_for_payload(base, payload: Mapping[str, Any] | None, *, game_id=None):
    payload = payload or {}
    # Request summaries commonly contain only seq/status/counts.  They do not
    # constitute a full state response and must not trigger an expensive full
    # state clone (nor pretend to provide comparison coverage).
    has_state = (isinstance(payload.get("snapshot"), Mapping) or
                 any(k in payload for k in ("my_hand", "discards", "melds", "phase", "turn")) or
                 isinstance(payload.get("events"), list))
    if not has_state:
        return base
    if base is None:
        reducer = ReferenceReducer(game_id=game_id,
                                   source=SourceRole.STATE_RESPONSE,
                                   evidence=EvidenceStrength.RECORDED)
    else:
        state = state_from_dict(base)
        reducer = ReferenceReducer(game_id=game_id,
                                   source=SourceRole.STATE_RESPONSE,
                                   evidence=EvidenceStrength.RECORDED,
                                   state=state)
    snap = payload.get("snapshot") if isinstance(payload.get("snapshot"), Mapping) else None
    if snap is None and any(k in payload for k in ("my_hand", "discards", "melds", "phase", "turn")):
        snap = payload
    if snap is not None:
        reducer.apply_snapshot(snap, source=SourceRole.STATE_RESPONSE)
    for item in payload.get("events", []) if isinstance(payload.get("events"), list) else []:
        if isinstance(item, Mapping):
            event = normalize_event(item, source=SourceRole.STATE_RESPONSE,
                                    game_id=game_id)
            reducer.apply_event(event)
    return _snapshot(reducer)


def _restore_observed(state_dict, *, derived: bool = False):
    state = state_from_dict(state_dict)
    if state is not None and derived:
        # A legacy response can seed a useful compatibility projection, but
        # it did not record that the live mirror actually applied it.
        state.evidence = EvidenceStrength.DERIVED
    return state


def _apply_once(reducer, event, seen):
    if event.event_id in seen:
        return
    reducer.apply_event(event, copy_result=False)
    seen.add(event.event_id)


def _has_trace(bundle: EvidenceBundle) -> bool:
    return bool(bundle.trace_metadata) or any(
        s.type in (LocalStepType.LOCAL_TRANSITION,
                   LocalStepType.PROCESSING_COMPLETE,
                   LocalStepType.STATE_ATTEMPT) and
        s.evidence == EvidenceStrength.RECORDED for s in bundle.local_steps)


def compile_bundle(bundle: EvidenceBundle, *, round_no: int | None = None,
                   rule_profile: str = "hangzhou") -> ReplaySession:
    """Compile a bundle without reading or writing any external state."""
    semantic_events, conflicts = deduplicate_events(bundle.events)
    quality = list(bundle.quality)
    quality.extend(conflicts)
    establish_causality(semantic_events, bundle.local_steps)

    selected_round = round_no
    if selected_round is None:
        candidates = sorted({e.round_no for e in semantic_events if e.round_no is not None})
        selected_round = candidates[0] if candidates else None
    server_events = [e for e in semantic_events
                     if e.source == SourceRole.SERVER_TIMELINE and
                     e.game_id in (None, bundle.game_id) and
                     (selected_round is None or e.round_no in (None, selected_round))]
    frames, _, frame_issues = build_server_frames(
        server_events, game_id=bundle.game_id,
        start_hands=(bundle.server_start_hands_by_round
                     if bundle.server_start_hands_by_round
                     else bundle.server_start_hands))
    quality.extend(frame_issues)
    for frame in frames:
        for issue in validate_state(frame.server_after):
            issue = dict(issue)
            issue.setdefault("roundNo", frame.round_no)
            issue.setdefault("seqNo", frame.seq_no)
            quality.append(issue)
    snapshots = [s for s in bundle.snapshots
                 if selected_round is None or
                 (isinstance(s.get("snap"), Mapping) and
                  s["snap"].get("round_no",
                                  s["snap"].get("roundNo", selected_round))
                  == selected_round)]
    add_snapshot_anchors(frames, snapshots, round_no=selected_round)
    steps = [s for s in bundle.local_steps
             if selected_round is None or s.round_no in (None, selected_round)]
    attach_steps(frames, steps)

    # Local steps are kept in captured/file order.  This is essential for
    # delayed application that refers to an older server seq.
    for index, step in enumerate(steps):
        step.index = index
        if step.local_ordinal is None:
            step.local_ordinal = index
    establish_causality(semantic_events, steps)
    event_by_id = {e.event_id: e for e in semantic_events}
    event_by_key = defaultdict(list)
    for event in semantic_events:
        event_by_key[(event.seq_no, str(event.type), event.seat,
                      event.tile, tuple(event.tiles))].append(event)

    trace_present = _has_trace(bundle)
    expected = ReferenceReducer(game_id=bundle.game_id, round_no=selected_round,
                                source=SourceRole.STATE_RESPONSE,
                                evidence=EvidenceStrength.RECORDED)
    observed = ReferenceReducer(game_id=bundle.game_id, round_no=selected_round,
                                source=SourceRole.LOCAL_TRACE if trace_present else SourceRole.LOCAL_DERIVED,
                                evidence=EvidenceStrength.RECORDED if trace_present else EvidenceStrength.DERIVED)
    expected_seen: set[str] = set()
    observed_seen: set[str] = set()
    step_states: dict[str, dict[str, Any]] = {}
    previous_step_id = None
    for step in steps:
        step.state_before = _snapshot(expected)
        observed_before = _snapshot(observed)
        step_payload = step.payload or {}
        event = event_by_id.get(step.related_event_id)
        if event is None:
            event_id = step_payload.get("eventId", step_payload.get("relatedEventId"))
            event = event_by_id.get(event_id)
        if event is None:
            key = (step.related_seq_no, str(step_payload.get("eventType", "")).upper(),
                   step_payload.get("seat"), step_payload.get("tile"),
                   tuple(step_payload.get("tiles") or []))
            event = next(iter(event_by_key.get(key, [])), None)
        if step.type == LocalStepType.INPUT and event is not None:
            if trace_present:
                # Input receipt makes Expected eligible, but actual Observed
                # changes only at a recorded transition boundary.
                pass
            else:
                _apply_once(expected, event, expected_seen)
                _apply_once(observed, event, observed_seen)
        elif step.type in (LocalStepType.LOCAL_TRANSITION,
                           LocalStepType.PROCESSING_COMPLETE):
            if event is not None:
                _apply_once(expected, event, expected_seen)
            if step.state_after is not None:
                restored = state_from_dict(step.state_after)
                if restored is not None:
                    observed.state = restored
            elif event is not None and str(step.outcome or step_payload.get("outcome", "")).upper() in (
                        "COMPLETED", "SUCCESS", "FAILED", "ERROR"):
                _apply_once(observed, event, observed_seen)
        elif step.type == LocalStepType.STATE_RESPONSE:
            # A response is available input.  Expected may use it at this
            # boundary; Observed waits for STATE_MERGE when trace exists.
            next_state = _state_for_payload(_snapshot(expected),
                                             step_payload, game_id=bundle.game_id)
            if next_state != _snapshot(expected):
                expected.state = state_from_dict(next_state) or expected.state
            if not trace_present:
                observed.state = (_restore_observed(next_state, derived=not trace_present)
                                  or observed.state)
        elif step.type == LocalStepType.STATE_MERGE:
            if step.state_after is not None:
                restored = state_from_dict(step.state_after)
                if restored is not None:
                    observed.state = restored
            else:
                payload = step_payload
                next_state = _state_for_payload(_snapshot(observed),
                                                payload, game_id=bundle.game_id)
                if str(step.outcome or "").upper() not in ("FAILED", "ERROR"):
                    observed.state = (_restore_observed(
                        next_state, derived=not trace_present)
                        or observed.state)
                expected.state = state_from_dict(_state_for_payload(
                    _snapshot(expected), payload,
                    game_id=bundle.game_id)) or expected.state
        elif step.type == LocalStepType.RESET:
            # Reset creates an evidence boundary.  Do not erase earlier step
            # states; new records will provide the next supported anchor.
            expected_seen.clear()
            observed_seen.clear()
            expected = ReferenceReducer(game_id=bundle.game_id, round_no=selected_round,
                                        source=SourceRole.STATE_RESPONSE,
                                        evidence=EvidenceStrength.RECORDED)
            observed = ReferenceReducer(
                game_id=bundle.game_id, round_no=selected_round,
                source=SourceRole.LOCAL_TRACE if trace_present else SourceRole.LOCAL_DERIVED,
                evidence=EvidenceStrength.RECORDED if trace_present else EvidenceStrength.DERIVED)
        step.state_after = _snapshot(expected)
        observed_after = _snapshot(observed)
        step_states[step.step_id] = {
            "expectedBefore": step.state_before,
            "expectedAfter": step.state_after,
            "observedBefore": observed_before,
            "observedAfter": observed_after,
        }
        if previous_step_id and not step.causal_parents:
            # Only use file/capture order as a weak local edge.  It is never
            # used to reorder events or claim a causal game fact.
            step.causal_parents.append(previous_step_id)
        previous_step_id = step.step_id

    server_by_seq = {(f.round_no, f.seq_no): f for f in frames}
    states = {
        "server": {(f.round_no, f.seq_no): f.server_after for f in frames},
        "steps": step_states,
    }
    diagnostics: list[Diagnostic] = []
    quality_diagnostic_types = {
        "STATE_INVARIANT_ERROR": DiagnosticType.STATE_INVARIANT_ERROR,
    }
    for item in quality:
        did = stable_id("diagnostic", "quality", item.get("kind"),
                        item.get("message"), tuple(item.get("rawRefs", [])))
        diagnostics.append(Diagnostic(
            did, quality_diagnostic_types.get(item.get("kind"),
                                              DiagnosticType.UNKNOWN_DATA), severity="WARN",
            message=item.get("message", item.get("kind", "data quality issue")),
            evidence=EvidenceStrength.UNKNOWN,
            raw_refs=list(item.get("rawRefs", [])), details=copy.deepcopy(item)))

    # Confirmed missing transitions are possible only with actual transition
    # evidence.  Legacy logs therefore never produce this diagnostic.
    missing = detect_missing_transitions(semantic_events, steps, step_states,
                                        game_id=bundle.game_id)
    diagnostics.extend(missing)
    missing_by_event = {d.caused_by_event_id: d for d in missing}

    # Expected/Observed mismatches at recorded completion boundaries.
    for step in steps:
        state = step_states[step.step_id]
        if step.type not in (LocalStepType.LOCAL_TRANSITION,
                             LocalStepType.PROCESSING_COMPLETE,
                             LocalStepType.STATE_MERGE):
            continue
        diff = expected_observed_diff(state["expectedAfter"], state["observedAfter"])
        if diff.changes and diff.complete and not any(d.caused_by_step_id == step.step_id for d in missing):
            diagnostics.append(Diagnostic(
                stable_id("diagnostic", "expected-observed", step.step_id),
                DiagnosticType.LOCAL_EXPECTED_OBSERVED_MISMATCH,
                severity="ERROR", round_no=step.round_no,
                seq_no=step.related_seq_no, local_step_index=step.index,
                local_ordinal=step.local_ordinal,
                message="Expected and Observed differ at a completed local boundary",
                expected=state["expectedAfter"], actual=state["observedAfter"],
                affected_fields=[c["field"] for c in diff.changes],
                caused_by_step_id=step.step_id,
                evidence=EvidenceStrength.RECORDED,
                raw_refs=list(step.raw_refs),
                navigation_target={"localStepIndex": step.index,
                                   "seqNo": step.related_seq_no}))

    # First visible difference uses public server facts and local availability;
    # private sequence gaps alone are intentionally ignored.
    local_public = {_event_key(e) for e in semantic_events
                    if e.source != SourceRole.SERVER_TIMELINE and
                    e.type in (EventType.DISCARD, EventType.CHI, EventType.PON,
                               EventType.KAN_OPEN, EventType.KAN_CLOSED,
                               EventType.KAN_ADDED, EventType.WIN)}
    visible = []
    for event in server_events:
        if event.type not in (EventType.DISCARD, EventType.CHI, EventType.PON,
                              EventType.KAN_OPEN, EventType.KAN_CLOSED,
                              EventType.KAN_ADDED, EventType.WIN):
            continue
        if _event_key(event) not in local_public:
            visible.append(event)
    if visible:
        event = min(visible, key=lambda e: (e.round_no or 0, e.seq_no or 0, e.event_id))
        diagnostics.append(Diagnostic(
            stable_id("diagnostic", "first-visible", event.event_id),
            DiagnosticType.FIRST_DIVERGENCE, severity="INFO",
            round_no=event.round_no, seq_no=event.seq_no,
            message="server fact is ahead of locally available input",
            status="NOT_RECEIVED_YET", expected=event.as_dict(), actual=None,
            affected_fields=["local input coverage"],
            caused_by_event_id=event.event_id, evidence=EvidenceStrength.RECORDED,
            raw_refs=list(event.raw_refs),
            navigation_target={"seqNo": event.seq_no,
                               "roundNo": event.round_no}))

    # Build requests and their three separate diffs.
    steps_by_request = defaultdict(list)
    for step in steps:
        if step.request_id:
            steps_by_request[step.request_id].append(step)
    requests = []
    for request in bundle.requests:
        related = steps_by_request.get(request.request_id, [])
        if not related and request.logical_request_id:
            related = [s for s in steps if s.request_id == request.logical_request_id]
        req_step = next((s for s in related if s.type == LocalStepType.STATE_REQUEST), None)
        response_step = next((s for s in related if s.type == LocalStepType.STATE_RESPONSE), None)
        merge_step = next((s for s in related if s.type == LocalStepType.STATE_MERGE), None)
        if req_step:
            request.state_before = req_step.state_before
            request.round_no = request.round_no or req_step.round_no
        response_payload = response_step.payload if response_step else request.response
        has_response_state = (isinstance(response_payload, Mapping) and
                              (isinstance(response_payload.get("snapshot"), Mapping) or
                               isinstance(response_payload.get("events"), list) or
                               any(k in response_payload for k in
                                   ("my_hand", "discards", "melds", "phase", "turn"))))
        response_state = (_state_for_payload(request.state_before, response_payload,
                                              game_id=bundle.game_id)
                          if has_response_state else None)
        request.state_before_merge = (merge_step.state_before if merge_step and merge_step.state_before
                                      else response_step.state_before if response_step else request.state_before)
        request.state_after_merge = (merge_step.state_after if merge_step and merge_step.state_after
                                     else None)
        request.request_to_response_diff = request_to_response_diff(
            request.state_before, response_state)
        request.effective_merge_diff = effective_merge_diff(
            request.state_before_merge, request.state_after_merge)
        if merge_step:
            merge_states = step_states.get(merge_step.step_id, {})
            request.expected_observed_diff = expected_observed_diff(
                merge_states.get("expectedAfter"), merge_states.get("observedAfter"))
        local_recovered = False
        for diag in missing:
            if merge_step and (merge_step.index > (diag.local_step_index or -1)):
                if request.state_after_merge and diag.expected:
                    local_recovered = compare_states(request.state_after_merge,
                                                     diag.expected).equal
        reconnect = any(s.type in (LocalStepType.SSE_DISCONNECT,
                                   LocalStepType.SSE_RECONNECT)
                        for s in steps if req_step and s.index < req_step.index)
        validation = any("WINDOW" in str(x).upper() or "CONFIRM" in str(x).upper()
                         for x in request.reasons) or bool(request.response.get("pending"))
        progress = _has_new_payload(request.response) or bool(request.response.get("events"))
        classify_request(
            request,
            has_conflict=any(d.type == DiagnosticType.UNKNOWN_DATA and
                             d.details.get("kind") == "CONFLICTING_EVENT_PAYLOAD"
                             for d in diagnostics),
            local_transition_recovered=local_recovered,
            reconnect_recovered=reconnect and not local_recovered,
            missed_event_recovered=False,
            validation_needed=validation,
            response_progress=progress,
            complete_comparison=request.request_to_response_diff.complete,
            effective_change=bool(request.request_to_response_diff.changes or
                                  request.effective_merge_diff.changes),
            unknown_reason=not bool(request.reasons))
        requests.append(request)

    # A merge proven to repair a mismatch gets a linked recovery diagnostic.
    for request in requests:
        if request.classification == RequestClassification.RECOVERY_CAUSED_BY_LOCAL_TRANSITION:
            merge_step = next((s for s in steps if s.request_id == request.request_id and
                               s.type == LocalStepType.STATE_MERGE), None)
            linked = next((d for d in missing if merge_step and
                           (d.local_step_index or -1) < merge_step.index), None)
            if linked:
                linked.recovered = True
                linked.recovered_by_request_id = request.request_id
                request.contributing_causes.append(linked.diagnostic_id)
                diagnostics.append(Diagnostic(
                    stable_id("diagnostic", "recovery", request.request_id),
                    DiagnosticType.STATE_RECOVERY, severity="INFO",
                    round_no=request.round_no,
                    seq_no=request.response_seq,
                    message="state merge repaired a proved local mismatch",
                    status="RECOVERED_BY_STATE",
                    caused_by_diagnostic_id=linked.diagnostic_id,
                    recovered=True, recovered_by_request_id=request.request_id,
                    evidence=EvidenceStrength.RECORDED,
                    raw_refs=list(request.raw_refs),
                    navigation_target={"requestId": request.request_id}))

    # Attach diagnostics to frames and create deterministic checkpoints.
    by_frame = {(f.round_no, f.seq_no): f for f in frames}
    for diag in diagnostics:
        frame = by_frame.get((diag.round_no, diag.seq_no))
        if frame and diag.diagnostic_id not in frame.diagnostics:
            frame.diagnostics.append(diag.diagnostic_id)
    checkpoints = []
    for index, step in enumerate(steps):
        if index % 16 == 0 or step.type in (LocalStepType.RESET,
                                             LocalStepType.STATE_MERGE):
            st = step_states[step.step_id]
            checkpoints.append(Checkpoint(
                stable_id("checkpoint", bundle.game_id, selected_round,
                           step.local_ordinal, step.step_id),
                step.round_no, step.related_seq_no, step.index,
                step.local_ordinal, st["expectedAfter"], st["observedAfter"],
                (by_frame.get((step.round_no, step.related_seq_no)).server_after
                 if by_frame.get((step.round_no, step.related_seq_no)) else None),
                [d.diagnostic_id for d in diagnostics
                 if d.local_step_index is not None and d.local_step_index <= step.index],
                evidence=EvidenceStrength.RECORDED if trace_present else EvidenceStrength.DERIVED,
                raw_refs=list(step.raw_refs)))

    all_games = bundle.identities.get("game", set())
    session_id = stable_id("session", bundle.game_id,
                           tuple(sorted(bundle.input_hashes.items())),
                           rule_profile, selected_round)
    session = ReplaySession(
        session_id=session_id, game_id=bundle.game_id,
        trace_schema_version=bundle.trace_metadata.get("traceSchemaVersion"),
        rule_profile=rule_profile, input_hashes=dict(bundle.input_hashes),
        source_coverage=_coverage(bundle, semantic_events, steps, trace_present),
        raw_records=list(bundle.raw_records), normalized_events=semantic_events,
        frames=frames, local_steps=steps, states=states, requests=requests,
        checkpoints=checkpoints, diagnostics=diagnostics,
        quality=quality, metadata={"selectedRound": selected_round,
                                   "identityConflicts": len(all_games) > 1,
                                   "tracePresent": trace_present})
    visible_diagnostic = next((d for d in diagnostics
                               if d.type == DiagnosticType.FIRST_DIVERGENCE), None)
    confirmed_diagnostic = next((d for d in diagnostics
                                 if d.type in (DiagnosticType.MISSING_LOCAL_TRANSITION,
                                               DiagnosticType.LOCAL_EXPECTED_OBSERVED_MISMATCH,
                                               DiagnosticType.STATE_INVARIANT_ERROR)), None)
    session.first_visible_divergence = (visible_diagnostic.as_dict()
                                        if visible_diagnostic else None)
    session.first_confirmed_failure = (confirmed_diagnostic.as_dict()
                                      if confirmed_diagnostic else None)
    index = TimelineIndex(frames, steps, bundle.game_id)
    session.cursor = index.first(selected_round) or Cursor(bundle.game_id,
                                                           selected_round, None,
                                                           Boundary.AFTER)
    session.view["selectedPlayer"] = bundle.seat if bundle.seat is not None else 0
    # Local observed/expected snapshots are also useful for consumers that do
    # not want to navigate through the HTML runtime.
    session.states["expectedFinal"] = _snapshot(expected)
    session.states["observedFinal"] = _snapshot(observed)
    return session


def _has_new_payload(response):
    return isinstance(response, Mapping) and bool(
        response.get("events") or response.get("snapshot") or
        response.get("finished") or response.get("round_ended"))


def _coverage(bundle, events, steps, trace_present):
    counts = defaultdict(int)
    for record in bundle.raw_records:
        counts[str(record.source_role)] += 1
    return {
        "rawRecords": dict(counts),
        "normalizedEvents": len(events),
        "localSteps": len(steps),
        "serverIndependent": any(e.source == SourceRole.SERVER_TIMELINE for e in events),
        "sse": any(e.source == SourceRole.SSE for e in events),
        "trace": trace_present,
        "traceComplete": not any(q.get("kind") == "INCOMPLETE_TRACE" for q in bundle.quality),
        "qualityRecords": len(bundle.quality),
    }


def compile_replay(local_path, *, server_path=None, trace_path=None,
                   http_dump_path=None, round_no=None) -> ReplaySession:
    from .adapters import import_sources
    bundle = import_sources(local_path, server_path=server_path,
                            trace_path=trace_path, http_dump_path=http_dump_path)
    return compile_bundle(bundle, round_no=round_no)
