"""Probability-valued actor policies used by belief updates and search."""

from __future__ import annotations

from dataclasses import dataclass
import math
import random
from typing import Any, Iterable, Mapping

from ..decision.profile import fingerprint
from ..game import (
    CHOW_HIGH, CHOW_LOW, CHOW_MID, HU, KONG_ADD_BASE, KONG_CLOSED_BASE,
    KONG_OPEN, PASS, PONG,
)
from ..shanten import shanten, ukeire


@dataclass(frozen=True)
class ActionDistribution:
    """A distribution over exactly the supplied legal action order."""

    actions: tuple[int, ...]
    probabilities: tuple[float, ...]
    logits: tuple[float, ...] = ()
    version: str = "action-distribution-v1"

    def __post_init__(self):
        actions = tuple(int(action) for action in self.actions)
        probabilities = tuple(float(value) for value in self.probabilities)
        if len(actions) != len(probabilities) or not actions:
            raise ValueError("actions and probabilities must have equal non-zero length")
        if len(set(actions)) != len(actions):
            raise ValueError("actions must be unique")
        if any(not math.isfinite(value) or value < 0 for value in probabilities):
            raise ValueError("probabilities must be finite and non-negative")
        total = math.fsum(probabilities)
        if total <= 0:
            raise ValueError("probabilities must have positive mass")
        object.__setattr__(self, "actions", actions)
        object.__setattr__(self, "probabilities",
                           tuple(value / total for value in probabilities))
        if self.logits:
            object.__setattr__(self, "logits", tuple(float(x) for x in self.logits))

    def probability(self, action: int) -> float:
        try:
            return self.probabilities[self.actions.index(int(action))]
        except ValueError:
            return 0.0

    def as_json(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "actions": list(self.actions),
            "probabilities": list(self.probabilities),
            "logits": list(self.logits),
        }


class ActorPolicy:
    """Interface shared by belief likelihood and search rollout actors."""

    version = "actor-policy-v1"

    def distribution(self, actor_view, legal_actions) -> ActionDistribution:
        raise NotImplementedError

    def probability(self, action, actor_view, legal_actions) -> float:
        if int(action) not in {int(value) for value in legal_actions}:
            return 0.0
        return self.distribution(actor_view, legal_actions).probability(action)

    def choose(self, actor_view, legal_actions, rng=None) -> int:
        distribution = self.distribution(actor_view, legal_actions)
        rng = rng or random.Random(0)
        value = rng.random()
        running = 0.0
        for action, probability in zip(distribution.actions,
                                       distribution.probabilities):
            running += probability
            if value < running:
                return action
        return distribution.actions[-1]

    def as_json(self) -> dict[str, Any]:
        return {"schema": "actor-policy-v1", "version": self.version}

    @property
    def fingerprint(self) -> str:
        return fingerprint(self.as_json(), 24)


def _softmax(values: Iterable[float], temperature: float) -> tuple[float, ...]:
    values = [float(value) / float(temperature) for value in values]
    peak = max(values)
    exps = [math.exp(value - peak) for value in values]
    total = math.fsum(exps)
    return tuple(value / total for value in exps)


def _tile_from_action(action):
    if 0 <= action < 34:
        return action
    if KONG_CLOSED_BASE - 33 <= action <= KONG_CLOSED_BASE:
        return KONG_CLOSED_BASE - action
    if KONG_ADD_BASE - 33 <= action <= KONG_ADD_BASE:
        return KONG_ADD_BASE - action
    return None


class HeuristicLikelihoodPolicy(ActorPolicy):
    """Public/actor-view-only softmax policy for the first belief version.

    The score is intentionally a likelihood model, not a claim that the
    heuristic is optimal.  All legal actions get finite mass; illegal actions
    are rejected by :meth:`probability` before epsilon smoothing in BeliefState.
    """

    def __init__(self, *, evaluator="shape-v2", temperature=1.0,
                 version="heuristic-likelihood-v1"):
        if float(temperature) <= 0 or not math.isfinite(float(temperature)):
            raise ValueError("temperature must be finite and positive")
        self.evaluator = str(evaluator)
        self.temperature = float(temperature)
        self.version = str(version)

    def _score(self, view, action, legal):
        action = int(action)
        if action == HU:
            return 100.0
        if action == PASS:
            return 0.0
        if action == PONG:
            return 3.0
        if action == KONG_OPEN:
            return 4.0
        if CHOW_HIGH <= action <= CHOW_LOW:
            return 2.0 - 0.05 * (action - CHOW_HIGH)
        tile = _tile_from_action(action)
        if tile is None:
            return -10.0
        hand = list(view.hands[view.current_seat()])
        locked = len(view.melds[view.current_seat()])
        if action >= 0:
            if hand[action] <= 0:
                return -math.inf
            hand[action] -= 1
            current = shanten(hand, locked)
            visible = view.visible_counts(view.current_seat())
            availability = ukeire(hand, locked, visible)[2]
            return -10.0 * current + availability / 34.0 - action * 1e-8
        # A self-kong is useful but keep it below an immediate HU and above a
        # neutral pass in the default likelihood model.
        return 4.5 - tile * 1e-8

    def distribution(self, actor_view, legal_actions) -> ActionDistribution:
        actions = tuple(int(action) for action in legal_actions)
        if not actions:
            raise ValueError("actor policy received an empty legal set")
        logits = tuple(self._score(actor_view, action, actions) for action in actions)
        if any(not math.isfinite(value) for value in logits):
            # This is only expected for a malformed actor view.  Keep the
            # failure explicit instead of manufacturing an action.
            raise ValueError("actor policy produced a non-finite legal-action score")
        probabilities = _softmax(logits, self.temperature)
        return ActionDistribution(actions, probabilities, logits, self.version)

    def as_json(self):
        return {
            "schema": "actor-policy-v1", "version": self.version,
            "kind": "heuristic-likelihood", "evaluator": self.evaluator,
            "temperature": self.temperature,
        }


class PolicyNetworkActorPolicy(ActorPolicy):
    """Adapter from :class:`PolicyValueNet` to the actor-policy interface."""

    def __init__(self, model, *, version=None):
        self.model = model
        self.version = version or getattr(model, "model_version", "policy-v1")

    def distribution(self, actor_view, legal_actions):
        return self.model.predict_game(actor_view, actor_view.current_seat(),
                                       legal_actions=legal_actions,
                                       distribution_version=self.version)

    def as_json(self):
        manifest = getattr(self.model, "manifest", None)
        return {
            "schema": "actor-policy-v1", "version": self.version,
            "kind": "policy-value-network",
            "model_fingerprint": (manifest.fingerprint if manifest else None),
        }
