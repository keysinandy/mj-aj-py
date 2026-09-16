"""Versioned, serialisable evaluator profiles.

Profiles are data contracts rather than implicit module globals.  Every
parameter that can affect candidate ordering is included in the canonical
payload and therefore in the short fingerprint written to evidence logs.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict, field
import hashlib
import json
import math
from typing import Any, Mapping


PROFILE_SCHEMA = "bot-ev-discard/profile-v1"
VALID_SCOPES = ("discard", "hu-piao", "all-root")
DEFAULT_BOUND_VERSION = "reward-envelope-v1"
VALID_BOUND_MODES = (
    "derived", "override", "legacy_conservative_fallback", "unknown",
)


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
    rules_version: str = "hangzhou-platform-guide-v34"
    belief_version: str = "uniform_unseen-v1"
    continuation_version: str = "frozen_shape_v1_self_kong_v1"
    kernel_version: str = "python-frontier-v1"
    tail_version: str = "zero-v1"
    horizon: int = 2
    node_budget: int = 4096
    # shape-v2 is evaluated inside the platform decision window.  Keep the
    # wider 36ms budget explicit in its profile; shape-v1 has an independent
    # budget in hand_eval.EvalProfile and is intentionally unchanged.
    time_budget_ms: float = 36.0
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
    # Reward bounds are part of the evaluator contract.  ``reward_upper_bound``
    # remains as the historical scalar override; the explicit fields make its
    # semantics visible in profile fingerprints and artifacts.
    bound_version: str = DEFAULT_BOUND_VERSION
    bound_mode: str = "derived"
    bound_override: float | None = None
    allow_legacy_bound_fallback: bool = False
    tau: Mapping[str, float] = field(default_factory=dict)
    lut_buckets: tuple = ()
    min_bucket_samples: int = 64

    def __post_init__(self):
        if self.bound_version is None or not str(self.bound_version):
            raise ValueError("bound_version must not be empty")
        object.__setattr__(self, "bound_version", str(self.bound_version))
        object.__setattr__(self, "bound_mode", str(self.bound_mode))
        if self.scope not in VALID_SCOPES:
            raise ValueError(f"unknown evaluator scope: {self.scope}")
        if self.horizon < 0:
            raise ValueError("horizon must be non-negative")
        if self.node_budget < 0 or self.time_budget_ms < 0:
            raise ValueError("budgets must be non-negative")
        if self.min_bucket_samples < 0:
            raise ValueError("min_bucket_samples must be non-negative")
        for name in ("reward_upper_bound", "bound_override"):
            value = getattr(self, name)
            if value is None:
                continue
            try:
                value = float(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{name} must be a finite positive number") from exc
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be a finite positive number")
            object.__setattr__(self, name, value)
        if self.bound_mode not in VALID_BOUND_MODES:
            raise ValueError(f"unknown bound_mode: {self.bound_mode}")
        if (self.reward_upper_bound is not None and
                self.bound_override is not None and
                float(self.reward_upper_bound) != float(self.bound_override)):
            raise ValueError(
                "reward_upper_bound and bound_override disagree")
        if (self.reward_upper_bound is not None or
                self.bound_override is not None):
            object.__setattr__(self, "bound_mode", "override")
        if not self.rules_version:
            raise ValueError("rules_version must not be empty")
        object.__setattr__(self, "tau", dict(sorted(self.tau.items())))
        object.__setattr__(self, "lut_buckets", tuple(self.lut_buckets or ()))

    @property
    def effective_bound_override(self):
        """Return the explicit scalar override, if this profile has one."""
        if self.bound_override is not None:
            return float(self.bound_override)
        if self.reward_upper_bound is not None:
            return float(self.reward_upper_bound)
        return None

    @property
    def reward_bound_version(self):
        """Compatibility spelling used by early offline experiments."""
        return self.bound_version

    @property
    def reward_bound_mode(self):
        return self.bound_mode

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

    @classmethod
    def shape_v2_hu_piao(cls, **changes) -> "ProfileSpec":
        """Return the independently gated HU/财飘 root profile."""
        values = {"scope": "hu-piao", "calibration_kind": "hu-piao"}
        values.update(changes)
        return cls.shape_v2(**values)

    @classmethod
    def shape_v2_all_root(cls, **changes) -> "ProfileSpec":
        """Return the all-root profile after the HU/财飘 gate.

        The scope is explicit in the profile payload and fingerprint.  No
        caller can accidentally turn a discard profile into a KONG/reaction
        profile by reusing an online default.
        """
        values = {"scope": "all-root", "calibration_kind": "all-root",
                  "horizon": 1}
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
