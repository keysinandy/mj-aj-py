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
    risk = _get(candidate, "risk", _get(candidate, "feed_risk"))
    # ``chain``/``locked`` are candidate features: a root discard may reset
    # the chain or change the exposed-meld count.  Fall back to the root only
    # for old rows that predate post-discard fields; callers can still see the
    # provenance through the returned metadata.
    post_chain = _get(candidate, "post_chain", _get(candidate, "chain"))
    if post_chain is None:
        post_chain = _context_value(context, "chain_count")
    post_chain_piao = _get(
        candidate, "post_chain_piao", _get(candidate, "chain_piao"))
    if post_chain_piao is None:
        post_chain_piao = _context_value(context, "chain_piao")
    post_locked = _get(candidate, "post_locked", _get(candidate, "locked"))
    if post_locked is None:
        post_locked = _context_value(context, "locked", 0)
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
        "locked": post_locked,
        "chain": post_chain,
        "chain_piao": post_chain_piao,
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
    # These fields are intentionally metadata until the online evaluator has
    # a versioned coefficient for them.  Keeping them beside the named model
    # features makes post-discard semantics available to offline reports
    # without silently expanding an existing profile schema.
    values["is_piao"] = _get(candidate, "is_piao")
    values["discarded_wild"] = _get(
        candidate, "discarded_wild", _get(candidate, "tile") == 33)
    values["feature_provenance"] = {
        "locked": "candidate.post_locked" if _get(candidate, "post_locked")
        is not None else "context.locked",
        "chain": "candidate.post_chain" if _get(candidate, "post_chain")
        is not None else "context.chain_count",
        "chain_piao": ("candidate.post_chain_piao"
                        if _get(candidate, "post_chain_piao") is not None
                        else "context.chain_piao"),
        "risk": "candidate.risk_or_feed_risk" if risk is not None else "missing",
    }
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
    if not len(targets):
        return {"n": 0, "mae": None, "rmse": None, "bias": None}
    predictions = np.asarray([model.predict(row) for row in rows], dtype=np.float64)
    errors = predictions - targets
    return {"n": int(len(targets)), "mae": float(np.mean(np.abs(errors))),
            "rmse": float(np.sqrt(np.mean(errors ** 2))),
            "bias": float(np.mean(errors))}


def _policy_ablation_diagnostic(rows, targets, name):
    """Describe a gate/threshold ablation without inventing a feature.

    Hard gates and action thresholds affect candidate eligibility, not the
    regression design matrix.  Their effect can only be measured when the
    data producer explicitly records the prior policy decision.  Returning a
    missing/incomplete status is safer than fitting the same model twice and
    labelling it an ablation.
    """
    fields = {
        "hard_gate": ("hard_gate_pass", "hard_gate_eligible", "hard_gate"),
        "threshold": ("action_threshold_pass", "threshold_pass",
                      "action_threshold"),
    }[name]
    present = [field for field in fields
               if any(isinstance(row, Mapping) and field in row for row in rows)]
    field = present[0] if present else None
    if field is None:
        return {"status": "missing_policy_field", "fields": list(fields),
                "with_policy": {"n": 0, "target_mean": None},
                "without_policy": {"n": len(rows),
                                    "target_mean": (float(np.mean(targets))
                                                     if targets else None)}}
    values = []
    malformed = []
    for index, row in enumerate(rows):
        value = row.get(field) if isinstance(row, Mapping) else None
        if isinstance(value, bool):
            values.append(value)
        elif isinstance(value, (int, float)) and value in (0, 1):
            values.append(bool(value))
        else:
            malformed.append(index)
            values.append(None)
    if malformed:
        return {"status": "incomplete_policy_field", "field": field,
                "fields": list(fields), "malformed_rows": malformed,
                "with_policy": {"n": 0, "target_mean": None},
                "without_policy": {"n": len(rows),
                                    "target_mean": (float(np.mean(targets))
                                                     if targets else None)}}
    selected = [float(target) for target, keep in zip(targets, values) if keep]
    return {
        "status": "available", "field": field, "fields": list(fields),
        "with_policy": {"n": len(selected),
                         "target_mean": (float(np.mean(selected))
                                          if selected else None)},
        "without_policy": {"n": len(rows),
                            "target_mean": (float(np.mean(targets))
                                             if targets else None)},
    }


