"""Versioned calibration artifact with disjoint train/validation seeds."""
from collections import defaultdict
import math
import statistics
from types import MappingProxyType

from .decision.profile import fingerprint
from .legacy_belief import MODEL_VERSION
from .legacy_tail import TAIL_MODEL_VERSION, frozen_tail_policy


def metrics(rows, bins=10):
    groups = defaultdict(list)
    for row in rows:
        p = min(1 - 1e-9, max(1e-9, float(row["prediction"])))
        y = int(row["label"])
        groups[min(bins - 1, int(p * bins))].append((p, y))
    n = len(rows)
    return {
        "samples": n,
        "brier": sum((float(r["prediction"]) - r["label"]) ** 2 for r in rows) / n if n else None,
        "log_loss": -sum(r["label"] * math.log(min(1-1e-9, max(1e-9, r["prediction"]))) +
                         (1-r["label"]) * math.log(1-min(1-1e-9, max(1e-9, r["prediction"]))) for r in rows) / n if n else None,
        "reliability": [{"bin": key, "samples": len(g),
                         "prediction": sum(p for p, y in g) / len(g),
                         "observed": sum(y for p, y in g) / len(g)} for key, g in sorted(groups.items())],
    }


def fit(rows, train_seeds, validation_seeds):
    if set(train_seeds) & set(validation_seeds):
        raise ValueError("calibration seeds overlap validation")
    groups = defaultdict(list)
    tails = defaultdict(list)
    for row in rows:
        if row["target"] == "continuation":
            tails[row["bucket"]].append(row)
            continue
        if row["seed"] in train_seeds:
            groups[f"{row['target']}|{row['bucket']}"].append(row)
    buckets = {}
    for key, group in groups.items():
        events = [r for r in group if r["label"]]
        buckets[key] = {"samples": len(group), "probability": (len(events) + .5) / (len(group) + 1),
                        "event_samples": len(events),
                        "conditional_multiplier": sum(r.get("multiplier", 1) for r in events) / len(events) if events else 1,
                        "multiplier_uncertainty": 1.96 * statistics.stdev([r.get("multiplier", 1) for r in events])/math.sqrt(len(events)) if len(events) > 1 else 1.0}
    reports = defaultdict(list)
    for row in rows:
        if row["target"] != "continuation" and row["seed"] in validation_seeds:
            key = f"{row['target']}|{row['bucket']}"
            prediction = buckets.get(key, {}).get("probability", row["prediction"])
            reports[key].append(dict(row, prediction=prediction))
    result = {"schema": "legacy-quality-calibration-v1", "model_version": MODEL_VERSION,
              "train_seeds": sorted(train_seeds), "validation_seeds": sorted(validation_seeds),
              "buckets": buckets, "validation": {k: metrics(v) for k, v in sorted(reports.items())}}
    if tails:
        tail_buckets = {}
        for key, group in sorted(tails.items()):
            training, held_out = defaultdict(list), defaultdict(list)
            for row in group:
                label = float(row["label"])
                if not math.isfinite(label):
                    raise ValueError("invalid terminal continuation label")
                if row["seed"] in train_seeds:
                    training[row["seed"]].append(label)
                elif row["seed"] in validation_seeds:
                    held_out[row["seed"]].append(label)
            means = [statistics.fmean(v) for v in training.values()]
            validation_means = [statistics.fmean(v) for v in held_out.values()]
            if not means:
                continue
            mean = statistics.fmean(means)
            tail_buckets[key] = {"mean": mean, "seed_samples": len(means),
                "samples": sum(map(len, training.values())),
                "standard_error": statistics.stdev(means)/math.sqrt(len(means)) if len(means) > 1 else 0.0,
                "validation_seed_samples": len(validation_means),
                "validation_bias": statistics.fmean(validation_means)-mean if validation_means else 0.0,
                "validation_mae": statistics.fmean(abs(v-mean) for v in validation_means) if validation_means else None}
        result.update(schema="legacy-quality-calibration-v2", tail_model_version=TAIL_MODEL_VERSION,
                      tail_policy="frozen_legacyV2", tail_policy_fingerprint=frozen_tail_policy(),
                      tail_buckets=tail_buckets)
    result["calibration_id"] = fingerprint(result)
    return result


def validate(artifact):
    data = dict(artifact)
    supplied = data.pop("calibration_id", None)
    if supplied != fingerprint(data) or data.get("model_version") != MODEL_VERSION:
        raise ValueError("calibration identity/version mismatch")
    if set(data["train_seeds"]) & set(data["validation_seeds"]):
        raise ValueError("calibration split leaks seeds")
    for row in data["buckets"].values():
        if row["samples"] < 1 or not 0 <= row["probability"] <= 1 or not math.isfinite(row["conditional_multiplier"]) or row["conditional_multiplier"] < 1:
            raise ValueError("invalid calibration cell")
    if "tail_buckets" in data:
        if (data.get("tail_model_version") != TAIL_MODEL_VERSION or data.get("tail_policy") != "frozen_legacyV2"
                or data.get("tail_policy_fingerprint") != frozen_tail_policy()):
            raise ValueError("continuation model/policy mismatch")
        for row in data["tail_buckets"].values():
            if (row["seed_samples"] < 1 or row["validation_seed_samples"] < 0
                    or row["samples"] < row["seed_samples"]
                    or any(not math.isfinite(row[k]) for k in ("mean", "standard_error", "validation_bias"))
                    or row["standard_error"] < 0):
                raise ValueError("invalid continuation cell")
    return artifact


class VerifiedCalibration:
    """Copy and freeze a verified inference model once outside the hot path."""
    def __init__(self, artifact):
        validate(artifact)
        self.calibration_id = artifact["calibration_id"]
        self.buckets = MappingProxyType({k: MappingProxyType(dict(v)) for k, v in artifact["buckets"].items()})
        self.tail_model_version = artifact.get("tail_model_version")
        self.tail_policy_fingerprint = artifact.get("tail_policy_fingerprint")
        self.tail_buckets = MappingProxyType({k: MappingProxyType(dict(v)) for k, v in artifact.get("tail_buckets", {}).items()})

    def get(self, key, default=None):
        if key in ("buckets", "calibration_id", "tail_model_version", "tail_policy_fingerprint", "tail_buckets"):
            return getattr(self, key)
        return default

    def __getitem__(self, key):
        if key == "buckets":
            return self.buckets
        if key == "calibration_id":
            return self.calibration_id
        raise KeyError(key)
