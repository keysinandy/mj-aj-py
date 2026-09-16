"""Versioned search-teacher samples without sampled hidden identities."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable, Mapping

from ..decision.profile import fingerprint


FEATURE_FINGERPRINT_PREFIX = "search-distill-features-v1"


def feature_fingerprint(planes, scalars=None):
    """Content digest of one sample's feature arrays.

    The digest lets the dataset quality report detect feature/target
    collisions and verify that a resumed run reproduced identical features.
    An empty string means the sample carries no features.
    """
    if planes is None and scalars is None:
        return ""
    import numpy as np

    digest = hashlib.sha256(FEATURE_FINGERPRINT_PREFIX.encode("utf-8"))
    for value in (planes, scalars):
        if value is None:
            digest.update(b"none")
            continue
        array = np.ascontiguousarray(np.asarray(value, dtype=np.float32))
        digest.update(repr(tuple(array.shape)).encode("utf-8"))
        digest.update(array.tobytes())
    return digest.hexdigest()[:24]


def _action_to_flat(action):
    """Map an engine action without importing the optional feature stack."""
    from ..game import (CHOW_LOW, HU, KONG_ADD_BASE, KONG_CLOSED_BASE,
                        KONG_OPEN, PASS, PONG)

    action = int(action)
    if action >= 0:
        if action < 34:
            return action
    elif action == PASS:
        return 34
    elif CHOW_LOW - 2 <= action <= CHOW_LOW:
        return 35 + (CHOW_LOW - action)
    elif action == PONG:
        return 38
    elif action == KONG_OPEN:
        return 39
    elif KONG_CLOSED_BASE - 33 <= action <= KONG_CLOSED_BASE:
        return 40 + (KONG_CLOSED_BASE - action)
    elif KONG_ADD_BASE - 33 <= action <= KONG_ADD_BASE:
        return 74 + (KONG_ADD_BASE - action)
    elif action == HU:
        return 108
    raise ValueError(f"unsupported engine action: {action}")


def work_identity(*, source_group, context_hash, history_hash, teacher_seed,
                  search_fingerprint):
    """Stable resume identity shared by the generator and the sample type."""
    return fingerprint({
        "schema": "search-distillation-work-v1",
        "source_group": str(source_group),
        "context_hash": str(context_hash),
        "history_hash": str(history_hash),
        "teacher_seed": int(teacher_seed),
        "search_fingerprint": str(search_fingerprint),
    }, 24)


def _finite(value, name):
    if value is None:
        return None
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    return value


def _action_mapping(value):
    if value is None:
        return {}
    return {int(key): _finite(item, "action value")
            for key, item in dict(value).items()}


@dataclass(frozen=True)
class SearchSample:
    """One information-state root sample and all teacher evidence."""

    context_hash: str
    history_hash: str
    legal_mask: tuple[bool, ...]
    visit_counts: Mapping[int, int]
    q_by_action: Mapping[int, float]
    root_value: float | None
    simulations: int
    ambiguous: bool
    confidence: float | None
    source_group: str
    belief_fingerprint: str
    search_fingerprint: str
    opponent_policy_version: str
    leaf_version: str
    policy_version_source: str = ""
    actual_round_score: float | None = None
    planes: Any = None
    scalars: Any = None
    reset_count: int = 0
    search_variance: float | None = None
    oracle: bool = False
    value_target_source: str = "search-root-v1"
    terminal_reward: float | None = None
    # Generation and teacher-budget provenance.
    generation: int = 0
    feature_fingerprint: str = ""
    forced: bool = False
    special_state_tags: tuple[str, ...] = ()
    teacher_budget_tier: int = 0
    teacher_requested_simulations: int = 0
    teacher_completed_simulations: int = 0
    teacher_failed_simulations: int = 0
    teacher_seed: int = 0
    teacher_status: str = ""
    teacher_stop_reason: str = ""
    teacher_top1_q: float | None = None
    teacher_top2_q: float | None = None
    teacher_q_gap: float | None = None
    # Public decision-bucket metadata (never hidden identities).
    phase: str = ""
    dealer: int = 0
    hero_seat: int = 0
    you_cai_bi_kao: bool = False
    shanten: int | None = None
    wall_remaining: int | None = None
    schema: str = "search-distillation-sample-v1"

    def __post_init__(self):
        if self.schema != "search-distillation-sample-v1":
            raise ValueError(f"unsupported search sample schema: {self.schema}")
        if not self.context_hash or not self.history_hash or not self.source_group:
            raise ValueError("context/history/source_group are required")
        if bool(self.oracle):
            raise ValueError("search distillation samples must set oracle=False")
        mask = tuple(bool(value) for value in self.legal_mask)
        if len(mask) != 109:
            raise ValueError("legal_mask must have 109 actions")
        visits = {int(key): int(value) for key, value in
                  dict(self.visit_counts or {}).items()}
        if any(value < 0 for value in visits.values()):
            raise ValueError("visit counts must be non-negative")
        q_values = _action_mapping(self.q_by_action)
        for action in set(visits) | set(q_values):
            if not 0 <= action < 109:
                raise ValueError("search sample action index out of range")
            if not mask[action]:
                raise ValueError("search sample contains an illegal action value")
        if int(self.simulations) < 0:
            raise ValueError("simulations must be non-negative")
        object.__setattr__(self, "legal_mask", mask)
        object.__setattr__(self, "visit_counts", visits)
        object.__setattr__(self, "q_by_action", q_values)
        object.__setattr__(self, "root_value", _finite(self.root_value, "root_value"))
        object.__setattr__(self, "actual_round_score",
                           _finite(self.actual_round_score, "actual_round_score"))
        terminal_reward = _finite(self.terminal_reward, "terminal_reward")
        if (terminal_reward is not None and self.actual_round_score is not None
                and terminal_reward != self.actual_round_score):
            raise ValueError("terminal_reward disagrees with actual_round_score")
        if terminal_reward is None:
            terminal_reward = self.actual_round_score
        object.__setattr__(self, "terminal_reward", terminal_reward)
        object.__setattr__(self, "confidence", _finite(self.confidence, "confidence"))
        object.__setattr__(self, "search_variance",
                           _finite(self.search_variance, "search_variance"))
        object.__setattr__(self, "simulations", int(self.simulations))
        object.__setattr__(self, "reset_count", int(self.reset_count))
        if int(self.generation) < 0:
            raise ValueError("generation must be non-negative")
        legal_count = sum(1 for value in mask if value)
        if bool(self.forced) and legal_count != 1:
            raise ValueError("forced samples must have exactly one legal action")
        if int(self.teacher_budget_tier) < -1:
            raise ValueError("teacher_budget_tier must be -1 (skip) or non-negative")
        requested = int(self.teacher_requested_simulations)
        completed = int(self.teacher_completed_simulations)
        failed = int(self.teacher_failed_simulations)
        if requested < 0 or completed < 0 or failed < 0:
            raise ValueError("teacher simulation counters must be non-negative")
        if completed > requested:
            raise ValueError("teacher completed simulations exceed the request")
        if int(self.teacher_seed) < 0:
            raise ValueError("teacher_seed must be non-negative")
        if self.teacher_status and self.teacher_status not in (
                "ok", "ambiguous", "incomplete_budget", "failed", "unsupported",
                "forced", "forced-sanity"):
            raise ValueError(f"unknown teacher status: {self.teacher_status}")
        top1 = _finite(self.teacher_top1_q, "teacher_top1_q")
        top2 = _finite(self.teacher_top2_q, "teacher_top2_q")
        gap = _finite(self.teacher_q_gap, "teacher_q_gap")
        if gap is not None and gap < 0:
            raise ValueError("teacher_q_gap must be non-negative")
        if gap is not None and top1 is not None and top2 is not None:
            if abs((top1 - top2) - gap) > 1e-9:
                raise ValueError("teacher_q_gap disagrees with top1/top2 Q values")
        object.__setattr__(self, "teacher_top1_q", top1)
        object.__setattr__(self, "teacher_top2_q", top2)
        object.__setattr__(self, "teacher_q_gap", gap)
        tags = tuple(sorted(str(tag) for tag in (self.special_state_tags or ())))
        if any(not tag for tag in tags):
            raise ValueError("special_state_tags must be non-empty strings")
        object.__setattr__(self, "special_state_tags", tags)
        object.__setattr__(self, "generation", int(self.generation))
        object.__setattr__(self, "teacher_requested_simulations", requested)
        object.__setattr__(self, "teacher_completed_simulations", completed)
        object.__setattr__(self, "teacher_failed_simulations", failed)
        object.__setattr__(self, "teacher_seed", int(self.teacher_seed))
        if int(self.dealer) not in range(4) or int(self.hero_seat) not in range(4):
            raise ValueError("dealer/hero_seat must be a seat index")
        if self.shanten is not None:
            shanten = int(self.shanten)
            if shanten < -1:
                raise ValueError("shanten must be -1 or non-negative")
            object.__setattr__(self, "shanten", shanten)
        if self.wall_remaining is not None:
            remaining = int(self.wall_remaining)
            if remaining < 0:
                raise ValueError("wall_remaining must be non-negative")
            object.__setattr__(self, "wall_remaining", remaining)
        object.__setattr__(self, "dealer", int(self.dealer))
        object.__setattr__(self, "hero_seat", int(self.hero_seat))
        object.__setattr__(self, "you_cai_bi_kao", bool(self.you_cai_bi_kao))

    @property
    def work_id(self):
        """Stable resume identity for one teacher work item.

        Two generator runs that declare the same source group, information
        state, teacher seed and search profile must produce the same identity
        so an interrupted run can resume without double-counting rows.
        """
        return work_identity(
            source_group=self.source_group, context_hash=self.context_hash,
            history_hash=self.history_hash, teacher_seed=self.teacher_seed,
            search_fingerprint=self.search_fingerprint)
    @property
    def policy_target(self):
        total = sum(self.visit_counts.get(action, 0)
                    for action, legal in enumerate(self.legal_mask) if legal)
        if total <= 0:
            return {action: 0.0 for action, legal in enumerate(self.legal_mask)
                    if legal}
        return {action: self.visit_counts.get(action, 0) / total
                for action, legal in enumerate(self.legal_mask) if legal}

    @property
    def sample_weight(self):
        # Low evidence, ambiguity and resets are down-weighted rather than
        # discarded.  Score variance is deliberately absent here: raw
        # score-squared units must never silently suppress a critical state.
        # Unit-safe variance/Q-gap weighting belongs to the versioned
        # SearchDistillationProfile used by the trainer.
        simulation_factor = min(1.0, self.simulations / 2048.0)
        ambiguity_factor = 0.5 if self.ambiguous else 1.0
        reset_factor = 0.5 ** max(0, self.reset_count)
        return max(0.0, simulation_factor * ambiguity_factor * reset_factor)

    def as_json(self, *, include_features=True):
        value = {
            "schema": self.schema, "context_hash": self.context_hash,
            "history_hash": self.history_hash,
            "legal_mask": list(self.legal_mask),
            "visit_counts": {str(k): v for k, v in self.visit_counts.items()},
            "q_by_action": {str(k): v for k, v in self.q_by_action.items()},
            "root_value": self.root_value,
            "actual_round_score": self.actual_round_score,
            "terminal_reward": self.terminal_reward,
            "value_target_source": self.value_target_source,
            "simulations": self.simulations, "ambiguous": self.ambiguous,
            "confidence": self.confidence, "sample_weight": self.sample_weight,
            "source_group": self.source_group,
            "belief_fingerprint": self.belief_fingerprint,
            "search_fingerprint": self.search_fingerprint,
            "opponent_policy_version": self.opponent_policy_version,
            "leaf_version": self.leaf_version,
            "policy_version_source": self.policy_version_source,
            "reset_count": self.reset_count, "search_variance": self.search_variance,
            "generation": self.generation,
            "feature_fingerprint": self.feature_fingerprint,
            "forced": bool(self.forced),
            "special_state_tags": list(self.special_state_tags),
            "teacher_budget_tier": self.teacher_budget_tier,
            "teacher_requested_simulations": self.teacher_requested_simulations,
            "teacher_completed_simulations": self.teacher_completed_simulations,
            "teacher_failed_simulations": self.teacher_failed_simulations,
            "teacher_seed": self.teacher_seed,
            "teacher_status": self.teacher_status,
            "teacher_stop_reason": self.teacher_stop_reason,
            "teacher_top1_q": self.teacher_top1_q,
            "teacher_top2_q": self.teacher_top2_q,
            "teacher_q_gap": self.teacher_q_gap,
            "phase": self.phase,
            "dealer": self.dealer,
            "hero_seat": self.hero_seat,
            "you_cai_bi_kao": bool(self.you_cai_bi_kao),
            "shanten": self.shanten,
            "wall_remaining": self.wall_remaining,
            "oracle": False,
        }
        if include_features:
            if self.planes is not None:
                value["planes"] = self.planes.tolist() if hasattr(self.planes, "tolist") else self.planes
            if self.scalars is not None:
                value["scalars"] = self.scalars.tolist() if hasattr(self.scalars, "tolist") else self.scalars
        value["fingerprint"] = fingerprint(value, 24)
        return value

    @classmethod
    def from_json(cls, data: Mapping[str, Any]):
        value = dict(data)
        supplied_fingerprint = value.pop("fingerprint", None)
        supplied_weight = value.pop("sample_weight", None)
        allowed = set(cls.__dataclass_fields__)
        unknown = set(value) - allowed
        if unknown:
            raise ValueError("unknown search sample fields: " +
                             ", ".join(sorted(unknown)))
        sample = cls(**value)
        if (supplied_weight is not None and
                abs(float(supplied_weight) - sample.sample_weight) > 1e-12):
            raise ValueError("search sample weight mismatch")
        if (supplied_fingerprint is not None and
                supplied_fingerprint != sample.as_json()["fingerprint"]):
            raise ValueError("search sample fingerprint mismatch")
        return sample

    @classmethod
    def from_search_result(cls, result, *, source_group, context=None,
                           history=None, belief=None,
                           opponent_policy_version="", policy_version_source="",
                           actual_round_score=None, planes=None, scalars=None,
                           generation=0, feature_fingerprint_value=None,
                           forced=False, special_state_tags=(),
                           teacher_seed=0, teacher_stop_reason="",
                           teacher_tier=None, teacher_status=None,
                           phase="", dealer=0, hero_seat=0,
                           you_cai_bi_kao=False, shanten=None,
                           wall_remaining=None):
        legal_mask = [False] * 109
        for action in result.legal_actions:
            legal_mask[_action_to_flat(action)] = True
        visits = {_action_to_flat(action): count
                  for action, count in result.visit_counts.items()}
        q_values = {_action_to_flat(action): value
                    for action, value in result.q_by_action.items()}
        search_variance = (sum(result.variance_by_action.values()) /
                           len(result.variance_by_action)
                           if result.variance_by_action else None)
        ranked = sorted(q_values.values(), reverse=True)
        top1 = ranked[0] if ranked else None
        top2 = ranked[1] if len(ranked) > 1 else None
        gap = (float(top1) - float(top2) if top1 is not None and
               top2 is not None else None)
        if teacher_tier is None:
            teacher_tier = -1 if forced and not result.simulations else 0
        return cls(
            context_hash=result.context_hash,
            history_hash=result.history_hash,
            legal_mask=tuple(legal_mask), visit_counts=visits,
            q_by_action=q_values, root_value=result.root_value,
            actual_round_score=actual_round_score,
            terminal_reward=actual_round_score,
            simulations=result.simulations, ambiguous=result.ambiguous,
            confidence=result.confidence, source_group=str(source_group),
            belief_fingerprint=result.belief_fingerprint,
            search_fingerprint=result.search_profile_fingerprint,
            opponent_policy_version=opponent_policy_version,
            leaf_version=getattr(result, "leaf_version", "terminal-rollout-v1"),
            policy_version_source=policy_version_source, planes=planes,
            scalars=scalars, search_variance=search_variance,
            reset_count=(getattr(belief, "reset_count", 0) if belief else 0),
            generation=int(generation),
            feature_fingerprint=(feature_fingerprint_value
                                 if feature_fingerprint_value is not None
                                 else feature_fingerprint(planes, scalars)),
            forced=bool(forced), special_state_tags=special_state_tags,
            teacher_budget_tier=int(teacher_tier),
            teacher_requested_simulations=int(result.requested_simulations),
            teacher_completed_simulations=int(result.simulations),
            teacher_failed_simulations=int(result.failed_simulations),
            teacher_seed=int(teacher_seed),
            teacher_status=(teacher_status if teacher_status is not None
                            else result.status),
            teacher_stop_reason=str(teacher_stop_reason),
            teacher_top1_q=top1, teacher_top2_q=top2, teacher_q_gap=gap,
            phase=str(phase), dealer=int(dealer), hero_seat=int(hero_seat),
            you_cai_bi_kao=bool(you_cai_bi_kao), shanten=shanten,
            wall_remaining=wall_remaining,
        )


class SearchDataset:
    def __init__(self, samples: Iterable[SearchSample] = ()):
        self.samples = list(samples)
        if any(not isinstance(sample, SearchSample) for sample in self.samples):
            raise TypeError("SearchDataset accepts SearchSample values")

    def append(self, sample: SearchSample):
        if not isinstance(sample, SearchSample):
            raise TypeError("dataset sample must be SearchSample")
        self.samples.append(sample)

    def __len__(self):
        return len(self.samples)

    @property
    def fingerprint(self):
        return dataset_fingerprint(self)

    def split_by_source_group(self, assignments: Mapping[str, str]):
        groups = {str(key): str(value) for key, value in assignments.items()}
        output = {}
        for sample in self.samples:
            split = groups.get(sample.source_group)
            if split is None:
                raise ValueError(f"missing split assignment for {sample.source_group}")
            output.setdefault(split, SearchDataset()).append(sample)
        return output

    def as_json(self, *, include_features=True):
        return {"schema": "search-distillation-dataset-v1",
                "count": len(self.samples),
                "source_groups": sorted({s.source_group for s in self.samples}),
                "oracle": False,
                "samples": [sample.as_json(include_features=include_features)
                            for sample in self.samples]}

    @classmethod
    def from_json(cls, data: Mapping[str, Any]):
        value = dict(data)
        supplied_schema = value.pop("schema", "search-distillation-dataset-v1")
        if supplied_schema != "search-distillation-dataset-v1":
            raise ValueError(f"unsupported search dataset schema: {supplied_schema}")
        count = value.pop("count", None)
        source_groups = value.pop("source_groups", None)
        oracle = value.pop("oracle", False)
        samples = value.pop("samples", None)
        if value:
            raise ValueError("unknown search dataset fields: " +
                             ", ".join(sorted(value)))
        if not isinstance(samples, list):
            raise ValueError("search dataset samples must be a list")
        dataset = cls(SearchSample.from_json(item) for item in samples)
        if count is not None and int(count) != len(dataset):
            raise ValueError("search dataset count mismatch")
        if oracle:
            raise ValueError("search dataset must set oracle=False")
        actual_groups = sorted({sample.source_group for sample in dataset.samples})
        if source_groups is not None and sorted(source_groups) != actual_groups:
            raise ValueError("search dataset source_groups mismatch")
        return dataset


def freeze_source_splits(source_groups: Iterable[str], *, seed=0,
                         train_ratio=.8, validation_ratio=.1):
    """Assign whole source groups to train/validation/final-test deterministically."""
    groups = sorted({str(group) for group in source_groups})
    if not 0 <= float(train_ratio) < 1 or not 0 <= float(validation_ratio) < 1:
        raise ValueError("split ratios must be in [0,1)")
    if float(train_ratio) + float(validation_ratio) >= 1:
        raise ValueError("train plus validation ratios must be below one")
    ordered = sorted(groups, key=lambda group: fingerprint({"seed": int(seed), "group": group}, 24))
    n = len(ordered)
    n_train = int(n * float(train_ratio))
    n_validation = int(n * float(validation_ratio))
    if n and n_train == 0:
        n_train = 1
    if n_train + n_validation >= n and n > 1:
        n_validation = max(0, n - n_train - 1)
    return {group: ("train" if index < n_train else
                   "validation" if index < n_train + n_validation else
                   "final-test") for index, group in enumerate(ordered)}


def write_search_dataset(path, dataset: SearchDataset | Iterable[SearchSample],
                         *, include_features=True):
    if not isinstance(dataset, SearchDataset):
        dataset = SearchDataset(dataset)
    Path(path).write_text(
        "".join(json.dumps(sample.as_json(include_features=include_features),
                           ensure_ascii=False) + "\n"
                for sample in dataset.samples), encoding="utf-8")
    return path


def read_search_dataset(path):
    samples = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            samples.append(SearchSample.from_json(json.loads(line)))
    return SearchDataset(samples)


def iter_work_ids(path):
    """Stream stable work identities from a JSONL dataset without features.

    Resume must not double-count completed work, and the identity only needs
    the provenance fields, so this avoids constructing full samples.
    """
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        data = json.loads(line)
        yield work_identity(
            source_group=data["source_group"],
            context_hash=data["context_hash"],
            history_hash=data["history_hash"],
            teacher_seed=data.get("teacher_seed", 0),
            search_fingerprint=data["search_fingerprint"])


def dataset_fingerprint(dataset: SearchDataset):
    """Stable digest over sample identities and feature-free sample hashes."""
    digest = hashlib.sha256(b"search-distillation-dataset-v1")
    rows = sorted(
        (sample.work_id,
         sample.as_json(include_features=False)["fingerprint"])
        for sample in dataset.samples)
    for work_id, sample_hash in rows:
        digest.update(work_id.encode("utf-8"))
        digest.update(sample_hash.encode("utf-8"))
    return digest.hexdigest()[:24]
