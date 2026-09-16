"""Candidate state pool, cheap scoring and deterministic active sampling.

The pool holds pre-teacher states collected from a policy rollout.  Only
actively selected states consume the expensive teacher; the rest stay
available for later generations.  Sampling is deterministic for a given
(profile, pool, seed) and never silently drops a bucket's quota.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import math
from pathlib import Path
from typing import Any, Iterable, Mapping

from ..decision.profile import fingerprint

POOL_SCHEMA = "search-candidate-pool-v1"
SAMPLING_SCHEMA = "active-sampling-profile-v1"
SOURCES = ("normal", "disagreement", "hard", "special", "random")
DEFAULT_RATIOS = (("normal", 0.40), ("disagreement", 0.20), ("hard", 0.20),
                  ("special", 0.15), ("random", 0.05))
DEFAULT_IMPORTANCE = (("normal", 1.0), ("disagreement", 1.4), ("hard", 1.6),
                      ("special", 1.3), ("random", 0.7))


def _finite(value, name):
    if value is None:
        return None
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    return value


@dataclass(frozen=True)
class ActiveSamplingProfile:
    schema: str = SAMPLING_SCHEMA
    version: str = "active-sampling-v1"
    ratios: tuple[tuple[str, float], ...] = DEFAULT_RATIOS
    importance: tuple[tuple[str, float], ...] = DEFAULT_IMPORTANCE
    hard_regret_threshold: float = 8.0
    hard_true_regret_threshold: float = 24.0
    disagreement_min_q_gap: float = 0.5
    entropy_normal_max: float = 0.35
    seed: int = 0

    def __post_init__(self):
        if self.schema != SAMPLING_SCHEMA:
            raise ValueError(f"unsupported sampling schema: {self.schema}")
        ratios = tuple((str(name), _finite(weight, "sampling ratio"))
                       for name, weight in (self.ratios or ()))
        if tuple(name for name, _ in ratios) != SOURCES:
            raise ValueError("sampling ratios must declare every source once")
        if any(weight is None or weight < 0 for _, weight in ratios):
            raise ValueError("sampling ratios must be non-negative")
        total = sum(weight for _, weight in ratios)
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"sampling ratios must sum to 1, got {total}")
        importance = tuple((str(name), _finite(weight, "importance weight"))
                           for name, weight in (self.importance or ()))
        if tuple(name for name, _ in importance) != SOURCES:
            raise ValueError("importance weights must declare every source once")
        if any(weight is None or weight <= 0 for _, weight in importance):
            raise ValueError("importance weights must be positive")
        for field_name in ("hard_regret_threshold",
                           "hard_true_regret_threshold",
                           "disagreement_min_q_gap", "entropy_normal_max"):
            value = _finite(getattr(self, field_name), field_name)
            if value is None or value < 0:
                raise ValueError(f"{field_name} must be non-negative")
            object.__setattr__(self, field_name, value)
        object.__setattr__(self, "ratios", ratios)
        object.__setattr__(self, "importance", importance)
        object.__setattr__(self, "seed", int(self.seed))

    @property
    def ratio_map(self):
        return dict(self.ratios)

    @property
    def importance_map(self):
        return dict(self.importance)

    def payload(self):
        value = asdict(self)
        value["ratios"] = [list(row) for row in self.ratios]
        value["importance"] = [list(row) for row in self.importance]
        return value

    @property
    def fingerprint(self):
        return fingerprint(self.payload(), 24)

    def as_json(self):
        value = self.payload()
        value["fingerprint"] = self.fingerprint
        return value


def active_sampling_profile_from_json(data: Mapping[str, Any]):
    value = dict(data)
    supplied = value.pop("fingerprint", None)
    allowed = set(ActiveSamplingProfile.__dataclass_fields__)
    unknown = set(value) - allowed
    if unknown:
        raise ValueError("unknown active sampling fields: " +
                         ", ".join(sorted(unknown)))
    profile = ActiveSamplingProfile(**value)
    if supplied is not None and supplied != profile.fingerprint:
        raise ValueError("active sampling fingerprint mismatch")
    return profile


@dataclass
class CandidateState:
    """One pre-teacher hero decision state with cheap student evidence."""

    state_id: str
    source_group: str
    generation: int
    policy_version_source: str
    context: Mapping[str, Any]
    history: Mapping[str, Any] | None
    legal_mask: tuple[bool, ...]
    planes: Any = None
    scalars: Any = None
    policy_action: int | None = None
    policy_prob_by_action: Mapping[int, float] = field(default_factory=dict)
    policy_entropy: float | None = None
    special_state_tags: tuple[str, ...] = ()
    phase: str = ""
    dealer: int = 0
    hero_seat: int = 0
    you_cai_bi_kao: bool = False
    shanten: int | None = None
    wall_remaining: int | None = None
    cheap_teacher_action: int | None = None
    cheap_teacher_q: Mapping[int, float] = field(default_factory=dict)
    disagreement: bool = False
    state_source: str = "normal"
    importance_factor: float = 1.0
    policy_regret: float | None = None

    def __post_init__(self):
        if not str(self.state_id) or not str(self.source_group):
            raise ValueError("candidate state requires state_id/source_group")
        mask = tuple(bool(value) for value in self.legal_mask)
        if len(mask) != 109:
            raise ValueError("candidate legal_mask must have 109 actions")
        if not any(mask):
            raise ValueError("candidate legal_mask has no legal action")
        object.__setattr__(self, "legal_mask", mask)
        if self.state_source not in SOURCES + ("forced", "reference"):
            raise ValueError(f"unknown candidate state_source: {self.state_source}")
        object.__setattr__(self, "policy_prob_by_action",
                           {int(key): float(value) for key, value in
                            dict(self.policy_prob_by_action or {}).items()})
        object.__setattr__(self, "cheap_teacher_q",
                           {int(key): float(value) for key, value in
                            dict(self.cheap_teacher_q or {}).items()})

    def as_json(self, *, include_features=True):
        value = {
            "schema": POOL_SCHEMA, "state_id": self.state_id,
            "source_group": self.source_group,
            "generation": int(self.generation),
            "policy_version_source": self.policy_version_source,
            "context": dict(self.context),
            "history": (dict(self.history) if self.history is not None
                        else None),
            "legal_mask": list(self.legal_mask),
            "policy_action": self.policy_action,
            "policy_prob_by_action": {str(key): item for key, item
                                      in self.policy_prob_by_action.items()},
            "policy_entropy": self.policy_entropy,
            "policy_regret": self.policy_regret,
            "special_state_tags": list(self.special_state_tags),
            "phase": self.phase, "dealer": int(self.dealer),
            "hero_seat": int(self.hero_seat),
            "you_cai_bi_kao": bool(self.you_cai_bi_kao),
            "shanten": self.shanten, "wall_remaining": self.wall_remaining,
            "cheap_teacher_action": self.cheap_teacher_action,
            "cheap_teacher_q": {str(key): item for key, item
                                in self.cheap_teacher_q.items()},
            "disagreement": bool(self.disagreement),
            "state_source": self.state_source,
            "importance_factor": self.importance_factor,
            "oracle": False,
        }
        if include_features:
            if self.planes is not None:
                value["planes"] = (self.planes.tolist()
                                   if hasattr(self.planes, "tolist")
                                   else self.planes)
            if self.scalars is not None:
                value["scalars"] = (self.scalars.tolist()
                                    if hasattr(self.scalars, "tolist")
                                    else self.scalars)
        value["fingerprint"] = fingerprint(value, 24)
        return value

    @classmethod
    def from_json(cls, data: Mapping[str, Any]):
        value = dict(data)
        supplied = value.pop("fingerprint", None)
        value.pop("schema", None)
        if value.pop("oracle", False):
            raise ValueError("candidate pool must set oracle=False")
        allowed = set(cls.__dataclass_fields__)
        unknown = set(value) - allowed
        if unknown:
            raise ValueError("unknown candidate fields: " +
                             ", ".join(sorted(unknown)))
        candidate = cls(**value)
        if supplied is not None and supplied != candidate.as_json()["fingerprint"]:
            raise ValueError("candidate fingerprint mismatch")
        return candidate


def write_candidate_pool(path, candidates: Iterable[CandidateState],
                         *, include_features=True):
    Path(path).write_text(
        "".join(json.dumps(candidate.as_json(include_features=include_features),
                           ensure_ascii=False) + "\n"
                for candidate in candidates), encoding="utf-8")
    return path


def read_candidate_pool(path):
    rows = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(CandidateState.from_json(json.loads(line)))
    return rows


def classify_candidate(*, legal_mask, policy_action, policy_prob_by_action,
                       special_tags=(), critical_tags=(), q_by_action=None,
                       cheap_teacher_action=None, previously_hard=False,
                       profile: ActiveSamplingProfile | None = None):
    """Assign one candidate to a declared source with an importance hint.

    Priority: forced > historical hard > expensive mistake (hard) >
    special > disagreement > normal.  ``random`` is a sampler bucket, not a
    classifier output.
    """
    profile = profile or ActiveSamplingProfile()
    legal = [index for index, ok in enumerate(legal_mask) if ok]
    if not legal:
        raise ValueError("candidate has no legal action")
    if len(legal) == 1:
        return "forced", 1.0, None
    if previously_hard:
        return "hard", profile.importance_map["hard"], None
    regret = None
    chosen = None if policy_action is None else int(policy_action)
    if q_by_action:
        q_values = {int(key): float(value) for key, value in q_by_action.items()}
        best = max(q_values.values())
        if chosen is not None and chosen in q_values:
            regret = best - q_values[chosen]
        if regret is not None and regret >= profile.hard_true_regret_threshold:
            return "hard", profile.importance_map["hard"], regret
        if regret is not None and regret >= profile.hard_regret_threshold:
            return "hard", profile.importance_map["hard"], regret
        best_action = max(q_values, key=q_values.get)
        if (chosen is not None and chosen != best_action and
                q_values[best_action] - q_values.get(chosen, -math.inf) >=
                profile.disagreement_min_q_gap):
            return ("disagreement",
                    profile.importance_map["disagreement"], regret)
    elif cheap_teacher_action is not None and chosen is not None:
        if int(cheap_teacher_action) != chosen:
            return "disagreement", profile.importance_map["disagreement"], None
    if set(special_tags) & set(critical_tags):
        return "special", profile.importance_map["special"], regret
    if chosen is not None and chosen != max(legal, key=lambda action:
                                            float(policy_prob_by_action.get(action, 0.0))):
        return "disagreement", profile.importance_map["disagreement"], regret
    entropy = None
    if policy_prob_by_action:
        probabilities = [max(0.0, float(value))
                         for value in policy_prob_by_action.values()]
        total = sum(probabilities)
        if total > 0:
            entropy = -sum((value / total) * math.log(value / total)
                           for value in probabilities if value > 0)
            entropy = entropy / math.log(max(2, len(probabilities)))
    if entropy is not None and entropy > profile.entropy_normal_max:
        return "disagreement", profile.importance_map["disagreement"], regret
    return "normal", profile.importance_map["normal"], regret


def _score_for_source(candidate: CandidateState, source: str):
    if source == "hard":
        return -(candidate.policy_regret if candidate.policy_regret is not None
                 else 0.0)
    if source == "disagreement":
        probabilities = candidate.policy_prob_by_action
        chosen = candidate.policy_action
        return -(float(probabilities.get(chosen, 0.0)) if chosen is not None
                 else 0.0)
    return 0.0


def sample_pool(candidates: Iterable[CandidateState], *,
                batch_size: int, profile: ActiveSamplingProfile | None = None,
                seed: int | None = None):
    """Deterministic quota sampling with short-bucket redistribution."""
    profile = profile or ActiveSamplingProfile()
    seed = int(profile.seed if seed is None else seed)
    candidates = list(candidates)
    if int(batch_size) <= 0:
        raise ValueError("batch_size must be positive")
    pool_by_source: dict[str, list[CandidateState]] = {name: []
                                                       for name in SOURCES}
    forced = []
    for candidate in candidates:
        if candidate.state_source == "forced":
            forced.append(candidate)
            continue
        if candidate.state_source not in SOURCES:
            continue
        pool_by_source[candidate.state_source].append(candidate)
    for source in SOURCES:
        pool_by_source[source].sort(key=lambda item: (
            _score_for_source(item, source),
            -int(fingerprint({"seed": seed, "state_id": item.state_id}, 16), 16)))
    target = min(int(batch_size), len(candidates) - len(forced))
    quotas = {}
    remainders = []
    for name, ratio in profile.ratios:
        exact = target * float(ratio)
        quotas[name] = int(math.floor(exact))
        remainders.append((exact - quotas[name], name))
    remaining = target - sum(quotas.values())
    for _, name in sorted(remainders, key=lambda item: (-item[0], item[1])):
        if remaining <= 0:
            break
        quotas[name] += 1
        remaining -= 1
    selected: list[CandidateState] = []
    used = set()
    for name in SOURCES:
        if name == "random":
            continue
        taken = 0
        for candidate in pool_by_source[name]:
            if taken >= quotas[name]:
                break
            if candidate.state_id in used:
                continue
            selected.append(candidate)
            used.add(candidate.state_id)
            taken += 1
    # "random" is a bucket without its own classification: draw it from all
    # remaining candidates deterministically.
    random_candidates = [candidate for candidate in candidates
                         if candidate.state_id not in used and
                         candidate.state_source != "forced"]
    random_candidates.sort(key=lambda item: -int(fingerprint(
        {"seed": seed, "state_id": item.state_id}, 16), 16))
    for candidate in random_candidates[:quotas["random"]]:
        selected.append(candidate)
        used.add(candidate.state_id)
    shortfall = target - len(selected)
    if shortfall > 0:
        leftovers = [candidate for source in ("normal", "special",
                                              "disagreement", "hard")
                     for candidate in pool_by_source[source]
                     if candidate.state_id not in used]
        leftovers.sort(key=lambda item: (
            -int(fingerprint({"seed": seed, "state_id": item.state_id}, 16), 16)))
        for candidate in leftovers:
            if shortfall <= 0:
                break
            selected.append(candidate)
            used.add(candidate.state_id)
            shortfall -= 1
    return selected


def pool_manifest(candidates: Iterable[CandidateState], *,
                  profile: ActiveSamplingProfile, selected=None):
    candidates = list(candidates)
    counts: dict[str, int] = {}
    for candidate in candidates:
        counts[candidate.state_source] = counts.get(candidate.state_source, 0) + 1
    value = {
        "schema": "search-candidate-pool-manifest-v1",
        "count": len(candidates),
        "by_source": dict(sorted(counts.items())),
        "source_groups": sorted({candidate.source_group
                                 for candidate in candidates}),
        "generations": sorted({int(candidate.generation)
                               for candidate in candidates}),
        "sampling_profile": profile.as_json(),
        "selected_count": (len(selected) if selected is not None else None),
        "selected_by_source": (
            dict(sorted({source: sum(1 for item in selected
                                     if item.state_source == source)
                         for source in SOURCES}.items()))
            if selected is not None else None),
        "oracle": False,
    }
    value["fingerprint"] = fingerprint(value, 24)
    return value
