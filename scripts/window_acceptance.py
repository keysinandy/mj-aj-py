"""Summarize one recorded room without contacting the server or changing logs.

Usage: python3 scripts/window_acceptance.py ROOM [--out local/report.json]
Exit 0 means no detected replay/legal errors, not complete window acceptance.
"""

import argparse
from collections import Counter, defaultdict
import json
import math
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mj.log_replay import replay_game
from mj.platform.bot_client import BotClient
from mj.platform.mirror import MirrorInconsistent


_DEMAND_COUNTER_KEYS = (
    "logical_demands", "logical_input_demands", "coalesced_demands",
    "successor_requests", "physical_state_requests",
    "logical_state_requests", "physical_state_attempts",
    "substituted_candidates", "cancelled_before_send",
    "suppressed_duplicates", "coalesced_or_suppressed",
)


def _request_count_metrics(rows):
    """Count request/attempt IDs without double-counting repeated summaries.

    New lifecycle logs carry opaque logical and physical IDs.  Legacy rows do
    not, so their historical row/count interpretation remains the fallback
    and the report explicitly marks that the counts are legacy-derived.
    """
    def is_lifecycle_id(value):
        if not isinstance(value, str) or ":state:" not in value:
            return False
        suffix = value.rsplit(":", 1)[-1]
        return suffix.isdigit()

    logical_ids = set()
    physical_ids = set()
    logical = 0
    physical = 0
    duplicate_logical = duplicate_physical = 0
    has_logical_ids = False
    has_lifecycle_ids = False
    has_physical_evidence = False
    physical_id_evidence = False
    physical_without_ids = 0
    for row in rows:
        logical_id = row.get("logical_request_id")
        if logical_id is not None:
            has_logical_ids = True
            has_lifecycle_ids = has_lifecycle_ids or is_lifecycle_id(
                logical_id)
            if logical_id in logical_ids:
                duplicate_logical += 1
            else:
                logical_ids.add(logical_id)
                logical += 1
        else:
            logical += 1
        transport = row.get("transport") or {}
        attempts = transport.get("state_attempts") or []
        if attempts:
            has_physical_evidence = True
            for attempt in attempts:
                attempt_id = attempt.get("transport_request_id")
                if attempt_id is None:
                    physical_without_ids += 1
                    physical += 1
                    continue
                physical_id_evidence = True
                if attempt_id in physical_ids:
                    duplicate_physical += 1
                else:
                    physical_ids.add(attempt_id)
                    physical += 1
        else:
            count = transport.get("state_physical_attempts")
            if count is not None:
                has_physical_evidence = True
                try:
                    physical += int(count or 0)
                except (TypeError, ValueError):
                    has_physical_evidence = False
    physical_value = physical if has_physical_evidence else None
    if physical_id_evidence and physical_without_ids:
        physical_source = "unique_ids_and_attempt_records"
    elif physical_id_evidence:
        physical_source = "unique_ids"
    elif has_physical_evidence:
        physical_source = "attempt_count"
    else:
        physical_source = "missing"
    return {
        "logical": logical,
        "physical": physical_value,
        "has_ids": has_logical_ids or physical_id_evidence,
        "has_logical_ids": has_logical_ids,
        "has_lifecycle_ids": has_lifecycle_ids,
        "has_physical_id_evidence": physical_id_evidence,
        "has_physical_evidence": has_physical_evidence,
        "duplicate_logical": duplicate_logical,
        "duplicate_physical": duplicate_physical,
        "source": ("unique_ids" if has_lifecycle_ids and physical_id_evidence
                   else "lifecycle_ids_without_attempt_ids"
                   if has_lifecycle_ids and not has_physical_evidence
                   else "legacy_ids_and_attempt_count"
                   if has_logical_ids
                   else physical_source
                   if has_physical_evidence
                   else "legacy_row_count"),
        "physical_source": physical_source,
    }


def _demand_fallback(snapshots):
    """Recover cumulative demand counters from req snapshots.

    A req snapshot is cumulative for one room, so the maximum observed value
    is safer than trusting the last line when a legacy log ends abruptly.
    Keep the last snapshot's derived/reason fields for diagnostics.
    """
    snapshots = [item for item in snapshots if isinstance(item, dict)]
    if not snapshots:
        return {}, "missing"
    result = dict(snapshots[-1])
    missing = []
    for key in _DEMAND_COUNTER_KEYS:
        values = []
        for item in snapshots:
            value = item.get(key)
            if value is None:
                continue
            try:
                values.append(int(value))
            except (TypeError, ValueError):
                continue
        if values:
            result[key] = max(values)
        else:
            # Do not turn an old/partial demand snapshot into a false zero.
            # The report can still use the available counters, but keeps the
            # missing-field boundary explicit for downstream aggregation.
            result.pop(key, None)
            missing.append(key)
    if missing:
        result["missing_counters"] = missing
    return result, "req_fallback"


def _gap_record(records, index, row):
    """Classify an observed gap using only fields already in JSONL."""
    response = row.get("res") or {}
    if not (response.get("gap") or row.get("gap")):
        return None
    transport = row.get("transport") or {}
    retries = sum(int(transport.get(name) or 0)
                  for name in ("retry_429", "retry_gateway", "retry_network"))
    requested = row.get("requested_seq", row.get("seq"))
    response_seq = response.get("seq", row.get("seq"))
    snapshot = bool(response.get("snapshot"))

    next_events = None
    recovery_status = "unknown"
    for later in records[index + 1:]:
        if later.get("type") == "events":
            next_events = later.get("events") or []
            break
        if later.get("type") == "snapshot":
            recovery_status = "authoritative_snapshot_applied"
            break
        if later.get("type") == "reset":
            recovery_status = "reset_unverified"
            break
        if later.get("type") == "end":
            recovery_status = "terminal_before_recovery"
            break
        if later.get("type") in ("req", "snapshot", "reset", "end"):
            break
    event_seqs = [event.get("seq") for event in (next_events or [])
                  if event.get("seq") is not None]

    if retries:
        reason = "transport_retry_related"
    elif snapshot or requested == 0 or row.get("request_kind") == "RESYNC":
        reason = "snapshot_reanchor"
    else:
        jump = False
        try:
            jump = (response_seq is not None and requested is not None
                    and int(response_seq) > int(requested) + 1)
        except (TypeError, ValueError):
            pass
        contiguous_batch = False
        discontinuous_events = False
        if event_seqs and requested is not None:
            try:
                expected = int(requested) + 1
                ordered = sorted({int(value) for value in event_seqs})
                contiguous_batch = (
                    ordered[0] == expected
                    and ordered == list(range(expected, ordered[-1] + 1)))
                discontinuous_events = ordered[0] > expected or not contiguous_batch
            except (TypeError, ValueError):
                discontinuous_events = True
        if contiguous_batch and jump:
            reason = "sse_batch_catchup"
        elif discontinuous_events:
            reason = "event_discontinuity"
        elif jump:
            reason = "log_truncation_or_unknown"
        else:
            reason = "log_truncation_or_unknown"

    # A missing decision is not proof of no opportunity loss.  Return None
    # until an authoritative snapshot boundary is actually recorded.
    decision_impact = None
    for later in records[index + 1:]:
        if later.get("type") == "decision":
            decision_impact = True
            break
        if later.get("type") == "snapshot":
            decision_impact = False
            break
        if later.get("type") in ("req", "reset", "end"):
            break
    return {
        "reason": reason,
        "decision_impact": decision_impact,
        "requested_seq": requested,
        "response_seq": response_seq,
        "request_kind": row.get("request_kind", "UNKNOWN_LEGACY"),
        "retries": retries,
        "recovery_status": ("events_observed" if next_events is not None
                             else recovery_status),
        "decision_impact_confidence": (
            "observed" if decision_impact is not None else "unknown"),
    }


def distribution(values):
    values = sorted(values)
    if not values:
        return {"n": 0, "p50": None, "p95": None, "max": None}

    def percentile(p):
        index = (len(values) - 1) * p
        lo, hi = math.floor(index), math.ceil(index)
        return round(values[lo] + (values[hi] - values[lo]) * (index - lo), 1)

    return {"n": len(values), "p50": percentile(.5),
            "p95": percentile(.95), "max": round(values[-1], 1)}


def _request_timing(row):
    """Break one logical request into queue, HTTP, backoff and residual time.

    A logical req's top-level throttle ticket is the last physical attempt in
    older logs.  It is only a valid total for a single-attempt request; for a
    retried request without per-attempt throttle fields the total is unknown.
    """
    transport = row.get("transport") or {}
    attempts = transport.get("state_attempts") or []
    try:
        attempt_count = int(transport.get("state_physical_attempts"))
    except (TypeError, ValueError):
        attempt_count = len(attempts)
    if attempt_count <= 0:
        attempt_count = len(attempts) or 1

    queue_values = []
    http_values = []
    for attempt in attempts:
        throttle = attempt.get("throttle") or {}
        value = throttle.get("queue_wait_ms")
        if value is None:
            value = attempt.get("queue_wait_ms")
        if value is not None:
            try:
                queue_values.append(float(value))
            except (TypeError, ValueError):
                pass
        timing = attempt.get("timing") or {}
        value = timing.get("total_ms", attempt.get("latency_ms"))
        if value is not None:
            try:
                http_values.append(float(value))
            except (TypeError, ValueError):
                pass

    logical_queue = (row.get("throttle") or {}).get("queue_wait_ms")
    queue_total = None
    queue_source = "missing"
    if attempts and len(queue_values) == len(attempts):
        queue_total = sum(queue_values)
        queue_source = "physical_attempts"
    elif attempt_count <= 1 and logical_queue is not None:
        try:
            queue_total = float(logical_queue)
            queue_source = "logical_single_attempt"
        except (TypeError, ValueError):
            pass

    http_total = sum(http_values) if http_values else None
    try:
        backoff = float(transport.get("backoff_ms"))
    except (TypeError, ValueError):
        backoff = None
    try:
        latency = float(row.get("latency_ms"))
    except (TypeError, ValueError):
        latency = None
    residual = None
    if latency is not None and queue_total is not None \
            and http_total is not None and backoff is not None:
        residual = max(0.0, latency - queue_total - http_total - backoff)
    return {
        "queue_total_ms": queue_total,
        "queue_source": queue_source,
        "http_total_ms": http_total,
        "backoff_ms": backoff,
        "residual_ms": residual,
    }


def _authoritative_eligible_window_key(row):
    """Return one phase-independent authoritative eligible-window key."""
    window_id = row.get("window_id")
    if not isinstance(window_id, dict):
        return None
    if window_id.get("identity_status") != "authoritative" \
            or window_id.get("source_discard_seq") is None:
        return None
    legal = row.get("legal")
    if not isinstance(legal, (list, tuple)) \
            or not any(action != -1 for action in legal):
        return None
    outcome = row.get("outcome", row.get("stage", "observed"))
    if outcome not in ("requested", "open", "confirmed", "observed"):
        return None
    # Phase is deliberately excluded: peng and chi are two observations of
    # one discard window, not two eligible windows.
    return tuple(window_id.get(field) for field in (
        "game_id", "round_id", "discard_owner", "source_discard_seq",
        "tile"))


def _eligible_window_identity(row):
    """Return (phase-independent key, identity class) for a legal window."""
    legal = row.get("legal")
    if not isinstance(legal, (list, tuple)) \
            or not any(action != -1 for action in legal):
        return None, None
    outcome = row.get("outcome", row.get("stage", "observed"))
    if outcome not in ("requested", "open", "confirmed", "observed",
                       "authoritative_open", "weak_key_open", "AUTHORIZED"):
        return None, None
    window_id = _window_id_from_row(row)
    logical = _logical_window_key(row)
    if logical is None:
        return None, None
    if isinstance(window_id, dict) \
            and window_id.get("identity_status") == "authoritative" \
            and window_id.get("source_discard_seq") is not None:
        return logical, "authoritative"
    if isinstance(window_id, dict) \
            and window_id.get("identity_status") == "legacy_unresolved":
        return logical, "legacy"
    return None, None


