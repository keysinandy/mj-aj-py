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
    reason_mask: int = 0
    reasons: dict[str, dict[str, Any]] = field(default_factory=dict)

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
            "reason_mask": self.reason_mask,
            "reasons": _json_value(self.reasons),
        }


class StateDemand(queue.Queue):
    """Per-game logical state demand with legacy SSE queue compatibility.

    Structured fields are facts.  ``kind_priority``, ``effective_deadline``
    and ``reason_mask`` are recomputed views, so closing one reason cannot
    leave a stale deadline or stale priority behind.
    """

    def __init__(self, maxsize: int = 0, *, clock=time.monotonic):
        super().__init__(maxsize=maxsize)
        self._demand_lock = threading.RLock()
        self._clock = clock
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
        # A completed physical request may still have pending reasons, but a
        # successor is only counted when the next physical request is
        # actually created.  Keeping this bit separate avoids counting a
        # speculative successor that a later reason-specific resolver clears.
        self._successor_ready = False
        self.logical_demands = 0
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
               if k not in ("status", "requested_at")}
        new = {k: v for k, v in incoming.items()
               if k not in ("status", "requested_at")}
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
        if _count_logical:
            self.logical_demands += 1
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
    ) -> Optional[StateRequest]:
        """Select one physical state request from the current demand.

        A caller may provide a default demand for the initial fetch.  Once a
        structured reason exists, it always wins.  FULL is selected from the
        boolean mode and never from numeric max ordering.
        """

        with self._demand_lock:
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
            self._logical_counter += 1
            request = StateRequest(
                logical_request_id=logical_request_id
                or f"state-{self._logical_counter}",
                seq=seq,
                mode=mode,
                kind=kind,
                started_generation=self.generation,
                effective_deadline=self.effective_deadline if self.effective_deadline is not None else deadline,
                reason_mask=self.reason_mask,
                reasons={
                    reason: copy.deepcopy(data)
                    for reason, data in self._reasons.items()
                    if data.get("status") == PENDING
                },
            )
            if self._successor_ready:
                self.logical_demands += 1
                self.successor_requests += 1
                self._successor_ready = False
            self.in_flight = True
            self._inflight = request
            self.physical_state_requests += 1
            return request

    def reconcile(
        self,
        response_seq: Optional[int],
        *,
        response_mode: Optional[str] = None,
        snapshot: Any = None,
        reason_results: Optional[Mapping[str, str]] = None,
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
            reason_results = reason_results or {}
            for reason, data in list(self._reasons.items()):
                if data.get("status") != PENDING:
                    continue
                result = reason_results.get(reason)
                if result in (SATISFIED, TERMINAL):
                    data["status"] = result
                    continue
                if reason == SSE_DELTA:
                    target = data.get("wanted_seq")
                    try:
                        covered = response_seq is not None and target is not None and int(response_seq) >= int(target)
                    except (TypeError, ValueError):
                        covered = False
                    if covered:
                        data["status"] = SATISFIED
                elif reason == RESYNC:
                    # A FULL request is only an intent.  The BotClient must
                    # mark RESYNC satisfied after Mirror.apply_snapshot()
                    # succeeds; a seq=0 response without a usable snapshot
                    # must remain pending.
                    pass
                # WINDOW_CONFIRM is resolved by the caller with a
                # reason-specific result; a state response alone must not
                # accidentally mark it satisfied.
            self._recompute()
            pending = self.has_pending
            if pending and request is not None:
                # Defer successor accounting until start_request() actually
                # creates the successor.  A caller may still resolve a
                # WINDOW_CONFIRM after this reconciliation; counting here
                # would report a request that was never sent.
                self._successor_ready = True
            return pending

    def resolve_reason(self, reason: str, status: str) -> bool:
        """Set one reason's terminal result and recompute derived fields."""

        status = str(status).upper()
        if status not in (SATISFIED, TERMINAL, PENDING):
            raise ValueError(f"unknown state-demand status: {status}")
        with self._demand_lock:
            if reason not in self._reasons:
                return False
            if self._reasons[reason].get("status") == status:
                return False
            self._reasons[reason]["status"] = status
            self.generation += 1
            self._recompute()
            if not self.has_pending:
                self._successor_ready = False
            return True

    def finish_window_confirm(self, status: str) -> bool:
        return self.resolve_reason(WINDOW_CONFIRM, status)

    def finish_resync(self, status: str = SATISFIED) -> bool:
        """Finish RESYNC only after the caller has rebuilt authoritative state."""

        return self.resolve_reason(RESYNC, status)

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
                "logical_demands": self.logical_demands,
                "coalesced_demands": self.coalesced_demands,
                "successor_requests": self.successor_requests,
                "physical_state_requests": self.physical_state_requests,
                "suppressed_duplicates": self.suppressed_duplicates,
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
    "TERMINAL",
    "WINDOW_CONFIRM",
    "WindowAttemptKey",
    "WindowId",
    "window_attempt_tuple",
]