def ablation_report(rows, targets, *, feature_sets=None, constraints=None,
                    feature_names=FEATURE_NAMES):
    """Fit declared I/EV2/B/C/hard-gate/threshold ablations."""
    if feature_sets is None:
        full = tuple(feature_names)
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
    targets = [float(target) for target in targets]
    for name, features in feature_sets.items():
        model = fit_global_linear(rows, targets, feature_names=features,
                                  constraints=constraints)
        removed = {
            "I": ["I"], "EV2": ["EV2"], "B": ["B"], "C": ["C"],
            "hard_gate": ["shanten_hard_gate"],
            "threshold": ["action_threshold"],
        }.get(name, [])
        value = {"model": model.as_json(),
                 "metrics": regression_metrics(model, rows, targets),
                 "removed": removed}
        if name in ("hard_gate", "threshold"):
            value["kind"] = "policy_eligibility_diagnostic"
            value["policy"] = _policy_ablation_diagnostic(
                rows, targets, name)
        else:
            value["kind"] = "feature_ablation"
        result[name] = value
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


def paired_q_regret_report(records, *, baseline="legacy", teacher="teacher",
                           strategies=("legacy", "shape-v1", "shape-v2"),
                           rounds=2000, seed=0, alpha=0.05,
                           require_independent_worlds=False):
    """Return clustered paired-Q and signed-regret intervals.

    ``records`` are grouped by ``source_group`` before any statistic is
    computed.  A group may contain one row with a ``q`` mapping or one row per
    strategy.  Invalid groups stay in the denominator report and are never
    converted to zero.  The interval is a diagnostic source-group bootstrap;
    callers still need the frozen independent-world/release gates before
    using the result for a profile.
    """
    strategies = tuple(dict.fromkeys(str(x) for x in strategies))
    if baseline not in strategies:
        strategies = (str(baseline),) + strategies
    if teacher not in strategies:
        required = strategies + (str(teacher),)
    else:
        required = strategies
    grouped = _group_records(records)
    pair_values = {name: [] for name in strategies if name != baseline}
    regret_values = {name: [] for name in strategies}
    pair_groups = {name: [] for name in pair_values}
    regret_groups = {name: [] for name in regret_values}
    invalid = []
    independent_groups = 0

    for group, rows in grouped.items():
        q = {}
        duplicate = set()
        for row in rows:
            if isinstance(row.get("q"), Mapping):
                items = row["q"].items()
            elif row.get("strategy") is not None:
                items = ((row["strategy"], row.get("value", row.get("q"))),)
            else:
                continue
            for name, value in items:
                name = str(name)
                if name in q:
                    duplicate.add(name)
                q[name] = value
        missing = [name for name in required
                   if name not in q or q[name] is None]
        if duplicate:
            invalid.append({"source_group": group,
                            "reason": "duplicate_strategy",
                            "strategies": sorted(duplicate)})
            continue
        if missing:
            invalid.append({"source_group": group, "reason": "missing_q",
                            "missing": sorted(set(missing))})
            continue
        try:
            numeric = {name: float(q[name]) for name in required}
        except (TypeError, ValueError) as exc:
            invalid.append({"source_group": group, "reason": "non_numeric_q",
                            "error": str(exc)})
            continue
        if not all(math.isfinite(value) for value in numeric.values()):
            invalid.append({"source_group": group, "reason": "nonfinite_q"})
            continue
        independent = all(row.get("independent_worlds") is True
                          for row in rows)
        if require_independent_worlds and not independent:
            invalid.append({"source_group": group,
                            "reason": "independent_worlds_unverified"})
            continue
        if independent:
            independent_groups += 1
        for name in strategies:
            regret_values[name].append(numeric[teacher] - numeric[name])
            regret_groups[name].append(group)
            if name != baseline:
                pair_values[name].append(numeric[name] - numeric[baseline])
                pair_groups[name].append(group)

    def interval(values, groups):
        if not values:
            return {"n": 0, "clusters": 0, "mean": None, "low": None,
                    "high": None, "alpha": float(alpha),
                    "method": "source-group-bootstrap"}
        return cluster_bootstrap(values, groups, rounds=rounds, seed=seed,
                                 alpha=alpha)

    pair_report = {}
    for name, values in pair_values.items():
        pair_report[name] = {
            "interval": interval(values, pair_groups[name]),
            "values": list(values),
            "negative_count": sum(value < 0 for value in values),
        }
    regret_report = {}
    for name, values in regret_values.items():
        regret_report[name] = {
            "interval": interval(values, regret_groups[name]),
            "values": list(values),
            "negative_count": sum(value < 0 for value in values),
        }
    result = {
        "schema": "bot-ev-discard/paired-q-regret-report-v1",
        "baseline": str(baseline), "teacher": str(teacher),
        "strategies": list(strategies),
        "source_groups": len(grouped),
        "valid_source_groups": len(regret_values[strategies[0]])
        if strategies else 0,
        "invalid": invalid,
        "paired_policy_delta": pair_report,
        "signed_regret": regret_report,
        "statistics": {"alpha": float(alpha), "rounds": int(rounds),
                       "seed": int(seed),
                       "cluster": "source_group"},
        "independent_worlds_verified": (
            bool(regret_values[strategies[0]]) and
            independent_groups == len(regret_values[strategies[0]])
            if strategies else False),
        "independent_worlds_required": bool(require_independent_worlds),
        "counterfactual_evaluation": True,
        "online_decision": False,
        "oracle": False,
    }
    result["fingerprint"] = fingerprint(result)
    return result


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