_PROTOCOL_HONORS = {"东": 27, "南": 28, "西": 29, "北": 30,
                    "中": 31, "发": 32, "白": 33}


def _protocol_tile_int(tile):
    """Protocol tile string ("2w"/"9t"/"发") → internal int, else None."""
    if not isinstance(tile, str):
        return None
    if tile in _PROTOCOL_HONORS:
        return _PROTOCOL_HONORS[tile]
    if len(tile) >= 2 and tile[0].isdigit() and tile[1] in ("w", "b", "t"):
        return int(tile[0]) - 1 + {"w": 0, "b": 9, "t": 18}[tile[1]]
    return None


def _claim_miss_attempt_key(row):
    """Canonical (window identity, phase) key for a claim/decision/action row.

    Returns None when the row carries no window identity: those records can
    only join by phase/seq heuristics, which is deliberately not attempted.
    """
    attempt = row.get("window_attempt_key")
    window_id = (attempt or {}).get("window_id") if isinstance(
        attempt, dict) else None
    if not isinstance(window_id, dict):
        window_id = row.get("window_id")
        phase = row.get("phase")
    else:
        phase = attempt.get("phase") or row.get("phase")
    if not isinstance(window_id, dict):
        return None
    if (window_id.get("identity_status") not in (None, "authoritative")
            or window_id.get("source_discard_seq") is None):
        return None
    source_seq = window_id.get("source_discard_seq")
    return (window_id.get("game_id"), window_id.get("round_id"),
            window_id.get("discard_owner"), source_seq,
            window_id.get("tile"), phase)


def _window_id_from_row(row):
    """Return the nested WindowId from either attempt or flat row shape."""
    attempt = row.get("window_attempt_key")
    if isinstance(attempt, dict) and isinstance(attempt.get("window_id"), dict):
        return attempt["window_id"]
    window_id = row.get("window_id")
    return window_id if isinstance(window_id, dict) else None


def _logical_window_key(row):
    """Phase-independent key used for eligibility/identity coverage."""
    window_id = _window_id_from_row(row)
    if not isinstance(window_id, dict):
        return None
    fields = ("game_id", "round_id", "discard_owner",
              "source_discard_seq", "tile")
    if window_id.get("identity_status") == "authoritative" \
            and window_id.get("source_discard_seq") is not None:
        return tuple(window_id.get(field) for field in fields)
    # Keep a separate weak key only for coverage diagnostics.  It must never
    # be passed to the strong attempt resolver.
    if window_id.get("identity_status") == "legacy_unresolved":
        return ("legacy", window_id.get("game_id"),
                window_id.get("round_id"), window_id.get("discard_owner"),
                window_id.get("tile"),
                repr(window_id.get("fallback")))
    return None


def _is_window_row(row):
    return (row.get("phase") in ("response_peng", "response_chi")
            or row.get("type") in ("window_confirm", "window_authorization",
                                    "window_terminal", "window_lifecycle"))


def _row_attempt_key(row):
    """Return authoritative phase key for all lifecycle record variants."""
    return _claim_miss_attempt_key(row)


def _row_time(row, *names):
    for name in names:
        value = row.get(name)
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
    value = row.get("ts")
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


CONFIRM_DIAGNOSTIC_CATEGORIES = (
    "C1_CONFIRM_NOT_CREATED",
    "C2_CONFIRM_NOT_DISPATCHED",
    "C3_CONFIRM_QUEUE_LATE",
    "C4_CONFIRM_HTTP_LATE",
    "C5_TIMELY_RESPONSE_NOT_AUTHORIZED",
    "PROTOCOL_PHASE_UNOBSERVABLE",
    "UNRESOLVED",
)


def _window_key_from_id(window_id, phase=None):
    """Return an exact authoritative WindowAttemptKey tuple, if available."""
    if not isinstance(window_id, dict):
        return None
    if window_id.get("identity_status") not in (None, "authoritative"):
        return None
    if window_id.get("source_discard_seq") is None:
        return None
    return tuple(window_id.get(field) for field in (
        "game_id", "round_id", "discard_owner", "source_discard_seq",
        "tile")) + (phase,)


def _request_window_key(row):
    """Extract a request's explicit WINDOW_CONFIRM key without heuristics."""
    demand = row.get("demand") or {}
    reasons = demand.get("reasons") or {}
    reason = reasons.get("WINDOW_CONFIRM")
    if isinstance(reason, dict):
        key = _window_key_from_id(reason.get("window_id"),
                                  reason.get("phase"))
        if key is not None:
            return key
    attempt = row.get("window_attempt_key")
    if isinstance(attempt, dict):
        key = _window_key_from_id(attempt.get("window_id"),
                                  attempt.get("phase"))
        if key is not None:
            return key
    return _window_key_from_id(row.get("window_id"), row.get("phase"))


def _confirm_terminal_row(row):
    """Whether a row is terminal evidence for a confirmation attempt."""
    kind = row.get("type")
    if kind == "window_terminal":
        return True
    if kind == "window_lifecycle":
        return row.get("stage") == "terminal" or row.get("state") in (
            "TERMINAL", "PREEMPTED", "POST_OK", "POST_REJECTED",
            "POST_UNCERTAIN")
    if kind == "window_confirm":
        return row.get("outcome") in ("closed", "stale", "expired", "miss")
    if kind == "claim_miss":
        reason = str(row.get("reason") or "")
        return reason.startswith(("window_confirm_", "server_timeout_"))
    return False


def _event_tile_int(event):
    value = event.get("tile")
    if isinstance(value, int):
        return value
    return _protocol_tile_int(value)


def _confirm_source_evidence(records, key, my_seat):
    """Find exact source-event/SSE boundaries for one authoritative window."""
    game_id, _round_id, owner, source_seq, tile, _phase = key
    source_events = []
    source_batches = []
    terminal_events = []
    for index, row in enumerate(records):
        if row.get("type") != "events":
            continue
        events = row.get("events") or []
        has_source = any(
            event.get("type") == "tile_discarded"
            and event.get("seq") == source_seq
            and event.get("seat") == owner
            and _event_tile_int(event) == tile
            for event in events)
        if has_source:
            source_events.append(index)
            source_batches.append({
                "record_index": index,
                "observed_at": row.get("ts"),
                "seq_to": row.get("seq_to"),
            })
        for event in events:
            event_seq = event.get("seq")
            if event_seq is None or source_seq is None:
                continue
            if event_seq <= source_seq:
                continue
            is_terminal = (
                (event.get("type") == "timeout"
                 and (my_seat is None or event.get("seat") == my_seat))
                or event.get("type") in ("tile_discarded", "chi", "peng",
                                            "gang"))
            if is_terminal:
                terminal_events.append({
                    "record_index": index,
                    "seq": event_seq,
                    "type": event.get("type"),
                    "seat": event.get("seat"),
                })
    sse_frames = []
    for index, row in enumerate(records):
        if row.get("type") != "sse_frame":
            continue
        if row.get("gid") not in (None, game_id):
            continue
        if row.get("seq") == source_seq:
            sse_frames.append({
                "record_index": index,
                "seq": row.get("seq"),
                "received_at": row.get("ts"),
                "accepted": row.get("accepted"),
                "wake_enqueued": row.get("wake_enqueued"),
                "deduplicated": row.get("deduplicated"),
            })
    return {
        "source_event_indexes": source_events,
        "source_batches": source_batches,
        "terminal_events": terminal_events,
        # Only the first post-source terminal boundary is a lifecycle join.
        # Later timeout/discard events belong to subsequent windows and must
        # not inflate this window's evidence list.
        "terminal_event_indexes": ([min(item["record_index"]
                                        for item in terminal_events)]
                                   if terminal_events else []),
        "sse_frames": sse_frames,
        "sse_frame_indexes": [item["record_index"] for item in sse_frames],
    }


def _confirm_transport_evidence(request_rows, gid=None):
    """Summarize only physical WINDOW_CONFIRM transport boundaries."""
    queue_values = []
    http_values = []
    backoff_values = []
    deadline_send_values = []
    deadline_response_values = []
    statuses = Counter()
    retry_429 = retry_gateway = retry_network = 0
    queue_late = False
    send_late = False
    response_late = False
    client_read_late = False
    retry_or_backoff = False
    attempt_indexes = []
    request_details = []
    for index, row in request_rows:
        transport = row.get("transport") or {}
        retry_429 += int(transport.get("retry_429") or 0)
        retry_gateway += int(transport.get("retry_gateway") or 0)
        retry_network += int(transport.get("retry_network") or 0)
        try:
            backoff = float(transport.get("backoff_ms"))
        except (TypeError, ValueError):
            backoff = None
        if backoff is not None:
            backoff_values.append(backoff)
            retry_or_backoff = retry_or_backoff or backoff > 0
        attempts = transport.get("state_attempts") or []
        if not attempts:
            attempts = [{}]
        request_attempts = []
        for attempt in attempts:
            throttle = attempt.get("throttle") or {}
            queue = throttle.get("queue_wait_ms", attempt.get(
                "queue_wait_ms"))
            send_left = attempt.get("deadline_left_at_send_ms")
            if send_left is None:
                send_left = throttle.get("deadline_left_ms")
            response_left = attempt.get("deadline_left_at_response_ms")
            if response_left is None:
                response_left = attempt.get("deadline_left_at_response")
            headers_left = attempt.get("deadline_left_at_headers_ms")
            timing = attempt.get("timing") or {}
            http_ms = timing.get("total_ms", attempt.get("latency_ms"))
            request_attempts.append({
                "attempt_index": attempt.get("attempt_index"),
                "started_epoch": attempt.get("started_epoch"),
                "status": attempt.get("status", row.get("status")),
                "gid": row.get("gid") or gid,
                "logical_request_id": (attempt.get("logical_request_id")
                                        or row.get("logical_request_id")),
                "reason": attempt.get("reason"),
                "generation": attempt.get("generation"),
                "server_trace_id": attempt.get("server_trace_id"),
                "queue_ms": queue,
                "throttle_status": throttle.get("status"),
                "throttle_enter": throttle.get("throttle_enter"),
                "throttle_granted": throttle.get("throttle_granted"),
                "http_start_mono": attempt.get("http_start_mono"),
                "headers_received_mono": attempt.get(
                    "headers_received_mono"),
                "body_finished_mono": attempt.get("body_finished_mono"),
                "pre_read_ms": timing.get("pre_read_ms"),
                "read_ms": timing.get("read_ms"),
                "deadline_left_at_headers_ms": headers_left,
                "deadline_left_at_send_ms": send_left,
                "deadline_left_at_response_ms": response_left,
                "deadline_missed": bool(throttle.get("deadline_missed")),
                "http_ms": http_ms,
                "retry_after_s": attempt.get("retry_after_s"),
            })
            attempt_indexes.append({
                "record_index": index,
                "attempt_index": attempt.get("attempt_index"),
                "logical_request_id": (attempt.get("logical_request_id")
                                        or row.get("logical_request_id")),
                "status": attempt.get("status", row.get("status")),
            })
            status = attempt.get("status", row.get("status"))
            statuses[str(status)] += 1
            if queue is not None:
                try:
                    queue_values.append(float(queue))
                except (TypeError, ValueError):
                    pass
            if send_left is not None:
                try:
                    send_left = float(send_left)
                    deadline_send_values.append(send_left)
                    send_late = send_late or send_left < 0
                    queue_late = queue_late or send_left < 0
                except (TypeError, ValueError):
                    pass
            queue_late = queue_late or bool(throttle.get("deadline_missed"))
            if response_left is not None:
                try:
                    response_left = float(response_left)
                    deadline_response_values.append(response_left)
                    response_late = response_late or response_left < 0
                    if headers_left is not None:
                        headers_value = float(headers_left)
                        client_read_late = client_read_late or (
                            headers_value >= 0 and response_left < 0)
                except (TypeError, ValueError):
                    pass
            total = timing.get("total_ms", attempt.get("latency_ms"))
            if total is not None:
                try:
                    http_values.append(float(total))
                except (TypeError, ValueError):
                    pass
            if status not in (None, 200, "200"):
                retry_or_backoff = True
        retry_or_backoff = retry_or_backoff or retry_429 > 0 \
            or retry_gateway > 0 or retry_network > 0
        response = row.get("res") or {}
        request_details.append({
            "record_index": index,
            "gid": row.get("gid") or gid,
            "ts": row.get("ts"),
            "logical_request_id": row.get("logical_request_id"),
            "request_kind": row.get("request_kind"),
            "requested_seq": row.get("requested_seq"),
            "status": row.get("status"),
            "latency_ms": row.get("latency_ms"),
            "response_seq": response.get("seq"),
            "has_snapshot": bool(response.get("snapshot")),
            "gap": response.get("gap"),
            "retry_429": transport.get("retry_429", 0),
            "retry_gateway": transport.get("retry_gateway", 0),
            "retry_network": transport.get("retry_network", 0),
            "backoff_ms": transport.get("backoff_ms"),
            "attempts": request_attempts,
        })
    return {
        "request_indexes": [index for index, _row in request_rows],
        "logical_request_ids": [row.get("logical_request_id")
                                for _index, row in request_rows
                                if row.get("logical_request_id") is not None],
        "request_details": request_details,
        "attempts": attempt_indexes,
        "statuses": dict(statuses),
        "queue_ms": distribution(queue_values),
        "http_ms": distribution(http_values),
        "backoff_ms": distribution(backoff_values),
        "retry_429": retry_429,
        "retry_gateway": retry_gateway,
        "retry_network": retry_network,
        "deadline_left_at_send_ms": distribution(deadline_send_values),
        "deadline_left_at_response_ms": distribution(deadline_response_values),
        "queue_late": queue_late,
        "send_late": send_late,
        "response_late": response_late,
        "client_read_late": client_read_late,
        "retry_or_backoff": retry_or_backoff,
    }


