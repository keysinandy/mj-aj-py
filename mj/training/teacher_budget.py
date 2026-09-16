"""Deterministic adaptive search-budget schedule for the distillation teacher.

The budget profile is a frozen contract: the tier ladder, promotion
thresholds and the forced-state sanity ratio all enter its fingerprint, so a
dataset can always state which teacher budget produced each row.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any, Mapping

from ..decision.profile import fingerprint

BUDGET_SCHEMA = "teacher-budget-profile-v1"
TEACHER_STATUSES = (
    "pending", "ok", "ambiguous", "incomplete_budget", "failed", "unsupported",
    "forced", "forced-sanity",
)


def _finite(value, name):
    if value is None:
        return None
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    return value


@dataclass(frozen=True)
class TeacherBudgetProfile:
    """Tier ladder and deterministic promotion rules.

    ``tiers`` is ordered from cheapest to most expensive.  ``default_tier``
    is where a training state starts; ``hard_state_tier`` is the minimum tier
    for explicitly hard/disagreement/critical states; ``reference_tier`` is
    reserved for the frozen high-budget reference set.
    """

    schema: str = BUDGET_SCHEMA
    version: str = "teacher-budget-v1"
    tiers: tuple[int, ...] = (512, 2048, 8192, 16000)
    default_tier: int = 1
    hard_state_tier: int = 2
    reference_tier: int = 3
    min_q_gap: float = 2.0
    min_visit_share: float = 0.55
    max_mean_variance: float = 144.0
    escalate_on_ambiguous: bool = True
    escalate_on_incomplete: bool = True
    escalate_on_failure: bool = True
    escalate_on_baseline_disagreement: bool = True
    escalate_on_critical_state: bool = True
    forced_sanity_ratio: float = 0.03
    search_seed: int = 0
    critical_tags: tuple[str, ...] = (
        "hu", "piao", "baotou", "four-white-boards", "luxury-seven-pairs",
        "kong-closed", "kong-add", "kong-open", "zhuada-quan",
    )

    def __post_init__(self):
        if self.schema != BUDGET_SCHEMA:
            raise ValueError(f"unsupported teacher budget schema: {self.schema}")
        if not str(self.version):
            raise ValueError("teacher budget version must not be empty")
        tiers = tuple(int(value) for value in (self.tiers or ()))
        if len(tiers) < 2 or any(value <= 0 for value in tiers):
            raise ValueError("teacher budget tiers must be positive")
        if list(tiers) != sorted(set(tiers)):
            raise ValueError("teacher budget tiers must be strictly increasing")
        for name in ("default_tier", "hard_state_tier", "reference_tier"):
            index = int(getattr(self, name))
            if not 0 <= index < len(tiers):
                raise ValueError(f"{name} is outside the declared tier ladder")
            object.__setattr__(self, name, index)
        if int(self.default_tier) > int(self.hard_state_tier):
            raise ValueError("default tier cannot exceed the hard-state tier")
        if int(self.hard_state_tier) > int(self.reference_tier):
            raise ValueError("hard-state tier cannot exceed the reference tier")
        gap = _finite(self.min_q_gap, "min_q_gap")
        share = _finite(self.min_visit_share, "min_visit_share")
        variance = _finite(self.max_mean_variance, "max_mean_variance")
        if gap is None or gap < 0:
            raise ValueError("min_q_gap must be non-negative")
        if share is None or not 0 < share <= 1:
            raise ValueError("min_visit_share must be in (0, 1]")
        if variance is None or variance < 0:
            raise ValueError("max_mean_variance must be non-negative")
        ratio = _finite(self.forced_sanity_ratio, "forced_sanity_ratio")
        if ratio is None or not 0.01 <= ratio <= 0.05:
            raise ValueError("forced_sanity_ratio must be within 1%-5%")
        object.__setattr__(self, "tiers", tiers)
        object.__setattr__(self, "min_q_gap", float(gap))
        object.__setattr__(self, "min_visit_share", float(share))
        object.__setattr__(self, "max_mean_variance", float(variance))
        object.__setattr__(self, "forced_sanity_ratio", float(ratio))
        object.__setattr__(self, "search_seed", int(self.search_seed))
        tags = tuple(sorted(str(tag) for tag in (self.critical_tags or ())))
        if any(not tag for tag in tags):
            raise ValueError("critical_tags must be non-empty strings")
        object.__setattr__(self, "critical_tags", tags)

    @property
    def max_tier(self):
        return len(self.tiers) - 1

    def budget(self, tier):
        tier = int(tier)
        if not 0 <= tier <= self.max_tier:
            raise ValueError("tier is outside the declared ladder")
        return self.tiers[tier]

    def payload(self):
        value = asdict(self)
        value["tiers"] = list(self.tiers)
        return value

    @property
    def fingerprint(self):
        return fingerprint(self.payload(), 24)

    def as_json(self):
        value = self.payload()
        value["fingerprint"] = self.fingerprint
        return value


def teacher_budget_profile_from_json(data: Mapping[str, Any]) -> TeacherBudgetProfile:
    value = dict(data)
    supplied = value.pop("fingerprint", None)
    allowed = set(TeacherBudgetProfile.__dataclass_fields__)
    unknown = set(value) - allowed
    if unknown:
        raise ValueError("unknown teacher budget fields: " +
                         ", ".join(sorted(unknown)))
    profile = TeacherBudgetProfile(**value)
    if supplied is not None and supplied != profile.fingerprint:
        raise ValueError("teacher budget fingerprint mismatch")
    return profile


@dataclass(frozen=True)
class TeacherEvidence:
    """Current-state evidence available after one search run."""

    status: str = "ok"
    ambiguous: bool = False
    q_gap: float | None = None
    top1_visit_share: float | None = None
    mean_variance: float | None = None
    baseline_disagreement: bool = False
    critical: bool = False

    def __post_init__(self):
        if self.status not in ("ok", "incomplete_budget", "failed", "unsupported"):
            raise ValueError(f"unsupported teacher evidence status: {self.status}")
        object.__setattr__(self, "q_gap", _finite(self.q_gap, "q_gap"))
        object.__setattr__(self, "top1_visit_share",
                           _finite(self.top1_visit_share, "top1_visit_share"))
        object.__setattr__(self, "mean_variance",
                           _finite(self.mean_variance, "mean_variance"))


@dataclass(frozen=True)
class TeacherTierDecision:
    """One immutable budget decision for a single decision state."""

    tier: int
    simulations: int
    status: str
    stop_reason: str
    forced: bool = False
    sanity: bool = False
    escalated: bool = False

    def __post_init__(self):
        if int(self.tier) < -1:
            raise ValueError("tier must be -1 (skip) or non-negative")
        if self.status not in TEACHER_STATUSES:
            raise ValueError(f"unsupported teacher decision status: {self.status}")
        if int(self.simulations) < 0:
            raise ValueError("simulations must be non-negative")

    def as_json(self):
        value = asdict(self)
        value["fingerprint"] = fingerprint(value, 24)
        return value


def forced_sanity_selected(profile: TeacherBudgetProfile, source_key: str):
    """Deterministic 1-5% forced-state subset retained for model sanity."""
    index = int(fingerprint({"forced_sanity": profile.fingerprint,
                             "source_key": str(source_key)}, 16), 16)
    return index % 10000 < int(round(profile.forced_sanity_ratio * 10000))


def forced_decision(profile: TeacherBudgetProfile, *, source_key,
                    forced=True) -> TeacherTierDecision:
    """A forced state consumes no teacher search at all.

    The deterministic 1-5% sanity subset is retained as a labelled row so the
    student still sees forced contexts; the remaining forced states are
    skipped entirely.
    """
    if not forced:
        raise ValueError("forced_decision requires a forced state")
    if forced_sanity_selected(profile, source_key):
        return TeacherTierDecision(
            tier=-1, simulations=0, status="forced-sanity",
            stop_reason="forced_sanity_subset", forced=True, sanity=True)
    return TeacherTierDecision(
        tier=-1, simulations=0, status="forced", stop_reason="forced_no_search",
        forced=True, sanity=False)


def start_decision(profile: TeacherBudgetProfile, *, forced=False,
                   baseline_disagreement=False, critical=False,
                   source_key="") -> TeacherTierDecision:
    """Choose the initial tier before any search simulation is spent."""
    if forced:
        return forced_decision(profile, source_key=source_key)
    tier = int(profile.default_tier)
    reasons = []
    if baseline_disagreement and profile.escalate_on_baseline_disagreement:
        tier = max(tier, int(profile.hard_state_tier))
        reasons.append("baseline_disagreement")
    if critical and profile.escalate_on_critical_state:
        tier = max(tier, int(profile.hard_state_tier))
        reasons.append("critical_state")
    return TeacherTierDecision(
        tier=tier, simulations=profile.budget(tier),
        status="pending", stop_reason="+".join(reasons) or "default_start",
        forced=False, sanity=False)


def escalation_reasons(profile: TeacherBudgetProfile,
                       evidence: TeacherEvidence):
    """Reasons that current evidence is not yet stable enough to stop."""
    reasons = []
    if evidence.status == "failed" and profile.escalate_on_failure:
        reasons.append("failed_search")
    if (evidence.status in ("incomplete_budget", "unsupported")
            and profile.escalate_on_incomplete):
        reasons.append("incomplete_budget")
    if evidence.ambiguous and profile.escalate_on_ambiguous:
        reasons.append("ambiguous")
    if (evidence.q_gap is not None and
            float(evidence.q_gap) < float(profile.min_q_gap)):
        reasons.append("small_q_gap")
    if (evidence.top1_visit_share is not None and
            float(evidence.top1_visit_share) < float(profile.min_visit_share)):
        reasons.append("low_visit_share")
    if (evidence.mean_variance is not None and
            float(evidence.mean_variance) > float(profile.max_mean_variance)):
        reasons.append("high_variance")
    if (evidence.baseline_disagreement and
            profile.escalate_on_baseline_disagreement):
        reasons.append("baseline_disagreement")
    if evidence.critical and profile.escalate_on_critical_state:
        reasons.append("critical_state")
    return tuple(reasons)


def next_decision(profile: TeacherBudgetProfile, current_tier,
                  evidence: TeacherEvidence) -> TeacherTierDecision:
    """Promote one deterministic tier step when evidence demands it."""
    current_tier = int(current_tier)
    if not 0 <= current_tier <= profile.max_tier:
        raise ValueError("current_tier is outside the declared ladder")
    reasons = escalation_reasons(profile, evidence)
    if reasons and current_tier < profile.max_tier:
        target = current_tier + 1
        if (("critical_state" in reasons or "baseline_disagreement" in reasons)
                and target < int(profile.hard_state_tier)):
            target = int(profile.hard_state_tier)
        return TeacherTierDecision(
            tier=target, simulations=profile.budget(target),
            status="pending", stop_reason="+".join(reasons),
            escalated=True)
    if reasons:
        stop_reason = "max_tier:" + "+".join(reasons)
    else:
        stop_reason = "stable"
    return TeacherTierDecision(
        tier=current_tier, simulations=profile.budget(current_tier),
        status="pending", stop_reason=stop_reason, escalated=False)


def final_status(evidence: TeacherEvidence) -> str:
    """Sample status for a state that stopped at the searched tier."""
    if evidence.status != "ok":
        return evidence.status
    return "ambiguous" if evidence.ambiguous else "ok"


def run_decision(profile: TeacherBudgetProfile, first: TeacherTierDecision,
                 evidence: TeacherEvidence) -> TeacherTierDecision:
    """Bookkeeping after the searched tier ``first``.

    Returns a ``pending`` decision at a higher tier when evidence demands
    promotion; callers loop until ``escalated`` is false (or the top tier is
    reached).  A decision that skipped search is returned unchanged.
    """
    if int(first.tier) < 0:
        return first
    follow = next_decision(profile, first.tier, evidence)
    if follow.escalated:
        return TeacherTierDecision(
            tier=follow.tier, simulations=follow.simulations,
            status="pending", stop_reason=follow.stop_reason,
            forced=first.forced, sanity=first.sanity, escalated=True)
    return TeacherTierDecision(
        tier=first.tier, simulations=profile.budget(first.tier),
        status=final_status(evidence), stop_reason=follow.stop_reason,
        forced=first.forced, sanity=first.sanity, escalated=False)
