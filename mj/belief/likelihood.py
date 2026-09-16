"""Compatibility exports and likelihood helpers for belief-v2."""

from __future__ import annotations

import math

from ..models.opponent_policy import (
    ActionDistribution, ActorPolicy, HeuristicLikelihoodPolicy,
    PolicyNetworkActorPolicy,
)


def smoothed_likelihood(policy: ActorPolicy, action, actor_view,
                        legal_actions, epsilon: float) -> float:
    """Apply epsilon smoothing only after the hard legality check."""
    legal = tuple(int(value) for value in legal_actions)
    if int(action) not in legal:
        return 0.0
    probability = float(policy.probability(action, actor_view, legal))
    if not math.isfinite(probability) or probability < 0:
        raise ValueError("actor policy returned an invalid probability")
    epsilon = float(epsilon)
    return (1.0 - epsilon) * probability + epsilon / len(legal)


__all__ = [
    "ActionDistribution", "ActorPolicy", "HeuristicLikelihoodPolicy",
    "PolicyNetworkActorPolicy", "smoothed_likelihood",
]