def _confirm_diagnostic(records, resolution, my_seat=None):
    """Build one conservative, mutually-exclusive confirmation diagnosis."""
    if resolution.get("loss_stage") != "CONFIRM":
        return None
    key = _row_attempt_key(resolution)
    if key is None:
        return {
            "category": "UNRESOLVED",
            "evidence_quality": "identity_unverifiable",
            "missing_evidence": ["authoritative_window_id"],
            "window_attempt_key": resolution.get("window_attempt_key"),
        }
    confirm_indexes = []
    terminal_row_indexes = []
    demand_indexes = []
    request_rows = []
    confirm_rows = []
    for index, row in enumerate(records):
        if _row_attempt_key(row) == key:
            if row.get("type") == "window_confirm":
                confirm_indexes.append(index)
                confirm_rows.append(row)
            if _confirm_terminal_row(row):
                terminal_row_indexes.append(index)
            if row.get("type") == "window_lifecycle" and (
                    row.get("state") == "CONFIRM_PENDING"
                    or row.get("outcome") == "PENDING"):
                demand_indexes.append(index)
        if row.get("type") == "req" and _request_window_key(row) == key:
            request_rows.append((index, row))
            demand = row.get("demand") or {}
            reason = (demand.get("reasons") or {}).get("WINDOW_CONFIRM")
            if isinstance(reason, dict) and reason.get("status") == "PENDING":
                demand_indexes.append(index)

    source = _confirm_source_evidence(records, key, my_seat)
    terminal_indexes = terminal_row_indexes + source["terminal_event_indexes"]
    terminal_index = min(terminal_indexes) if terminal_indexes else None
    bounded_requests = [(index, row) for index, row in request_rows
                         if terminal_index is None or index <= terminal_index]
    room_gid = next((row.get("gid") for row in records
                     if row.get("type") == "meta"
                     and row.get("gid") is not None), None)
    transport = _confirm_transport_evidence(bounded_requests, gid=room_gid)
    expected_phase = key[-1]
    expected_rows = [row for row in confirm_rows
                     if row.get("snapshot_phase") == expected_phase]
    exact_deadline = resolution.get("exact_deadline_at")
    if exact_deadline is None:
        for row in confirm_rows:
            if row.get("exact_deadline_at") is not None:
                exact_deadline = row.get("exact_deadline_at")
                break
    response_after_deadline = False
    response_before_deadline = False
    for row in confirm_rows:
        response_at = _row_time(row, "confirm_response_at", "observed_at")
        if response_at is None or exact_deadline is None:
            continue
        try:
            response_after_deadline |= response_at >= float(exact_deadline)
            response_before_deadline |= response_at < float(exact_deadline)
        except (TypeError, ValueError):
            pass
    transport["response_after_exact_deadline"] = response_after_deadline
    transport["response_before_exact_deadline"] = response_before_deadline
    # A retry/backoff is a contribution, not proof that the window was
    # missed.  C4 requires a response/deadline boundary showing that the
    # physical confirmation completed late; retry evidence is retained in
    # the record for the separate transport analysis.
    transport_late = (transport["response_late"]
                      or response_after_deadline)
    source_index = (min(source["source_event_indexes"])
                    if source["source_event_indexes"] else None)
    same_batch = bool(source_index is not None
                       and source_index in source["terminal_event_indexes"])
    source_separate = bool(source_index is not None and terminal_index is not None
                           and source_index < terminal_index and not same_batch)
    expected_authorized_predicate = False
    for row in expected_rows:
        responding = row.get("responding_seats")
        legal = row.get("legal")
        deadline_left = row.get("deadline_left_ms")
        timely = response_before_deadline
        if deadline_left is not None:
            try:
                timely = timely or float(deadline_left) >= 0
            except (TypeError, ValueError):
                pass
        if (timely and isinstance(responding, list) and my_seat in responding
                and isinstance(legal, (list, tuple))
                and any(action != -1 for action in legal)):
            expected_authorized_predicate = True
            break

    category = "UNRESOLVED"
    reason = "insufficient_confirmation_boundary"
    missing = []
    if bounded_requests and transport["queue_late"] and not transport_late:
        category = "C3_CONFIRM_QUEUE_LATE"
        reason = "throttle_grant_after_deadline"
    elif bounded_requests and transport_late:
        category = "C4_CONFIRM_HTTP_LATE"
        reason = ("response_retry_or_backoff_after_window_boundary"
                  if transport["retry_or_backoff"]
                  else "http_response_after_window_boundary")
    elif bounded_requests and expected_authorized_predicate:
        category = "C5_TIMELY_RESPONSE_NOT_AUTHORIZED"
        reason = "expected_phase_and_authority_were_present_but_not_opened"
    elif not bounded_requests:
        if same_batch:
            category = "PROTOCOL_PHASE_UNOBSERVABLE"
            reason = "source_and_terminal_shared_one_events_batch"
        elif source_index is None:
            category = "PROTOCOL_PHASE_UNOBSERVABLE"
            reason = "source_event_not_observable_in_recorded_stream"
        elif demand_indexes:
            category = "C2_CONFIRM_NOT_DISPATCHED"
            reason = "pending_confirmation_without_physical_request"
        elif source_separate:
            category = "C1_CONFIRM_NOT_CREATED"
            reason = "source_observed_before_terminal_without_confirmation_demand"
        else:
            category = "PROTOCOL_PHASE_UNOBSERVABLE"
            reason = "no_separate_authoritative_confirmation_boundary"
    elif not expected_rows:
        if transport["retry_or_backoff"] and not transport_late:
            category = "UNRESOLVED"
            reason = "retry_without_missed_window_boundary"
            missing.append("response_after_deadline_boundary")
        else:
            category = "PROTOCOL_PHASE_UNOBSERVABLE"
            reason = "expected_phase_not_exposed_before_terminal"
    else:
        if transport["retry_or_backoff"]:
            missing.append("response_after_deadline_boundary")
        missing.extend(["authorization_predicate"])

    if source_index is None:
        missing.append("source_event")
    if terminal_index is None:
        missing.append("terminal_boundary")
    if not bounded_requests:
        missing.append("physical_window_confirm_request")
    if not confirm_rows:
        missing.append("window_confirm_resolver_record")
    return {
        "category": category,
        "reason": reason,
        "evidence_quality": ("complete" if category not in (
            "UNRESOLVED", "PROTOCOL_PHASE_UNOBSERVABLE") else "partial"),
        "missing_evidence": sorted(set(missing)),
        "window_attempt_key": resolution.get("window_attempt_key"),
        "window_id": resolution.get("window_id"),
        "phase": expected_phase,
        "source_event_indexes": source["source_event_indexes"],
        "sse_frame_indexes": source["sse_frame_indexes"],
        "demand_record_indexes": sorted(set(demand_indexes)),
        "physical_request_indexes": transport["request_indexes"],
        "resolver_record_indexes": sorted(set(confirm_indexes)),
        "terminal_record_indexes": sorted(set(terminal_row_indexes)),
        "terminal_event_indexes": source["terminal_event_indexes"],
        "same_batch_source_terminal": same_batch,
        "source_observed_before_terminal": source_separate,
        "confirm_logical_request_ids": transport["logical_request_ids"],
        "transport_contributed": bool(
            transport["queue_late"] or transport["response_late"]
            or transport["retry_or_backoff"] or response_after_deadline),
        "transport": transport,
        "snapshot_phases": sorted({row.get("snapshot_phase")
                                    for row in confirm_rows
                                    if row.get("snapshot_phase") is not None}),
        "exact_deadline_at": exact_deadline,
    }


CANONICAL_PRECEDENCE = (
    "SUCCESS", "STRATEGY_PASS", "RULE_PREEMPTED", "POST_REJECTED",
    "POST_UNCERTAIN", "SUBMIT", "DECISION", "CONFIRM", "UNKNOWN",
)


