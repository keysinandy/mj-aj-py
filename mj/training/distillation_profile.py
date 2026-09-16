"""Frozen contracts shared by search-teacher distillation generations.

Everything in this module is a data contract: changing a default or a
threshold changes the corresponding fingerprint, which is written into every
dataset/checkpoint artifact and checked by the release gates.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import math
from pathlib import Path
from typing import Any, Iterable, Mapping

from ..decision.profile import fingerprint
from .teacher_budget import TeacherBudgetProfile

DISTILLATION_SCHEMA = "search-distillation-profile-v1"
POPULATION_SCHEMA = "opponent-population-profile-v1"
BASELINE_SCHEMA = "search-teacher-distillation-bc/baseline-freeze-v1"

BASELINE_GIT = "8a94fdeb1a7801289f2bd707b24b271d99bb8961"
RULES_VERSION = "hangzhou-platform-guide-v34"
SCORE_UNITS = "hero_round_score_points"

# Frozen with the belief-search-policy-iteration evaluation plan.  Every
# dataset split assignment is derived from these source-game seed ranges, so
# a sample can always be traced back to its owning split.
FROZEN_SPLITS = {
    "train": {"seed_start": 240000, "games": 1024},
    "validation": {"seed_start": 241024, "games": 1024},
    "final_test": {"seed_start": 242048, "games": 8192},
}

# Engine/source files covered by the baseline rule fingerprint.
BASELINE_SOURCE_FILES = (
    "mj/game.py",
    "mj/scoring.py",
    "mj/win.py",
    "mj/features.py",
    "mj/model.py",
    "mj/training/search_data.py",
    "mj/training/policy_value_train.py",
    "mj/search/pomcp.py",
    "mj/search/profile.py",
    "mj/search/report.py",
    "mj/decision/profile.py",
    "mj/decision/policy_v3.py",
    "mj/models/policy_value.py",
)


def file_digest(path) -> str:
    """SHA-256 of one file, used to freeze rule/engine sources."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def source_digests(root="."):
    root = Path(root)
    return {name: file_digest(root / name) for name in BASELINE_SOURCE_FILES}


def _finite(value, name, *, allow_none=True):
    if value is None:
        if allow_none:
            return None
        raise ValueError(f"{name} must not be None")
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    return value


@dataclass(frozen=True)
class OpponentPopulationProfile:
    """Frozen weighted opponent population for trajectory generation."""

    schema: str = POPULATION_SCHEMA
    version: str = "opponent-population-v1"
    members: tuple[tuple[str, float], ...] = (
        ("legacy", 1.0), ("shape-v1", 1.0), ("shape-v2", 1.0))

    def __post_init__(self):
        if self.schema != POPULATION_SCHEMA:
            raise ValueError(f"unsupported opponent population schema: {self.schema}")
        if not str(self.version):
            raise ValueError("opponent population version must not be empty")
        rows = []
        for member in self.members or ():
            if len(member) != 2:
                raise ValueError("population member must be (version, weight)")
            name, weight = str(member[0]), _finite(member[1], "member weight",
                                                   allow_none=False)
            if not name:
                raise ValueError("population member version must not be empty")
            if weight <= 0:
                raise ValueError("population member weight must be positive")
            rows.append((name, weight))
        if not rows:
            raise ValueError("opponent population must declare at least one member")
        names = [name for name, _ in rows]
        if len(set(names)) != len(names):
            raise ValueError("opponent population member versions must be unique")
        object.__setattr__(self, "members", tuple(rows))

    def payload(self):
        value = asdict(self)
        value["members"] = [list(row) for row in self.members]
        return value

    @property
    def fingerprint(self):
        return fingerprint(self.payload(), 24)

    def as_json(self):
        value = self.payload()
        value["fingerprint"] = self.fingerprint
        return value

    def member_for(self, *, generation, source_group, seat):
        """Deterministic weighted pick for one seat of one source game."""
        key = {
            "population": self.fingerprint, "generation": int(generation),
            "source_group": str(source_group), "seat": int(seat) % 4,
        }
        index = int(fingerprint(key, 16), 16)
        total = sum(weight for _, weight in self.members)
        position = (index / float(2 ** 64)) * total
        for name, weight in self.members:
            position -= weight
            if position < 0:
                return name
        return self.members[-1][0]

    def version_of(self, name):
        for member, _ in self.members:
            if member == name:
                return f"{self.version}:{member}"
        raise ValueError(f"unknown population member: {name!r}")


