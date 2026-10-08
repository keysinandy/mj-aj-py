"""One cooperative deadline, propagated to native and Python branches."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import replace
import time

_current = ContextVar("legacy_decision_budget", default=None)


class DecisionTimeout(RuntimeError):
    pass


class SearchBudgetController:
    def __init__(self, budget_ms, clock=time.perf_counter):
        self.clock = clock
        self.started = clock()
        self.deadline = self.started + budget_ms / 1000

    @property
    def remaining_ms(self):
        return max(0.0, (self.deadline - self.clock()) * 1000)

    def check(self):
        if self.remaining_ms <= 0:
            raise DecisionTimeout("decision_deadline")

    @contextmanager
    def activate(self):
        token = _current.set(self)
        try:
            yield self
        finally:
            _current.reset(token)

    @staticmethod
    def ambiguous(intervals):
        """Escalation is useful only where a challenger can overlap the leader."""
        intervals = tuple(intervals)
        if len(intervals) < 2:
            return False
        best_lower = max(low for low, high in intervals)
        return sum(high >= best_lower for low, high in intervals) > 1


def cap_ms(value):
    current = _current.get()
    return min(float(value), current.remaining_ms) if current else float(value)


def cap_profile(profile):
    current = _current.get()
    if current is None:
        return profile
    hard = min(profile.hard_budget_ms, current.remaining_ms)
    return replace(profile, hard_budget_ms=hard,
                   soft_budget_ms=min(profile.soft_budget_ms, hard),
                   time_budget_ms=min(profile.time_budget_ms, hard))


def expired():
    current = _current.get()
    return current is not None and current.remaining_ms <= 0
