"""Held-out belief calibration metrics.

This module deliberately accepts truth as an evaluation-only argument.  The
truth is never passed into :class:`BeliefState.update`; callers can therefore
use the same code for a hidden simulator split and for a public-only online
shadow run without changing posterior semantics.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Iterable, Mapping

from ..decision.profile import fingerprint


def _clip_probability(value):
    return min(1.0 - 1e-12, max(1e-12, float(value)))


def brier_score(probabilities: Iterable[float], outcomes: Iterable[bool | int]):
    pairs = [(float(probability), bool(outcome))
             for probability, outcome in zip(probabilities, outcomes)]
    if not pairs:
        return None
    return sum((probability - int(outcome)) ** 2
               for probability, outcome in pairs) / len(pairs)


def log_loss(probabilities: Iterable[float], outcomes: Iterable[bool | int]):
    pairs = [(_clip_probability(probability), bool(outcome))
             for probability, outcome in zip(probabilities, outcomes)]
    if not pairs:
        return None
    return -sum(math.log(probability if outcome else 1.0 - probability)
                for probability, outcome in pairs) / len(pairs)


def coverage(probabilities: Iterable[float], outcomes: Iterable[bool | int],
             level=0.95):
    """Coverage of a Bernoulli posterior percentile proxy.

    A marginal is considered covered when its probability is at least the
    requested one-sided confidence threshold for a true-held event.  This is
    intentionally a simple, auditable diagnostic rather than a claim of a
    calibrated interval distribution.
    """
    threshold = float(level)
    pairs = [(float(probability), bool(outcome))
             for probability, outcome in zip(probabilities, outcomes)
             if outcome]
    if not pairs:
        return None
    return sum(probability >= threshold for probability, _ in pairs) / len(pairs)


def _flatten_marginals(summary: Mapping[str, Any], truths: Mapping[Any, Any]):
    predicted = []
    outcomes = []
    opponent = (summary.get("marginals") or {}).get(
        "opponent_hand_probability", {})
    for seat, row in opponent.items():
        truth_row = truths.get(seat, truths.get(int(seat), ()))
        for tile, value in enumerate(row):
            predicted.append(value)
            outcomes.append(bool(truth_row[tile]) if tile < len(truth_row) else False)
    return predicted, outcomes


@dataclass(frozen=True)
class CalibrationReport:
    schema: str
    belief_version: str
    source_groups: int
    brier: float | None
    log_loss: float | None
    true_held_coverage: float | None
    live_wall_brier: float | None
    ess_mean: float | None
    reset_rate: float | None
    resample_rate: float | None
    baseline_brier: float | None = None
    baseline_log_loss: float | None = None
    passed: bool = False
    fingerprint: str = ""

    def as_json(self):
        return {
            "schema": self.schema, "belief_version": self.belief_version,
            "source_groups": self.source_groups, "brier": self.brier,
            "log_loss": self.log_loss,
            "true_held_coverage": self.true_held_coverage,
            "live_wall_brier": self.live_wall_brier, "ess_mean": self.ess_mean,
            "reset_rate": self.reset_rate, "resample_rate": self.resample_rate,
            "baseline_brier": self.baseline_brier,
            "baseline_log_loss": self.baseline_log_loss,
            "passed": self.passed,
            "fingerprint": self.fingerprint or fingerprint({
                "schema": self.schema, "belief_version": self.belief_version,
                "source_groups": self.source_groups, "brier": self.brier,
                "log_loss": self.log_loss, "baseline_brier": self.baseline_brier,
                "baseline_log_loss": self.baseline_log_loss,
                "passed": self.passed,
            }, 24),
        }


def evaluate_belief_rows(rows: Iterable[Mapping[str, Any]], *, belief_version="belief-v2",
                         baseline_rows=None, min_groups=1):
    """Evaluate held-out rows with prediction/truth kept in separate fields.

    Each row may contain ``summary`` (or a summary mapping directly),
    ``truth_opponent_hand`` and optional ``truth_live_wall``.  A row's
    ``source_group`` is counted once; no train/validation assignment is made
    here, so the caller remains responsible for frozen split ownership.
    """
    rows = list(rows)
    predictions, outcomes = [], []
    live_predictions, live_outcomes = [], []
    ess, resets, resamples = [], [], []
    groups = set()
    for row in rows:
        summary = row.get("summary", row)
        truth = row.get("truth_opponent_hand") or {}
        p, y = _flatten_marginals(summary, truth)
        predictions.extend(p)
        outcomes.extend(y)
        live = ((summary.get("marginals") or {}).get("live_wall_probability")
                or [])
        live_truth = row.get("truth_live_wall") or []
        live_predictions.extend(live)
        live_outcomes.extend(bool(value) for value in live_truth[:len(live)])
        groups.add(row.get("source_group", row.get("context_hash")))
        if summary.get("ess") is not None:
            ess.append(float(summary["ess"]))
        resets.append(bool(summary.get("reset_count", 0)))
        resamples.append(bool(summary.get("resample_count", 0)))
    baseline_brier = baseline_log = None
    if baseline_rows is not None:
        baseline = evaluate_belief_rows(
            baseline_rows, belief_version="uniform_unseen-v1", min_groups=min_groups)
        baseline_brier = baseline.brier
        baseline_log = baseline.log_loss
    brier = brier_score(predictions, outcomes)
    ll = log_loss(predictions, outcomes)
    passed = (len(groups) >= int(min_groups) and brier is not None and ll is not None
              and (baseline_brier is None or brier <= baseline_brier)
              and (baseline_log is None or ll <= baseline_log))
    report = CalibrationReport(
        schema="belief-calibration-report-v1", belief_version=str(belief_version),
        source_groups=len(groups), brier=brier, log_loss=ll,
        true_held_coverage=coverage(predictions, outcomes),
        live_wall_brier=brier_score(live_predictions, live_outcomes),
        ess_mean=(sum(ess) / len(ess) if ess else None),
        reset_rate=(sum(resets) / len(resets) if resets else None),
        resample_rate=(sum(resamples) / len(resamples) if resamples else None),
        baseline_brier=baseline_brier, baseline_log_loss=baseline_log,
        passed=passed)
    return report


def calibration_gate(report: CalibrationReport | Mapping[str, Any], *,
                     require_improvement=True):
    value = report.as_json() if hasattr(report, "as_json") else dict(report)
    if not value.get("passed"):
        return {"passed": False, "reason": "calibration_metrics_failed"}
    if require_improvement and value.get("baseline_brier") is not None:
        if value.get("brier") is None or value["brier"] > value["baseline_brier"]:
            return {"passed": False, "reason": "no_brier_improvement"}
    if require_improvement and value.get("baseline_log_loss") is not None:
        if value.get("log_loss") is None or value["log_loss"] > value["baseline_log_loss"]:
            return {"passed": False, "reason": "no_log_loss_improvement"}
    return {"passed": True, "reason": "calibration_passed",
            "fingerprint": value.get("fingerprint")}