def opponent_population_from_json(data: Mapping[str, Any]) -> OpponentPopulationProfile:
    value = dict(data)
    supplied = value.pop("fingerprint", None)
    allowed = set(OpponentPopulationProfile.__dataclass_fields__)
    unknown = set(value) - allowed
    if unknown:
        raise ValueError("unknown opponent population fields: " +
                         ", ".join(sorted(unknown)))
    profile = OpponentPopulationProfile(**value)
    if supplied is not None and supplied != profile.fingerprint:
        raise ValueError("opponent population fingerprint mismatch")
    return profile


def feature_contract_fingerprint() -> str:
    """Fingerprint of the value/policy feature contract (lazy import)."""
    from ..models.policy_value import ValueFeatureContract
    return ValueFeatureContract().fingerprint


def baseline_profile_fingerprints():
    """Default belief/search/model/runtime contract fingerprints."""
    from ..belief.profile import BeliefProfile
    from ..decision.policy_v3 import PolicyV3Profile
    from ..models.policy_value import PolicyValueModelManifest
    from ..search.profile import SearchProfile
    return {
        "belief_profile": BeliefProfile().fingerprint,
        "search_profile": SearchProfile().fingerprint,
        "model_manifest": PolicyValueModelManifest().fingerprint,
        "runtime_profile": PolicyV3Profile().fingerprint,
        "feature_contract": feature_contract_fingerprint(),
    }


