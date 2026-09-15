"""Versioned, serialisable evaluator profiles.

Profiles are data contracts rather than implicit module globals.  Every
parameter that can affect candidate ordering is included in the canonical
payload and therefore in the short fingerprint written to evidence logs.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict, field
import hashlib
import json
from typing import Any, Mapping


PROFILE_SCHEMA = "bot-ev-discard/profile-v1"
VALID_SCOPES = ("discard", "hu-piao", "all-root")


class ProfileFingerprintError(ValueError):
    """Raised when a supplied profile fingerprint does not match its data."""


def canonical_json(value: Any) -> str:
    """Return deterministic JSON used by manifests and fingerprints."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def fingerprint(value: Any, length: int = 16) -> str:
    """Hash canonical JSON, keeping the default log-friendly short form."""
    if length <= 0:
        raise ValueError("fingerprint length must be positive")
    digest = hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
    return digest[:length]


@dataclass(frozen=True)
class ProfileSpec:
    """Complete fast/teacher configuration visible to an evaluator."""

    name: str = "shape-v2"
    version: str = "shape-v2"
    scope: str = "discard"
    schema: str = PROFILE_SCHEMA
    reward_units: str = "base-score points"
    reward_name: str = "hero_round_settlement_delta"
    rules_version: str = "hangzhou-platform-guide-v21"
    belief_version: str = "uniform_unseen-v1"
    continuation_version: str = "frozen_shape_v1"
    kernel_version: str = "python-frontier-v1"
    tail_version: str = "zero-v1"
    horizon: int = 2
    node_budget: int = 4096
    time_budget_ms: float = 17.5
    explanation: bool = True
    calibrated: bool = False
    calibration_fingerprint: str = ""
    calibration_kind: str = "none"
    calibration_intercept: float = 0.0
    # Q0 is deliberately a soft score.  No coefficient is a hard shanten or
    # wildcard exclusion.  EV2 is a separate complete layer and is never
    # silently replaced by zero when unavailable.
    q0_shanten_weight: float = -1.0
    q0_u1_weight: float = 0.01
    q0_ev1_weight: float = 0.0
    q0_ev2_weight: float = 1.0
    q0_fan_weight: float = 0.0
    q0_risk_weight: float = 0.0
    reward_upper_bound: float | None = None
    tau: Mapping[str, float] = field(default_factory=dict)
    lut_buckets: tuple = ()
    min_bucket_samples: int = 64

    def __post_init__(self):
        if self.scope not in VALID_SCOPES:
            raise ValueError(f"unknown evaluator scope: {self.scope}")
        if self.horizon < 0:
            raise ValueError("horizon must be non-negative")
        if self.node_budget < 0 or self.time_budget_ms < 0:
            raise ValueError("budgets must be non-negative")
        if self.min_bucket_samples < 0:
            raise ValueError("min_bucket_samples must be non-negative")
        if self.reward_upper_bound is not None and self.reward_upper_bound <= 0:
            raise ValueError("reward_upper_bound must be positive when supplied")
        if not self.rules_version:
            raise ValueError("rules_version must not be empty")
        object.__setattr__(self, "tau", dict(sorted(self.tau.items())))
        object.__setattr__(self, "lut_buckets", tuple(self.lut_buckets or ()))

    @classmethod
    def shape_v2(cls, **changes) -> "ProfileSpec":
        values = {"name": "shape-v2", "version": "shape-v2"}
        values.update(changes)
        return cls(**values)

    @classmethod
    def shape_v2_discard(cls, **changes) -> "ProfileSpec":
        values = {"scope": "discard"}
        values.update(changes)
        return cls.shape_v2(**values)

    def payload(self) -> dict:
        """Canonical profile data excluding its derived fingerprint."""
        value = asdict(self)
        value["tau"] = dict(sorted(value["tau"].items()))
        value["lut_buckets"] = list(value["lut_buckets"])
        return value

    @property
    def fingerprint(self) -> str:
        return fingerprint(self.payload())

    def as_json(self) -> dict:
        value = self.payload()
        value["fingerprint"] = self.fingerprint
        return value


def validate_profile_fingerprint(profile: ProfileSpec | Mapping[str, Any],
                                 expected: str | None = None) -> str:
    """Validate and return a profile fingerprint.

    A mapping may be loaded from an artifact and may contain a derived
    ``fingerprint`` key.  Unknown keys are rejected so a typo cannot silently
    produce a different runtime contract.
    """
    if isinstance(profile, ProfileSpec):
        actual = profile.fingerprint
        supplied = expected
    else:
        data = dict(profile)
        supplied = expected if expected is not None else data.pop("fingerprint", None)
        allowed = set(ProfileSpec().__dataclass_fields__)
        unknown = set(data) - allowed
        if unknown:
            raise ProfileFingerprintError(
                f"unknown profile fields: {', '.join(sorted(unknown))}")
        try:
            actual = ProfileSpec(**data).fingerprint
        except (TypeError, ValueError) as exc:
            raise ProfileFingerprintError(str(exc)) from exc
    if supplied is not None and supplied != actual:
        raise ProfileFingerprintError(
            f"profile fingerprint mismatch: supplied={supplied} actual={actual}")
    return actual


def profile_from_json(data: Mapping[str, Any]) -> ProfileSpec:
    """Load a profile artifact after checking its derived fingerprint."""
    value = dict(data)
    supplied = value.pop("fingerprint", None)
    allowed = set(ProfileSpec().__dataclass_fields__)
    unknown = set(value) - allowed
    if unknown:
        raise ProfileFingerprintError(
            f"unknown profile fields: {', '.join(sorted(unknown))}")
    profile = ProfileSpec(**value)
    if supplied is not None and supplied != profile.fingerprint:
        raise ProfileFingerprintError(
            f"profile fingerprint mismatch: supplied={supplied} actual={profile.fingerprint}")
    return profile
