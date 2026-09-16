"""Public-information value targets derived from search samples."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from ..decision.profile import fingerprint


def _finite(value, name):
    if value is None:
        return None
    value = float(value)
    if value != value or value in (float("inf"), float("-inf")):
        raise ValueError(f"{name} must be finite")
    return value


@dataclass(frozen=True)
class ValueSample:
    """One value target with both teacher and actual terminal evidence.

    ``planes`` and ``scalars`` are optional so a JSONL extraction pass can
    keep provenance first and featureize in a separate, dependency-aware
    step.  Hidden sampled worlds are intentionally not fields of this type.
    """

    context_hash: str
    history_hash: str
    root_value: float | None
    terminal_reward: float | None
    source_group: str
    belief_fingerprint: str = ""
    search_fingerprint: str = ""
    opponent_policy_version: str = ""
    leaf_version: str = ""
    policy_version_source: str = ""
    target_source: str = "search-root-v1"
    simulations: int = 0
    ambiguous: bool = False
    confidence: float | None = None
    sample_weight: float = 1.0
    planes: Any = None
    scalars: Any = None
    oracle: bool = False
    schema: str = "value-distillation-sample-v1"

    def __post_init__(self):
        if self.schema != "value-distillation-sample-v1":
            raise ValueError(f"unsupported value sample schema: {self.schema}")
        if not self.context_hash or not self.history_hash or not self.source_group:
            raise ValueError("context/history/source_group are required")
        if self.oracle:
            raise ValueError("value samples must set oracle=False")
        if int(self.simulations) < 0:
            raise ValueError("simulations must be non-negative")
        if float(self.sample_weight) < 0 or float(self.sample_weight) != float(self.sample_weight):
            raise ValueError("sample_weight must be non-negative and finite")
        object.__setattr__(self, "root_value", _finite(self.root_value, "root_value"))
        object.__setattr__(self, "terminal_reward",
                           _finite(self.terminal_reward, "terminal_reward"))
        object.__setattr__(self, "confidence", _finite(self.confidence, "confidence"))
        object.__setattr__(self, "simulations", int(self.simulations))
        object.__setattr__(self, "sample_weight", float(self.sample_weight))

    @property
    def target(self):
        """Return the selected value target without overwriting raw evidence."""
        if self.target_source in ("terminal-v1", "actual-terminal-v1"):
            return self.terminal_reward
        if self.target_source in ("search-root-v1", "teacher-v1"):
            return self.root_value
        if self.target_source == "terminal-preferred-v1":
            return (self.terminal_reward if self.terminal_reward is not None
                    else self.root_value)
        raise ValueError(f"unknown value target source: {self.target_source}")

    def as_json(self, *, include_features=True):
        value = {
            "schema": self.schema,
            "context_hash": self.context_hash,
            "history_hash": self.history_hash,
            "root_value": self.root_value,
            "terminal_reward": self.terminal_reward,
            "source_group": self.source_group,
            "belief_fingerprint": self.belief_fingerprint,
            "search_fingerprint": self.search_fingerprint,
            "opponent_policy_version": self.opponent_policy_version,
            "leaf_version": self.leaf_version,
            "policy_version_source": self.policy_version_source,
            "target_source": self.target_source,
            "simulations": self.simulations,
            "ambiguous": bool(self.ambiguous),
            "confidence": self.confidence,
            "sample_weight": self.sample_weight,
            "oracle": False,
        }
        if include_features:
            if self.planes is not None:
                value["planes"] = (self.planes.tolist()
                                    if hasattr(self.planes, "tolist") else self.planes)
            if self.scalars is not None:
                value["scalars"] = (self.scalars.tolist()
                                     if hasattr(self.scalars, "tolist") else self.scalars)
        value["fingerprint"] = fingerprint(value, 24)
        return value

    @classmethod
    def from_json(cls, data: Mapping[str, Any]):
        value = dict(data)
        supplied_fingerprint = value.pop("fingerprint", None)
        unknown = set(value) - set(cls.__dataclass_fields__)
        if unknown:
            raise ValueError("unknown value sample fields: " +
                             ", ".join(sorted(unknown)))
        sample = cls(**value)
        if (supplied_fingerprint is not None and
                supplied_fingerprint != sample.as_json()["fingerprint"]):
            raise ValueError("value sample fingerprint mismatch")
        return sample

    @classmethod
    def from_search_sample(cls, sample, *, target_source="search-root-v1"):
        return cls(
            context_hash=sample.context_hash,
            history_hash=sample.history_hash,
            root_value=sample.root_value,
            terminal_reward=getattr(sample, "terminal_reward", None),
            source_group=sample.source_group,
            belief_fingerprint=sample.belief_fingerprint,
            search_fingerprint=sample.search_fingerprint,
            opponent_policy_version=sample.opponent_policy_version,
            leaf_version=sample.leaf_version,
            policy_version_source=sample.policy_version_source,
            target_source=target_source,
            simulations=sample.simulations,
            ambiguous=sample.ambiguous,
            confidence=sample.confidence,
            sample_weight=sample.sample_weight,
            planes=sample.planes,
            scalars=sample.scalars,
        )


class ValueDataset:
    def __init__(self, samples: Iterable[ValueSample] = ()):
        self.samples = list(samples)
        if any(not isinstance(sample, ValueSample) for sample in self.samples):
            raise TypeError("ValueDataset accepts ValueSample values")

    def __len__(self):
        return len(self.samples)

    def append(self, sample):
        if not isinstance(sample, ValueSample):
            raise TypeError("value dataset sample must be ValueSample")
        self.samples.append(sample)

    @classmethod
    def from_search_dataset(cls, dataset, *, target_source="search-root-v1"):
        return cls(ValueSample.from_search_sample(sample,
                                                  target_source=target_source)
                   for sample in dataset.samples)

    def split_by_source_group(self, assignments: Mapping[str, str]):
        result = {}
        for sample in self.samples:
            split = assignments.get(sample.source_group)
            if split is None:
                raise ValueError(f"missing split assignment for {sample.source_group}")
            result.setdefault(str(split), ValueDataset()).append(sample)
        return result

    def as_json(self):
        return {
            "schema": "value-distillation-dataset-v1",
            "count": len(self.samples),
            "source_groups": sorted({s.source_group for s in self.samples}),
            "oracle": False,
            "samples": [sample.as_json() for sample in self.samples],
        }

    @classmethod
    def from_json(cls, data: Mapping[str, Any]):
        value = dict(data)
        schema = value.pop("schema", "value-distillation-dataset-v1")
        if schema != "value-distillation-dataset-v1":
            raise ValueError(f"unsupported value dataset schema: {schema}")
        count = value.pop("count", None)
        source_groups = value.pop("source_groups", None)
        oracle = value.pop("oracle", False)
        samples = value.pop("samples", None)
        if value or not isinstance(samples, list):
            raise ValueError("invalid value dataset fields")
        dataset = cls(ValueSample.from_json(item) for item in samples)
        if count is not None and int(count) != len(dataset):
            raise ValueError("value dataset count mismatch")
        if oracle:
            raise ValueError("value dataset must set oracle=False")
        actual_groups = sorted({sample.source_group for sample in dataset.samples})
        if source_groups is not None and sorted(source_groups) != actual_groups:
            raise ValueError("value dataset source_groups mismatch")
        return dataset


def write_value_dataset(path, dataset: ValueDataset | Iterable[ValueSample]):
    if not isinstance(dataset, ValueDataset):
        dataset = ValueDataset(dataset)
    Path(path).write_text(
        "".join(json.dumps(sample.as_json(), ensure_ascii=False) + "\n"
                for sample in dataset.samples), encoding="utf-8")
    return path


def read_value_dataset(path):
    return ValueDataset(
        ValueSample.from_json(json.loads(line))
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip())