def _canonical_window_resolutions(records, claim_events=None, my_seat=None):
    """Build one mutually-exclusive resolution per authoritative attempt.

    The resolver intentionally prefers explicit lifecycle/action/decision
    facts and only uses timeout/claim-miss rows as terminal evidence.  It does
    not join rows that lack an authoritative WindowId by seq/phase guesses.
    """
    grouped = defaultdict(list)
    identity_unverifiable = []
    for index, row in enumerate(records):
        if not _is_window_row(row):
            continue
        phase = row.get("phase")
        if phase not in ("response_peng", "response_chi"):
            attempt = row.get("window_attempt_key") or {}
            phase = attempt.get("phase") if isinstance(attempt, dict) else None
        if phase not in ("response_peng", "response_chi"):
            continue
        key = _row_attempt_key(row)
        if key is None:
            if row.get("type") in ("claim_miss", "action", "decision",
                                    "window_confirm", "window_terminal",
                                    "window_lifecycle"):
                identity_unverifiable.append({"record_index": index,
                                               "phase": phase,
                                               "reason": row.get("reason")})
            continue
        grouped[key].append((index, row))

    claim_events = claim_events or []
    resolutions = []
    for key, entries in grouped.items():
        rows = [row for _index, row in entries]
        first = rows[0]
        wid = _window_id_from_row(first) or {}
        phase = key[-1]
        confirms = [row for row in rows if row.get("type") == "window_confirm"]
        authorizations = [row for row in rows
                          if row.get("type") == "window_authorization"]
        lifecycle = [row for row in rows
                     if row.get("type") == "window_lifecycle"]
        decisions = [row for row in rows if row.get("type") == "decision"]
        actions = [row for row in rows if row.get("type") == "action"]
        misses = [row for row in rows if row.get("type") == "claim_miss"]
        terminals = [row for row in rows if row.get("type") == "window_terminal"]

        # A weak-key authorization (outcome="weak_key_open") is a guessed
        # epoch-key decision, not an authoritative open; it must not flip a
        # window into the DECISION loss bucket on its own.
        authorization_outcomes = {row.get("outcome") for row in authorizations}
        authoritative_open = (
            any(outcome != "weak_key_open"
                for outcome in authorization_outcomes)
            or any(row.get("outcome") in ("open", "confirmed",
                                          "authoritative_open", "AUTHORIZED")
                   for row in confirms + lifecycle))
        success = any(row.get("ok") is True
                      or row.get("outcome") == "SUCCESS"
                      for row in actions + lifecycle)
        action_pass = any(row.get("action") == -1
                          or row.get("decision_result") == "PASS"
                          or row.get("outcome") == "STRATEGY_PASS"
                          for row in decisions + lifecycle)
        nonpass_decisions = [row for row in decisions
                             if row.get("action") not in (None, -1)]
        rejected = any(row.get("status") == 409
                       or row.get("outcome") == "POST_REJECTED"
                       or row.get("post_status") == "POST_REJECTED"
                       for row in actions + lifecycle)
        uncertain = any(row.get("outcome") == "POST_UNCERTAIN"
                        or row.get("post_status") == "POST_UNCERTAIN"
                        or (row.get("type") == "claim_miss"
                            and str(row.get("reason") or "").startswith(
                                "action_uncertain"))
                        for row in actions + lifecycle + misses)
        opponent = (_window_claim_evidence(claim_events, key, my_seat)
                    == "opponent")
        explicit_preempt = any(row.get("outcome") in (
            "RULE_PREEMPTED", "PREEMPTED") for row in lifecycle + terminals)
        opponent = opponent or explicit_preempt
        terminal_reason = None
        for row in reversed(terminals + lifecycle + confirms + misses):
            if row.get("terminal_reason"):
                terminal_reason = row.get("terminal_reason")
                break
            if row.get("reason") and row.get("outcome") in (
                    "closed", "stale", "expired", "miss", "TERMINAL"):
                terminal_reason = row.get("reason")
                break
        if terminal_reason is None:
            for row in misses:
                if row.get("reason"):
                    terminal_reason = row.get("reason")
                    break

        exact_deadline = None
        for row in authorizations + decisions + actions + confirms + lifecycle:
            if row.get("exact_deadline_at") is not None:
                exact_deadline = row.get("exact_deadline_at")
                break
        authorization_seq = None
        for row in authorizations + confirms + decisions + actions:
            if row.get("authorization_snapshot_seq") is not None:
                authorization_seq = row.get("authorization_snapshot_seq")
                break
        decision_id = None
        decision_action = None
        decision_finished = None
        for row in decisions:
            decision_id = row.get("id", row.get("decision"))
            decision_action = row.get("action")
            decision_finished = _row_time(row, "decision_finished_at")
            if decision_action is not None:
                break

        # A success/pass/preemption fact is stronger than any late miss or
        # timeout.  Only after those are absent do we assign a loss stage.
        if success:
            outcome, loss_stage, loss_reason = "SUCCESS", "NONE", None
        elif action_pass:
            outcome, loss_stage, loss_reason = "STRATEGY_PASS", "NONE", None
        elif opponent:
            outcome, loss_stage, loss_reason = "RULE_PREEMPTED", "NONE", None
        elif rejected:
            outcome, loss_stage, loss_reason = (
                "POST_REJECTED", "POST_RESULT", "post_rejected")
        elif uncertain:
            outcome, loss_stage, loss_reason = (
                "POST_UNCERTAIN", "POST_RESULT", "post_uncertain")
        elif nonpass_decisions:
            explicit_submit = [row for row in rows
                               if str(row.get("reason") or "").startswith(
                                   "submit_")]
            outcome, loss_stage = "CLIENT_LOSS", "SUBMIT"
            loss_reason = (explicit_submit[-1].get("reason")
                           if explicit_submit else "submit_result_missing")
        elif authoritative_open:
            outcome, loss_stage = "CLIENT_LOSS", "DECISION"
            outcome_reason = "decision_not_started_before_terminal"
            for row in decisions:
                start = _row_time(row, "decision_started_at")
                finish = _row_time(row, "decision_finished_at")
                if exact_deadline is not None and start is not None \
                        and finish is not None:
                    if start < float(exact_deadline) <= finish:
                        outcome_reason = "decision_deadline_expired_during_compute"
                        break
            loss_reason = outcome_reason
        elif confirms or misses or terminals:
            outcome, loss_stage = "CLIENT_LOSS", "CONFIRM"
            loss_reason = "server_timeout_before_authoritative_confirm"
            for row in reversed(confirms + terminals + misses):
                reason = str(row.get("reason") or "")
                if reason.startswith("confirm_"):
                    loss_reason = reason
                    break
                if reason.startswith("server_timeout_"):
                    loss_reason = "server_timeout_before_authoritative_confirm"
                    break
        else:
            outcome, loss_stage, loss_reason = "UNKNOWN", "UNKNOWN", None

        evidence_quality = "authoritative_identity"
        if exact_deadline is not None and (authorizations or confirms):
            evidence_quality = "authoritative_identity_full_timing"
        elif not confirms and not authorizations:
            evidence_quality = "authoritative_identity_partial_timing"
        resolution = {
            "window_id": wid,
            "window_attempt_key": (first.get("window_attempt_key")
                                    or {"window_id": wid, "phase": phase}),
            "phase": phase,
            "identity_status": wid.get("identity_status"),
            "identity_origin": wid.get("identity_origin"),
            "first_seen_via": wid.get("first_seen_via"),
            "outcome": outcome,
            "loss_stage": loss_stage,
            "loss_reason": loss_reason,
            "authoritative_open": authoritative_open,
            "confirm_logical_request_id": next(
                (row.get("logical_request_id") for row in confirms
                 if row.get("logical_request_id") is not None), None),
            "authorization_snapshot_seq": authorization_seq,
            "decision_id": decision_id,
            "decision_action": decision_action,
            "action_posted": bool(actions),
            "action_status": next((row.get("status") for row in actions
                                    if row.get("status") is not None), None),
            "exact_deadline_at": exact_deadline,
            "terminal_reason": terminal_reason,
            "evidence_quality": evidence_quality,
            "record_indexes": [index for index, _row in entries],
        }
        resolutions.append(resolution)
    return resolutions, identity_unverifiable


def _recovery_chains(records, action_rows, gid=None):
    """Link response-action errors to the first following RESYNC evidence."""
    if gid is None:
        gid = next((item.get("gid") for item in records
                    if item.get("type") == "meta" and item.get("gid")),
                   None)
    chains = []
    duplicate_post_after_409 = 0
    duplicate_post_after_uncertain = 0
    for index, row in action_rows:
        status = row.get("status")
        outcome = row.get("outcome") or row.get("post_status")
        if status != 409 and outcome != "POST_UNCERTAIN":
            continue
        key = _row_attempt_key(row)
        resync = None
        terminal_snapshot = None
        for later in records[index + 1:]:
            if later.get("type") == "req":
                requested = later.get("requested_seq", later.get("seq"))
                if requested == 0 or later.get("request_kind") == "RESYNC":
                    resync = later
                    break
            if later.get("type") == "end":
                break
        if resync is not None:
            start = records.index(resync, index + 1)
            for later in records[start + 1:]:
                if later.get("type") == "snapshot":
                    terminal_snapshot = later
                    break
                if later.get("type") == "end":
                    break
        duplicates = 0
        if key is not None:
            for later in records[index + 1:]:
                if later.get("type") != "action" \
                        or _row_attempt_key(later) != key:
                    continue
                payload = later.get("payload") or {}
                if payload.get("action") != "pass":
                    duplicates += 1
        if status == 409:
            duplicate_post_after_409 += duplicates
        else:
            duplicate_post_after_uncertain += duplicates
        transport = row.get("transport") or {}
        attempts = transport.get("action_attempts") or []
        window_id = _window_id_from_row(row)
        chain = {
            "gid": row.get("gid") or gid or (window_id or {}).get("game_id"),
            "status": status,
            "outcome": outcome,
            "window_id": window_id,
            "window_attempt_key": row.get("window_attempt_key"),
            "phase": row.get("phase"),
            "source_discard_seq": (_window_id_from_row(row) or {}).get(
                "source_discard_seq"),
            "authorization_snapshot_seq": row.get(
                "authorization_snapshot_seq"),
            "authorization_age_ms": row.get("authorization_age_ms"),
            "deadline_left_at_send_ms": row.get(
                "deadline_left_at_send_ms"),
            "deadline_left_at_response_ms": row.get(
                "deadline_left_at_response_ms"),
            "action_http_ms": row.get("latency_ms"),
            "queue_ms": [item.get("queue_wait_ms") for item in attempts
                         if item.get("queue_wait_ms") is not None],
            "backoff_ms": transport.get("backoff_ms"),
            "server_trace_id": row.get("server_trace_id") or next(
                (item.get("server_trace_id") for item in reversed(attempts)
                 if item.get("server_trace_id") is not None), None),
            "resync_seq": (resync.get("requested_seq", resync.get("seq"))
                           if resync is not None else None),
            "resync_phase": ((terminal_snapshot.get("snap") or {}).get(
                "phase") if terminal_snapshot is not None else None),
            "final_meld_observed": terminal_snapshot is not None,
            "resync_linked": resync is not None,
            "duplicate_old_action_posts": duplicates,
        }
        chains.append(chain)
    return chains, duplicate_post_after_409, duplicate_post_after_uncertain


CLAIM_MISS_CATEGORIES = (
    "success", "strategy_pass", "opponent_preempted",
    "unsubmitted_candidate", "rejected", "uncertain", "unknown")


def _window_claim_evidence(claim_events, attempt_key, my_seat):
    """Echo evidence for one discard window: "mine" / "opponent" / None.

    A chi/peng/gang event claiming the window tile after the source discard
    seq in the same round.  "mine" is a server-accepted action echo; absence
    of an echo never downgrades a recorded transport outcome.
    """
    if attempt_key is None:
        return None
    game_id, round_id, owner, source_seq, tile, _phase = attempt_key
    if source_seq is None or tile is None:
        return None
    result = None
    for event in claim_events:
        if (event["tile"] == tile and event["round"] == round_id
                and event["seq"] is not None and event["seq"] > source_seq):
            if my_seat is not None and event["seat"] == my_seat:
                return "mine"
            if event["seat"] != owner:
                result = "opponent"
    return result


def _classify_claim_misses(miss_rows, action_outcomes, decision_actions,
                           claim_events, my_seat):
    """Per-window claim_miss classification (plan §6: raw counts preserved).

    Categories in precedence order:
    - success: same-attempt action HTTP 200, or a server echo of our claim.
    - rejected / uncertain: POST answered 409/INVALID_ACTION, or transport
      terminal with unknown result.
    - strategy_pass: a recorded decision chose PASS for the same attempt.
    - opponent_preempted: an opponent claimed the same discard window
      (rule priority ended our chance; not a client loss).
    - unsubmitted_candidate: legal non-pass candidate existed but no
      non-pass POST was formed.
    - unknown: no window identity on the record (cannot join evidence).
    """
    counts = Counter()
    for row in miss_rows:
        reason = row.get("reason") or ""
        key = _claim_miss_attempt_key(row)
        if key is None:
            counts["unknown"] += 1
            continue
        claim_side = _window_claim_evidence(claim_events, key, my_seat)
        if action_outcomes.get(key) == "ok" or claim_side == "mine":
            counts["success"] += 1
        elif reason.startswith("action_rejected"):
            counts["rejected"] += 1
        elif reason.startswith("action_uncertain"):
            counts["uncertain"] += 1
        elif -1 in decision_actions.get(key, set()):
            counts["strategy_pass"] += 1
        elif claim_side == "opponent":
            counts["opponent_preempted"] += 1
        else:
            counts["unsubmitted_candidate"] += 1
    return counts


