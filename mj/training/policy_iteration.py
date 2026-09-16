"""Versioned policy-iteration bookkeeping and stopping gates."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import math
from typing import Any, Mapping

from ..decision.profile import fingerprint


@dataclass(frozen=True)
class IterationRecord:
    generation: int
    policy_version: str
    continuation_version: str
    dataset_version: str
    belief_fingerprint: str
    search_fingerprint: str
    root_regret_mean: float | None = None
    paired_score_mean: float | None = None
    paired_score_ci: tuple[float, float] | None = None
    belief_calibration_passed: bool = False
    search_cost_ms: float | None = None
    policy_latency_ms: float | None = None
    population_regression: bool = False
    self_play_score_mean: float | None = None
    source_groups: tuple[str, ...] = ()

    def as_json(self):
        value = asdict(self)
        value["fingerprint"] = fingerprint(value, 24)
        return value


@dataclass(frozen=True)
class PolicyIterationManifest:
    schema: str = "policy-iteration-manifest-v1"
    max_generations: int = 4
    no_improvement_generations: int = 2
    minimum_regret_improvement: float = 0.0
    minimum_score_improvement: float = 0.0
    source_split: str = "frozen-final-test-v1"
    opponent_split: str = "population"
    rollback_policy: str = "last-all-gates-passed"
    records: tuple[IterationRecord, ...] = field(default_factory=tuple)

    def __post_init__(self):
        if int(self.max_generations) <= 0 or int(self.no_improvement_generations) <= 0:
            raise ValueError("iteration limits must be positive")
        if float(self.minimum_regret_improvement) < 0 or float(self.minimum_score_improvement) < 0:
            raise ValueError("improvement thresholds must be non-negative")
        object.__setattr__(self, "records", tuple(self.records or ()))

    @property
    def fingerprint(self):
        return fingerprint({
            "schema": self.schema, "max_generations": self.max_generations,
            "no_improvement_generations": self.no_improvement_generations,
            "minimum_regret_improvement": self.minimum_regret_improvement,
            "minimum_score_improvement": self.minimum_score_improvement,
            "source_split": self.source_split, "opponent_split": self.opponent_split,
            "rollback_policy": self.rollback_policy,
            "records": [record.as_json() for record in self.records],
        }, 24)

    def as_json(self):
        return {
            "schema": self.schema, "max_generations": self.max_generations,
            "no_improvement_generations": self.no_improvement_generations,
            "minimum_regret_improvement": self.minimum_regret_improvement,
            "minimum_score_improvement": self.minimum_score_improvement,
            "source_split": self.source_split, "opponent_split": self.opponent_split,
            "rollback_policy": self.rollback_policy,
            "records": [record.as_json() for record in self.records],
            "fingerprint": self.fingerprint,
        }


def _improved(current, previous, minimum, *, lower_is_better):
    if current is None or previous is None:
        return False
    delta = (previous - current) if lower_is_better else (current - previous)
    # Equality is stability, not evidence of an improvement.  A zero
    # threshold therefore still requires a strictly positive change.
    return delta > float(minimum)


def should_stop_iteration(records, manifest: PolicyIterationManifest | None = None):
    """Return an explicit stop decision; never search seeds until a result wins."""
    records = list(records)
    manifest = manifest or PolicyIterationManifest()
    if not records:
        return {"stop": False, "reason": "no_records"}
    latest = records[-1]
    if latest.population_regression:
        return {"stop": True, "reason": "population_regression", "rollback": True}
    if latest.generation >= manifest.max_generations:
        return {"stop": True, "reason": "max_generations", "rollback": False}
    if len(records) < manifest.no_improvement_generations + 1:
        return {"stop": False, "reason": "insufficient_generations"}
    recent_start = len(records) - manifest.no_improvement_generations
    recent = records[recent_start:]
    adjacent = zip(records[recent_start - 1:-1], recent)
    improved = [
        _improved(current.root_regret_mean, previous.root_regret_mean,
                  manifest.minimum_regret_improvement, lower_is_better=True)
        and _improved(current.paired_score_mean, previous.paired_score_mean,
                      manifest.minimum_score_improvement, lower_is_better=False)
        for previous, current in adjacent]
    if not any(improved):
        return {"stop": True, "reason": "consecutive_no_significant_improvement",
                "rollback": True}
    return {"stop": False, "reason": "improvement_observed"}


def append_iteration(manifest: PolicyIterationManifest, record: IterationRecord):
    if record.generation != len(manifest.records):
        raise ValueError("iteration generations must be append-only")
    return PolicyIterationManifest(
        schema=manifest.schema, max_generations=manifest.max_generations,
        no_improvement_generations=manifest.no_improvement_generations,
        minimum_regret_improvement=manifest.minimum_regret_improvement,
        minimum_score_improvement=manifest.minimum_score_improvement,
        source_split=manifest.source_split, opponent_split=manifest.opponent_split,
        rollback_policy=manifest.rollback_policy,
        records=manifest.records + (record,))


def iteration_versions(generation: int, *, continuation_version: str,
                       belief_fingerprint: str, search_fingerprint: str,
                       policy_source_version: str):
    """Create the immutable names for one ``pi_k -> dataset_k`` boundary."""
    generation = int(generation)
    if generation < 0:
        raise ValueError("generation must be non-negative")
    if not all(str(value) for value in (
            continuation_version, belief_fingerprint, search_fingerprint,
            policy_source_version)):
        raise ValueError("iteration provenance fields must not be empty")
    identity = {
        "schema": "policy-iteration-boundary-v1",
        "generation": generation,
        "continuation_version": str(continuation_version),
        "belief_fingerprint": str(belief_fingerprint),
        "search_fingerprint": str(search_fingerprint),
        "policy_source_version": str(policy_source_version),
    }
    boundary = fingerprint(identity, 24)
    return {
        "generation": generation,
        "policy_version_source": str(policy_source_version),
        "continuation_version": str(continuation_version),
        "dataset_version": f"dataset{generation}-{boundary}",
        "teacher_fingerprint": boundary,
        "identity": identity,
    }


def validate_teacher_provenance(*, dataset_generation: int,
                               policy_source_version: str,
                               continuation_version: str,
                               expected_source_version: str | None = None):
    """Reject relabelling a later policy's data as an earlier teacher."""
    if int(dataset_generation) < 0:
        raise ValueError("dataset_generation must be non-negative")
    if not str(policy_source_version) or not str(continuation_version):
        raise ValueError("teacher provenance is incomplete")
    if expected_source_version is not None and (
            str(policy_source_version) != str(expected_source_version)):
        raise ValueError("policy source version does not match teacher contract")
    return True


