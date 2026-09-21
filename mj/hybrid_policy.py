"""Hybrid legacy/learned policy routing for the discard-only v1 scope.

The legacy decision layer owns every non-discard action.  A learned policy is
consulted only after the legacy layer has classified the current decision as
an ordinary discard.  Keeping that gate in one module makes the boundary
testable instead of relying on every caller to remember the action contract.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable, Iterable

from .bot import choose_action
from .game import (
    CHOW_HIGH,
    CHOW_LOW,
    HU,
    KONG_ADD_BASE,
    KONG_CLOSED_BASE,
    KONG_OPEN,
    PASS,
    PONG,
)

DISCARD_FLATS = frozenset(range(34))


class IllegalLearnedAction(ValueError):
    """Raised when strict mode receives an action outside the exposed mask."""


def action_kind(action: int) -> str:
    """Return the stable action class used by audit records."""
    action = int(action)
    if 0 <= action <= 33:
        return "discard"
    if action == HU:
        return "hu"
    if action == PASS:
        return "pass"
    if action == PONG:
        return "pong"
    if action == KONG_OPEN:
        return "kong_open"
    if CHOW_HIGH <= action <= CHOW_LOW:
        return "chow"
    if KONG_CLOSED_BASE - 33 <= action <= KONG_CLOSED_BASE:
        return "kong_closed"
    if KONG_ADD_BASE - 33 <= action <= KONG_ADD_BASE:
        return "kong_add"
    return "unknown"


@dataclass(frozen=True)
class HybridDecision:
    """A gate decision and the action finally selected for execution."""

    seat: int
    phase: str
    legal_actions: tuple[int, ...]
    legacy_action: int
    selected_action: int
    route: str
    reason: str
    learned_action: int | None = None
    fallback: bool = False

    @property
    def learned_allowed(self) -> bool:
        return self.route == "ordinary_discard"

    @property
    def action_kind(self) -> str:
        return action_kind(self.selected_action)

    def as_audit(self, *, executed: bool) -> dict:
        return {
            "seat": self.seat,
            "phase": self.phase,
            "legal_actions": list(self.legal_actions),
            "legacy_action": self.legacy_action,
            "selected_action": self.selected_action,
            "learned_action": self.learned_action,
            "route": self.route,
            "reason": self.reason,
            "fallback": self.fallback,
            "executed": bool(executed),
        }


class HybridPolicy:
    """Route ordinary discards to a learned policy and everything else to legacy.

    ``legacy_policy`` returns an engine action (the same integer accepted by
    :meth:`mj.game.Game.step`).  ``learned_action`` supplied to ``resolve`` is
    the flat discard index 0..33, which is identical to the engine action for
    this v1 scope.  Invalid learned actions use the explicit legacy fallback
    by default; callers such as promotion validation can request strict mode.
    """

    def __init__(self, legacy_policy: Callable | None = None, *,
                 evaluator: str = "legacy",
                 safeguards: Iterable[int] = (),
                 learned_policy: Callable | None = None,
                 learned: Callable | None = None):
        self.legacy_policy = legacy_policy or choose_action
        self.evaluator = evaluator
        self.safeguards = frozenset(int(a) for a in safeguards)
        if learned_policy is not None and learned is not None:
            raise ValueError("pass only one of learned_policy and learned")
        self.learned_policy = learned_policy if learned_policy is not None else learned

    def _legacy_action(self, game, seat: int) -> int:
        if self.legacy_policy is choose_action:
            action = self.legacy_policy(game, seat, evaluator=self.evaluator)
        else:
            action = self.legacy_policy(game, seat)
        if isinstance(action, tuple):
            action = action[0]
        action = int(action)
        legal = tuple(int(a) for a in game.legal_actions())
        if action not in legal:
            raise ValueError(
                f"legacy policy selected illegal action {action}; "
                f"legal={legal}")
        return action

    def gate(self, game, seat: int) -> HybridDecision:
        legal = tuple(int(a) for a in game.legal_actions())
        if not legal:
            raise ValueError("cannot route a completed game")
        legacy = self._legacy_action(game, seat)
        if (0 <= legacy <= 33 and legacy not in self.safeguards
                and legacy in legal):
            route = "ordinary_discard"
            reason = "legacy_gate_ordinary_discard"
        else:
            route = "legacy"
            reason = f"legacy_gate_{action_kind(legacy)}"
        return HybridDecision(
            seat=int(seat), phase=str(getattr(game, "phase", "unknown")),
            legal_actions=legal, legacy_action=legacy,
            selected_action=legacy, route=route, reason=reason)

    def resolve(self, game, seat: int, learned_action: int | None = None,
                *, strict: bool = False,
                decision: HybridDecision | None = None) -> HybridDecision:
        decision = decision or self.gate(game, seat)
        if not decision.learned_allowed or learned_action is None:
            return decision
        learned = int(learned_action)
        if learned not in DISCARD_FLATS or learned not in decision.legal_actions:
            message = (
                f"learned action {learned} is outside ordinary discard mask; "
                f"legal={decision.legal_actions}")
            if strict:
                raise IllegalLearnedAction(message)
            return replace(
                decision, selected_action=decision.legacy_action,
                learned_action=learned, route="legacy_fallback",
                reason="invalid_learned_action", fallback=True)
        return replace(
            decision, selected_action=learned, learned_action=learned,
            route="learned_discard", reason="learned_ordinary_discard")

    def select(self, game, seat: int, *, strict: bool = False) -> HybridDecision:
        """Select through the configured learned callback, if one is present.

        The callback uses the simple legacy-compatible ``(game, seat)``
        signature and may return an engine/flat discard action.  This helper is
        useful for deterministic parity checks such as
        ``HybridPolicy(learned=choose_action)``.
        """
        decision = self.gate(game, seat)
        if not decision.learned_allowed or self.learned_policy is None:
            return decision
        learned_action = self.learned_policy(game, seat)
        if isinstance(learned_action, tuple):
            learned_action = learned_action[0]
        return self.resolve(game, seat, int(learned_action),
                            strict=strict, decision=decision)


__all__ = [
    "DISCARD_FLATS", "HybridDecision", "HybridPolicy",
    "IllegalLearnedAction", "action_kind",
]