@dataclass(frozen=True)
class SearchDistillationProfile:
    """Recipe for one distillation generation (targets, weights, provenance).

    Phase 1 is policy-only: ``value_weight`` must stay zero until the Value v2
    transform contract has a declared fingerprint.
    """

    schema: str = DISTILLATION_SCHEMA
    version: str = "search-distill-v1"
    generation: int = 0
    policy_version_source: str = ""
    teacher_budget_fingerprint: str = ""
    feature_contract_fingerprint: str = ""
    belief_profile_fingerprint: str = ""
    search_profile_fingerprint: str = ""
    opponent_population_fingerprint: str = ""
    leaf_version: str = "terminal-rollout-v1"
    continuation_version: str = "heuristic-likelihood-v1"
    rules_version: str = RULES_VERSION
    score_units: str = SCORE_UNITS
    target_mode: str = "visit"
    lambda_visit: float = 1.0
    lambda_q: float = 0.0
    tau_q: float = 4.0
    full_evidence_simulations: int = 2048
    ambiguity_weight: float = 0.5
    reset_weight: float = 0.5
    tau_gap: float = 2.0
    min_importance_weight: float = 0.25
    variance_scale: float | None = None
    catastrophic_regret_threshold: float = 24.0
    value_weight: float = 0.0
    value_contract_fingerprint: str = ""
    critical_bucket_minimums: tuple[tuple[str, int], ...] = (
        ("hu", 128), ("piao", 16), ("baotou", 16),
        ("four-white-boards", 8), ("seven-pairs", 32),
        ("luxury-seven-pairs", 4), ("kong-closed", 16), ("kong-add", 16),
        ("kong-open", 16), ("pong-pass", 32), ("chow-pass", 32),
        ("zhuada-quan", 16), ("wall-tail", 64), ("reaction-cursor", 64),
        ("wild-discard-possible", 32),
    )
    oracle: bool = False

    def __post_init__(self):
        if self.schema != DISTILLATION_SCHEMA:
            raise ValueError(f"unsupported distillation schema: {self.schema}")
        if not str(self.version):
            raise ValueError("distillation version must not be empty")
        if int(self.generation) < 0:
            raise ValueError("generation must be non-negative")
        if self.oracle:
            raise ValueError("search distillation must set oracle=False")
        if self.target_mode not in ("visit", "q-soft", "hybrid"):
            raise ValueError(f"unsupported policy target mode: {self.target_mode}")
        if self.rules_version != RULES_VERSION:
            raise ValueError("distillation profile must target the frozen rules")
        if self.score_units != SCORE_UNITS:
            raise ValueError("distillation profile must target round-score points")
        lambda_visit = _finite(self.lambda_visit, "lambda_visit", allow_none=False)
        lambda_q = _finite(self.lambda_q, "lambda_q", allow_none=False)
        if lambda_visit < 0 or lambda_q < 0 or lambda_visit + lambda_q <= 0:
            raise ValueError("policy mixing weights must be non-negative with mass")
        if self.target_mode == "visit" and lambda_q != 0:
            raise ValueError("visit-only mode requires lambda_q=0")
        if self.target_mode == "q-soft" and lambda_visit != 0:
            raise ValueError("q-soft-only mode requires lambda_visit=0")
        if self.target_mode == "hybrid" and not (lambda_visit > 0 and lambda_q > 0):
            raise ValueError("hybrid mode requires both mixing weights")
        tau_q = _finite(self.tau_q, "tau_q", allow_none=False)
        if tau_q <= 0:
            raise ValueError("tau_q must be positive")
        tau_gap = _finite(self.tau_gap, "tau_gap", allow_none=False)
        if tau_gap <= 0:
            raise ValueError("tau_gap must be positive")
        if not 0 <= _finite(self.min_importance_weight, "min_importance_weight",
                            allow_none=False) <= 1:
            raise ValueError("min_importance_weight must be in [0, 1]")
        for name in ("ambiguity_weight", "reset_weight"):
            value = _finite(getattr(self, name), name, allow_none=False)
            if not 0 <= value <= 1:
                raise ValueError(f"{name} must be in [0, 1]")
            object.__setattr__(self, name, value)
        if int(self.full_evidence_simulations) <= 0:
            raise ValueError("full_evidence_simulations must be positive")
        threshold = _finite(self.catastrophic_regret_threshold,
                            "catastrophic_regret_threshold", allow_none=False)
        if threshold <= 0:
            raise ValueError("catastrophic_regret_threshold must be positive")
        value_weight = _finite(self.value_weight, "value_weight", allow_none=False)
        if value_weight < 0:
            raise ValueError("value_weight must be non-negative")
        if value_weight > 0 and not self.value_contract_fingerprint:
            raise ValueError("value training requires a Value v2 contract fingerprint")
        if self.variance_scale is not None:
            scale = _finite(self.variance_scale, "variance_scale", allow_none=False)
            if scale <= 0:
                raise ValueError("variance_scale must be positive")
            object.__setattr__(self, "variance_scale", scale)
        rows = []
        for requirement in self.critical_bucket_minimums or ():
            if len(requirement) != 2:
                raise ValueError("coverage requirement must be (tag, minimum)")
            tag, minimum = str(requirement[0]), int(requirement[1])
            if not tag:
                raise ValueError("coverage requirement tag must not be empty")
            if minimum <= 0:
                raise ValueError("coverage requirement minimum must be positive")
            rows.append((tag, minimum))
        tags = [tag for tag, _ in rows]
        if len(set(tags)) != len(tags):
            raise ValueError("coverage requirement tags must be unique")
        object.__setattr__(self, "critical_bucket_minimums", tuple(rows))
        object.__setattr__(self, "generation", int(self.generation))
        object.__setattr__(self, "lambda_visit", lambda_visit)
        object.__setattr__(self, "lambda_q", lambda_q)
        object.__setattr__(self, "tau_q", tau_q)
        object.__setattr__(self, "tau_gap", tau_gap)
        object.__setattr__(self, "full_evidence_simulations",
                           int(self.full_evidence_simulations))
        object.__setattr__(self, "catastrophic_regret_threshold", threshold)
        object.__setattr__(self, "value_weight", value_weight)

    def payload(self):
        return asdict(self)

    @property
    def fingerprint(self):
        return fingerprint(self.payload(), 24)

    def as_json(self):
        value = self.payload()
        value["fingerprint"] = self.fingerprint
        return value


def search_distillation_profile_from_json(data: Mapping[str, Any]):
    value = dict(data)
    supplied = value.pop("fingerprint", None)
    allowed = set(SearchDistillationProfile.__dataclass_fields__)
    unknown = set(value) - allowed
    if unknown:
        raise ValueError("unknown distillation profile fields: " +
                         ", ".join(sorted(unknown)))
    profile = SearchDistillationProfile(**value)
    if supplied is not None and supplied != profile.fingerprint:
        raise ValueError("distillation profile fingerprint mismatch")
    return profile