class PolicyIterationRunner:
    """Small stateful coordinator for append-only iteration evidence.

    It deliberately does not hide training/search side effects.  Callers run
    those jobs, then append an ``IterationRecord`` containing their measured
    gates.  This makes rollback and stopping decisions reproducible in JSON.
    """

    def __init__(self, manifest: PolicyIterationManifest | None = None):
        self.manifest = manifest or PolicyIterationManifest()

    @property
    def records(self):
        return self.manifest.records

    @property
    def next_generation(self):
        return len(self.records)

    def boundary(self, *, continuation_version, belief_fingerprint,
                 search_fingerprint, policy_source_version):
        return iteration_versions(
            self.next_generation,
            continuation_version=continuation_version,
            belief_fingerprint=belief_fingerprint,
            search_fingerprint=search_fingerprint,
            policy_source_version=policy_source_version)

    def record(self, item: IterationRecord):
        self.manifest = append_iteration(self.manifest, item)
        return self.manifest

    def stop_decision(self):
        return should_stop_iteration(self.records, self.manifest)

    def as_json(self):
        value = self.manifest.as_json()
        value["stop_decision"] = self.stop_decision()
        return value


def promotion_gate(*, candidate_regret_mean, previous_regret_mean,
                   candidate_score_ci, previous_score_ci,
                   safety_passed, performance_passed,
                   population_passed):
    """Evaluate the non-negotiable gates before changing the runtime policy."""
    regret_ok = (candidate_regret_mean is not None and
                 previous_regret_mean is not None and
                 float(candidate_regret_mean) <= float(previous_regret_mean))
    score_ok = (candidate_score_ci is not None and
                previous_score_ci is not None and
                float(candidate_score_ci[0]) >= float(previous_score_ci[0]))
    gates = {
        "reference_regret": regret_ok,
        "paired_score_ci": score_ok,
        "safety": bool(safety_passed),
        "performance": bool(performance_passed),
        "population": bool(population_passed),
    }
    return {"passed": all(gates.values()), "gates": gates,
            "rollback": not all(gates.values()),
            "reason": ("all_gates_passed" if all(gates.values())
                       else "promotion_gate_failed")}
