"""Strict search-v1 profile contract."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any, Mapping

from ..decision.profile import fingerprint


SEARCH_PROFILE_SCHEMA = "information-set-search-v1"


@dataclass(frozen=True)
class SearchProfile:
    schema: str = SEARCH_PROFILE_SCHEMA
    version: str = "search-v1"
    belief_profile_fingerprint: str = ""
    actor_policy_version: str = "heuristic-likelihood-v1"
    leaf_version: str = "terminal-rollout-v1"
    simulation_budget: int = 2048
    max_depth: int = 64
    wall_clock_ms: float | None = None
    c_puct: float = 1.5
    root_noise: bool = False
    seed: int = 0
    prior_source: str = "actor-policy-v1"
    tie_break: str = "legal-order-v1"
    reward: str = "hero_round_settlement_delta"
    rules_version: str = "hangzhou-platform-guide-v34"
    confidence_alpha: float = 0.05
    ambiguity_margin: float = 0.0

    def __post_init__(self):
        if self.schema != SEARCH_PROFILE_SCHEMA:
            raise ValueError(f"unsupported search profile schema: {self.schema}")
        if not str(self.version) or not str(self.actor_policy_version):
            raise ValueError("search versions must not be empty")
        if int(self.simulation_budget) < 0 or int(self.max_depth) <= 0:
            raise ValueError("search budgets must be non-negative/depth positive")
        if not math.isfinite(float(self.c_puct)) or float(self.c_puct) < 0:
            raise ValueError("c_puct must be finite and non-negative")
        if self.wall_clock_ms is not None and (
                not math.isfinite(float(self.wall_clock_ms)) or
                float(self.wall_clock_ms) < 0):
            raise ValueError("wall_clock_ms must be finite and non-negative")
        if not 0 < float(self.confidence_alpha) < 1:
            raise ValueError("confidence_alpha must be in (0, 1)")
        if float(self.ambiguity_margin) < 0:
            raise ValueError("ambiguity_margin must be non-negative")
        if self.tie_break != "legal-order-v1":
            raise ValueError(f"unsupported tie break: {self.tie_break}")
        if self.reward != "hero_round_settlement_delta":
            raise ValueError(f"unsupported search reward: {self.reward}")
        object.__setattr__(self, "simulation_budget", int(self.simulation_budget))
        object.__setattr__(self, "max_depth", int(self.max_depth))
        object.__setattr__(self, "c_puct", float(self.c_puct))
        object.__setattr__(self, "seed", int(self.seed))
        if self.wall_clock_ms is not None:
            object.__setattr__(self, "wall_clock_ms", float(self.wall_clock_ms))

    def payload(self):
        return asdict(self)

    @property
    def fingerprint(self):
        return fingerprint(self.payload(), 24)

    def as_json(self):
        value = self.payload()
        value["fingerprint"] = self.fingerprint
        return value


def search_profile_from_json(data: Mapping[str, Any]) -> SearchProfile:
    value = dict(data)
    supplied = value.pop("fingerprint", None)
    allowed = set(SearchProfile.__dataclass_fields__)
    unknown = set(value) - allowed
    if unknown:
        raise ValueError("unknown search profile fields: " +
                         ", ".join(sorted(unknown)))
    profile = SearchProfile(**value)
    if supplied is not None and supplied != profile.fingerprint:
        raise ValueError(
            f"search profile fingerprint mismatch: supplied={supplied} "
            f"actual={profile.fingerprint}")
    return profile