def _request_group(rows):
    timings = [_request_timing(row) for row in rows]
    queue_sources = Counter(item["queue_source"] for item in timings)
    known_queue = [item["queue_total_ms"] for item in timings
                   if item["queue_total_ms"] is not None]
    known_http = [item["http_total_ms"] for item in timings
                  if item["http_total_ms"] is not None]
    known_backoff = [item["backoff_ms"] for item in timings
                     if item["backoff_ms"] is not None]
    known_residual = [item["residual_ms"] for item in timings
                      if item["residual_ms"] is not None]
    return {
        "requests": len(rows),
        "queue_ms": distribution(known_queue),
        "queue_ms_known": len(known_queue),
        "queue_ms_unknown": len(rows) - len(known_queue),
        "queue_source": dict(queue_sources),
        "http_ms": distribution(known_http),
        "backoff_ms": distribution(known_backoff),
        "residual_ms": distribution(known_residual),
        "latency_ms": distribution([
            float(r.get("latency_ms")) for r in rows
            if r.get("latency_ms") is not None]),
        "retry_429": sum(r.get("transport", {}).get("retry_429", 0)
                          for r in rows),
        "deadline_missed": sum(bool(
            r.get("throttle", {}).get("deadline_missed"))
            for r in rows),
    }


def _request_groups_by_kind(rows):
    grouped = defaultdict(list)
    for row in rows:
        grouped[row.get("request_kind", "UNKNOWN_LEGACY")].append(row)
    return {kind: _request_group(items)
            for kind, items in sorted(grouped.items())}


TRANSPORT_EVIDENCE_CLASSES = (
    "QUEUE_LATE",
    "HTTP_RESPONSE_LATE",
    "RETRY_BACKOFF_CONTRIBUTED",
    "SERVER_GATEWAY_LATE",
    "CLIENT_TRANSPORT_LATE",
    "UNRESOLVED",
)


def _load_external_transport_rows(paths):
    """Load optional gateway/server JSONL evidence without guessing joins."""
    rows = []
    for path in paths or []:
        path = Path(path)
        with path.open(encoding="utf8") as stream:
            for line_number, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    rows.append({
                        "_source_file": str(path),
                        "_source_line": line_number,
                        "_input_error": "invalid_json",
                    })
                    continue
                values = value if isinstance(value, list) else [value]
                for item in values:
                    if not isinstance(item, dict):
                        rows.append({
                            "_source_file": str(path),
                            "_source_line": line_number,
                            "_input_error": "record_not_object",
                        })
                        continue
                    record = dict(item)
                    record.setdefault("_source_file", str(path))
                    record.setdefault("_source_line", line_number)
                    rows.append(record)
    return rows


def _external_value(row, names):
    for name in names:
        value = row.get(name)
        if value is not None and value != "":
            return value
    return None


def _external_gid(row):
    return _external_value(row, ("gid", "game_id"))


def _external_logical_id(row):
    return _external_value(row, (
        "logical_request_id", "client_request_id", "request_id"))


def _external_trace_id(row):
    return _external_value(row, (
        "server_trace_id", "trace_id", "request_trace_id"))


def _same_external_gid(detail, row):
    detail_gid = detail.get("gid")
    row_gid = _external_gid(row)
    return (detail_gid is None or row_gid is None or detail_gid == row_gid)


def _external_join(detail, all_details, external_rows):
    """Join one attempt by explicit id only; return ambiguity explicitly."""
    if not external_rows:
        return {"status": "not_provided", "rows": []}
    logical_id = detail.get("logical_request_id")
    attempt_index = detail.get("attempt_index")
    trace_id = detail.get("server_trace_id")
    local_logical_count = sum(
        item.get("logical_request_id") == logical_id
        and _same_external_gid(detail, item)
        for item in all_details)
    candidates = []
    for row in external_rows:
        if row.get("_input_error") or not _same_external_gid(detail, row):
            continue
        row_trace = _external_trace_id(row)
        row_logical = _external_logical_id(row)
        row_attempt = row.get("attempt_index")
        matched_by = None
        if trace_id is not None and row_trace == trace_id:
            matched_by = "server_trace_id"
        elif (logical_id is not None and row_logical == logical_id
              and row_attempt is not None
              and str(row_attempt) == str(attempt_index)):
            matched_by = "logical_request_id+attempt_index"
        elif (logical_id is not None and row_logical == logical_id
              and row_attempt is None and local_logical_count == 1):
            matched_by = "logical_request_id"
        if matched_by is not None:
            candidates.append((matched_by, row))
    unique = []
    seen = set()
    for matched_by, row in candidates:
        marker = id(row)
        if marker not in seen:
            seen.add(marker)
            unique.append((matched_by, row))
    if not unique:
        return {
            "status": ("missing_external" if logical_id is not None
                       or trace_id is not None else "missing_local_correlation"),
            "rows": [],
        }
    if len(unique) > 1:
        return {
            "status": "duplicate_match",
            "rows": [row for _matched_by, row in unique],
        }
    matched_by, row = unique[0]
    return {"status": "matched", "matched_by": matched_by, "rows": [row]}


def _external_response_epoch(row):
    value = _external_value(row, (
        "response_body_finished_epoch", "response_finished_epoch",
        "response_headers_epoch", "server_response_epoch"))
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _external_server_late(row, exact_deadline_at):
    if exact_deadline_at is None:
        return False
    clock_domain = str(row.get("clock_domain") or "unknown").lower()
    clock_sync = str(row.get("clock_sync")
                     or row.get("clock_quality") or "unknown").lower()
    if clock_domain not in ("epoch", "wall_epoch", "unix_epoch") \
            or clock_sync not in ("synchronized", "trusted"):
        return False
    response_epoch = _external_response_epoch(row)
    try:
        return response_epoch is not None and response_epoch >= float(
            exact_deadline_at)
    except (TypeError, ValueError):
        return False


def _transport_attribution(diagnostic, external_rows=None):
    """Resolve transport evidence without inferring an external root cause."""
    transport = diagnostic.get("transport") or {}
    details = [
        attempt
        for request in transport.get("request_details", [])
        for attempt in request.get("attempts", [])
    ]
    external_rows = external_rows or []
    joins = []
    server_late = False
    for attempt in details:
        joined = _external_join(attempt, details, external_rows)
        row = (joined.get("rows") or [None])[0]
        late = bool(row is not None and _external_server_late(
            row, diagnostic.get("exact_deadline_at")))
        server_late = server_late or late
        joins.append({
            "logical_request_id": attempt.get("logical_request_id"),
            "attempt_index": attempt.get("attempt_index"),
            "record_status": attempt.get("status"),
            "join_status": joined.get("status"),
            "matched_by": joined.get("matched_by"),
            "server_trace_id": attempt.get("server_trace_id"),
            "external_rows": [
                {
                    "source": item.get("source"),
                    "source_file": item.get("_source_file"),
                    "source_line": item.get("_source_line"),
                    "clock_domain": item.get("clock_domain"),
                    "clock_sync": item.get("clock_sync",
                                         item.get("clock_quality")),
                    "server_response_epoch": _external_response_epoch(item),
                    "server_late_after_deadline": _external_server_late(
                        item, diagnostic.get("exact_deadline_at")),
                }
                for item in joined.get("rows", [])
            ],
        })

    secondary = []
    if transport.get("retry_or_backoff"):
        secondary.append("RETRY_BACKOFF_CONTRIBUTED")
    if transport.get("queue_ms", {}).get("n"):
        secondary.append("QUEUE_OBSERVED")
    if transport.get("response_late"):
        secondary.append("HTTP_RESPONSE_OBSERVED")
    if server_late:
        primary = "SERVER_GATEWAY_LATE"
    elif transport.get("queue_late"):
        primary = "QUEUE_LATE"
    elif transport.get("client_read_late"):
        primary = "CLIENT_TRANSPORT_LATE"
    elif transport.get("response_late") \
            or transport.get("response_after_exact_deadline"):
        primary = "HTTP_RESPONSE_LATE"
    elif transport.get("retry_or_backoff"):
        primary = "RETRY_BACKOFF_CONTRIBUTED"
    else:
        primary = "UNRESOLVED"
    join_statuses = Counter(join.get("join_status") for join in joins)
    if not external_rows:
        correlation_quality = "not_provided"
    elif join_statuses.get("duplicate_match"):
        correlation_quality = "ambiguous"
    elif join_statuses.get("matched"):
        correlation_quality = ("matched" if len(join_statuses) == 1
                               else "partial")
    else:
        correlation_quality = "missing"
    missing = []
    if external_rows and not join_statuses.get("matched"):
        missing.append("external_timing_join")
    if diagnostic.get("exact_deadline_at") is None:
        missing.append("exact_deadline_at")
    return {
        "primary_class": primary,
        "secondary_contributors": sorted(set(secondary)),
        "window_miss_proven": primary in (
            "QUEUE_LATE", "HTTP_RESPONSE_LATE", "SERVER_GATEWAY_LATE",
            "CLIENT_TRANSPORT_LATE"),
        "correlation_quality": correlation_quality,
        "join_status_counts": dict(join_statuses),
        "missing_evidence": sorted(set(missing)),
        "joins": joins,
    }


def _transport_diagnostic_summary(diagnostics, external_rows):
    records = []
    for diagnostic in diagnostics:
        if diagnostic.get("category") not in (
                "C4_CONFIRM_HTTP_LATE", "UNRESOLVED"):
            continue
        attribution = _transport_attribution(diagnostic, external_rows)
        diagnostic["transport_attribution"] = attribution
        record = {
            "window_attempt_key": diagnostic.get("window_attempt_key"),
            "category": diagnostic.get("category"),
            "primary_class": attribution["primary_class"],
            "window_miss_proven": attribution["window_miss_proven"],
            "correlation_quality": attribution["correlation_quality"],
            "secondary_contributors": attribution[
                "secondary_contributors"],
            "missing_evidence": attribution["missing_evidence"],
        }
        records.append(record)
    joins = Counter()
    classes = Counter()
    for record in records:
        classes[record["primary_class"]] += 1
        joins[record["correlation_quality"]] += 1
    return {
        "classes": list(TRANSPORT_EVIDENCE_CLASSES),
        "counts": dict(classes),
        "correlation_quality": dict(joins),
        "external_timing": {
            "provided": bool(external_rows),
            "rows": len(external_rows),
            "invalid_rows": sum("_input_error" in row
                                 for row in external_rows),
        },
        "records": records,
    }


