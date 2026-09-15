"""Offline calibration and paired-evaluation primitives for shape-v2.

The module is deliberately dependency-light: it provides deterministic
feature extraction, a globally fitted box-constrained linear model, sparse LUT
fallbacks, and source-group clustered statistics.  It does not run games and
is not imported by the online action path.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import math
import random
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

from .profile import canonical_json, fingerprint


CALIBRATION_SCHEMA = "bot-ev-discard/calibration-v1"
FEATURE_NAMES = (
    "shanten", "U1", "p1", "I", "EV1", "EV2", "B", "C",
    "structure_ukeire", "visible_unknown", "live_wall", "dealer",
    "locked", "chain", "chain_piao", "risk", "you_cai_bi_kao",
    "wild_count",
)
SPLITS = ("train", "validation", "final_test")

# These are the only fitted features represented by the current online Fast
# EV profile.  A model that depends on shape-v1-only B/C or I features must
# remain an offline artifact until the online evaluator computes them too.
ONLINE_FEATURES = frozenset({"shanten", "U1", "EV1", "EV2", "chain", "risk"})


class CalibrationError(ValueError):
    """Calibration input is malformed or crosses a declared boundary."""


def _get(item: Any, name: str, default=None):
    if isinstance(item, Mapping):
        return item.get(name, default)
    return getattr(item, name, default)


def _context_value(context, name, default=None):
    if context is None:
        return default
    value = getattr(context, name, default)
    if value is None:
        return default
    return value


def candidate_features(candidate, context=None, *, allow_missing=False):
    """Return named features and an explicit missing-field tuple.

    Complete calibration rows never silently turn an absent EV into a zero.
    ``allow_missing=True`` is only for a diagnostic model and adds a
    ``<name>_missing`` marker for every missing numeric field.
    """
    structure = _get(candidate, "structure_ukeire", ()) or ()
    waits = _get(candidate, "waits", ()) or ()
    unknown_pool = _get(candidate, "visible_unknown",
                         _get(candidate, "unknown_pool"))
    u1 = _get(candidate, "u1", _get(candidate, "U1"))
    p1 = _get(candidate, "p1")
    if p1 is None and unknown_pool not in (None, 0) and u1 is not None:
        p1 = float(u1) / float(unknown_pool)
    hand = _context_value(context, "hand")
    wild_count = _get(candidate, "wild_count")
    if wild_count is None and hand is not None and len(hand) == 34:
        wild_count = hand[33]
    risk = _get(candidate, "risk")
    if risk is None and unknown_pool is not None and u1 is not None:
        risk = max(0.0, float(unknown_pool) - float(u1))
    values = {
        "shanten": _get(candidate, "shanten"),
        "U1": u1,
        "p1": p1,
        "I": _get(candidate, "I", _get(candidate, "improvement")),
        "EV1": _get(candidate, "ev1", _get(candidate, "EV1")),
        "EV2": _get(candidate, "ev2", _get(candidate, "EV2")),
        "B": _get(candidate, "B", _get(candidate, "b")),
        "C": _get(candidate, "C", _get(candidate, "c")),
        "structure_ukeire": len(structure),
        "visible_unknown": unknown_pool,
        "live_wall": _context_value(context, "live_wall"),
        "dealer": _context_value(context, "dealer"),
        "locked": _context_value(context, "locked", 0),
        "chain": _context_value(context, "chain_count"),
        "chain_piao": _context_value(context, "chain_piao"),
        "risk": risk,
        "you_cai_bi_kao": _context_value(context, "you_cai_bi_kao"),
        "wild_count": wild_count,
    }
    missing = tuple(name for name, value in values.items() if value is None)
    if missing and not allow_missing:
        raise CalibrationError("missing calibration features: " +
                               ", ".join(missing))
    for name in missing:
        values[name] = 0.0
        if allow_missing:
            values[f"{name}_missing"] = 1.0
    return values, missing


def _row_values(row, feature_names):
    if isinstance(row, CalibrationRow):
        row = row.features
    if isinstance(row, Mapping) and "features" in row:
        row = row["features"]
    if isinstance(row, Mapping):
        try:
            return np.asarray([float(row[name]) for name in feature_names],
                              dtype=np.float64)
        except KeyError as exc:
            raise CalibrationError(f"feature missing: {exc.args[0]}") from exc
    values = np.asarray(row, dtype=np.float64)
    if values.shape != (len(feature_names),):
        raise CalibrationError(
            f"feature row must have {len(feature_names)} values")
    return values


def _row_target(row):
    if isinstance(row, Mapping):
        return row.get("target")
    return getattr(row, "target", None)


@dataclass(frozen=True)
class CalibrationRow:
    features: Mapping[str, float]
    target: float
    source_group: str
    bucket: tuple | None = None

    def as_json(self):
        return {"features": dict(self.features), "target": self.target,
                "source_group": self.source_group,
                "bucket": list(self.bucket) if self.bucket is not None else None}


@dataclass(frozen=True)
class LinearModel:
    """A deterministic linear reward model with optional coefficient bounds."""

    feature_names: tuple
    coefficients: tuple
    intercept: float
    constraints: tuple = ()
    n_samples: int = 0
    target_mean: float | None = None
    training_fingerprint: str = ""

    def predict(self, row):
        values = _row_values(row, self.feature_names)
        return float(self.intercept + np.dot(values, self.coefficients))

    def as_json(self):
        return {
            "schema": CALIBRATION_SCHEMA, "kind": "global_linear",
            "feature_names": list(self.feature_names),
            "coefficients": list(self.coefficients),
            "intercept": self.intercept,
            "constraints": [list(x) for x in self.constraints],
            "n_samples": self.n_samples, "target_mean": self.target_mean,
            "training_fingerprint": self.training_fingerprint,
            "fingerprint": fingerprint({
                "feature_names": self.feature_names,
                "coefficients": self.coefficients,
                "intercept": self.intercept,
                "constraints": self.constraints,
            }),
        }


def _constraint_vectors(feature_names, constraints):
    lower = np.full(len(feature_names) + 1, -np.inf, dtype=np.float64)
    upper = np.full(len(feature_names) + 1, np.inf, dtype=np.float64)
    normalised = []
    for name, bounds in (constraints or {}).items():
        if name == "intercept":
            index = 0
        elif name in feature_names:
            index = 1 + feature_names.index(name)
        else:
            raise CalibrationError(f"constraint references unknown feature {name}")
        if len(bounds) != 2:
            raise CalibrationError(f"constraint for {name} must be (lo, hi)")
        lo, hi = (None if x is None else float(x) for x in bounds)
        if lo is not None and hi is not None and lo > hi:
            raise CalibrationError(f"constraint lower > upper for {name}")
        lower[index] = -np.inf if lo is None else lo
        upper[index] = np.inf if hi is None else hi
        normalised.append((name, lo, hi))
    return lower, upper, tuple(normalised)


def fit_global_linear(rows: Iterable, targets: Sequence[float] | None = None,
                      *, feature_names=FEATURE_NAMES, constraints=None,
                      l2=1e-8, max_iter=4000) -> LinearModel:
    """Fit one model across all source groups.

    Box constraints are projected during deterministic gradient descent.  The
    default has no sign constraints: introducing a new feature must not
    accidentally recreate a hard shanten gate.
    """
    rows = list(rows)
    feature_names = tuple(feature_names)
    if targets is None:
        if not all(_row_target(row) is not None for row in rows):
            raise CalibrationError("targets are required for bare feature rows")
        targets = [_row_target(row) for row in rows]
    targets = np.asarray(targets, dtype=np.float64)
    if len(rows) != len(targets) or not len(rows):
        raise CalibrationError("rows and targets must be non-empty and aligned")
    x = np.vstack([_row_values(row, feature_names) for row in rows])
    if not np.isfinite(x).all() or not np.isfinite(targets).all():
        raise CalibrationError("calibration input contains non-finite values")
    design = np.column_stack([np.ones(len(x)), x])
    lower, upper, normalised = _constraint_vectors(feature_names, constraints)
    if not constraints:
        coef = np.linalg.lstsq(design, targets, rcond=None)[0]
    else:
        gram = (design.T @ design) / len(design)
        rhs = (design.T @ targets) / len(design)
        if l2:
            gram = gram + np.eye(len(gram)) * float(l2)
        eig = float(np.linalg.norm(gram, ord=2))
        step = 1.0 / max(eig, 1e-9)
        coef = np.zeros(design.shape[1], dtype=np.float64)
        coef[0] = float(np.mean(targets))
        for _ in range(max(1, int(max_iter))):
            proposal = coef - step * (gram @ coef - rhs)
            proposal = np.minimum(np.maximum(proposal, lower), upper)
            if np.max(np.abs(proposal - coef)) < 1e-11:
                coef = proposal
                break
            coef = proposal
    train_fp = fingerprint({"features": x.tolist(), "targets": targets.tolist(),
                            "constraints": normalised})
    return LinearModel(
        feature_names=feature_names,
        coefficients=tuple(float(v) for v in coef[1:]),
        intercept=float(coef[0]), constraints=normalised,
        n_samples=len(rows), target_mean=float(np.mean(targets)),
        training_fingerprint=train_fp)


def bucket_key(context, candidate=None):
    """Return the frozen wall/shanten/dealer/locked LUT bucket."""
    wall = _context_value(context, "live_wall")
    if wall is None:
        wall_bucket = "unknown"
    elif wall <= 8:
        wall_bucket = "0-8"
    elif wall <= 16:
        wall_bucket = "9-16"
    elif wall <= 32:
        wall_bucket = "17-32"
    else:
        wall_bucket = "33+"
    sh = _get(candidate, "shanten") if candidate is not None else None
    sh_bucket = ("unknown" if sh is None else "0" if sh == 0 else
                 "1" if sh == 1 else "2" if sh == 2 else "3+")
    dealer = _context_value(context, "dealer")
    hero = _context_value(context, "hero_seat", 0)
    dealer_bucket = ("unknown" if dealer is None else
                     "dealer" if int(dealer) == int(hero) else "nondealer")
    locked = _context_value(context, "locked", 0)
    return (wall_bucket, sh_bucket, dealer_bucket, int(locked)
            if locked is not None else "unknown")


@dataclass(frozen=True)
class SparseLUT:
    global_model: LinearModel
    bucket_models: Mapping[tuple, LinearModel]
    min_bucket_samples: int

    def predict(self, row, bucket=None):
        model = self.bucket_models.get(tuple(bucket)) if bucket is not None else None
        return (model or self.global_model).predict(row)

    def as_json(self):
        buckets = {canonical_json(list(k)): model.as_json()
                   for k, model in sorted(self.bucket_models.items(), key=lambda x: str(x[0]))}
        return {"schema": CALIBRATION_SCHEMA, "kind": "sparse_lut",
                "min_bucket_samples": self.min_bucket_samples,
                "global": self.global_model.as_json(), "buckets": buckets,
                "fingerprint": fingerprint({
                    "global": self.global_model.as_json(),
                    "buckets": buckets,
                    "min_bucket_samples": self.min_bucket_samples,
                })}


def fit_sparse_lut(rows: Iterable, targets: Sequence[float] | None = None,
                   *, feature_names=FEATURE_NAMES, constraints=None,
                   min_bucket_samples=64) -> SparseLUT:
    """Fit supported buckets; every sparse bucket falls back globally."""
    rows = list(rows)
    if targets is None:
        if not all(_row_target(row) is not None for row in rows):
            raise CalibrationError("targets are required for bare feature rows")
        targets = [_row_target(row) for row in rows]
    global_model = fit_global_linear(
        rows, targets, feature_names=feature_names, constraints=constraints)
    groups = {}
    for i, row in enumerate(rows):
        key = (row.get("bucket") if isinstance(row, Mapping)
               else getattr(row, "bucket", None))
        if key is None:
            continue
        groups.setdefault(tuple(key), []).append(i)
    bucket_models = {}
    for key, indices in sorted(groups.items(), key=lambda x: str(x[0])):
        if len(indices) < int(min_bucket_samples):
            continue
        bucket_models[key] = fit_global_linear(
            [rows[i] for i in indices], [targets[i] for i in indices],
            feature_names=feature_names, constraints=constraints)
    return SparseLUT(global_model, bucket_models, int(min_bucket_samples))


def regression_metrics(model, rows, targets):
    targets = np.asarray(targets, dtype=np.float64)
    predictions = np.asarray([model.predict(row) for row in rows], dtype=np.float64)
    errors = predictions - targets
    return {"n": int(len(targets)), "mae": float(np.mean(np.abs(errors))),
            "rmse": float(np.sqrt(np.mean(errors ** 2))),
            "bias": float(np.mean(errors))}


def ablation_report(rows, targets, *, feature_sets=None, constraints=None):
    """Fit declared I/EV2/B/C/hard-gate/threshold ablations."""
    if feature_sets is None:
        full = tuple(FEATURE_NAMES)
        feature_sets = {
            "I": tuple(x for x in full if x != "I"),
            "EV2": tuple(x for x in full if x != "EV2"),
            "B": tuple(x for x in full if x != "B"),
            "C": tuple(x for x in full if x != "C"),
            # Hard gates and action thresholds are policy variants, not
            # numeric features.  Keeping the same feature set makes that
            # distinction explicit instead of smuggling a new gate into the
            # regression design matrix.
            "hard_gate": full,
            "threshold": full,
        }
    result = {}
    for name, features in feature_sets.items():
        model = fit_global_linear(rows, targets, feature_names=features,
                                  constraints=constraints)
        removed = {
            "I": ["I"], "EV2": ["EV2"], "B": ["B"], "C": ["C"],
            "hard_gate": ["shanten_hard_gate"],
            "threshold": ["action_threshold"],
        }.get(name, [])
        result[name] = {"model": model.as_json(),
                        "metrics": regression_metrics(model, rows, targets),
                        "removed": removed}
    return {"schema": CALIBRATION_SCHEMA, "kind": "ablation", "models": result,
            "fingerprint": fingerprint(result)}


def calibrated_profile(profile, model, *, lut=None):
    """Project a fitted model into the online profile's sortable coefficients.

    Only the feature coefficients consumed by the current Fast EV are copied;
    the full model/LUT fingerprint is carried in the profile so it participates
    in the profile fingerprint.  A model with other feature names is still
    useful offline but cannot silently become an online calibrated profile.
    """
    unsupported = sorted(set(model.feature_names) - ONLINE_FEATURES)
    if unsupported:
        raise CalibrationError(
            "model features are not represented by Fast EV: " +
            ", ".join(unsupported))
    values = dict(profile.tau)
    mapping = {"shanten": "q0_shanten_weight", "U1": "q0_u1_weight",
               "EV1": "q0_ev1_weight", "EV2": "q0_ev2_weight",
               "chain": "q0_fan_weight", "risk": "q0_risk_weight"}
    # A fitted model owns the supported coefficient vector.  Do not leave
    # diagnostic ProfileSpec defaults active for features omitted by a sparse
    # online model, otherwise its prediction would depend on unrelated
    # baseline weights.
    changes = {"calibrated": True, "tau": values,
               "calibration_fingerprint": model.as_json()["fingerprint"],
               "calibration_kind": "global_linear",
               "calibration_intercept": float(model.intercept),
               "q0_shanten_weight": 0.0, "q0_u1_weight": 0.0,
               "q0_ev1_weight": 0.0, "q0_ev2_weight": 0.0,
               "q0_fan_weight": 0.0, "q0_risk_weight": 0.0}
    for feature, field_name in mapping.items():
        if feature in model.feature_names:
            changes[field_name] = float(
                model.coefficients[model.feature_names.index(feature)])
    if lut is not None:
        changes["lut_buckets"] = tuple(
            canonical_json(list(key)) for key in lut.bucket_models)
        changes["calibration_fingerprint"] = lut.as_json()["fingerprint"]
        changes["calibration_kind"] = "sparse_lut"
    return replace(profile, **changes)


def _group_records(records):
    groups = {}
    for row in records:
        group = row.get("source_group", row.get("group"))
        if group is None:
            raise CalibrationError("paired record needs source_group")
        groups.setdefault(str(group), []).append(row)
    return groups


def paired_q_regret(records, *, baseline="legacy", teacher="teacher",
                    strategies=("legacy", "shape-v1", "shape-v2")):
    """Calculate paired score deltas and signed teacher regret.

    Records may be either ``{group, q: {strategy: value}}`` or one row per
    strategy ``{group, strategy, q}``.  Missing/ambiguous pairs are retained
    in ``invalid`` and never converted to zero.  Negative regret is preserved.
    """
    grouped = _group_records(records)
    pairs = {name: [] for name in strategies if name != baseline}
    regrets = {name: [] for name in strategies}
    invalid = []
    for group, rows in grouped.items():
        q = {}
        for row in rows:
            if isinstance(row.get("q"), Mapping):
                q.update({str(k): v for k, v in row["q"].items()})
            elif row.get("strategy") is not None:
                q[str(row["strategy"])] = row.get("value", row.get("q"))
        missing = [name for name in set(strategies) | {teacher} if
                   name not in q or q[name] is None]
        if missing:
            invalid.append({"source_group": group, "missing": sorted(set(missing))})
            continue
        for name in strategies:
            regrets[name].append(float(q[teacher]) - float(q[name]))
            if name != baseline:
                pairs[name].append(float(q[name]) - float(q[baseline]))
    return {
        "schema": CALIBRATION_SCHEMA, "baseline": baseline,
        "teacher": teacher, "pairs": pairs, "regret": regrets,
        "invalid": invalid,
        "fingerprint": fingerprint({"pairs": pairs, "regret": regrets,
                                      "invalid": invalid}),
    }


def _mean_interval(values, alpha=0.05):
    values = np.asarray(values, dtype=np.float64)
    if not len(values):
        return {"n": 0, "mean": None, "low": None, "high": None}
    # This is a fixed-checkpoint diagnostic interval for clustered means.  A
    # release decision should use cluster_bootstrap below.
    mean = float(np.mean(values))
    if len(values) == 1:
        half = math.inf
    else:
        half = 2.0 * float(np.std(values, ddof=1)) / math.sqrt(len(values))
    return {"n": int(len(values)), "mean": mean,
            "low": mean - half, "high": mean + half,
            "half_width": half, "method": "bounded-diagnostic-fixed-checkpoint"}


def cluster_bootstrap(values, groups, *, rounds=2000, seed=0, alpha=0.05):
    """Resample complete source groups, never individual decisions."""
    values = np.asarray(values, dtype=np.float64)
    groups = np.asarray([str(x) for x in groups])
    if len(values) != len(groups) or not len(values):
        raise CalibrationError("values/groups must be non-empty and aligned")
    unique = sorted(set(groups))
    by_group = {group: values[groups == group] for group in unique}
    rng = random.Random(int(seed))
    means = []
    for _ in range(max(1, int(rounds))):
        sampled = [rng.choice(unique) for _ in unique]
        means.append(float(np.mean(np.concatenate([by_group[g] for g in sampled]))))
    means.sort()
    lo_i = max(0, int(math.floor((alpha / 2) * len(means))) - 1)
    hi_i = min(len(means) - 1,
               int(math.ceil((1 - alpha / 2) * len(means))) - 1)
    return {"n": int(len(values)), "clusters": len(unique),
            "mean": float(np.mean(values)), "low": means[lo_i],
            "high": means[hi_i], "rounds": int(rounds), "seed": int(seed),
            "alpha": float(alpha), "method": "source-group-bootstrap"}


def release_gate(values, groups, *, required_pairs=4096, ci_lower=0.0,
                 rounds=2000, seed=0, invalid=0):
    """Return an explicit gate result; callers must keep legacy on failure."""
    interval = cluster_bootstrap(values, groups, rounds=rounds, seed=seed)
    pairs = len(values)
    passed = (pairs >= int(required_pairs) and not invalid and
              interval["low"] > float(ci_lower))
    return {"passed": bool(passed), "legacy_default": not passed,
            "pairs": pairs, "required_pairs": int(required_pairs),
            "invalid": int(invalid), "interval": interval,
            "criterion": {"ci_lower_gt": float(ci_lower),
                          "invalid_must_equal": 0}}


def freeze_split_manifest(*, train_start=240000, train_games=1024,
                          validation_start=241024, validation_games=1024,
                          final_test_start=242048, final_test_games=8192,
                          seed_fingerprint=""):
    """Create and validate the non-overlapping source-seed split manifest."""
    specs = {
        "train": (int(train_start), int(train_games)),
        "validation": (int(validation_start), int(validation_games)),
        "final_test": (int(final_test_start), int(final_test_games)),
    }
    ranges = []
    for name, (start, count) in specs.items():
        if start < 0 or count <= 0:
            raise CalibrationError(f"invalid split {name}")
        ranges.append((start, start + count, name))
    for i, (lo, hi, name) in enumerate(ranges):
        for other_lo, other_hi, other in ranges[i + 1:]:
            if lo < other_hi and other_lo < hi:
                raise CalibrationError(f"split overlap: {name}/{other}")
    manifest = {
        "schema": "bot-ev-discard/split-manifest-v1",
        "source_group": "one source seed owns every decision and rotation",
        "splits": {name: {"seed_start": start, "games": count}
                   for name, (start, count) in specs.items()},
        "seat_dealer": {"seat": "i%4", "dealer": "(i//4)%4",
                        "combinations": 16, "balanced": True},
        "you_cai_bi_kao": [False, True],
        "seed_fingerprint": seed_fingerprint,
    }
    manifest["fingerprint"] = fingerprint(manifest)
    return manifest


def scheduled_game(seed, index, *, you_cai_bi_kao=None):
    """Return the frozen seat/dealer/ycbk assignment for a source game."""
    index = int(index)
    return {"seed": int(seed), "index": index, "seat": index % 4,
            "dealer": (index // 4) % 4,
            "you_cai_bi_kao": bool(index % 2) if you_cai_bi_kao is None
            else bool(you_cai_bi_kao),
            "source_group": f"seed:{int(seed)}:index:{index}"}


def validate_evidence_fingerprint(actual, expected, *, fields=None):
    """Check that evidence was produced under the same frozen contract."""
    fields = tuple(fields or ("profile_fingerprint", "rule_version",
                              "kernel_version", "scope"))
    mismatch = {field: (actual.get(field), expected.get(field))
                for field in fields if actual.get(field) != expected.get(field)}
    return {"valid": not mismatch, "mismatch": mismatch,
            "checked_fields": list(fields)}


# Short aliases used by scripts and notebooks.
fit_linear = fit_global_linear
fit_lut = fit_sparse_lut
paired_regret = paired_q_regret
