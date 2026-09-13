"""Candidate indexing around the single shared :class:`StateThrottle`.

The scheduler owns mutable pre-admission views; the throttle remains the only
physical permit queue.  This deliberately small adapter lets the existing
BotClient/Api path migrate incrementally without introducing a second limiter.
"""

from __future__ import annotations

from dataclasses import dataclass
import threading
import time
from typing import Any, Optional

from .state_demand import (
    RESYNC,
    SSE_DELTA,
    StateCandidate,
    StateDemand,
    StateRequest,
)
from .throttle import StateThrottle, ThrottleTicket


@dataclass
class ScheduledCandidate:
    candidate_id: str
    gid: Any
    demand: Optional[StateDemand]
    default_seq: Optional[int]
    default_kind: str
    deadline: Optional[float]
    created_at: float
    state: str = "QUEUED"

    def as_json(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "gid": self.gid,
            "default_seq": self.default_seq,
            "default_kind": self.default_kind,
            "deadline": self.deadline,
            "created_at": self.created_at,
            "state": self.state,
        }


@dataclass
class ScheduledRequest:
    candidate: ScheduledCandidate
    request: Optional[StateRequest]
    ticket: Optional[ThrottleTicket]


class StateScheduler:
    """Manage candidates while delegating every permit to one throttle."""

    def __init__(self, throttle: Optional[StateThrottle] = None,
                 *, clock=time.monotonic):
        self.throttle = throttle or StateThrottle(clock=clock)
        self.clock = clock
        self._lock = threading.RLock()
        self._candidates: dict[str, ScheduledCandidate] = {}
        self._serial = 0

    @property
    def candidates(self) -> dict[str, ScheduledCandidate]:
        with self._lock:
            return dict(self._candidates)

    def register(self, gid, demand: Optional[StateDemand] = None, *,
                 default_seq=None, default_kind=SSE_DELTA, deadline=None,
                 candidate_id=None) -> ScheduledCandidate:
        with self._lock:
            if demand is not None:
                local = demand.queue_candidate(
                    default_seq=default_seq,
                    default_kind=default_kind,
                    deadline=deadline,
                    candidate_id=candidate_id,
                )
                candidate_id = local.candidate_id
                created_at = local.created_at
            else:
                self._serial += 1
                candidate_id = (candidate_id or
                                f"scheduler:candidate:{self._serial}")
                created_at = self.clock()
            existing = self._candidates.get(candidate_id)
            if existing is not None:
                self.update(existing, default_seq=default_seq,
                            default_kind=default_kind, deadline=deadline)
                return existing
            candidate = ScheduledCandidate(
                candidate_id=candidate_id,
                gid=gid,
                demand=demand,
                default_seq=default_seq,
                default_kind=default_kind,
                deadline=deadline,
                created_at=created_at,
            )
            self._candidates[candidate_id] = candidate
            return candidate

    def update(self, candidate_or_id, *, default_seq=None,
               default_kind=None, deadline=None) -> Optional[ScheduledCandidate]:
        with self._lock:
            candidate = self._resolve(candidate_or_id)
            if candidate is None or candidate.state not in ("QUEUED", "WAITING"):
                return None
            if candidate.demand is not None:
                local = candidate.demand.queue_candidate(
                    default_seq=default_seq if default_seq is not None
                    else candidate.default_seq,
                    default_kind=default_kind or candidate.default_kind,
                    deadline=deadline if deadline is not None
                    else candidate.deadline,
                    candidate_id=candidate.candidate_id,
                )
                candidate.default_seq = local.default_seq
                candidate.default_kind = local.default_kind
                candidate.deadline = local.deadline
            else:
                if default_seq is not None:
                    candidate.default_seq = default_seq
                if default_kind is not None:
                    candidate.default_kind = default_kind
                if deadline is not None:
                    candidate.deadline = deadline
            # If it has already been registered with the throttle, update the
            # same waiter in place.  No second limiter is introduced.
            self.throttle.update_waiter(candidate.candidate_id,
                                        deadline=candidate.deadline)
            return candidate

    def withdraw(self, candidate_or_id, reason="cancelled_before_send") -> bool:
        with self._lock:
            candidate = self._resolve(candidate_or_id)
            if candidate is None or candidate.state not in ("QUEUED", "WAITING"):
                return False
            self.throttle.withdraw_waiter(candidate.candidate_id)
            candidate.state = "CANCELLED"
            self._candidates.pop(candidate.candidate_id, None)
            if candidate.demand is not None:
                candidate.demand.withdraw_candidate(reason)
            return True

    def acquire(self, candidate_or_id) -> Optional[ScheduledRequest]:
        """Wait for the shared permit, then freeze the latest demand."""
        with self._lock:
            candidate = self._resolve(candidate_or_id)
            if candidate is None or candidate.state not in ("QUEUED", "WAITING"):
                return None
            candidate.state = "WAITING"
        ticket = self.throttle.acquire(
            candidate.gid, candidate.deadline,
            candidate_id=candidate.candidate_id)
        if ticket is None:
            with self._lock:
                current = self._candidates.pop(candidate.candidate_id, None)
                if current is not None:
                    current.state = "CANCELLED"
            return None
        # Recheck cancellation and freeze the demand while holding the
        # scheduler lock.  ``withdraw`` cannot pass this boundary, so a close
        # racing with permit issuance either wins before admission or observes
        # the already-owned request and leaves it to the coordinator's close.
        with self._lock:
            current = self._candidates.get(candidate.candidate_id)
            if current is None or current.state != "WAITING":
                candidate.state = "CANCELLED"
                return ScheduledRequest(candidate, None, ticket)
            current.state = "ADMITTED"
            request = None
            if current.demand is not None:
                request = current.demand.admit_candidate()
                if request is None:
                    # Permit was consumed by a candidate that was resolved
                    # while waiting.  It cannot be refunded, but its
                    # accounting remains explicit and no HTTP attempt is
                    # made.
                    current.state = "CANCELLED"
            current.state = "IN_FLIGHT" if request is not None else "CANCELLED"
            if current.state == "CANCELLED":
                self._candidates.pop(current.candidate_id, None)
            return ScheduledRequest(current, request, ticket)

    def complete(self, candidate_or_id, *, pending=False):
        with self._lock:
            candidate = self._resolve(candidate_or_id)
            if candidate is None:
                return False
            candidate.state = "QUEUED" if pending else "COMPLETE"
            if not pending:
                self._candidates.pop(candidate.candidate_id, None)
            return True

    def _resolve(self, candidate_or_id) -> Optional[ScheduledCandidate]:
        if isinstance(candidate_or_id, ScheduledCandidate):
            return self._candidates.get(candidate_or_id.candidate_id)
        return self._candidates.get(str(candidate_or_id))


__all__ = ["ScheduledCandidate", "ScheduledRequest", "StateScheduler"]