def summarize(paths, *, acceptance_scope="unspecified", commit=None,
              transport_logs=None):
    types, actions, misses, confirms, transport = (Counter() for _ in range(5))
    gap_reasons = Counter()
    gap_impacts = Counter()
    strong_gap_risks = 0
    requests = []
    diagnostic_attempts = defaultdict(list)
    games = []
    room_request_rows = []
    room_miss_counts = []
    room_confirm_counts = []
    room_claim_classifications = []
    audit_totals = Counter()
    boundary_recovered = 0
    external_transport_rows = _load_external_transport_rows(transport_logs)
    for path in paths:
        with path.open(encoding="utf8") as stream:
            records = [json.loads(line) for line in stream if line.strip()]
        audit = Counter()
        bot = BotClient(None, "audit", None)
        mirror = None
        cursor = None
        decision_keys, successful_keys, boundary_keys = {}, set(), []
        rounds = set()
        room_requests = []
        room_physical = []
        room_physical_count = 0
        room_confirms = Counter()
        room_misses = Counter()
        room_confirm_keys = set()
        room_eligible_keys = set()
        room_confirm_seq0 = 0
        room_confirm_physical = 0
        room_id = None
        end_row = None
        demand_end = None
        request_demands = []
        room_gaps = []
        room_miss_rows = []
        room_action_outcomes = {}
        room_decision_actions = {}
        room_claim_events = []
        my_seat = None
        room_legacy_eligible_keys = set()
        room_identity_origins = Counter()
        room_first_seen_via = Counter()
        room_409_rows = []
        room_uncertain_rows = []
        for index, row in enumerate(records):
            kind = row["type"]
            types[kind] += 1
            if kind == "meta":
                room_id = row.get("tid") or row.get("room_id")
                bot.base = row.get("base", 1)
                bot.you_cai_bi_kao = bool(row.get("you_cai_bi_kao"))
            elif kind == "snapshot":
                mirror = bot._mirror_from_snapshot(row["snap"])
                cursor = row.get("seq")
                rounds.add(mirror.round_no)
                my_seat = row["snap"].get("seat", my_seat)
            elif kind == "events":
                cursor = row.get("seq_to")
                for event in row["events"]:
                    audit["events_recorded"] += 1
                    if event.get("type") == "round_ended":
                        audit["round_ended_recorded"] += 1
                    event_round = mirror.round_no if mirror is not None else None
                    if event.get("type") in ("chi", "peng", "gang"):
                        room_claim_events.append({
                            "seq": event.get("seq"),
                            "round": event_round,
                            "seat": event.get("seat"),
                            "tile": _protocol_tile_int(event.get("tile")),
                        })
                    if mirror is not None:
                        try:
                            mirror.apply_event(event)
                        except MirrorInconsistent:
                            audit["mirror_errors"] += 1
                            mirror = None
            elif kind == "decision":
                audit["decisions_recorded"] += 1
                attempt_key = _claim_miss_attempt_key(row)
                if attempt_key is not None:
                    room_decision_actions.setdefault(
                        attempt_key, set()).add(row.get("action"))
                eligible_key = _authoritative_eligible_window_key(row)
                if eligible_key is not None:
                    room_eligible_keys.add(eligible_key)
                eligible_key, identity_kind = _eligible_window_identity(row)
                if eligible_key is not None:
                    if identity_kind == "legacy":
                        room_legacy_eligible_keys.add(eligible_key)
                    window_id = _window_id_from_row(row) or {}
                    room_identity_origins[window_id.get(
                        "identity_origin", "unknown")] += 1
                    room_first_seen_via[window_id.get(
                        "first_seen_via", "unknown")] += 1
                if mirror is None:
                    audit["decisions_without_mirror"] += 1
                    continue
                # Historical mirror discard counts may differ after reanchor.
                # Same authoritative seq plus round/pending is stronger evidence
                # for these immediate recoveries; do not merge later same tiles.
                decision_keys[row["id"]] = (
                    row["phase"], mirror.round_no, mirror.pending, row.get("seq"))
                try:
                    legal = sorted(mirror.build_game(row["phase"]).legal_actions())
                except MirrorInconsistent:
                    audit["decisions_unbuildable"] += 1
                else:
                    audit["decisions_checked"] += 1
                    if legal != row["legal"]:
                        audit["legal_mismatches"] += 1
            elif kind == "reset":
                mirror = None
            elif kind == "req":
                requests.append(row)
                room_requests.append(row)
                pending_reasons = row.get("reason") or []
                requested_seq = row.get("requested_seq", row.get("seq"))
                request_kind = row.get("request_kind")
                confirm_kind = request_kind in (
                    "WINDOW_CHI", "WINDOW_PENG", "WINDOW_CONFIRM")
                legacy_confirm_reason = (
                    request_kind is None
                    and "WINDOW_CONFIRM" in pending_reasons)
                if (requested_seq == 0
                        and (confirm_kind or legacy_confirm_reason)):
                    room_confirm_seq0 += 1
                    transport_meta = row.get("transport") or {}
                    state_attempts = transport_meta.get("state_attempts") or []
                    if state_attempts:
                        room_confirm_physical += len(state_attempts)
                    else:
                        # Older logs may retain only the cumulative physical
                        # count.  Use it as a count, never as a fake attempt
                        # detail list.
                        try:
                            room_confirm_physical += int(
                                transport_meta.get(
                                    "state_physical_attempts") or 0)
                        except (TypeError, ValueError):
                            pass
                if isinstance(row.get("demand"), dict):
                    request_demands.append(row["demand"])
                gap = _gap_record(records, index, row)
                if gap is not None:
                    room_gaps.append(gap)
                    gap_reasons[gap["reason"]] += 1
                    impact = gap["decision_impact"]
                    gap_impacts["true" if impact is True else
                                 "false" if impact is False else "unknown"] += 1
                    if (gap["reason"] == "event_discontinuity"
                            and gap["decision_impact"]):
                        strong_gap_risks += 1
                    audit["gaps"] += 1
                for name in ("retry_429", "retry_gateway", "retry_network"):
                    transport[name] += row.get("transport", {}).get(name, 0)
                meta = row.get("transport", {})
                diagnostic_attempts["state"].extend(
                    meta.get("state_attempts", []))
                physical_rows = meta.get("state_attempts", [])
                room_physical.extend(physical_rows)
                physical_count = meta.get(
                    "state_physical_attempts", row.get("attempts") or 1)
                try:
                    room_physical_count += int(physical_count or 0)
                except (TypeError, ValueError):
                    pass
                transport["physical_attempts"] += physical_count
            elif kind == "action":
                payload = row.get("payload", {}).get("action", "unknown")
                actions[f'{row.get("phase")}/{payload}/{"ok" if row.get("ok") else "failed"}'] += 1
                diagnostic_attempts["action"].extend(
                    row.get("transport", {}).get("action_attempts", []))
                attempt_key = _claim_miss_attempt_key(row)
                if attempt_key is not None:
                    if row.get("ok"):
                        room_action_outcomes[attempt_key] = "ok"
                    else:
                        room_action_outcomes.setdefault(attempt_key, "failed")
                if row.get("status") == 409:
                    room_409_rows.append((index, row))
                if (row.get("outcome") == "POST_UNCERTAIN"
                        or row.get("post_status") == "POST_UNCERTAIN"):
                    room_uncertain_rows.append((index, row))
                if row.get("ok") and row.get("decision") in decision_keys:
                    successful_keys.add(decision_keys[row["decision"]])
            elif kind == "claim_miss":
                misses[f'{row.get("phase")}/{row.get("reason")}'] += 1
                room_misses[f'{row.get("phase")}/{row.get("reason")}'] += 1
                room_miss_rows.append(row)
                if row.get("reason") == "decision_boundary_resync" and mirror is not None:
                    boundary_keys.append((row["phase"], mirror.round_no,
                                          mirror.pending, row.get("seq", cursor)))
            elif kind == "window_confirm":
                outcome = row.get("outcome", row.get("stage", "unknown"))
                confirms[outcome] += 1
                room_confirms[outcome] += 1
                eligible_key = _authoritative_eligible_window_key(row)
                if eligible_key is not None:
                    room_eligible_keys.add(eligible_key)
                eligible_key, identity_kind = _eligible_window_identity(row)
                if eligible_key is not None:
                    if identity_kind == "legacy":
                        room_legacy_eligible_keys.add(eligible_key)
                    window_id = _window_id_from_row(row) or {}
                    room_identity_origins[window_id.get(
                        "identity_origin", "unknown")] += 1
                    room_first_seen_via[window_id.get(
                        "first_seen_via", "unknown")] += 1
                if outcome == "requested":
                    room_confirm_keys.add((
                        row.get("logical_request_id"),
                        json.dumps(row.get("window_id"), sort_keys=True,
                                   ensure_ascii=False),
                        row.get("phase")))
            elif kind in ("window_authorization", "window_lifecycle",
                          "window_terminal"):
                eligible_key, identity_kind = _eligible_window_identity(row)
                if eligible_key is not None:
                    if identity_kind == "legacy":
                        room_legacy_eligible_keys.add(eligible_key)
                    window_id = _window_id_from_row(row) or {}
                    room_identity_origins[window_id.get(
                        "identity_origin", "unknown")] += 1
                    room_first_seen_via[window_id.get(
                        "first_seen_via", "unknown")] += 1
            elif kind == "end":
                end_row = row
                demand_end = row.get("demand")
        canonical_resolutions, identity_unverifiable = (
            _canonical_window_resolutions(
                records, claim_events=room_claim_events, my_seat=my_seat))
        confirm_diagnostics = []
        for resolution in canonical_resolutions:
            diagnostic = _confirm_diagnostic(records, resolution, my_seat)
            resolution["confirm_diagnostic"] = diagnostic
            if diagnostic is not None:
                confirm_diagnostics.append(diagnostic)
        _transport_diagnostic_summary(confirm_diagnostics,
                                      external_transport_rows)
        confirm_diagnostic_counts = Counter(
            {category: 0 for category in CONFIRM_DIAGNOSTIC_CATEGORIES})
        confirm_diagnostic_counts.update(
            item["category"] for item in confirm_diagnostics)
        recovery_rows = room_409_rows + room_uncertain_rows
        recovery_chains, duplicate_post_after_409, \
            duplicate_post_after_uncertain = _recovery_chains(
                records, recovery_rows)
        boundary_recovered += sum(key in successful_keys for key in boundary_keys)
        audit_totals.update(audit)
        if isinstance(end_row, dict) and "demand" in end_row \
                and end_row.get("demand") is not None:
            demand = dict(end_row["demand"])
            # Recorder.end() preserves whether the terminal snapshot was
            # explicit or recovered from the last req.  Do not erase that
            # evidence boundary while summarizing a room.
            demand_source = end_row.get("demand_source") or "end"
        else:
            demand, demand_source = _demand_fallback(request_demands)
        request_metrics = _request_count_metrics(room_requests)
        if request_metrics["physical"] is not None:
            room_physical_count = request_metrics["physical"]
        lifecycle_logical = (demand.get("logical_state_requests")
                             if isinstance(demand, dict) else None)
        if lifecycle_logical is None and request_metrics["has_lifecycle_ids"]:
            lifecycle_logical = request_metrics["logical"]
        lifecycle_physical = (demand.get("physical_state_attempts")
                              if isinstance(demand, dict) else None)
        if (lifecycle_physical is None
                and (request_metrics["has_lifecycle_ids"]
                     or request_metrics["has_physical_id_evidence"])):
            lifecycle_physical = request_metrics["physical"]
        demand_reasons = (demand.get("reasons")
                          if isinstance(demand, dict) else None)
        dirty_reasons = []
        if isinstance(demand_reasons, dict):
            dirty_reasons = [reason for reason, data in demand_reasons.items()
                             if isinstance(data, dict)
                             and data.get("status") == "PENDING"]
        demand_terminal_clean = (
            bool(demand)
            and not dirty_reasons
            and not bool((demand or {}).get("in_flight"))
            and (demand.get("reason_mask") in (0, None)))
        demand_terminal_status = (
            "clean" if demand_terminal_clean
            else "dirty" if demand else "missing")
        replay = replay_game(records, want_samples=False)
        end_reason = (end_row or {}).get("reason")
        has_end = end_row is not None
        explicit_status = end_row or {}
        terminal_end = end_reason in ("finished", "inaccessible", "closed", "void")
        transport_status = explicit_status.get("transport_status") or (
            "complete" if terminal_end and not explicit_status.get("error")
            else "partial")
        # A terminal marker alone does not prove that the state coordinator
        # drained. A missing or dirty demand snapshot leaves transport
        # evidence incomplete as well as window evidence incomplete.
        if demand_terminal_status != "clean":
            transport_status = "partial"
        explicit_window_status = explicit_status.get("window_status")
        legacy_windows = len(room_legacy_eligible_keys)
        authoritative_windows = len(room_eligible_keys)
        eligible_windows_total = authoritative_windows + legacy_windows
        canonical_unknown = sum(
            resolution.get("outcome") == "UNKNOWN"
            for resolution in canonical_resolutions)
        strong_gap_room = bool(room_gaps and any(
            gap["reason"] == "event_discontinuity"
            and gap["decision_impact"] is True for gap in room_gaps))
        if legacy_windows:
            window_status = "window_partial_identity"
        elif demand_terminal_status == "dirty" or strong_gap_room \
                or canonical_unknown:
            window_status = "partial"
        else:
            window_status = explicit_window_status or (
                "complete" if end_reason == "finished"
                else "partial")
        reported_game_status = explicit_status.get("game_status")
        # One round_ended event is not enough to certify a multi-round game.
        # A marker-backed game is complete only when every observed round has
        # a settlement marker; a terminal log with no markers is the separate
        # protocol_skipped case.  Missing markers after a partial stream stay
        # partial instead of being upgraded by an explicit end status.
        settlement_complete = bool(audit["round_ended_recorded"]) and (
            not rounds or audit["round_ended_recorded"] >= len(rounds))
        if settlement_complete:
            game_status = reported_game_status or "complete"
        elif audit["round_ended_recorded"]:
            game_status = "partial"
        elif has_end and terminal_end and not explicit_status.get("error"):
            # An explicit client "complete" cannot replace missing settlement
            # evidence. Preserve it separately for diagnosis.
            game_status = "protocol_skipped"
        else:
            game_status = reported_game_status or "partial"
        claim_categories = _classify_claim_misses(
            room_miss_rows, room_action_outcomes, room_decision_actions,
            room_claim_events, my_seat)
        claim_classification = {
            "raw_record_count": len(room_miss_rows),
            "window_linked": sum(
                1 for miss_row in room_miss_rows
                if _claim_miss_attempt_key(miss_row) is not None),
            "by_category": {category: claim_categories.get(category, 0)
                            for category in CLAIM_MISS_CATEGORIES},
        }
        canonical_counts = Counter(
            resolution.get("outcome") for resolution in canonical_resolutions)
        false_claim_miss = 0
        for miss_row in room_miss_rows:
            key = _row_attempt_key(miss_row)
            if key is None:
                continue
            matching = [item for item in canonical_resolutions
                        if item.get("window_attempt_key", {}).get("phase")
                        == key[-1]
                        and _claim_miss_attempt_key(item) == key]
            if matching and matching[0].get("outcome") in (
                    "SUCCESS", "STRATEGY_PASS", "RULE_PREEMPTED"):
                false_claim_miss += 1
        canonical_client_loss = sum(
            item.get("outcome") in ("CLIENT_LOSS", "POST_REJECTED",
                                     "POST_UNCERTAIN")
            for item in canonical_resolutions)
        functional_failures = [item for item in canonical_resolutions
                               if item.get("outcome") in (
                                   "CLIENT_LOSS", "UNKNOWN")]
        functional_status = (
            "fail" if functional_failures or duplicate_post_after_409
            else "explained_failures" if canonical_counts.get(
                "POST_REJECTED", 0) or canonical_counts.get(
                "POST_UNCERTAIN", 0)
            else "pass")
        window_evidence_status = (
            "partial_identity" if legacy_windows
            else "partial" if (canonical_unknown or strong_gap_room
                                or demand_terminal_status == "dirty")
            else "complete" if end_reason in ("finished", "inaccessible",
                                               "closed", "void")
            else "partial")
        room_request_rows.append(room_requests)
        room_miss_counts.append(room_misses)
        room_confirm_counts.append(room_confirms)
        room_claim_classifications.append(claim_classification)
        games.append({"file": str(path), "room_id": room_id,
            "reported_game_status": reported_game_status,
            "end": end_reason,
            "status": {"transport_status": transport_status,
                       "window_status": window_status,
                       "game_status": game_status},
            "rounds_in_snapshots": sorted(rounds), "coverage": dict(audit),
            "replay_illegal": len(replay["illegal"]),
            "replay_warnings": replay["warnings"],
            "replay_rounds_settled": replay["n_rounds"],
            "replay_clean": replay["clean"],
            "eligible_windows": len(room_eligible_keys),
            "eligible_windows_authoritative": len(room_eligible_keys),
            "eligible_windows_total": eligible_windows_total,
            "legacy_eligible_windows": legacy_windows,
            "authoritative_identity_coverage": (
                authoritative_windows / eligible_windows_total
                if eligible_windows_total else None),
            "identity_origins": dict(room_identity_origins),
            "first_seen_via": dict(room_first_seen_via),
            "canonical_resolutions": canonical_resolutions,
            "confirm_diagnostics": confirm_diagnostics,
            "confirm_diagnostic_counts": dict(confirm_diagnostic_counts),
            "canonical_outcomes": dict(canonical_counts),
            "canonical_client_loss_count": canonical_client_loss,
            "raw_claim_miss_count": len(room_miss_rows),
            "false_claim_miss_count": false_claim_miss,
            "identity_unverifiable_count": len(identity_unverifiable),
            "identity_unverifiable": identity_unverifiable,
            "window_evidence_status": window_evidence_status,
            "window_functional_status": functional_status,
            "all_action_409": sum(chain.get("status") == 409
                                    for chain in recovery_chains),
            "window_409_count": sum(
                chain.get("status") == 409
                and chain.get("phase") in ("response_peng", "response_chi")
                for chain in recovery_chains),
            "normal_action_409": sum(
                chain.get("status") == 409
                and chain.get("phase") not in ("response_peng", "response_chi")
                for chain in recovery_chains),
            "window_409_chains": [chain for chain in recovery_chains
                                  if chain.get("phase") in (
                                      "response_peng", "response_chi")],
            "normal_409_chains": [chain for chain in recovery_chains
                                  if chain.get("status") == 409
                                  and chain.get("phase") not in (
                                      "response_peng", "response_chi")],
            "recovery_chains": recovery_chains,
            "post_uncertain_count": len(room_uncertain_rows),
            "duplicate_post_after_409": duplicate_post_after_409,
            "duplicate_post_after_uncertain": duplicate_post_after_uncertain,
            "demand_terminal_status": demand_terminal_status,
            "demand_dirty_reasons": dirty_reasons,
            "window_confirm_seq0": room_confirm_seq0,
            "window_confirm_physical_attempts": room_confirm_physical,
            "demand": demand,
            "demand_source": demand_source,
            "gap_counts": dict(Counter(gap["reason"] for gap in room_gaps)),
            "gap_decision_impact": sum(
                gap["decision_impact"] is True for gap in room_gaps),
            "gap_decision_impact_unknown": sum(
                gap["decision_impact"] is None for gap in room_gaps),
            "transport_requests": request_metrics["logical"],
            "transport_physical_attempts": room_physical_count,
            "logical_state_requests": lifecycle_logical,
            "physical_state_attempts": lifecycle_physical,
            "substituted_candidates": (
                demand.get("substituted_candidates")
                if isinstance(demand, dict) else None),
            "cancelled_before_send": (
                demand.get("cancelled_before_send")
                if isinstance(demand, dict) else None),
            "request_id_metrics": request_metrics,
            "transport_summary": _request_group(room_requests),
            "state_by_kind": _request_groups_by_kind(room_requests),
            "claim_miss_classification": claim_classification})

    by_kind = defaultdict(list)
    physical = []
    for row in requests:
        by_kind[row.get("request_kind", "UNKNOWN_LEGACY")].append(row)
        physical.extend(row.get("transport", {}).get("state_attempts", []))
    starts = sorted(item["started_epoch"] for item in physical
                    if item.get("started_epoch") is not None)
    left, peak = 0, 0
    for right, stamp in enumerate(starts):
        while starts[left] <= stamp - 1.0:
            left += 1
        peak = max(peak, right - left + 1)

    def diagnostic_group(items):
        status = Counter(str(item.get("status")) for item in items)
        total = [item.get("timing", {}).get("total_ms")
                 for item in items
                 if item.get("timing", {}).get("total_ms") is not None]
        pre_read = [item.get("timing", {}).get("pre_read_ms")
                    for item in items
                    if item.get("timing", {}).get("pre_read_ms") is not None]
        read = [item.get("timing", {}).get("read_ms")
                for item in items
                if item.get("timing", {}).get("read_ms") is not None]
        retry_after = [item.get("retry_after_s") for item in items
                       if item.get("retry_after_s") is not None]
        queue = []
        for item in items:
            throttle = item.get("throttle") or {}
            value = throttle.get("queue_wait_ms", item.get("queue_wait_ms"))
            if value is not None:
                queue.append(value)
        return {
            "attempts": len(items),
            "status": dict(status),
            "total_ms": distribution(total),
            "pre_read_ms": distribution(pre_read),
            "read_ms": distribution(read),
            "timed_out": sum(bool(item.get("timed_out")) for item in items),
            "retry_after_s": distribution(retry_after),
            "queue_ms": distribution(queue),
        }

    def layer_room_indexes(status_key):
        return [i for i, room in enumerate(games)
                if room.get("status", {}).get(status_key) == "complete"]

    def demand_totals(indexes):
        totals = Counter()
        missing = set()
        for index in indexes:
            demand = games[index].get("demand") or {}
            for key in ("logical_demands", "coalesced_demands",
                        "logical_input_demands", "successor_requests",
                        "physical_state_requests", "logical_state_requests",
                        "physical_state_attempts", "substituted_candidates",
                        "cancelled_before_send", "suppressed_duplicates",
                        "coalesced_or_suppressed"):
                value = demand.get(key)
                if value is None:
                    missing.add(key)
                    continue
                try:
                    totals[key] += int(value)
                except (TypeError, ValueError):
                    missing.add(key)
        logical = totals["logical_demands"]
        physical_requests = totals["physical_state_requests"]
        totals["coalescing_ratio"] = (
            round(1.0 - physical_requests / logical, 6)
            if logical and not ({"logical_demands",
                                 "physical_state_requests"} & missing)
            else None)
        logical_input = totals.get("logical_input_demands")
        if logical_input:
            totals["coalesced_or_suppressed_ratio"] = round(
                (totals["coalesced_demands"]
                 + totals["suppressed_duplicates"]) / logical_input, 6)
        else:
            totals["coalesced_or_suppressed_ratio"] = None
        if missing:
            totals["missing_counters"] = sorted(missing)
        return dict(totals)

    def demand_source_counts(indexes):
        return dict(Counter(games[i].get("demand_source", "missing")
                            for i in indexes))

    def claim_miss_classification_totals(indexes):
        total = Counter()
        raw = 0
        linked = 0
        for index in indexes:
            classification = room_claim_classifications[index]
            raw += classification["raw_record_count"]
            linked += classification["window_linked"]
            total.update(classification["by_category"])
        return {
            "raw_record_count": raw,
            "window_linked": linked,
            "by_category": {category: total.get(category, 0)
                            for category in CLAIM_MISS_CATEGORIES},
        }

    def layer_summary(status_key):
        indexes = layer_room_indexes(status_key)
        # Request rows intentionally remain unmodified in the public report;
        # use room-local demand/attempt counts for the layer denominator.
        layer_request_count = sum(games[i]["transport_requests"]
                                  for i in indexes)
        layer_physical = sum(games[i]["transport_physical_attempts"]
                             for i in indexes)
        lifecycle_physical_values = [
            games[i].get("physical_state_attempts")
            for i in indexes
            if games[i].get("physical_state_attempts") is not None]
        layer_rows = [row for index in indexes for row in room_request_rows[index]]
        layer_misses = Counter()
        layer_confirms = Counter()
        for index in indexes:
            layer_misses.update(room_miss_counts[index])
            layer_confirms.update(room_confirm_counts[index])
        canonical_outcomes = Counter()
        canonical_losses = 0
        for index in indexes:
            canonical_outcomes.update(
                games[index].get("canonical_outcomes", {}))
            canonical_losses += games[index].get(
                "canonical_client_loss_count", 0)
        return {
            "rooms_complete": len(indexes),
            "rooms_excluded": len(games) - len(indexes),
            "denominator": len(indexes),
            "requests": layer_request_count,
            "physical_state_attempts": (
                sum(lifecycle_physical_values)
                if lifecycle_physical_values else None),
            "transport_physical_attempts": layer_physical,
            "logical_state_requests": (
                sum(games[i].get("logical_state_requests")
                    for i in indexes
                    if games[i].get("logical_state_requests") is not None)
                if any(games[i].get("logical_state_requests") is not None
                       for i in indexes) else None),
            "request_id_sources": dict(Counter(
                (games[i].get("request_id_metrics") or {}).get(
                    "source", "missing")
                for i in indexes)),
            "eligible_windows": sum(games[i]["eligible_windows"]
                                     for i in indexes),
            "window_confirm_seq0": sum(games[i]["window_confirm_seq0"]
                                        for i in indexes),
            "request_status": dict(Counter(
                row.get("status") for row in layer_rows)),
            "state": _request_group(layer_rows),
            "claim_miss_records": dict(layer_misses),
            "claim_miss_classification": claim_miss_classification_totals(
                indexes),
            "window_confirm_records": dict(layer_confirms),
            "demand": demand_totals(indexes),
            "demand_source_counts": demand_source_counts(indexes),
            "canonical_outcomes": dict(canonical_outcomes),
            "canonical_client_loss_count": canonical_losses,
        }

    all_canonical = Counter()
    all_loss_stages = Counter()
    all_recovery_chains = []
    all_hard_fail = Counter()
    all_identity_origins = Counter()
    all_first_seen = Counter()
    all_confirm_diagnostics = []
    all_confirm_diagnostic_counts = Counter(
        {category: 0 for category in CONFIRM_DIAGNOSTIC_CATEGORIES})
    for game in games:
        all_canonical.update(game.get("canonical_outcomes", {}))
        for resolution in game.get("canonical_resolutions", []):
            all_loss_stages[resolution.get("loss_stage", "UNKNOWN")] += 1
        all_recovery_chains.extend(game.get("recovery_chains", []))
        all_identity_origins.update(game.get("identity_origins", {}))
        all_first_seen.update(game.get("first_seen_via", {}))
        all_confirm_diagnostics.extend(game.get("confirm_diagnostics", []))
        all_confirm_diagnostic_counts.update(
            game.get("confirm_diagnostic_counts", {}))
        for name in ("duplicate_post_after_409",
                     "duplicate_post_after_uncertain"):
            if game.get(name, 0):
                all_hard_fail[name] += game[name]
    all_eligible = sum(game.get("eligible_windows_total", 0)
                       for game in games)
    all_authoritative = sum(game.get("eligible_windows_authoritative", 0)
                            for game in games)
    hard_fail_checks = {
        "duplicate_old_action_post": sum(all_hard_fail.values()),
        "pending_confirmation_claim_miss": sum(
            1 for game in games
            for resolution in game.get("canonical_resolutions", [])
            if resolution.get("loss_reason") == "phase_not_reached"),
        "success_or_pass_client_loss": sum(
            1 for game in games
            for resolution in game.get("canonical_resolutions", [])
            if resolution.get("outcome") in ("SUCCESS", "STRATEGY_PASS")
            and resolution.get("loss_stage") not in ("NONE", None)),
        "dirty_demand": sum(game.get("demand_terminal_status") == "dirty"
                             for game in games),
        "gap_decision_impact_risk": strong_gap_risks,
    }
    window_409_chains = [chain for chain in all_recovery_chains
                         if chain.get("status") == 409
                         and chain.get("phase") in (
                             "response_peng", "response_chi")]
    normal_409_chains = [chain for chain in all_recovery_chains
                         if chain.get("status") == 409
                         and chain.get("phase") not in (
                             "response_peng", "response_chi")]
    uncertain_chains = [chain for chain in all_recovery_chains
                        if chain.get("outcome") == "POST_UNCERTAIN"]

    return {
        "files": len(paths), "record_counts": dict(types), "actions": dict(actions),
        "independent_rooms": len({
            game.get("room_id") or game.get("file") for game in games}),
        "gids": len(games),
        "claim_miss_records": dict(misses), "window_confirm_records": dict(confirms),
        "claim_miss_classification": claim_miss_classification_totals(
            range(len(games))),
        "legacy_boundary_misses_later_succeeded": boundary_recovered,
        "gap_classification": {
            "total": sum(gap_reasons.values()),
            "by_reason": dict(gap_reasons),
            "decision_impact": dict(gap_impacts),
            "strong_risk": strong_gap_risks,
        },
        "window_identity_coverage": {
            "eligible_windows_total": all_eligible,
            "authoritative_eligible_windows": all_authoritative,
            "legacy_eligible_windows": all_eligible - all_authoritative,
            "authoritative_identity_coverage": (
                all_authoritative / all_eligible if all_eligible else None),
            "identity_origin": dict(all_identity_origins),
            "first_seen_via": dict(all_first_seen),
        },
        "window_attribution": {
            "canonical_outcomes": dict(all_canonical),
            "loss_stage": dict(all_loss_stages),
            "raw_claim_miss_count": sum(
                game.get("raw_claim_miss_count", 0) for game in games),
            "canonical_client_loss_count": sum(
                game.get("canonical_client_loss_count", 0) for game in games),
            "false_claim_miss_count": sum(
                game.get("false_claim_miss_count", 0) for game in games),
            "identity_unverifiable_count": sum(
                game.get("identity_unverifiable_count", 0) for game in games),
            "confirm_diagnostic_counts": dict(all_confirm_diagnostic_counts),
            "confirm_diagnostics": all_confirm_diagnostics,
        },
        "confirmation_diagnostics": {
            "categories": list(CONFIRM_DIAGNOSTIC_CATEGORIES),
            "counts": dict(all_confirm_diagnostic_counts),
            "reasons": dict(Counter(item.get("reason")
                                     for item in all_confirm_diagnostics)),
            "transport_contributed": sum(
                bool(item.get("transport_contributed"))
                for item in all_confirm_diagnostics),
            "records": all_confirm_diagnostics,
        },
        "transport_diagnostics": _transport_diagnostic_summary(
            all_confirm_diagnostics, external_transport_rows),
        "acceptance_scope": {
            "kind": acceptance_scope,
            "commit": commit,
            "eligible_for_final_denominator": (
                acceptance_scope == "fresh_acceptance"),
        },
        "window_409": {
            "all_action_409": len(window_409_chains) + len(normal_409_chains),
            "window_409_count": len(window_409_chains),
            "normal_action_409": len(normal_409_chains),
            "window_409_linked": sum(bool(chain.get("window_attempt_key"))
                                      for chain in window_409_chains),
            "window_409_unlinked": sum(
                not bool(chain.get("window_attempt_key"))
                for chain in window_409_chains),
            "chains": window_409_chains,
            "normal_chains": normal_409_chains,
            "uncertain_chains": uncertain_chains,
            "duplicate_post_after_409": sum(
                game.get("duplicate_post_after_409", 0) for game in games),
            "duplicate_post_after_uncertain": sum(
                game.get("duplicate_post_after_uncertain", 0)
                for game in games),
        },
        "hard_fail_checks": hard_fail_checks,
        "state_demand_terminal": {
            "clean": sum(game.get("demand_terminal_status") == "clean"
                          for game in games),
            "dirty": sum(game.get("demand_terminal_status") == "dirty"
                          for game in games),
            "missing": sum(game.get("demand_terminal_status") == "missing"
                            for game in games),
        },
        "acceptance_sections": [
            "run_manifest", "transport", "window_identity",
            "window_lifecycle", "confirm_decision_submit", "window_409",
            "retry_contribution", "gap_classification",
            "state_demand_terminal", "game_layer", "hard_fail",
            "per_room", "cross_room",
        ],
        "demand_source_counts": dict(Counter(
            game.get("demand_source", "missing") for game in games)),
        "transport": dict(transport),
        "transport_phases": {
            kind: diagnostic_group(items)
            for kind, items in sorted(diagnostic_attempts.items())
        },
        "physical_state_evidence": {
            "attempts_logged": len(physical),
            "status": dict(Counter(str(item.get("status")) for item in physical)),
            "max_starts_per_rolling_second": peak if starts else None,
            "note": "Client start times; not server arrival times. Legacy logs lack these fields.",
        },
        "state_request_metrics": {
            "logical_state_requests": (
                sum(game.get("logical_state_requests")
                    for game in games
                    if game.get("logical_state_requests") is not None)
                if any(game.get("logical_state_requests") is not None
                       for game in games) else None),
            "physical_state_attempts": (
                sum(game.get("physical_state_attempts")
                    for game in games
                    if game.get("physical_state_attempts") is not None)
                if any(game.get("physical_state_attempts") is not None
                       for game in games) else None),
            "substituted_candidates": (
                sum(game.get("substituted_candidates")
                    for game in games
                    if game.get("substituted_candidates") is not None)
                if any(game.get("substituted_candidates") is not None
                       for game in games) else None),
            "cancelled_before_send": (
                sum(game.get("cancelled_before_send")
                    for game in games
                    if game.get("cancelled_before_send") is not None)
                if any(game.get("cancelled_before_send") is not None
                       for game in games) else None),
            "metric_version": "state-request-lifecycle-v1",
            "source": (
                "recorder demand snapshots and unique request IDs"
                if any(game.get("logical_state_requests") is not None
                       or game.get("physical_state_attempts") is not None
                       for game in games)
                else "legacy_or_missing"),
            "request_id_sources": dict(Counter(
                (game.get("request_id_metrics") or {}).get(
                    "source", "missing")
                for game in games)),
        },
        "request_status": dict(Counter(r.get("status") for r in requests)),
        "state_all": _request_group(requests),
        "state_urgent": _request_group([r for r in requests
                                         if r.get("throttle", {}).get("urgent")]),
        "state_by_kind": {k: _request_group(v)
                           for k, v in sorted(by_kind.items())},
        "layer_status_counts": {
            key: dict(Counter(room["status"][key] for room in games))
            for key in ("transport_status", "window_status", "game_status")
        },
        "layer_metrics": {
            "transport": layer_summary("transport_status"),
            "window": layer_summary("window_status"),
            "game": layer_summary("game_status"),
        },
        "coverage": dict(audit_totals), "games": games,
        "limitations": [
            "Only recorded decisions and observable windows can be verified.",
            "Snapshot gaps can hide opportunities; zero misses is not proof of zero loss.",
            "Settlement coverage is limited to recorded round_ended events.",
            "Different deals and network conditions prevent causal claims from one room.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("room")
    parser.add_argument("--root", type=Path, default=Path("local/games"))
    parser.add_argument("--out", type=Path)
    parser.add_argument("--scope", choices=("unspecified", "diagnostic_baseline",
                                              "fresh_acceptance"),
                        default="unspecified",
                        help="Acceptance denominator scope for the report")
    parser.add_argument("--commit",
                        help="Commit recorded in the acceptance scope marker")
    parser.add_argument("--transport-log", action="append", type=Path,
                        default=[],
                        help="Optional gateway/server timing JSONL; repeatable")
    args = parser.parse_args()
    paths = sorted(args.root.glob(f"**/*_{args.room}_r*_b*.jsonl"))
    if not paths:
        parser.error(f"No logs for room {args.room}")
    report = summarize(paths, acceptance_scope=args.scope, commit=args.commit,
                       transport_logs=args.transport_log)
    result = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(result + "\n", encoding="utf8")
    else:
        print(result)
    return int(any(g["replay_illegal"] or g["coverage"].get("legal_mismatches")
                   for g in report["games"]))


if __name__ == "__main__":
    sys.exit(main())