def cluster_bootstrap(values, groups, *, rounds=2000, seed=0, alpha=0.05,
                      simultaneous_comparisons=1):
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
    comparisons = max(1, int(simultaneous_comparisons))
    effective_alpha = float(alpha) / comparisons
    # Recompute the quantiles with the adjusted level when this is a
    # simultaneous family.  The resamples themselves are unchanged.
    lo_i = max(0, int(math.floor((effective_alpha / 2) * len(means))) - 1)
    hi_i = min(len(means) - 1,
               int(math.ceil((1 - effective_alpha / 2) * len(means))) - 1)
    return {"n": int(len(values)), "clusters": len(unique),
            "mean": float(np.mean(values)), "low": means[lo_i],
            "high": means[hi_i], "rounds": int(rounds), "seed": int(seed),
            "alpha": float(alpha), "effective_alpha": effective_alpha,
            "simultaneous_comparisons": comparisons,
            "method": ("source-group-bootstrap-simultaneous"
                        if comparisons > 1 else "source-group-bootstrap")}


def cluster_bootstrap_simultaneous(series, groups, *, rounds=2000, seed=0,
                                   alpha=0.05):
    """Return same-resample, Bonferroni-adjusted clustered intervals.

    ``series`` maps metric names to aligned per-source-group values.  All
    metrics use the same sampled source groups, and the family size is carried
    into each interval so a caller cannot confuse marginal and simultaneous
    coverage.  No individual decision is ever resampled.
    """
    if not isinstance(series, Mapping) or not series:
        raise CalibrationError("simultaneous series must be non-empty")
    names = tuple(str(name) for name in series)
    arrays = {name: np.asarray(series[name], dtype=np.float64)
              for name in names}
    groups = np.asarray([str(x) for x in groups])
    if not len(groups) or any(len(values) != len(groups)
                              for values in arrays.values()):
        raise CalibrationError("series/groups must be non-empty and aligned")
    unique = sorted(set(groups))
    by_group = {
        name: {group: values[groups == group] for group in unique}
        for name, values in arrays.items()
    }
    rng = random.Random(int(seed))
    means = {name: [] for name in names}
    for _ in range(max(1, int(rounds))):
        sampled = [rng.choice(unique) for _ in unique]
        for name in names:
            means[name].append(float(np.mean(np.concatenate(
                [by_group[name][group] for group in sampled]))))
    comparisons = len(names)
    result = {}
    for name in names:
        ordered = sorted(means[name])
        effective_alpha = float(alpha) / comparisons
        lo_i = max(0, int(math.floor(
            (effective_alpha / 2) * len(ordered))) - 1)
        hi_i = min(len(ordered) - 1, int(math.ceil(
            (1 - effective_alpha / 2) * len(ordered))) - 1)
        result[name] = {
            "n": int(len(arrays[name])), "clusters": len(unique),
            "mean": float(np.mean(arrays[name])), "low": ordered[lo_i],
            "high": ordered[hi_i], "rounds": int(rounds), "seed": int(seed),
            "alpha": float(alpha), "effective_alpha": effective_alpha,
            "simultaneous_comparisons": comparisons,
            "method": "source-group-bootstrap-simultaneous",
        }
    return result


