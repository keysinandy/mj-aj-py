"""Dataset split integrity, quality reporting and coverage gates.

Reports are pure functions over a ``SearchDataset`` so the same code path is
used for generation-time validation, resume checks and release evidence.
"""

from __future__ import annotations

from collections import Counter
import math
from typing import Iterable, Mapping

from ..decision.profile import fingerprint
from .search_data import SearchDataset, feature_fingerprint

SPLITS = ("train", "validation", "final-test")
REPORT_SCHEMA = "search-distillation-dataset-report-v1"


def _counter(values):
    return {str(key): int(count)
            for key, count in sorted(Counter(values).items(),
                                     key=lambda item: str(item[0]))}


def _distribution(values):
    rows = sorted(float(value) for value in values)
    if not rows:
        return {"count": 0, "min": None, "p50": None, "p95": None,
                "max": None, "mean": None}
    def percentile(p):
        index = min(len(rows) - 1, max(0, int(math.ceil(p * len(rows)) - 1)))
        return rows[index]
    return {"count": len(rows), "min": rows[0], "p50": percentile(.50),
            "p95": percentile(.95), "max": rows[-1],
            "mean": sum(rows) / len(rows)}


def merge_split_assignments(*assignments: Mapping[str, str]):
    """Merge per-generation split maps, rejecting cross-generation conflicts."""
    merged: dict[str, str] = {}
    for assignment in assignments:
        for group, split in (assignment or {}).items():
            group, split = str(group), str(split)
            if split not in SPLITS:
                raise ValueError(f"unknown split: {split!r}")
            if group in merged and merged[group] != split:
                raise ValueError(
                    f"source group {group!r} changes split "
                    f"{merged[group]!r} -> {split!r}")
            merged[group] = split
    return merged


def validate_split_integrity(dataset: SearchDataset,
                             assignments: Mapping[str, str]):
    """Every source group is fully inside exactly one split."""
    groups = {}
    for sample in dataset.samples:
        groups.setdefault(sample.source_group, 0)
        groups[sample.source_group] += 1
    missing = sorted(group for group in groups if group not in assignments)
    if missing:
        raise ValueError("missing split assignment for: " +
                         ", ".join(missing))
    by_split: dict[str, list[str]] = {}
    for group, split in assignments.items():
        group, split = str(group), str(split)
        if split not in SPLITS:
            raise ValueError(f"unknown split: {split!r}")
        by_split.setdefault(split, []).append(group)
    duplicates = {split: sorted(groups) for split, groups in by_split.items()
                  if len(set(groups)) != len(groups)}
    if duplicates:
        raise ValueError(f"source group assigned twice: {duplicates}")
    value = {
        "schema": "source-split-integrity-v1",
        "groups": {group: assignments[group] for group in sorted(groups)},
        "counts": {split: len(sorted(set(by_split.get(split, []))))
                   for split in SPLITS},
        "samples": {split: sum(
            1 for sample in dataset.samples
            if assignments.get(sample.source_group) == split)
            for split in SPLITS},
        "oracle": False,
    }
    value["fingerprint"] = fingerprint(value, 24)
    return value


def _shanten_bucket(value):
    if value is None:
        return "unknown"
    value = int(value)
    if value < 0:
        return "won"
    if value <= 1:
        return "0-1"
    if value <= 3:
        return "2-3"
    return "4+"


def _wall_bucket(value):
    if value is None:
        return "unknown"
    value = int(value)
    if value > 20:
        return ">20"
    if value > 10:
        return "11-20"
    return "<=10"


