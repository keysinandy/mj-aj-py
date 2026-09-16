"""Versioned actor and policy/value model contracts.

The actor policy is dependency-light and is imported eagerly.  The neural
model remains lazy because NumPy/Torch are optional for rule, belief and
history consumers (including the online fallback path).
"""

from .opponent_policy import (
    ActionDistribution,
    ActorPolicy,
    HeuristicLikelihoodPolicy,
    PolicyNetworkActorPolicy,
)

__all__ = [
    "ActionDistribution", "ActorPolicy", "HeuristicLikelihoodPolicy",
    "PolicyNetworkActorPolicy", "PolicyValueModelManifest", "PolicyValueNet",
    "ValueFeatureContract", "policy_value_manifest_from_json",
]


def __getattr__(name):
    if name in {
        "PolicyValueModelManifest", "PolicyValueNet", "ValueFeatureContract",
        "policy_value_manifest_from_json",
    }:
        from . import policy_value
        value = getattr(policy_value, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
