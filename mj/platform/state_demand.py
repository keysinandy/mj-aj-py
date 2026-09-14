"""State demand coordination primitives.

The SSE stream is a wake-up source, not the authoritative state.  This module
keeps the logical demand that led to a state request separate from the
physical request used to satisfy it.  It intentionally retains the small
``queue.Queue`` compatibility surface used by the older SSE wake path while
exposing the structured demand model used by the state loop.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import copy
import queue
import threading
import time
import uuid
from typing import Any, Iterable, Mapping, Optional


SSE_DELTA = "SSE_DELTA"
RESYNC = "RESYNC"
WINDOW_CONFIRM = "WINDOW_CONFIRM"

SATISFIED = "SATISFIED"
TERMINAL = "TERMINAL"
PENDING = "PENDING"

_REASON_BITS = {
    SSE_DELTA: 1,
    RESYNC: 2,
    WINDOW_CONFIRM: 4,
}
_KIND_PRIORITY = {
    SSE_DELTA: 1,
    RESYNC: 2,
    WINDOW_CONFIRM: 3,
}

# State requests are correlated across all game workers belonging to one
# process.  A per-``StateDemand`` counter (the old ``state-1`` scheme) is not
# sufficient once several gids share a token or a worker is recreated.
_RUN_ID = uuid.uuid4().hex[:12]
_REQUEST_ID_LOCK = threading.Lock()
_REQUEST_ID_COUNTER = 0
_CANDIDATE_ID_LOCK = threading.Lock()
_CANDIDATE_ID_COUNTER = 0


def _allocate_request_id(run_id: str, kind: str = "state", gid: Any = None) -> str:
    global _REQUEST_ID_COUNTER
    with _REQUEST_ID_LOCK:
        _REQUEST_ID_COUNTER += 1
        number = _REQUEST_ID_COUNTER
    gid_value = gid if gid is not None else "unknown"
    return f"{run_id}:{kind}:{gid_value}:{number}"


def _allocate_candidate_id(run_id: str, gid: Any = None) -> str:
    global _CANDIDATE_ID_COUNTER
    with _CANDIDATE_ID_LOCK:
        _CANDIDATE_ID_COUNTER += 1
        number = _CANDIDATE_ID_COUNTER
    gid_value = gid if gid is not None else "unknown"
    return f"{run_id}:candidate:{gid_value}:{number}"


def _json_value(value: Any) -> Any:
    """Return a small JSON-safe representation for diagnostics."""

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "to_json"):
        return value.to_json()
    if hasattr(value, "as_json"):
        return value.as_json()
    if isinstance(value, Mapping):
        return {str(k): _json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_value(v) for v in value]
    return str(value)


@dataclass(frozen=True)
class WindowId:
    """Logical identity of one discard response window.

    ``source_discard_seq`` is authoritative when it came from an explicit
    source-sequence field or the event seq of a ``tile_discarded`` payload.
    Snapshot watermarks are never source identity.  Legacy windows retain a
    weak fallback for logging and local dedupe, but are marked
    ``legacy_unresolved`` and must not be treated as proof of identity.
    """

    game_id: Any
    round_id: Any
    discard_owner: Any
    source_discard_seq: Any
    tile: Any
    identity_status: str = "authoritative"
    fallback: Any = None
    # Diagnostic provenance of the source identity; it is not authority
    # semantics and therefore remains separate from identity_status.
    identity_origin: str = field(default="unknown", compare=False)
    first_seen_via: str = field(default="unknown", compare=False)

    @property
    def authoritative(self) -> bool:
        return self.identity_status == "authoritative" and self.source_discard_seq is not None

    def as_tuple(self) -> tuple[Any, Any, Any, Any, Any]:
        return (
            self.game_id,
            self.round_id,
            self.discard_owner,
            self.source_discard_seq,
            self.tile,
        )

    def as_json(self) -> dict[str, Any]:
        return {
            "game_id": _json_value(self.game_id),
            "round_id": _json_value(self.round_id),
            "discard_owner": _json_value(self.discard_owner),
            "source_discard_seq": _json_value(self.source_discard_seq),
            "tile": _json_value(self.tile),
            "identity_status": self.identity_status,
            "fallback": _json_value(self.fallback),
            "identity_origin": self.identity_origin,
            "first_seen_via": self.first_seen_via,
        }


@dataclass(frozen=True)
class WindowAttemptKey:
    """Logical window/phase key.

    This is deliberately not a physical HTTP attempt id.  Re-fetching the
    same ``(WindowId, phase)`` creates another physical attempt while keeping
    the same logical key.
    """

    window_id: WindowId
    phase: Any

    def as_tuple(self) -> tuple[Any, Any]:
        return (self.window_id.as_tuple(), self.phase)

    def as_json(self) -> dict[str, Any]:
        return {"window_id": self.window_id.as_json(), "phase": _json_value(self.phase)}


@dataclass
class StateRequest:
    """A logical state request selected from the current demand."""

    logical_request_id: str
    seq: Optional[int]
    mode: str
    kind: str
    started_generation: int
    attempt_index: int = 1
    effective_deadline: Optional[float] = None
    deadline_source: str = "unknown"
    reason_mask: int = 0
    reasons: dict[str, dict[str, Any]] = field(default_factory=dict)
    candidate_id: Optional[str] = None
    candidate_created_at: Optional[float] = None
    queued_at: Optional[float] = None
    admitted_at: Optional[float] = None
    successor_of: Optional[str] = None
    # The physical id is allocated for an attempt, not for the logical
    # request.  Keeping it optional preserves the old plan shape before HTTP
    # admission.
    transport_request_id: Optional[str] = None
    evaluated_revisions: dict[str, int] = field(default_factory=dict)
    satisfied_reasons: tuple[str, ...] = ()
    response_applied_at: Optional[float] = None

    @property
    def full_snapshot(self) -> bool:
        return self.mode == "FULL"

    def as_json(self) -> dict[str, Any]:
        return {
            "logical_request_id": self.logical_request_id,
            "seq": self.seq,
            "mode": self.mode,
            "kind": self.kind,
            "started_generation": self.started_generation,
            "attempt_index": self.attempt_index,
            "effective_deadline": self.effective_deadline,
            "deadline_source": self.deadline_source,
            "reason_mask": self.reason_mask,
            "reasons": _json_value(self.reasons),
            "candidate_id": self.candidate_id,
            "candidate_created_at": self.candidate_created_at,
            "queued_at": self.queued_at,
            "admitted_at": self.admitted_at,
            "successor_of": self.successor_of,
            "transport_request_id": self.transport_request_id,
            "evaluated_revisions": _json_value(self.evaluated_revisions),
            "satisfied_reasons": list(self.satisfied_reasons),
            "response_applied_at": self.response_applied_at,
        }


@dataclass
class StateCandidate:
    """Mutable pre-admission view of one gid's state demand.

    A candidate is deliberately separate from ``StateRequest``.  It may be
    upgraded or withdrawn while waiting for the shared permit; after
    admission the request's mode and sequence are frozen.
    """

    candidate_id: str
    default_seq: Optional[int]
    default_kind: str
    deadline: Optional[float]
    created_at: float
    state: str = "QUEUED"
    update_count: int = 0

    def as_json(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "default_seq": self.default_seq,
            "default_kind": self.default_kind,
            "deadline": self.deadline,
            "created_at": self.created_at,
            "state": self.state,
            "update_count": self.update_count,
        }


class StateDemand(queue.Queue):
    """Per-game logical state demand with legacy SSE queue compatibility.

    Structured fields are facts.  ``kind_priority``, ``effective_deadline``
    and ``reason_mask`` are recomputed views, so closing one reason cannot
    leave a stale deadline or stale priority behind.
    """

    def __init__(self, maxsize: int = 0, *, clock=time.monotonic,
                 run_id: Optional[str] = None, gid: Any = None):
        super().__init__(maxsize=maxsize)
        self._demand_lock = threading.RLock()
        self._clock = clock
        self.run_id = str(run_id or _RUN_ID)
        self.gid = gid
        self._reasons: dict[str, dict[str, Any]] = {}
        self.watermark_target: Optional[int] = None
        self.full_snapshot_required = False
        self.kind_priority: Optional[str] = None
        self.effective_deadline: Optional[float] = None
        self.reason_mask = 0
        self.generation = 0
        self.in_flight = False
        self._inflight: Optional[StateRequest] = None
        self._logical_counter = 0
        self._candidate_counter = 0
        self._candidate: Optional[StateCandidate] = None
        self._last_request_id: Optional[str] = None
        self._transport_attempt_counter = 0
        self._transport_recorded_attempts = 0
        self._transport_started = False
        self._lifecycle = "IDLE"
        self.closed = False
        self.closing = False
        self.close_reason: Optional[str] = None
        self.cancelled_before_send = 0
        self.substituted_candidates = 0
        self.physical_state_attempts = 0
        self.logical_state_requests = 0
        self._last_evaluated_revisions: dict[str, int] = {}
        self._last_satisfied_reasons: tuple[str, ...] = ()
        self._last_response_applied_at: Optional[float] = None
        # A completed physical request may still have pending reasons, but a
        # successor is only counted when the next physical request is
        # actually created.  Keeping this bit separate avoids counting a
        # speculative successor that a later reason-specific resolver clears.
        self._successor_ready = False
        self.logical_demands = 0
        self.logical_input_demands = 0
        self.coalesced_demands = 0
        self.successor_requests = 0
        self.physical_state_requests = 0
        self.suppressed_duplicates = 0

    @property
    def reasons(self) -> dict[str, dict[str, Any]]:
        with self._demand_lock:
            return copy.deepcopy(self._reasons)

    @property
    def pending_reasons(self) -> dict[str, dict[str, Any]]:
        """Return only active reasons for a new physical request.

        ``reasons`` deliberately retains SATISFIED/TERMINAL entries for
        diagnostics and post-completion reconciliation.  A request plan must
        not expose that history as if it were still part of the physical
        request, otherwise an ordinary SSE request is reported as carrying
        stale RESYNC/WINDOW_CONFIRM work.
        """

        with self._demand_lock:
            return copy.deepcopy({
                reason: data
                for reason, data in self._reasons.items()
                if data.get("status") == PENDING
            })

    @property
    def has_pending(self) -> bool:
        with self._demand_lock:
            return any(reason.get("status") == PENDING for reason in self._reasons.values())

    @property
    def full_snapshot(self) -> bool:
        return self.full_snapshot_required

    def _put(self, item: Any) -> None:
        """Keep the old ``(watermark, closed)`` wake queue semantics."""

        # Queue.put() already holds queue.Queue.mutex while calling _put.
        # Take the demand lock inside that same critical section so the legacy
        # wake item and the structured reason cannot observe half of one
        # another.  All other structured mutations use this same lock.
        with self._demand_lock:
            if isinstance(item, tuple) and len(item) >= 2:
                # The old queue keeps only the strongest wake.  A closed wake
                # is sticky, while an unknown watermark remains unknown.
                if self.queue:
                    previous = self.queue.pop()
                    previous_seq = (previous[0]
                                    if isinstance(previous, tuple) and previous
                                    else None)
                    previous_closed = bool(
                        previous[1]
                        if isinstance(previous, tuple) and len(previous) > 1
                        else False)
                    current_seq = item[0]
                    if previous_seq is None or current_seq is None:
                        merged_seq = None
                    else:
                        try:
                            merged_seq = max(previous_seq, current_seq)
                        except TypeError:
                            merged_seq = current_seq
                    item = (merged_seq, previous_closed or bool(item[1]))
                super()._put(item)
                # Make tuple wakeups participate in the structured model too.
                self._submit(
                    SSE_DELTA,
                    wanted_seq=item[0],
                    _count_logical=True,
                )
                return
            super()._put(item)

    def _reason_changed(self, reason: str, incoming: dict[str, Any]) -> bool:
        previous = self._reasons.get(reason)
        if previous is None:
            return True
        # Status is deliberately not part of the semantic comparison: a new
        # occurrence reopens a satisfied/terminal reason with fresh metadata.
        old = {k: v for k, v in previous.items()
               if k not in ("status", "requested_at", "revision")}
        new = {k: v for k, v in incoming.items()
               if k not in ("status", "requested_at", "revision")}
        return old != new or previous.get("status") != PENDING

    def _recompute(self) -> None:
        pending = {
            reason: data
            for reason, data in self._reasons.items()
            if data.get("status") == PENDING
        }
        targets = [
            data.get("wanted_seq")
            for reason, data in pending.items()
            if reason == SSE_DELTA and data.get("wanted_seq") is not None
        ]
        self.watermark_target = max(targets) if targets else None
        self.full_snapshot_required = any(
            reason in (RESYNC, WINDOW_CONFIRM) for reason in pending
        )
        if pending:
            self.kind_priority = max(
                pending,
                key=lambda reason: _KIND_PRIORITY.get(reason, 0),
            )
            deadlines = [
                data.get("deadline")
                for data in pending.values()
                if data.get("deadline") is not None
            ]
            self.effective_deadline = min(deadlines) if deadlines else None
        else:
            self.kind_priority = None
            self.effective_deadline = None
        mask = 0
        for reason in pending:
            mask |= _REASON_BITS.get(reason, 0)
        self.reason_mask = mask

    def _submit(
        self,
        reason: str,
        *,
        wanted_seq: Optional[int] = None,
        cause: Any = None,
        requested_at: Optional[float] = None,
        window_id: Any = None,
        phase: Any = None,
        deadline: Optional[float] = None,
        status: str = PENDING,
        _count_logical: bool = True,
        **metadata: Any,
    ) -> bool:
        reason = str(reason).upper()
        if self.closed or self.closing:
            return False
        if requested_at is None:
            requested_at = self._clock()
        incoming: dict[str, Any] = {
            "status": status,
            "wanted_seq": wanted_seq,
            "cause": cause,
            "requested_at": requested_at,
            "window_id": window_id,
            "phase": phase,
            "deadline": deadline,
        }
        incoming.update(metadata)
        # Keep a reason's own parameters as the source of truth.  In
        # particular, do not encode FULL as wanted_seq=0.
        if reason == SSE_DELTA and wanted_seq is not None:
            try:
                incoming["wanted_seq"] = int(wanted_seq)
            except (TypeError, ValueError):
                incoming["wanted_seq"] = wanted_seq
        changed = self._reason_changed(reason, incoming)
        previous = self._reasons.get(reason)
        if previous is not None and reason == SSE_DELTA:
            old_target = previous.get("wanted_seq")
            new_target = incoming.get("wanted_seq")
            if (
                old_target is not None
                and new_target is not None
                and old_target >= new_target
            ):
                # A lower or duplicate watermark is a no-op, not a semantic
                # generation change.
                changed = False
                incoming = previous.copy()
        if changed:
            incoming["revision"] = int(previous.get("revision", 0)) + 1 \
                if previous is not None else 1
        elif previous is not None:
            incoming["revision"] = previous.get("revision", 1)
        if _count_logical:
            self.logical_demands += 1
            self.logical_input_demands += 1
        if not changed:
            if _count_logical:
                self.suppressed_duplicates += 1
            return False
        if previous is not None and _count_logical:
            if self.in_flight or previous.get("status") == PENDING:
                self.coalesced_demands += 1
        self._reasons[reason] = incoming
        self.generation += 1
        self._recompute()
        if self._candidate is not None and self._candidate.state == "QUEUED":
            self._candidate.update_count += 1
            if self._candidate.default_kind == SSE_DELTA and reason != SSE_DELTA:
                self.substituted_candidates += 1
            # Keep the scheduler view derived from the latest reason ledger;
            # created_at remains untouched so an upgrade does not lose FIFO
            # age.  The throttle's waiter is updated by StateScheduler when
            # present, while direct BotClient users still get accurate
            # candidate diagnostics here.
            self._candidate.default_kind = self.kind_priority or self._candidate.default_kind
            # An SSE watermark is a wake-up target, not the caller's applied
            # cursor.  The candidate may already be waiting for the shared
            # throttle while the stream advances; replacing its local
            # ``default_seq`` here would skip every event between the old
            # cursor and the newest wake.  Keep the cursor frozen until
            # admission and expose the newer target only through the reason
            # ledger/diagnostics.
            self._candidate.deadline = self.effective_deadline
        return True

    def submit(
        self,
        reason: str,
        *,
        wanted_seq: Optional[int] = None,
        cause: Any = None,
        requested_at: Optional[float] = None,
        window_id: Any = None,
        phase: Any = None,
        deadline: Optional[float] = None,
        **metadata: Any,
    ) -> bool:
        """Merge one logical reason and return whether state changed."""

        with self._demand_lock:
            return self._submit(
                reason,
                wanted_seq=wanted_seq,
                cause=cause,
                requested_at=requested_at,
                window_id=window_id,
                phase=phase,
                deadline=deadline,
                **metadata,
            )

    def submit_sse(self, wanted_seq: Optional[int]) -> bool:
        return self.submit(SSE_DELTA, wanted_seq=wanted_seq)

    def submit_resync(self, cause: Any = None, *, requested_at: Optional[float] = None) -> bool:
        return self.submit(RESYNC, cause=cause, requested_at=requested_at)

    def submit_window_confirm(
        self,
        window_id: Any,
        phase: Any,
        *,
        deadline: Optional[float] = None,
        requested_at: Optional[float] = None,
    ) -> bool:
        return self.submit(
            WINDOW_CONFIRM,
            window_id=window_id,
            phase=phase,
            deadline=deadline,
            requested_at=requested_at,
        )

    # ---------- pre-admission candidate lifecycle ----------

    def queue_candidate(self, *, default_seq: Optional[int] = None,
                        default_kind: str = SSE_DELTA,
                        deadline: Optional[float] = None,
                        candidate_id: Optional[str] = None) -> StateCandidate:
        """Create or update the one queued candidate for this gid.

        This method is intentionally side-effect compatible with the legacy
        ``start_request(default_seq=...)`` entry: a default candidate also
        creates the corresponding logical reason, but it does not consume a
        StateThrottle permit or mark a request in flight.
        """
        with self._demand_lock:
            if self.closed or self.closing:
                raise RuntimeError("state demand is closed")
            now = self._clock()
            if self._candidate is not None and self._candidate.state == "QUEUED":
                candidate = self._candidate
                if default_seq is not None:
                    candidate.default_seq = default_seq
                if deadline is not None:
                    candidate.deadline = deadline
                if default_kind:
                    candidate.default_kind = default_kind
                candidate.update_count += 1
            else:
                self._candidate_counter += 1
                candidate = StateCandidate(
                    candidate_id=(candidate_id or
                                  _allocate_candidate_id(self.run_id, self.gid)),
                    default_seq=default_seq,
                    default_kind=default_kind,
                    deadline=deadline,
                    created_at=now,
                )
                self._candidate = candidate
                self._lifecycle = "QUEUED"
            if not self.has_pending:
                if default_kind == RESYNC:
                    self._submit(RESYNC, cause="candidate_default")
                elif default_kind == WINDOW_CONFIRM:
                    self._submit(WINDOW_CONFIRM, phase="default",
                                 deadline=deadline)
                else:
                    self._submit(SSE_DELTA, wanted_seq=default_seq)
            # A reason may have been submitted before the candidate was
            # created (for example an SSE wake followed by a scheduler
            # registration).  In that case its effective deadline is the
            # authoritative scheduler view even when this call omitted an
            # explicit default deadline.
            if self.effective_deadline is not None:
                candidate.deadline = self.effective_deadline
            return candidate

    def candidate_snapshot(self) -> Optional[dict[str, Any]]:
        with self._demand_lock:
            candidate = self._candidate.as_json() if self._candidate else None
            if candidate is None:
                return None
            candidate.update({
                "mode": "FULL" if self.full_snapshot_required else "DELTA",
                "seq": (0 if self.full_snapshot_required
                         else candidate.get("default_seq", self.watermark_target)),
                "reason_mask": self.reason_mask,
                "revisions": {
                    reason: data.get("revision")
                    for reason, data in self._reasons.items()
                    if data.get("status") == PENDING
                },
            })
            return candidate

    def withdraw_candidate(self, reason: str = "cancelled_before_send") -> bool:
        with self._demand_lock:
            if self._candidate is None or self._candidate.state != "QUEUED":
                return False
            self._candidate.state = "CANCELLED"
            self.cancelled_before_send += 1
            self._lifecycle = "IDLE"
            self._candidate = None
            return True

    def admit_candidate(self) -> Optional[StateRequest]:
        """Freeze the latest queued candidate at the admission boundary."""
        with self._demand_lock:
            if self.closed or self.closing:
                return None
            candidate = self._candidate
            if candidate is None:
                return None
            if candidate.state == "IN_FLIGHT":
                return self._inflight
            if candidate.state != "QUEUED":
                return None
            if not self.has_pending:
                candidate.state = "CANCELLED"
                self._candidate = None
                self.cancelled_before_send += 1
                self._lifecycle = "IDLE"
                self._successor_ready = False
                return None
            request = self.start_request(
                default_seq=candidate.default_seq,
                default_kind=candidate.default_kind,
                deadline=candidate.deadline,
                candidate_id=candidate.candidate_id,
                candidate_created_at=candidate.created_at,
            )
            return request

    def mark_transport_started(self) -> bool:
        with self._demand_lock:
            if not self.in_flight or self.closed:
                return False
            self._transport_started = True
            self._lifecycle = "IN_FLIGHT"
            return True

    def next_transport_request_id(self, request: Optional[StateRequest] = None) -> str:
        with self._demand_lock:
            current = request or self._inflight
            if current is None:
                raise RuntimeError("no admitted state request")
            self._transport_attempt_counter += 1
            transport_id = (f"{current.logical_request_id}:attempt:"
                            f"{self._transport_attempt_counter}")
            current.transport_request_id = transport_id
            self.physical_state_attempts += 1
            return transport_id

    def record_transport_attempts(self, attempts: Optional[int]) -> int:
        """Reconcile HTTP retry diagnostics with the logical admission.

        ``next_transport_request_id`` accounts for the first physical start.
        The HTTP layer may perform additional 429/network attempts under the
        same logical id; add only the extra attempts here so one logical
        request with a 429->200 retry reports two physical attempts.
        """
        try:
            total = int(attempts)
        except (TypeError, ValueError):
            return self.physical_state_attempts
        if total <= 0:
            return self.physical_state_attempts
        with self._demand_lock:
            if self._inflight is None:
                return self.physical_state_attempts
            already = max(
                1 if self._transport_started else 0,
                self._transport_recorded_attempts,
            )
            self.physical_state_attempts += max(0, total - already)
            self._transport_recorded_attempts = max(
                self._transport_recorded_attempts, total)
            return self.physical_state_attempts

    def begin_reconcile(self, request: Optional[StateRequest] = None) -> bool:
        """Mark the response-application phase while retaining ownership."""
        with self._demand_lock:
            if not self.in_flight:
                return False
            if request is not None and self._inflight is not request:
                return False
            self._lifecycle = "RECONCILING"
            return True

    def _next_kind(self, default_kind: str) -> str:
        if self.kind_priority is not None:
            return self.kind_priority
        return default_kind

    def start_request(
        self,
        *,
        default_seq: Optional[int] = None,
        default_kind: str = SSE_DELTA,
        deadline: Optional[float] = None,
        logical_request_id: Optional[str] = None,
        candidate_id: Optional[str] = None,
        candidate_created_at: Optional[float] = None,
    ) -> Optional[StateRequest]:
        """Select one physical state request from the current demand.

        A caller may provide a default demand for the initial fetch.  Once a
        structured reason exists, it always wins.  FULL is selected from the
        boolean mode and never from numeric max ordering.
        """

        with self._demand_lock:
            if self.closed or self.closing:
                return None
            if self.in_flight:
                return self._inflight
            if not self.has_pending and default_seq is None:
                return None
            kind = self._next_kind(default_kind)
            if not self.has_pending and default_seq is not None:
                if default_kind == RESYNC:
                    self._submit(RESYNC, cause="default_request")
                elif default_kind == WINDOW_CONFIRM:
                    self._submit(WINDOW_CONFIRM, phase="default", deadline=deadline)
                else:
                    self._submit(SSE_DELTA, wanted_seq=default_seq)
                kind = self._next_kind(default_kind)
            mode = "FULL" if self.full_snapshot_required else "DELTA"
            # SSE watermarks are coordination targets, not cursors.  The
            # physical incremental request must start at the caller's local
            # applied cursor; using watermark_target here would skip events
            # between that cursor and the wake watermark.  The target remains
            # in the request/reason diagnostics and is reconciled by the
            # returned watermark.  The target is only a compatibility
            # fallback for direct callers that do not provide a cursor.
            if mode == "FULL":
                seq = 0
            elif default_seq is not None:
                seq = default_seq
            else:
                seq = self.watermark_target
            now = self._clock()
            candidate = self._candidate
            if candidate is not None and candidate.state == "QUEUED":
                candidate.state = "IN_FLIGHT"
                self._lifecycle = "ADMITTED"
                candidate_id = candidate_id or candidate.candidate_id
                candidate_created_at = (candidate_created_at
                                        if candidate_created_at is not None
                                        else candidate.created_at)
            logical_request_id = (logical_request_id or
                                  _allocate_request_id(self.run_id, gid=self.gid))
            request = StateRequest(
                logical_request_id=logical_request_id,
                seq=seq,
                mode=mode,
                kind=kind,
                started_generation=self.generation,
                effective_deadline=self.effective_deadline if self.effective_deadline is not None else deadline,
                deadline_source=("reason_effective"
                                 if self.effective_deadline is not None
                                 else "candidate_scheduler"
                                 if deadline is not None else "none"),
                reason_mask=self.reason_mask,
                reasons={
                    reason: copy.deepcopy(data)
                    for reason, data in self._reasons.items()
                    if data.get("status") == PENDING
                },
                candidate_id=candidate_id,
                candidate_created_at=candidate_created_at,
                queued_at=(candidate_created_at if candidate_created_at is not None
                           else now),
                admitted_at=now,
                successor_of=self._last_request_id if self._successor_ready
                else None,
            )
            if self._successor_ready:
                self.logical_demands += 1
                self.successor_requests += 1
                self._successor_ready = False
            self.in_flight = True
            self._inflight = request
            self._transport_started = False
            self._transport_recorded_attempts = 0
            self._lifecycle = "ADMITTED"
            self.physical_state_requests += 1
            self.logical_state_requests += 1
            self._last_request_id = request.logical_request_id
            return request

    def reconcile(
        self,
        response_seq: Optional[int],
        *,
        response_mode: Optional[str] = None,
        snapshot: Any = None,
        reason_results: Optional[Mapping[str, str]] = None,
        applied: bool = True,
        evaluated_at: Optional[float] = None,
    ) -> bool:
        """Reconcile a completed response against the latest demand.

        Returns whether a pending reason remains.  Generation changes are
        intentionally not sufficient to request a successor: callers must
        inspect the result of this reason-specific reconciliation.
        """

        with self._demand_lock:
            request = self._inflight
            self.in_flight = False
            self._inflight = None
            self._transport_started = False
            reason_results = reason_results or {}
            evaluated_revisions = ({
                reason: int(data.get("revision", 1))
                for reason, data in (request.reasons.items()
                                     if request is not None else ())
            })
            satisfied_reasons = [
                reason for reason, data in self._reasons.items()
                if request is not None and reason in request.reasons
                and data.get("status") == SATISFIED
            ]
            evaluated_at = self._clock() if evaluated_at is None else evaluated_at
            for reason, data in list(self._reasons.items()):
                if data.get("status") != PENDING:
                    continue
                evaluated_revisions.setdefault(
                    reason, int(data.get("revision", 1)))
                result = reason_results.get(reason)
                if result in (SATISFIED, TERMINAL):
                    data["status"] = result
                    if result == SATISFIED and reason not in satisfied_reasons:
                        satisfied_reasons.append(reason)
                    continue
                if reason == SSE_DELTA:
                    target = data.get("wanted_seq")
                    try:
                        covered = response_seq is not None and target is not None and int(response_seq) >= int(target)
                    except (TypeError, ValueError):
                        covered = False
                    if applied and covered:
                        data["status"] = SATISFIED
                        if reason not in satisfied_reasons:
                            satisfied_reasons.append(reason)
                elif reason == RESYNC:
                    # A FULL request is only an intent.  The BotClient must
                    # mark RESYNC satisfied after Mirror.apply_snapshot()
                    # succeeds; a seq=0 response without a usable snapshot
                    # must remain pending.
                    pass
                # WINDOW_CONFIRM is resolved by the caller with a
                # reason-specific result; a state response alone must not
                # accidentally mark it satisfied.
            if request is not None:
                request.evaluated_revisions = dict(evaluated_revisions)
                request.satisfied_reasons = tuple(satisfied_reasons)
                request.response_applied_at = evaluated_at if applied else None
            self._last_evaluated_revisions = dict(evaluated_revisions)
            self._last_satisfied_reasons = tuple(satisfied_reasons)
            self._last_response_applied_at = evaluated_at if applied else None
            self._recompute()
            pending = self.has_pending
            if pending and request is not None:
                # Defer successor accounting until start_request() actually
                # creates the successor.  A caller may still resolve a
                # WINDOW_CONFIRM after this reconciliation; counting here
                # would report a request that was never sent.
                self._successor_ready = True
                self._lifecycle = "QUEUED"
                if self._candidate is not None:
                    self._candidate.state = "QUEUED"
            else:
                self._lifecycle = "IDLE"
                if self._candidate is not None:
                    self._candidate.state = "COMPLETE"
                    self._candidate = None
            return pending

    def resolve_reason(self, reason: str, status: str, *,
                       expected_revision: Optional[int] = None,
                       window_id: Any = None,
                       window_attempt_key: Any = None,
                       terminal_reason: Optional[str] = None,
                       evaluated_revision: Optional[int] = None) -> bool:
        """Set one reason's terminal result and recompute derived fields."""

        status = str(status).upper()
        if status not in (SATISFIED, TERMINAL, PENDING):
            raise ValueError(f"unknown state-demand status: {status}")
        with self._demand_lock:
            if self.closed or self.closing:
                return False
            if reason not in self._reasons:
                return False
            current = self._reasons[reason]
            if (expected_revision is not None
                    and current.get("revision") != expected_revision):
                return False
            if window_id is not None and current.get("window_id") != window_id:
                return False
            if window_attempt_key is not None \
                    and current.get("window_attempt_key") != window_attempt_key:
                return False
            if self._reasons[reason].get("status") == status:
                return False
            current["status"] = status
            if terminal_reason is not None:
                current["terminal_reason"] = terminal_reason
            if evaluated_revision is not None:
                current["evaluated_revision"] = evaluated_revision
            self.generation += 1
            self._recompute()
            if not self.has_pending:
                self._successor_ready = False
            return True

    def close(self, reason: str = "closed") -> bool:
        """Idempotently stop new work and terminalize pending reasons."""
        with self._demand_lock:
            if self.closed or self.closing:
                return False
            self.closing = True
            self.close_reason = str(reason)
            for data in self._reasons.values():
                if data.get("status") == PENDING:
                    data["status"] = TERMINAL
                    data["terminal_reason"] = str(reason)
            self._recompute()
            if self._candidate is not None and self._candidate.state == "QUEUED":
                self._candidate.state = "CANCELLED"
                self.cancelled_before_send += 1
                self._candidate = None
            # An admitted request that has not started HTTP can be released
            # immediately.  A real transport remains owned until its caller
            # invokes finalize_close().
            if self.in_flight and self._transport_started:
                self._lifecycle = "CLOSING"
            else:
                if self.in_flight and not self._transport_started:
                    self.cancelled_before_send += 1
                    if self._candidate is not None:
                        self._candidate.state = "CANCELLED"
                        self._candidate = None
                self.in_flight = False
                self._inflight = None
                self._transport_started = False
                self._lifecycle = "CLOSED"
                self.closed = True
                self.closing = False
            self._successor_ready = False
            self._recompute()
            return True

    def finalize_close(self, reason: Optional[str] = None) -> bool:
        with self._demand_lock:
            if self.closed:
                return False
            self.in_flight = False
            self._inflight = None
            self._transport_started = False
            self.closed = True
            self.closing = False
            self._lifecycle = "CLOSED"
            if self._candidate is not None:
                self._candidate.state = "CANCELLED"
                self._candidate = None
            if reason is not None:
                self.close_reason = str(reason)
            self._successor_ready = False
            self._recompute()
            return True

    def finish_window_confirm(self, status: str, **kwargs) -> bool:
        return self.resolve_reason(WINDOW_CONFIRM, status, **kwargs)

    def finish_resync(self, status: str = SATISFIED, **kwargs) -> bool:
        """Finish RESYNC only after the caller has rebuilt authoritative state."""

        return self.resolve_reason(RESYNC, status, **kwargs)

    def request_snapshot(self) -> dict[str, Any]:
        with self._demand_lock:
            return {
                "watermark_target": self.watermark_target,
                "full_snapshot_required": self.full_snapshot_required,
                "reasons": _json_value(self._reasons),
                "kind_priority": self.kind_priority,
                "effective_deadline": self.effective_deadline,
                "reason_mask": self.reason_mask,
                "generation": self.generation,
                "in_flight": self.in_flight,
                "closed": self.closed,
                "closing": self.closing,
                "lifecycle": self._lifecycle,
                "close_reason": self.close_reason,
                "candidate": (self._candidate.as_json()
                               if self._candidate is not None else None),
                "evaluated_revisions": _json_value(
                    self._last_evaluated_revisions),
                "satisfied_reasons": list(self._last_satisfied_reasons),
                "response_applied_at": self._last_response_applied_at,
                "cancelled_before_send": self.cancelled_before_send,
                "substituted_candidates": self.substituted_candidates,
                "logical_state_requests": self.logical_state_requests,
                "physical_state_attempts": self.physical_state_attempts,
                "logical_demands": self.logical_demands,
                "logical_input_demands": self.logical_input_demands,
                "coalesced_demands": self.coalesced_demands,
                "successor_requests": self.successor_requests,
                "physical_state_requests": self.physical_state_requests,
                "suppressed_duplicates": self.suppressed_duplicates,
                "coalesced_or_suppressed": (
                    self.coalesced_demands + self.suppressed_duplicates),
                "metric_version": "state-request-lifecycle-v1",
                "metric_source": "StateDemand",
            }

    def acknowledge(self, seq: Optional[int]) -> None:
        """Clear obsolete legacy wake notifications.

        Structured reason completion happens in ``reconcile``; this method is
        retained for callers that only need to drain the old wake queue.
        """

        with self.mutex:
            while self.queue:
                item = self.queue[0]
                if not isinstance(item, tuple) or len(item) < 2:
                    self.queue.popleft()
                    continue
                watermark, closed = item[0], bool(item[1])
                if closed:
                    break
                if watermark is None or seq is None:
                    break
                try:
                    if int(watermark) <= int(seq):
                        self.queue.popleft()
                        continue
                except (TypeError, ValueError):
                    break
                break
            self.not_empty.notify_all()


def window_attempt_tuple(value: Any) -> tuple[Any, Any]:
    """Compatibility helper for old tuple-based window set consumers."""

    if isinstance(value, WindowAttemptKey):
        return value.as_tuple()
    if isinstance(value, tuple):
        return value
    return (value, None)


__all__ = [
    "PENDING",
    "RESYNC",
    "SATISFIED",
    "SSE_DELTA",
    "StateDemand",
    "StateRequest",
    "StateCandidate",
    "TERMINAL",
    "WINDOW_CONFIRM",
    "WindowAttemptKey",
    "WindowId",
    "window_attempt_tuple",
]
