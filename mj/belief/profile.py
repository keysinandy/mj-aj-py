"""Versioned contracts for the belief posterior.

Profiles are intentionally strict.  A misspelled temperature, resampler or
seed would otherwise produce evidence that looks comparable while describing
a different posterior.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any, Mapping

from ..decision.profile import fingerprint


BELIEF_PROFILE_SCHEMA = "belief-profile-v2"


@dataclass(frozen=True)
class BeliefProfile:
    """All parameters that affect belief initialization or update."""

    schema: str = BELIEF_PROFILE_SCHEMA
    version: str = "belief-v2"
    particle_count: int = 512
    ess_ratio: float = 0.5
    likelihood_policy: str = "heuristic-shape-v2"
    temperature: float = 1.0
    epsilon: float = 0.01
    resampler: str = "systematic-v1"
    rejuvenation: str = "none-v1"
    seed: int = 0
    min_effective_particles: int = 1
    history_schema: str = "information-history-v1"
    compatibility: str = "strict-v1"

    def __post_init__(self):
        if self.schema != BELIEF_PROFILE_SCHEMA:
            raise ValueError(f"unsupported belief profile schema: {self.schema}")
        if not str(self.version):
            raise ValueError("belief profile version must not be empty")
        if int(self.particle_count) <= 0:
            raise ValueError("particle_count must be positive")
        if not 0 < float(self.ess_ratio) <= 1:
            raise ValueError("ess_ratio must be in (0, 1]")
        if not math.isfinite(float(self.temperature)) or float(self.temperature) <= 0:
            raise ValueError("temperature must be finite and positive")
        if not 0 <= float(self.epsilon) < 1:
            raise ValueError("epsilon must be in [0, 1)")
        if self.resampler != "systematic-v1":
            raise ValueError(f"unsupported resampler: {self.resampler}")
        if self.rejuvenation not in ("none-v1", "constraint-swap-v1"):
            raise ValueError(f"unsupported rejuvenation: {self.rejuvenation}")
        if int(self.min_effective_particles) <= 0:
            raise ValueError("min_effective_particles must be positive")
        object.__setattr__(self, "particle_count", int(self.particle_count))
        object.__setattr__(self, "ess_ratio", float(self.ess_ratio))
        object.__setattr__(self, "temperature", float(self.temperature))
        object.__setattr__(self, "epsilon", float(self.epsilon))
        object.__setattr__(self, "seed", int(self.seed))
        object.__setattr__(self, "min_effective_particles",
                           int(self.min_effective_particles))

    def payload(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def fingerprint(self) -> str:
        return fingerprint(self.payload(), 24)

    def as_json(self) -> dict[str, Any]:
        value = self.payload()
        value["fingerprint"] = self.fingerprint
        return value


def belief_profile_from_json(data: Mapping[str, Any]) -> BeliefProfile:
    """Load a profile without silently dropping unknown fields."""
    value = dict(data)
    supplied = value.pop("fingerprint", None)
    allowed = set(BeliefProfile.__dataclass_fields__)
    unknown = set(value) - allowed
    if unknown:
        raise ValueError("unknown belief profile fields: " +
                         ", ".join(sorted(unknown)))
    profile = BeliefProfile(**value)
    if supplied is not None and supplied != profile.fingerprint:
        raise ValueError(
            f"belief profile fingerprint mismatch: supplied={supplied} "
            f"actual={profile.fingerprint}")
    return profile
