"""Value v2 calibration reporting layered on the model transform contract.

The transform itself lives in :mod:`mj.models.policy_value` so the search
leaf can validate the same contract without importing the training package.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any, Mapping

from ..decision.profile import fingerprint
from ..models.policy_value import (
    VALUE_CONTRACT_SCHEMA,
    ValueTransformContract,
    value_contract_from_json,
)

CALIBRATION_SCHEMA = "value-calibration-gate-v1"

__all__ = [
    "CALIBRATION_SCHEMA", "VALUE_CONTRACT_SCHEMA", "ValueCalibrationRequirement",
    "ValueTransformContract", "calibration_gate", "value_contract_from_json",
]


@dataclass(frozen=True)
class ValueCalibrationRequirement:
    max_mae: float = 8.0
    max_rmse: float = 12.0
    min_ranking_accuracy: float = 0.55
    max_bucket_error: float = 8.0
    min_count: int = 32

    def __post_init__(self):
        for name in ("max_mae", "max_rmse", "max_bucket_error"):
            value = float(getattr(self, name))
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
            object.__setattr__(self, name, value)
        ranking = float(self.min_ranking_accuracy)
        if not 0 <= ranking <= 1:
            raise ValueError("min_ranking_accuracy must be in [0, 1]")
        if int(self.min_count) < 0:
            raise ValueError("min_count must be non-negative")
        object.__setattr__(self, "min_ranking_accuracy", ranking)
        object.__setattr__(self, "min_count", int(self.min_count))

    def payload(self):
        return asdict(self)

    @property
    def fingerprint(self):
        return fingerprint(self.payload(), 24)


def calibration_gate(metrics: Mapping[str, Any], *,
                     requirement: ValueCalibrationRequirement | None = None):
    """Evaluate MAE/RMSE/ranking/bucket calibration before enabling a leaf."""
    requirement = requirement or ValueCalibrationRequirement()
    violations = []
    count = int(metrics.get("count") or 0)
    if count < requirement.min_count:
        violations.append("insufficient_count")
    mae = metrics.get("mae")
    if mae is None or float(mae) > requirement.max_mae:
        violations.append("mae")
    rmse = metrics.get("rmse")
    if rmse is None or float(rmse) > requirement.max_rmse:
        violations.append("rmse")
    ranking = metrics.get("ranking_accuracy")
    if ranking is None or float(ranking) < requirement.min_ranking_accuracy:
        violations.append("ranking_accuracy")
    for bucket, row in (metrics.get("bucket_calibration") or {}).items():
        predicted, actual = row.get("predicted_mean"), row.get("actual_mean")
        if predicted is None or actual is None:
            continue
        if abs(float(predicted) - float(actual)) > requirement.max_bucket_error:
            violations.append(f"bucket:{bucket}")
    value = {
        "schema": CALIBRATION_SCHEMA,
        "passed": not violations,
        "violations": violations,
        "requirement": requirement.payload(),
        "metrics": dict(metrics),
        "oracle": False,
    }
    value["fingerprint"] = fingerprint(value, 24)
    return value
