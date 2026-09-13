"""Single-owner state fetch coordination.

This module is intentionally transport-agnostic.  It provides the lifecycle
boundary around ``StateDemand`` and ``StateScheduler`` while BotClient keeps
ownership of mirror application and window authorization.
"""

from __future__ import annotations

from dataclasses import dataclass
import inspect
import threading
import time
from typing import Any, Callable, Mapping, Optional

from .state_demand import (
    PENDING,
    RESYNC,
    SATISFIED,
    StateDemand,
    StateRequest,
    TERMINAL,
)
from .state_scheduler import ScheduledRequest, StateScheduler


@dataclass
class FetchOperation:
    request: StateRequest
    candidate_id: Optional[str]
    ticket: Any = None
    transport_request_id: Optional[str] = None
    state: str = "ADMITTED"
    response: Any = None
    error: Optional[BaseException] = None


class StateFetchCoordinator:
    """Own one gid's candidate and fetch chain until reconciliation ends."""

    def __init__(self, gid, demand: Optional[StateDemand] = None,
                 scheduler: Optional[StateScheduler] = None,
                 *, clock=time.monotonic, on_reconciled=None):
        self.gid = gid
        self.demand = demand or StateDemand(clock=clock, gid=gid)
        self.scheduler = scheduler
        self.clock = clock
        self.on_reconciled = on_reconciled
        self._lock = threading.RLock()
        self._active: Optional[FetchOperation] = None
        self._pending_candidate_id: Optional[str] = None
        self._closed = False
        self._close_reason = None

    @property
    def active(self) -> Optional[FetchOperation]:
        with self._lock:
            return self._active

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    def cancel_requested(self) -> bool:
        """Return whether this fetch chain has been explicitly stopped."""
        with self._lock:
            return self._closed

    def adopt(self, request: StateRequest) -> bool:
        """Attach a request frozen by legacy demand code to this coordinator.

        BotClient still performs its domain-specific candidate preparation in
        the worker loop.  Adoption lets that path use the same coordinator
        ownership and response boundary without creating a second request or
        changing the existing fake-API call shape.
        """
        with self._lock:
            if self._closed or self._active is not None:
                return False
            if self.demand._inflight is not request:
                return False
            self._active = FetchOperation(
                request=request,
                candidate_id=request.candidate_id,
                state="ADMITTED",
            )
            return True

    def submit(self, reason: str, **kwargs) -> bool:
        with self._lock:
            if self._closed:
                return False
        changed = self.demand.submit(reason, **kwargs)
        # If a candidate is already queued, refresh its derived view.  The
        # scheduler update is an index update; the throttle remains the only
        # permit queue.
        with self._lock:
            candidate = self.demand.candidate_snapshot()
            if candidate is not None and self.scheduler is not None:
                self.scheduler.update(candidate["candidate_id"],
                                      deadline=self.demand.effective_deadline)
        return changed

    def begin(self, *, default_seq=None, default_kind="SSE_DELTA",
              deadline=None) -> Optional[StateRequest]:
        """Queue and admit the latest candidate, or return busy/closed."""
        with self._lock:
            if self._closed or self._active is not None:
                return None
            try:
                candidate = self.demand.queue_candidate(
                    default_seq=default_seq,
                    default_kind=default_kind,
                    deadline=deadline,
                )
            except RuntimeError:
                return None
        admitted_ticket = None
        if self.scheduler is not None:
            try:
                scheduled = self.scheduler.register(
                    self.gid, self.demand, default_seq=default_seq,
                    default_kind=default_kind, deadline=deadline,
                    candidate_id=candidate.candidate_id,
                )
            except RuntimeError:
                with self._lock:
                    self._pending_candidate_id = None
                self.demand.withdraw_candidate("closed_before_admission")
                return None
            with self._lock:
                self._pending_candidate_id = scheduled.candidate_id
            admitted = self.scheduler.acquire(scheduled)
            request = admitted.request if admitted is not None else None
            admitted_ticket = admitted.ticket if admitted is not None else None
        else:
            request = self.demand.admit_candidate()
        if request is None:
            with self._lock:
                self._pending_candidate_id = None
            return None
        candidate_id = request.candidate_id
        with self._lock:
            if self._closed:
                # The permit may already have been consumed; release the
                # logical owner without attempting HTTP.
                self.demand.close(self._close_reason or "closed_before_send")
                self.demand.finalize_close(self._close_reason or "closed_before_send")
                close_after_admission = True
            else:
                self._active = FetchOperation(
                    request=request,
                    candidate_id=request.candidate_id,
                    ticket=admitted_ticket,
                    state="ADMITTED",
                )
                self._pending_candidate_id = None
                close_after_admission = False
        if close_after_admission:
            if self.scheduler is not None and candidate_id is not None:
                self.scheduler.complete(candidate_id, pending=False)
            return None
        return request

    def mark_transport_started(self) -> Optional[str]:
        with self._lock:
            operation = self._active
            if operation is None or self._closed:
                return None
            self.demand.mark_transport_started()
            operation.state = "IN_FLIGHT"
            operation.transport_request_id = self.demand.next_transport_request_id(
                operation.request)
            return operation.transport_request_id

    def record_transport_result(self, attempts=None):
        with self._lock:
            operation = self._active
            if operation is None:
                return
            self.demand.record_transport_attempts(attempts)
            self.demand.begin_reconcile(operation.request)

    def complete(self, response=None, *, response_seq=None,
                 response_mode=None, snapshot=None,
                 reason_results: Optional[Mapping[str, str]] = None,
                 apply: Optional[Callable[[Any], Any]] = None,
                 error: Optional[BaseException] = None) -> bool:
        """Apply a response, then reconcile reason-specific completion."""
        with self._lock:
            operation = self._active
            if operation is None:
                return False
            operation.state = "RECONCILING"
            operation.response = response
            operation.error = error
            self.demand.begin_reconcile(operation.request)
        if error is None and apply is not None:
            try:
                apply_result = apply(response)
            except BaseException as exc:
                error = exc
                operation.error = exc
            else:
                if isinstance(apply_result, Mapping):
                    reason_results = dict(reason_results or {})
                    reason_results.update(apply_result)
        if error is not None:
            # A failed transport does not silently satisfy anything.  Keep
            # the request pending for the caller's existing retry/recovery
            # policy, then release the owner.
            self.demand.reconcile(
                response_seq, response_mode=response_mode, snapshot=snapshot,
                applied=False)
        else:
            self.demand.reconcile(
                response_seq, response_mode=response_mode, snapshot=snapshot,
                reason_results=reason_results, applied=True)
        pending = self.demand.has_pending
        reconciled_snapshot = self.demand.request_snapshot()
        callback = self.on_reconciled
        if callback is not None:
            try:
                callback(operation.request, reconciled_snapshot, pending, error)
            except BaseException:
                # Diagnostics must never reopen or abort the state owner.
                pass
        with self._lock:
            if self.scheduler is not None and operation.candidate_id is not None:
                self.scheduler.complete(operation.candidate_id, pending=pending)
            operation.state = "QUEUED" if pending else "COMPLETE"
            self._active = None
        return pending

    def fetch(self, api, *, default_seq=None, default_kind="SSE_DELTA",
              deadline=None, request_timeout=None) -> Any:
        """Perform one bounded state request through the coordinator."""
        request = self.begin(default_seq=default_seq, default_kind=default_kind,
                             deadline=deadline)
        if request is None:
            return None
        transport_id = self.mark_transport_started()
        kwargs = {
            "deadline": request.effective_deadline,
            "logical_request_id": request.logical_request_id,
            "reason": list(request.reasons),
            "generation": request.started_generation,
            "transport_request_id": transport_id,
        }
        if operation := self.active:
            if operation.ticket is not None:
                kwargs["state_ticket"] = operation.ticket
            if operation.candidate_id is not None:
                kwargs["candidate_id"] = operation.candidate_id
        if self.scheduler is not None:
            kwargs["state_throttle"] = self.scheduler.throttle
        kwargs["cancel_check"] = self.cancel_requested
        if request_timeout is not None:
            kwargs["request_timeout"] = request_timeout
        try:
            method = getattr(api, "game_state")
            try:
                params = inspect.signature(method).parameters
            except (TypeError, ValueError):
                params = {}
            accepts_kwargs = any(
                item.kind == inspect.Parameter.VAR_KEYWORD
                for item in params.values())
            accepts_lifecycle = accepts_kwargs or any(
                key in params for key in kwargs)
            if accepts_lifecycle:
                response = method(self.gid, request.seq, **kwargs)
            else:
                # Small replay fakes intentionally expose only (gid, seq);
                # inspect the signature before calling so a TypeError raised
                # by a real transport cannot trigger a duplicate GET.
                response = method(self.gid, request.seq)
        except BaseException as exc:
            self.record_transport_result(self._transport_attempts(api))
            self.complete(error=exc)
            raise
        self.record_transport_result(self._transport_attempts(api))
        self.complete(response, response_seq=(response or {}).get("seq"),
                      response_mode=request.mode,
                      snapshot=(response or {}).get("snapshot"))
        return response

    @staticmethod
    def _transport_attempts(api=None):
        try:
            from .api import Api, _TLS
            if api is not None and not isinstance(api, Api):
                return None
            meta = getattr(_TLS, "request_meta", None)
        except Exception:
            meta = None
        if isinstance(meta, dict):
            return meta.get("state_physical_attempts", meta.get("attempts"))
        return None

    def close(self, reason="closed") -> bool:
        with self._lock:
            if self._closed:
                return False
            self._closed = True
            self._close_reason = str(reason)
            pending_candidate_id = self._pending_candidate_id
            self._pending_candidate_id = None
            self.demand.close(str(reason))
            operation = self._active
            active_candidate_id = (operation.candidate_id
                                   if operation is not None else None)
            if operation is None:
                self.demand.finalize_close(str(reason))
        if self.scheduler is not None and pending_candidate_id is not None:
            self.scheduler.withdraw(pending_candidate_id, reason=str(reason))
        # An admitted operation is no longer represented by a queued
        # scheduler candidate, but a retry may currently be waiting on the
        # same throttle with its candidate id.  Remove that waiter and let
        # ``cancel_requested`` stop the retry loop before another HTTP call.
        if self.scheduler is not None and active_candidate_id is not None:
            self.scheduler.throttle.withdraw_waiter(active_candidate_id)
        return True

    def finalize_close(self, reason=None) -> bool:
        with self._lock:
            result = self.demand.finalize_close(reason or self._close_reason)
            self._active = None
            self._closed = True
            return result


__all__ = ["FetchOperation", "StateFetchCoordinator"]