def release_gate(values, groups, *, required_pairs=4096, ci_lower=0.0,
                 rounds=2000, seed=0, invalid=0, alpha=0.05,
                 simultaneous_comparisons=1):
    """Return an explicit gate result; callers must keep legacy on failure."""
    interval = cluster_bootstrap(
        values, groups, rounds=rounds, seed=seed, alpha=alpha,
        simultaneous_comparisons=simultaneous_comparisons)
    pairs = len(values)
    passed = (pairs >= int(required_pairs) and not invalid and
              interval["low"] > float(ci_lower))
    return {"passed": bool(passed), "legacy_default": not passed,
        "pairs": pairs, "required_pairs": int(required_pairs),
        "invalid": int(invalid), "interval": interval,
        "criterion": {"ci_lower_gt": float(ci_lower),
                          "invalid_must_equal": 0,
                          "alpha": float(alpha),
                          "simultaneous_comparisons": int(
                              max(1, simultaneous_comparisons))}}


def freeze_split_manifest(*, train_start=240000, train_games=1024,
                          validation_start=241024, validation_games=1024,
                          final_test_start=242048, final_test_games=8192,
                          seed_fingerprint="", profile_fingerprint="",
                          rule_version="hangzhou-platform-guide-v34",
                          kernel_version="python-frontier-v1",
                          scope="discard",
                          continuation_version="frozen_shape_v1_self_kong_v1",
                          strategy="shape-v2"):
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
        "contract": {
            "profile_fingerprint": profile_fingerprint,
            "rule_version": rule_version,
            "kernel_version": kernel_version,
            "scope": scope,
            "continuation_version": continuation_version,
            "strategy": strategy,
        },
    }
    manifest["fingerprint"] = fingerprint(manifest)
    return manifest


def split_for_seed(seed, manifest=None):
    """Return the frozen split owning ``seed`` or ``None``.

    The ranges are half-open and are deliberately resolved from the manifest
    instead of from a caller's current command-line defaults.  This prevents
    a later expansion from silently moving a source game between train and
    holdout.
    """
    manifest = manifest or freeze_split_manifest()
    value = int(seed)
    for name, spec in manifest.get("splits", {}).items():
        start = int(spec["seed_start"])
        stop = start + int(spec["games"])
        if start <= value < stop:
            return str(name)
    return None


