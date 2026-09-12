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
    "suppressed_duplicates", "coalesced_or_suppressed",
)


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
                       "authoritative_open", "AUTHORIZED"):
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

        authoritative_open = bool(authorizations) or any(
            row.get("outcome") in ("open", "confirmed",
                                    "authoritative_open", "AUTHORIZED")
            for row in confirms + lifecycle)
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


def _recovery_chains(records, action_rows):
    """Link response-action errors to the first following RESYNC evidence."""
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
        chain = {
            "gid": row.get("gid"),
            "status": status,
            "outcome": outcome,
            "window_id": _window_id_from_row(row),
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


def summarize(paths):
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
            "transport_requests": len(room_requests),
            "transport_physical_attempts": room_physical_count,
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
                        "physical_state_requests", "suppressed_duplicates",
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
            "physical_state_attempts": layer_physical,
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
    for game in games:
        all_canonical.update(game.get("canonical_outcomes", {}))
        for resolution in game.get("canonical_resolutions", []):
            all_loss_stages[resolution.get("loss_stage", "UNKNOWN")] += 1
        all_recovery_chains.extend(game.get("recovery_chains", []))
        all_identity_origins.update(game.get("identity_origins", {}))
        all_first_seen.update(game.get("first_seen_via", {}))
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
    args = parser.parse_args()
    paths = sorted(args.root.glob(f"**/*_{args.room}_r*_b*.jsonl"))
    if not paths:
        parser.error(f"No logs for room {args.room}")
    report = summarize(paths)
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
