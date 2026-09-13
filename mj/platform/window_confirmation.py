"""Owned window-confirmation state and timing provenance."""

from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Any, Optional

from .state_demand import WindowAttemptKey, _json_value


@dataclass
class WindowTiming:
    """The four independent clocks used by confirmation scheduling."""

    not_before: Optional[float] = None
    scheduler_deadline: Optional[float] = None
    observation_budget_deadline: Optional[float] = None
    exact_window_deadline: Optional[float] = None
    not_before_source: str = "unknown"
    scheduler_deadline_source: str = "unknown"
    observation_budget_source: str = "unknown"
    exact_deadline_source: str = "authoritative_snapshot"

    def as_json(self) -> dict[str, Any]:
        return {
            "not_before": self.not_before,
            "scheduler_deadline": self.scheduler_deadline,
            "observation_budget_deadline": self.observation_budget_deadline,
            "exact_window_deadline": self.exact_window_deadline,
            "not_before_source": self.not_before_source,
            "scheduler_deadline_source": self.scheduler_deadline_source,
            "observation_budget_source": self.observation_budget_source,
            "exact_deadline_source": self.exact_deadline_source,
        }


@dataclass
class WindowConfirmation:
    """Mutable observation state for exactly one WindowAttemptKey."""

    window_attempt_key: Optional[WindowAttemptKey]
    expected_phase: Any
    timing: WindowTiming = field(default_factory=WindowTiming)
    legal: tuple[Any, ...] = ()
    pending: Optional[tuple[Any, Any]] = None
    revision: Optional[int] = None
    created_at: float = field(default_factory=time.monotonic)
    pending_retries: int = 0
    outcome: str = "PENDING"
    generation: Optional[int] = None
    successor_of: Optional[str] = None
    observation_budget_exhausted: bool = False

    @property
    def not_before(self):
        return self.timing.not_before

    @property
    def scheduler_deadline(self):
        return self.timing.scheduler_deadline

    @property
    def observation_budget_deadline(self):
        return self.timing.observation_budget_deadline

    @property
    def exact_window_deadline(self):
        return self.timing.exact_window_deadline

    @property
    def exact_deadline_authoritative(self) -> bool:
        return self.exact_window_deadline is not None

    def observe(self, outcome: str, *, revision=None,
                exact_window_deadline=None, reason=None) -> bool:
        """Record an outcome only for the same logical confirmation."""
        if revision is not None and self.revision is not None \
                and revision != self.revision:
            return False
        if exact_window_deadline is not None:
            self.timing.exact_window_deadline = exact_window_deadline
        self.outcome = str(outcome)
        if self.outcome in ("PENDING", "unconfirmed", "phase_pending"):
            self.pending_retries += 1
        return True

    def budget_exhausted(self, now=None) -> bool:
        now = time.monotonic() if now is None else now
        deadline = self.observation_budget_deadline
        return deadline is not None and now >= deadline

    def can_successor(self, now=None) -> bool:
        if self.outcome not in ("PENDING", "unconfirmed", "phase_pending"):
            return False
        return not self.budget_exhausted(now)

    def carry_budget(self, successor: "WindowConfirmation") -> "WindowConfirmation":
        """Carry only the observation budget for the same key and phase."""
        if (self.window_attempt_key != successor.window_attempt_key
                or self.expected_phase != successor.expected_phase):
            return successor
        successor.pending_retries = max(
            self.pending_retries, successor.pending_retries)
        if successor.timing.observation_budget_deadline is None:
            successor.timing.observation_budget_deadline = \
                self.observation_budget_deadline
        elif self.observation_budget_deadline is not None:
            successor.timing.observation_budget_deadline = min(
                successor.timing.observation_budget_deadline,
                self.observation_budget_deadline)
        successor.successor_of = self.successor_of
        return successor

    def as_json(self) -> dict[str, Any]:
        return {
            "window_attempt_key": (_json_value(self.window_attempt_key)
                                   if self.window_attempt_key is not None else None),
            "expected_phase": self.expected_phase,
            "timing": self.timing.as_json(),
            "legal": list(self.legal),
            "pending": self.pending,
            "revision": self.revision,
            "created_at": self.created_at,
            "pending_retries": self.pending_retries,
            "outcome": self.outcome,
            "generation": self.generation,
            "successor_of": self.successor_of,
            "confirmation_observation_budget_exhausted": (
                self.observation_budget_exhausted),
        }


__all__ = ["WindowConfirmation", "WindowTiming"]