def split_rows(rows, manifest=None, *, require_all_splits=False):
    """Validate and partition calibration rows by source seed/group.

    Rows may carry an explicit ``split``.  If they carry ``seed`` or
    ``source_seed``, the value must agree with the frozen manifest.  A row
    without either an explicit split or a seed is rejected because assigning
    it by row order would make the holdout irreproducible.
    """
    manifest = manifest or freeze_split_manifest()
    result = {name: [] for name in SPLITS}
    owners = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise CalibrationError("calibration rows must be mappings")
        explicit = row.get("split")
        seed = row.get("seed", row.get("source_seed"))
        inferred = split_for_seed(seed, manifest) if seed is not None else None
        if explicit is not None:
            explicit = "final_test" if explicit == "final-test" else str(explicit)
            if explicit not in result:
                raise CalibrationError(f"unknown calibration split: {explicit}")
        if explicit is None and inferred is None:
            raise CalibrationError(
                "each calibration row needs split or a seed in the frozen manifest")
        if explicit is not None and inferred is not None and explicit != inferred:
            raise CalibrationError(
                f"row split {explicit} disagrees with frozen seed split {inferred}")
        owner = explicit or inferred
        group = row.get("source_group", row.get("group"))
        if group is None:
            raise CalibrationError("each calibration row needs source_group")
        group = str(group)
        previous = owners.get(group)
        if previous is not None and previous != owner:
            raise CalibrationError(
                f"source_group crosses calibration splits: {group}")
        owners[group] = owner
        result[owner].append(row)
    if require_all_splits and any(not result[name] for name in SPLITS):
        missing = ", ".join(name for name in SPLITS if not result[name])
        raise CalibrationError("calibration split is empty: " + missing)
    return result


def _fit_split_model(rows, *, feature_names, constraints, min_bucket_samples):
    targets = [float(row["target"]) for row in rows]
    model = fit_global_linear(rows, targets, feature_names=feature_names,
                              constraints=constraints)
    lut = fit_sparse_lut(rows, targets, feature_names=feature_names,
                         constraints=constraints,
                         min_bucket_samples=min_bucket_samples)
    return model, lut


def split_calibration_artifact(rows, manifest=None, *, feature_names=FEATURE_NAMES,
                               constraints=None, min_bucket_samples=64,
                               require_all_splits=True):
    """Fit train-only models and report validation/final-test diagnostics.

    The function is intentionally offline and side-effect free.  It is the
    implementation behind the calibration CLI: fitting consumes only the
    frozen train rows, while validation and final-test rows are used solely
    for metrics.  The returned artifact records the split and model
    fingerprints so a profile cannot be reused after a contract change.
    """
    manifest = manifest or freeze_split_manifest()
    partitions = split_rows(rows, manifest,
                            require_all_splits=require_all_splits)
    feature_names = tuple(feature_names)
    train = partitions["train"]
    model, lut = _fit_split_model(
        train, feature_names=feature_names, constraints=constraints,
        min_bucket_samples=min_bucket_samples)

    def metrics_for(part, predictor, *, bucket_fallbacks=0):
        targets = [float(row["target"]) for row in part]
        values = [row.get("features", row) for row in part]
        if not targets:
            return {"n": 0, "mae": None, "rmse": None, "bias": None,
                    "bucket_fallbacks": int(bucket_fallbacks)}
        predictions = np.asarray([predictor(value, row) for value, row in
                                  zip(values, part)], dtype=np.float64)
        actual = np.asarray(targets, dtype=np.float64)
        error = predictions - actual
        return {"n": len(actual), "mae": float(np.mean(np.abs(error))),
                "rmse": float(np.sqrt(np.mean(error ** 2))),
                "bias": float(np.mean(error)),
                "bucket_fallbacks": int(bucket_fallbacks)}

    global_metrics = {
        name: metrics_for(partitions[name], lambda value, _row: model.predict(value))
        for name in SPLITS
    }
    lut_metrics = {}
    for name in SPLITS:
        part = partitions[name]
        fallback_count = sum(
            1 for row in part
            if (row.get("bucket") if isinstance(row, Mapping) else None)
            is not None and tuple(row["bucket"]) not in lut.bucket_models)
        lut_metrics[name] = metrics_for(
            part,
            lambda value, row: lut.predict(
                value, row.get("bucket") if isinstance(row, Mapping) else None),
            bucket_fallbacks=fallback_count)
    ablations = ablation_report(
        train, [float(row["target"]) for row in train],
        constraints=constraints, feature_names=feature_names)
    for value in ablations["models"].values():
        # Ablations are fitted on train and evaluated on the untouched
        # validation split; the existing train metrics remain useful for
        # debugging but are not the selection metric.
        feature_names_for_model = tuple(value["model"]["feature_names"])
        fitted = fit_global_linear(
            train, [float(row["target"]) for row in train],
            feature_names=feature_names_for_model, constraints=constraints)
        value["validation_metrics"] = regression_metrics(
            fitted,
            [row.get("features", row) for row in partitions["validation"]],
            [float(row["target"]) for row in partitions["validation"]])
        value["final_test_metrics"] = regression_metrics(
            fitted,
            [row.get("features", row) for row in partitions["final_test"]],
            [float(row["target"]) for row in partitions["final_test"]])
    artifact = {
        "schema": "bot-ev-discard/split-calibration-artifact-v1",
        "manifest": manifest,
        "contract": dict(manifest.get("contract", {})),
        "feature_names": list(feature_names),
        "constraints": constraints or {},
        "min_bucket_samples": int(min_bucket_samples),
        "source_groups": {
            name: len({str(row.get("source_group", row.get("group")))
                       for row in part})
            for name, part in partitions.items()
        },
        "rows": {name: len(part) for name, part in partitions.items()},
        "global_model": model.as_json(),
        "sparse_lut": lut.as_json(),
        "metrics": {"global": global_metrics, "sparse_lut": lut_metrics},
        "ablations": ablations,
        "oracle": False,
        "fit_split": "train",
        "selection_split": "validation",
        "evaluation_splits": ["validation", "final_test"],
        "model_selection": {
            "criterion": "validation_rmse",
            "final_test_is_report_only": True,
        },
    }
    artifact["fingerprint"] = fingerprint(artifact, 24)
    return artifact