def dataset_report(dataset: SearchDataset):
    """Counts by generation, legal count, phase and critical buckets."""
    samples = list(dataset.samples)
    legal_counts = [sum(1 for value in sample.legal_mask if value)
                    for sample in samples]
    q_gaps = [sample.teacher_q_gap for sample in samples
              if sample.teacher_q_gap is not None]
    variances = [sample.search_variance for sample in samples
                 if sample.search_variance is not None]
    report = {
        "schema": REPORT_SCHEMA,
        "count": len(samples),
        "source_groups": len({sample.source_group for sample in samples}),
        "generation": _counter(sample.generation for sample in samples),
        "policy_version_source": _counter(
            sample.policy_version_source for sample in samples),
        "opponent_policy_version": _counter(
            sample.opponent_policy_version for sample in samples),
        "legal_action_count": _counter(legal_counts),
        "multi_action": _counter(bool(count > 1) for count in legal_counts),
        "phase": _counter(sample.phase or "unknown" for sample in samples),
        "you_cai_bi_kao": _counter(bool(sample.you_cai_bi_kao)
                                   for sample in samples),
        "dealer": _counter(bucket for bucket in
                           ("dealer" if sample.hero_seat == sample.dealer
                            else "non-dealer" for sample in samples)),
        "shanten_bucket": _counter(_shanten_bucket(sample.shanten)
                                   for sample in samples),
        "wall_bucket": _counter(_wall_bucket(sample.wall_remaining)
                                for sample in samples),
        "special_state_tags": _counter(
            tag for sample in samples
            for tag in (sample.special_state_tags or ("untagged",))),
        "forced": _counter(bool(sample.forced) for sample in samples),
        "teacher_budget_tier": _counter(sample.teacher_budget_tier
                                        for sample in samples),
        "teacher_status": _counter(sample.teacher_status or "unknown"
                                   for sample in samples),
        "simulations": _distribution(sample.simulations
                                     for sample in samples),
        "requested_simulations": _distribution(
            sample.teacher_requested_simulations for sample in samples),
        "completed_simulations": _distribution(
            sample.teacher_completed_simulations for sample in samples),
        "failed_simulations": _distribution(
            sample.teacher_failed_simulations for sample in samples),
        "failed_simulations_nonzero": sum(
            1 for sample in samples if sample.teacher_failed_simulations > 0),
        "belief_resets": _distribution(sample.reset_count
                                       for sample in samples),
        "belief_resets_nonzero": sum(
            1 for sample in samples if sample.reset_count > 0),
        "ambiguous": _counter(bool(sample.ambiguous) for sample in samples),
        "ambiguous_rate": (sum(1 for sample in samples if sample.ambiguous) /
                           len(samples) if samples else None),
        "q_gap": _distribution(q_gaps),
        "q_gap_missing": sum(1 for sample in samples
                             if sample.teacher_q_gap is None),
        "search_variance": _distribution(variances),
        "search_variance_missing": sum(1 for sample in samples
                                       if sample.search_variance is None),
        "features": {
            "with_features": sum(1 for sample in samples
                                 if sample.planes is not None),
            "with_fingerprint": sum(1 for sample in samples
                                    if sample.feature_fingerprint),
        },
        "oracle": False,
    }
    report["fingerprint"] = fingerprint(report, 24)
    return report


def duplicate_report(dataset: SearchDataset):
    """Detect context collisions, exact duplicates and repeated work ids."""
    contexts: dict[str, set] = {}
    exact: Counter = Counter()
    work_ids: Counter = Counter()
    for sample in dataset.samples:
        feature_key = sample.feature_fingerprint or None
        contexts.setdefault(sample.context_hash, set()).add(feature_key)
        target = tuple(sorted((int(action), float(probability))
                              for action, probability in
                              sample.policy_target.items()
                              if probability))
        exact[(sample.context_hash, sample.history_hash, feature_key,
               target, sample.teacher_status)] += 1
        work_ids[sample.work_id] += 1
    collisions = [
        {"context_hash": context, "feature_fingerprints": sorted(
            value for value in values if value)}
        for context, values in sorted(contexts.items())
        if len(values) > 1
    ]
    duplicates = [{"key": list(map(str, key)), "count": count}
                  for key, count in sorted(exact.items(),
                                           key=lambda item: -item[1])
                  if count > 1]
    repeated = [{"work_id": work_id, "count": count}
                for work_id, count in sorted(work_ids.items())
                if count > 1]
    value = {
        "schema": "search-distillation-duplicate-report-v1",
        "count": len(dataset.samples),
        "context_collisions": collisions,
        "exact_duplicates": duplicates,
        "repeated_work_ids": repeated,
        "unique_work_ids": len(work_ids),
        "oracle": False,
    }
    value["fingerprint"] = fingerprint(value, 24)
    return value


def coverage_gate(report: Mapping, requirements: Iterable[tuple[str, int]]):
    """Check minimum critical-bucket counts from the frozen profile."""
    counts = dict(report.get("special_state_tags") or {})
    missing = []
    rows = []
    for tag, minimum in requirements:
        available = int(counts.get(tag, 0))
        passed = available >= int(minimum)
        rows.append({"tag": str(tag), "minimum": int(minimum),
                     "available": available, "passed": passed})
        if not passed:
            missing.append(str(tag))
    value = {
        "schema": "search-distillation-coverage-gate-v1",
        "passed": not missing, "missing": missing, "buckets": rows,
        "oracle": False,
    }
    value["fingerprint"] = fingerprint(value, 24)
    return value


def verify_feature_fingerprints(dataset: SearchDataset):
    """Recompute per-sample feature digests and reject mismatches."""
    checked = 0
    mismatches = []
    for sample in dataset.samples:
        if sample.planes is None and sample.scalars is None:
            continue
        if not sample.feature_fingerprint:
            mismatches.append({"context_hash": sample.context_hash,
                               "reason": "missing_fingerprint"})
            continue
        recomputed = feature_fingerprint(sample.planes, sample.scalars)
        checked += 1
        if recomputed != sample.feature_fingerprint:
            mismatches.append({"context_hash": sample.context_hash,
                               "reason": "fingerprint_mismatch"})
    value = {
        "schema": "search-distillation-feature-fingerprint-check-v1",
        "checked": checked, "mismatches": mismatches,
        "passed": not mismatches,
    }
    value["fingerprint"] = fingerprint(value, 24)
    return value