# ---------------------------------------------------------------------------
# Special-state tags


def special_state_tags(game, seat):
    """Return the deterministic critical-rule tags for one decision state.

    Tags only use public/hero-private material.  They are coverage buckets for
    dataset and release reports, never a hidden-world identity.
    """
    from ..game import (CHOW_LOW, HU, KONG_ADD_BASE, KONG_CLOSED_BASE,
                        KONG_OPEN, PASS, PONG)
    from ..tiles import W
    from ..win import is_baotou, is_chiitoi

    seat = int(seat)
    tags = set()
    tags.add("dealer" if seat == game.dealer else "non-dealer")
    tags.add("ycbk-on" if bool(getattr(game, "you_cai_bi_kao", False))
             else "ycbk-off")
    if int(game.live_wall_left()) <= 10:
        tags.add("wall-tail")
    if int(getattr(game, "freeze", 0)) > 0 or game.in_freeze(seat):
        tags.add("zhuada-quan")
    if str(game.phase).startswith("response") or game.phase == "react":
        tags.add("reaction-cursor")
    legal = set(int(action) for action in game.legal_actions())
    melds = game.melds[seat]
    locked = len(melds)
    concealed = list(game.hands[seat])
    if HU in legal:
        tags.add("hu")
        standing = list(concealed)
        drawn = game.drawn[seat]
        if drawn is not None and standing[drawn] > 0:
            standing[drawn] -= 1
        if is_baotou(standing, locked):
            tags.add("baotou")
        chain_piao = int(game.chain_piao[seat])
        if chain_piao > 0:
            tags.add("piao")
        if concealed[W] + chain_piao == 4:
            tags.add("four-white-boards")
        if locked == 0:
            chiitoi, groups = is_chiitoi(concealed)
            if chiitoi:
                tags.add("seven-pairs")
                if groups:
                    tags.add("luxury-seven-pairs")
    if W in legal:
        tags.add("wild-discard-possible")
    if KONG_OPEN in legal:
        tags.add("kong-open")
    if any(KONG_CLOSED_BASE - 33 <= action <= KONG_CLOSED_BASE
           for action in legal):
        tags.add("kong-closed")
    if any(KONG_ADD_BASE - 33 <= action <= KONG_ADD_BASE for action in legal):
        tags.add("kong-add")
    if "reaction-cursor" in tags:
        if PONG in legal and PASS in legal:
            tags.add("pong-pass")
        if any(CHOW_LOW - 2 <= action <= CHOW_LOW for action in legal) \
                and PASS in legal:
            tags.add("chow-pass")
    if "seven-pairs" not in tags and locked == 0:
        pairs = sum(count // 2 for count in concealed[:33]) + concealed[W] // 2
        if pairs >= 5:
            tags.add("seven-pairs-shape")
    return tuple(sorted(tags))


# ---------------------------------------------------------------------------
# pi0 selection


@dataclass(frozen=True)
class PolicyCandidate:
    """One low-latency candidate measured on the frozen reference set."""

    name: str
    mean_reference_regret: float | None
    p95_reference_regret: float | None = None
    paired_score_ci: tuple[float, float] | None = None
    batch1_latency_ms: float | None = None
    illegal_actions: int = 0
    nonfinite_outputs: int = 0

    def __post_init__(self):
        if not str(self.name):
            raise ValueError("policy candidate name must not be empty")
        if self.illegal_actions < 0 or self.nonfinite_outputs < 0:
            raise ValueError("policy candidate counters must be non-negative")
        object.__setattr__(self, "illegal_actions", int(self.illegal_actions))
        object.__setattr__(self, "nonfinite_outputs",
                           int(self.nonfinite_outputs))

    def passes_runtime_gate(self, *, max_latency_ms, max_p95_regret=None):
        reasons = []
        if self.mean_reference_regret is None:
            reasons.append("missing_mean_regret")
        if self.illegal_actions:
            reasons.append("illegal_actions")
        if self.nonfinite_outputs:
            reasons.append("nonfinite_outputs")
        if self.batch1_latency_ms is None:
            reasons.append("missing_latency")
        elif float(self.batch1_latency_ms) > float(max_latency_ms):
            reasons.append("latency")
        if (max_p95_regret is not None and self.p95_reference_regret is not None
                and float(self.p95_reference_regret) > float(max_p95_regret)):
            reasons.append("p95_regret")
        return (not reasons), tuple(reasons)


def select_strongest_fast_policy(candidates: Iterable[PolicyCandidate], *,
                                 max_latency_ms,
                                 max_p95_regret=None) -> Mapping[str, Any]:
    """Freeze the ``pi0`` choice: fastest regret first, never evaluator name.

    Ranking is (mean reference regret ascending, paired-score CI lower bound
    descending, latency ascending, name) so equal evidence still resolves
    deterministically.  Accuracy/top1 is deliberately not an input.
    """
    rows = []
    for candidate in candidates:
        passed, reasons = candidate.passes_runtime_gate(
            max_latency_ms=max_latency_ms, max_p95_regret=max_p95_regret)
        ci_lower = (float(candidate.paired_score_ci[0])
                    if candidate.paired_score_ci else float("-inf"))
        rows.append({
            "name": candidate.name,
            "eligible": passed,
            "reasons": list(reasons),
            "mean_reference_regret": candidate.mean_reference_regret,
            "p95_reference_regret": candidate.p95_reference_regret,
            "paired_score_ci": (list(candidate.paired_score_ci)
                                if candidate.paired_score_ci else None),
            "batch1_latency_ms": candidate.batch1_latency_ms,
            "sort_key": [candidate.mean_reference_regret,
                         -ci_lower,
                         candidate.batch1_latency_ms, candidate.name],
        })
    eligible = [row for row in rows if row["eligible"]]
    eligible.sort(key=lambda row: row["sort_key"])
    selected = eligible[0]["name"] if eligible else None
    return {
        "schema": "pi0-selection-v1",
        "selected": selected,
        "reason": ("lowest_reference_regret_with_paired_score_tiebreak"
                   if eligible else "no_runtime_eligible_candidate"),
        "ranking": [{"name": row["name"], "eligible": row["eligible"],
                     "reasons": row["reasons"],
                     "mean_reference_regret": row["mean_reference_regret"],
                     "p95_reference_regret": row["p95_reference_regret"],
                     "paired_score_ci": row["paired_score_ci"],
                     "batch1_latency_ms": row["batch1_latency_ms"]}
                    for row in eligible] + [row for row in rows
                                            if not row["eligible"]],
        "fingerprint": fingerprint({
            "candidates": [row["sort_key"] for row in rows],
            "max_latency_ms": float(max_latency_ms),
            "max_p95_regret": max_p95_regret,
            "selected": selected,
        }, 24),
    }


# ---------------------------------------------------------------------------
# Baseline freeze


def baseline_freeze(*, root=".", git_revision=BASELINE_GIT,
                    teacher_budget: TeacherBudgetProfile | None = None,
                    distillation: SearchDistillationProfile | None = None,
                    opponent_population: OpponentPopulationProfile | None = None,
                    splits: Mapping[str, Any] | None = None):
    """Freeze HEAD, rules, profile fingerprints and split schedules."""
    teacher_budget = teacher_budget or TeacherBudgetProfile()
    distillation = distillation or SearchDistillationProfile()
    opponent_population = opponent_population or OpponentPopulationProfile()
    value = {
        "schema": BASELINE_SCHEMA,
        "git_revision": str(git_revision),
        "rules_version": RULES_VERSION,
        "score_units": SCORE_UNITS,
        "oracle": False,
        "splits": {name: dict(row) for name, row in
                   (splits or FROZEN_SPLITS).items()},
        "fingerprints": {
            "teacher_budget": teacher_budget.fingerprint,
            "search_distillation": distillation.fingerprint,
            "opponent_population": opponent_population.fingerprint,
            **baseline_profile_fingerprints(),
        },
        "profiles": {
            "teacher_budget": teacher_budget.as_json(),
            "search_distillation": distillation.as_json(),
            "opponent_population": opponent_population.as_json(),
        },
        "source_sha256": source_digests(root),
    }
    value["fingerprint"] = fingerprint(value, 24)
    return value


def write_baseline_freeze(path, **kwargs):
    import json

    value = baseline_freeze(**kwargs)
    Path(path).write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    return value