def evidence_contract(profile=None, *, manifest=None, strategy="shape-v2",
                      scope="discard", continuation_version=None):
    """Return the immutable contract used by calibration/evidence artifacts."""
    if profile is None:
        from .profile import ProfileSpec
        profile = ProfileSpec.shape_v2(scope=scope)
    if profile.scope != scope:
        raise CalibrationError(
            f"profile scope {profile.scope} disagrees with evidence scope {scope}")
    continuation_version = (continuation_version or
                            profile.continuation_version)
    return {
        "profile_fingerprint": profile.fingerprint,
        "rule_version": profile.rules_version,
        "kernel_version": profile.kernel_version,
        "scope": scope,
        "continuation_version": continuation_version,
        "strategy": strategy,
        "reward_units": profile.reward_units,
        "belief_version": profile.belief_version,
        "tail_version": profile.tail_version,
        "horizon": profile.horizon,
        "manifest_fingerprint": ((manifest or {}).get("fingerprint")
                                  if manifest is not None else None),
    }


def calibration_evidence_manifest(profile=None, *, split_manifest=None,
                                  strategy="shape-v2", scope="discard",
                                  calibration_artifact=None,
                                  teacher_artifact=None,
                                  score_evidence=None, strict=False,
                                  require_all=False):
    """Build a versioned manifest and validity checks for P4 evidence.

    The function does not assert that a release gate passed.  It binds every
    supplied artifact to the same profile/rule/kernel/scope/continuation
    contract, so stale evidence is visible before publication.
    """
    if profile is None:
        from .profile import ProfileSpec
        profile = ProfileSpec.shape_v2(scope=scope)
    split_manifest = split_manifest or freeze_split_manifest(
        profile_fingerprint=profile.fingerprint,
        rule_version=profile.rules_version,
        kernel_version=profile.kernel_version, scope=scope,
        continuation_version=profile.continuation_version,
        strategy=strategy)
    expected = evidence_contract(
        profile, manifest=split_manifest, strategy=strategy, scope=scope)
    split_payload = dict(split_manifest)
    supplied_split_fingerprint = split_payload.pop("fingerprint", None)
    actual_split_fingerprint = fingerprint(split_payload)
    split_self_check = {
        "valid": (supplied_split_fingerprint is not None and
                  supplied_split_fingerprint == actual_split_fingerprint),
        "supplied": supplied_split_fingerprint,
        "actual": actual_split_fingerprint,
    }
    base_fields = ("profile_fingerprint", "rule_version", "kernel_version",
                   "scope", "continuation_version", "strategy")
    strict_fields = base_fields + (
        "reward_units", "belief_version", "tail_version", "horizon",
        "manifest_fingerprint")
    artifacts = {}
    invalidated = []
    for name, artifact in (("calibration", calibration_artifact),
                           ("teacher", teacher_artifact),
                           ("score_evidence", score_evidence)):
        if artifact is None:
            artifacts[name] = {"present": False, "valid": False}
            if require_all:
                invalidated.append({"artifact": name,
                                    "reason": "missing_artifact"})
            continue
        actual = dict(artifact.get("contract", {}))
        if not actual:
            actual.update({key: artifact.get(key) for key in (
                "profile_fingerprint", "rule_version", "kernel_version",
                "scope", "continuation_version", "strategy")
                          if artifact.get(key) is not None})
            profile_data = artifact.get("profile")
            if isinstance(profile_data, Mapping):
                actual.setdefault("profile_fingerprint",
                                  profile_data.get("fingerprint"))
                actual.setdefault("rule_version",
                                  profile_data.get("rules_version"))
                actual.setdefault("kernel_version",
                                  profile_data.get("kernel_version"))
                actual.setdefault("continuation_version",
                                  profile_data.get("continuation_version"))
                actual.setdefault("scope", profile_data.get("scope"))
            metadata = artifact.get("metadata")
            if isinstance(metadata, Mapping):
                actual.setdefault("strategy", metadata.get("strategy"))
        checked_fields = strict_fields if strict else base_fields
        # A score run is bound to the frozen final-test seed schedule by its
        # own ``seed_start/games`` fields.  Older score runners do not carry
        # the split fingerprint in their contract, so requiring that one
        # field here would turn otherwise auditable schedule-bound evidence
        # into a false mismatch.  Calibration artifacts still require the
        # split fingerprint in strict mode.
        if name == "score_evidence" and strict:
            checked_fields = tuple(field for field in checked_fields
                                   if field != "manifest_fingerprint")
        check = validate_evidence_fingerprint(
            actual, expected, fields=checked_fields)
        valid = bool(check["valid"])
        reasons = []
        if not valid:
            reasons.append("contract_mismatch")
        if strict and artifact.get("manifest") is not None:
            supplied = artifact["manifest"].get("fingerprint")
            if supplied != split_manifest.get("fingerprint"):
                valid = False
                reasons.append("artifact_manifest_mismatch")
        if strict and not actual:
            valid = False
            reasons.append("missing_contract")
        artifacts[name] = {
            "present": True, "valid": valid,
            "mismatch": check["mismatch"],
            "fingerprint": artifact.get(
                "fingerprint", artifact.get("artifact_fingerprint")),
        }
        if reasons:
            artifacts[name]["invalidated_reasons"] = reasons
            invalidated.append({"artifact": name, "reason": reasons})
    missing_required = [name for name in ("calibration", "teacher",
                                           "score_evidence")
                        if not artifacts[name]["present"]]
    value = {
        "schema": "bot-ev-discard/calibration-evidence-manifest-v1",
        "contract": expected,
        "split_manifest": split_manifest,
        "split_manifest_self_check": split_self_check,
        "profile": profile.as_json(),
        "artifacts": artifacts,
        "strict": bool(strict),
        "require_all": bool(require_all),
        "missing_required_artifacts": missing_required,
        "invalidated": invalidated,
        "contract_valid": bool(
            split_self_check["valid"] if strict else True) and not invalidated,
        "legacy_default": True,
        "offline_only": True,
    }
    value["fingerprint"] = fingerprint(value, 24)
    return value


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
                              "kernel_version", "scope",
                              "continuation_version", "strategy"))
    mismatch = {field: (actual.get(field), expected.get(field))
                for field in fields if actual.get(field) != expected.get(field)}
    return {"valid": not mismatch, "mismatch": mismatch,
            "checked_fields": list(fields)}


# Short aliases used by scripts and notebooks.
fit_linear = fit_global_linear
fit_lut = fit_sparse_lut
paired_regret = paired_q_regret
