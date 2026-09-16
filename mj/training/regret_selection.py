"""Frozen reference contexts, network-only regret evaluation and selection.

Reference evidence comes from the high-budget information-set search on
contexts that live outside every training source group.  Checkpoint selection
is regret-first: top-1 agreement is a diagnostic only.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from ..decision.profile import fingerprint
from .distillation_profile import SearchDistillationProfile
from .policy_value_train import inference_benchmark, policy_kl
from .search_data import SearchSample, work_identity

REFERENCE_SET_SCHEMA = "search-reference-set-v1"
REGRET_REPORT_SCHEMA = "search-bc-regret-report-v1"


def load_reference_rows(path) -> list:
    rows = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _sample_of(row):
    return SearchSample.from_json(row["sample"])


def reference_split(rows, assignments: Mapping[str, str], *,
                    allowed_splits=("validation", "final-test")):
    """Rows whose source group is outside the training split."""
    allowed = set(str(value) for value in allowed_splits)
    selected, rejected = [], []
    for row in rows:
        split = assignments.get(str(row["source_group"]))
        if split in allowed:
            selected.append(row)
        else:
            rejected.append({"source_group": row["source_group"], "split": split})
    return selected, rejected


def freeze_reference_contexts(rows, assignments: Mapping[str, str], *,
                              allowed_splits=("validation", "final-test")):
    """Freeze the reference set and reject train-group leakage."""
    selected, rejected = reference_split(rows, assignments,
                                         allowed_splits=allowed_splits)
    if rejected:
        raise ValueError(
            f"reference contexts include non-reference splits: {rejected[:5]}")
    value = {
        "schema": REFERENCE_SET_SCHEMA,
        "count": len(selected),
        "source_groups": sorted({row["source_group"] for row in selected}),
        "splits": sorted(set(allowed_splits)),
        "oracle": False,
    }
    rows_identity = [
        work_identity(
            source_group=row["source_group"],
            context_hash=row["sample"]["context_hash"],
            history_hash=row["sample"]["history_hash"],
            teacher_seed=row["sample"].get("teacher_seed", 0),
            search_fingerprint=row["sample"]["search_fingerprint"])
        for row in selected]
    value["fingerprint"] = fingerprint(
        {**value, "rows": rows_identity}, 24)
    return value


@dataclass(frozen=True)
class CheckpointEvaluation:
    path: str
    mean_reference_regret: float | None
    p50_reference_regret: float | None
    p95_reference_regret: float | None
    catastrophic_regret_rate: float | None
    policy_kl: float | None
    top1_action_agreement: float | None
    count: int
    skipped: dict
    bucket_regret: dict
    latency: dict
    fingerprint: str = ""

    def as_json(self):
        value = {
            "schema": REGRET_REPORT_SCHEMA, "path": self.path,
            "mean_reference_regret": self.mean_reference_regret,
            "p50_reference_regret": self.p50_reference_regret,
            "p95_reference_regret": self.p95_reference_regret,
            "catastrophic_regret_rate": self.catastrophic_regret_rate,
            "policy_kl": self.policy_kl,
            "top1_action_agreement": self.top1_action_agreement,
            "count": self.count, "skipped": dict(self.skipped),
            "bucket_regret": self.bucket_regret,
            "latency": dict(self.latency), "oracle": False,
        }
        value["fingerprint"] = self.fingerprint or fingerprint(value, 24)
        return value


def _percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1,
                max(0, int(math.ceil(fraction * len(ordered)) - 1)))
    return ordered[index]


def _bucket_summary(rows):
    buckets = {}
    for bucket, regret in rows:
        entry = buckets.setdefault(bucket, [])
        entry.append(regret)
    return {bucket: {"count": len(values), "mean": sum(values) / len(values),
                     "p95": _percentile(values, .95)}
            for bucket, values in sorted(buckets.items())}


def evaluate_checkpoint(model, rows, *, profile: SearchDistillationProfile,
                        device="cpu", catastrophe_threshold=None,
                        benchmark_samples=0, benchmark_repeats=1):
    """Run network-only actions against the frozen reference evidence."""
    import torch

    from ..features import action_to_flat

    threshold = (float(catastrophe_threshold)
                 if catastrophe_threshold is not None
                 else float(profile.catastrophic_regret_threshold))
    regrets, kls, agreements = [], [], []
    buckets = []
    skipped = {"forced": 0, "missing_q": 0, "missing_features": 0,
               "no_legal_action": 0, "model_error": 0}
    evaluated_rows = []
    model.to(device)
    model.eval()
    with torch.no_grad():
        for row in rows:
            sample = _sample_of(row)
            if sample.forced:
                skipped["forced"] += 1
                continue
            if not sample.q_by_action:
                skipped["missing_q"] += 1
                continue
            if sample.planes is None or sample.scalars is None:
                skipped["missing_features"] += 1
                continue
            legal = [index for index, ok in enumerate(sample.legal_mask) if ok]
            if not legal:
                skipped["no_legal_action"] += 1
                continue
            try:
                planes = torch.as_tensor(
                    [sample.planes], dtype=torch.float32, device=device)
                scalars = torch.as_tensor(
                    [sample.scalars], dtype=torch.float32, device=device)
                mask = torch.as_tensor(
                    [sample.legal_mask], dtype=torch.bool, device=device)
                action = int(action_to_flat(
                    model.select_action(planes, scalars, mask)))
                distribution = model.policy_distribution(planes, scalars,
                                                         mask)
            except Exception:
                skipped["model_error"] += 1
                continue
            q_values = {int(action): float(value)
                        for action, value in sample.q_by_action.items()}
            if action not in q_values:
                skipped["missing_q"] += 1
                continue
            best = max(q_values.values())
            regret = best - q_values[action]
            regrets.append(regret)
            evaluated_rows.append(row)
            target = sample.policy_target
            predicted = {action_to_flat(item): probability for item, probability
                         in zip(distribution.actions,
                                distribution.probabilities)}
            kl = policy_kl(target, predicted)
            if kl is not None:
                kls.append(kl)
            agreements.append(int(max(q_values, key=q_values.get)) == action)
            for tag in (sample.special_state_tags or ("untagged",)):
                buckets.append((f"tag:{tag}", regret))
            buckets.append((f"phase:{sample.phase or 'unknown'}", regret))
    mean = sum(regrets) / len(regrets) if regrets else None
    catastrophic = (sum(1 for value in regrets if value > threshold) /
                    len(regrets) if regrets else None)
    latency = {}
    if benchmark_samples and evaluated_rows:
        latency = batch1_benchmark(
            model, evaluated_rows[:int(benchmark_samples)],
            repeats=int(benchmark_repeats), device=device)
    report = CheckpointEvaluation(
        path=str(getattr(model, "checkpoint_path", "")),
        mean_reference_regret=mean,
        p50_reference_regret=_percentile(regrets, .50),
        p95_reference_regret=_percentile(regrets, .95),
        catastrophic_regret_rate=catastrophic,
        policy_kl=(sum(kls) / len(kls) if kls else None),
        top1_action_agreement=(sum(agreements) / len(agreements)
                               if agreements else None),
        count=len(regrets), skipped=skipped,
        bucket_regret=_bucket_summary(buckets), latency=latency)
    return report


def batch1_benchmark(model, rows, *, repeats=1, device="cpu"):
    """Full CPU path: feature extraction, mask, forward and argmax."""
    import torch

    def callable_for(row):
        from ..belief import InformationHistory
        from ..decision.context import PublicDecisionContext

        sample = _sample_of(row)
        context = (PublicDecisionContext.from_public(row["context"])
                   if row.get("context") is not None else None)
        history = (InformationHistory.from_json(row["history"])
                   if row.get("history") is not None else None)
        mask = list(sample.legal_mask)

        def infer(_):
            if context is not None:
                planes, scalars = model.extract_features(
                    context, history=history, belief=None)
                planes = torch.as_tensor([planes], dtype=torch.float32,
                                         device=device)
                scalars = torch.as_tensor([scalars], dtype=torch.float32,
                                          device=device)
            else:
                planes = torch.as_tensor([sample.planes], dtype=torch.float32,
                                         device=device)
                scalars = torch.as_tensor([sample.scalars],
                                          dtype=torch.float32, device=device)
            mask_tensor = torch.as_tensor([mask], dtype=torch.bool,
                                          device=device)
            action = model.select_action(planes, scalars, mask_tensor)
            return int(action)

        return infer

    inputs = [callable_for(row) for row in rows]
    return inference_benchmark(lambda infer: infer(None), inputs,
                               repeats=int(repeats))


def select_best_checkpoint(evaluations: Sequence[CheckpointEvaluation], *,
                           previous_mean=None, previous_p95=None,
                           p95_limit=None, catastrophic_limit=0.02,
                           p95_regression_factor=1.25,
                           latency_p95_ms=None, special_state_regression=0.0):
    """Regret-first selection with p95/catastrophic/special-state gates."""
    rows = []
    for evaluation in evaluations:
        reasons = []
        if evaluation.mean_reference_regret is None:
            reasons.append("missing_mean_regret")
        if (p95_limit is not None and evaluation.p95_reference_regret is not None
                and evaluation.p95_reference_regret > float(p95_limit)):
            reasons.append("p95_limit")
        if (previous_p95 is not None and evaluation.p95_reference_regret is not None
                and evaluation.p95_reference_regret >
                float(previous_p95) * float(p95_regression_factor)):
            reasons.append("p95_regression")
        if (evaluation.catastrophic_regret_rate is not None and
                evaluation.catastrophic_regret_rate > float(catastrophic_limit)):
            reasons.append("catastrophic_regret")
        if special_state_regression and evaluation.bucket_regret:
            for bucket, summary in evaluation.bucket_regret.items():
                if (bucket.startswith("tag:") and summary["mean"] is not None
                        and previous_mean is not None
                        and summary["mean"] > float(previous_mean) +
                        float(special_state_regression)):
                    reasons.append(f"special_state:{bucket}")
        if (latency_p95_ms is not None and evaluation.latency
                and evaluation.latency.get("p95_ms") is not None
                and evaluation.latency["p95_ms"] > float(latency_p95_ms)):
            reasons.append("latency_p95")
        rows.append({
            "path": evaluation.path, "eligible": not reasons,
            "reasons": reasons,
            "mean_reference_regret": evaluation.mean_reference_regret,
            "p95_reference_regret": evaluation.p95_reference_regret,
            "catastrophic_regret_rate": evaluation.catastrophic_regret_rate,
            "top1_action_agreement": evaluation.top1_action_agreement,
            "latency_p95_ms": (evaluation.latency or {}).get("p95_ms"),
        })
    eligible = [row for row in rows if row["eligible"]
                and row["mean_reference_regret"] is not None]
    eligible.sort(key=lambda row: (row["mean_reference_regret"],
                                   row["p95_reference_regret"], row["path"]))
    selected = eligible[0] if eligible else None
    promoted = False
    reason = "no_eligible_checkpoint"
    if selected is not None:
        if previous_mean is None or selected["mean_reference_regret"] < float(previous_mean):
            promoted = True
            reason = "reference_regret_improved"
        else:
            reason = "reference_regret_not_improved"
    value = {
        "schema": "search-bc-checkpoint-selection-v1",
        "selected": selected["path"] if selected else None,
        "promoted": promoted, "reason": reason,
        "ranking": eligible + [row for row in rows if not row["eligible"]],
        "primary_selector": "mean_reference_regret",
        "top1_is_diagnostic_only": True,
        "oracle": False,
    }
    value["fingerprint"] = fingerprint(value, 24)
    return value


def write_selection_report(path, *, evaluations, selection):
    value = {
        "schema": "search-bc-selection-report-v1",
        "selection": selection,
        "evaluations": [evaluation.as_json() for evaluation in evaluations],
        "oracle": False,
    }
    value["fingerprint"] = fingerprint(value, 24)
    Path(path).write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    return value
