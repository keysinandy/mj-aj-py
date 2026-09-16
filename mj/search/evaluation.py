"""Offline search/reference and paired-score reporting helpers."""

from __future__ import annotations

import math
from typing import Iterable, Mapping

from ..decision.profile import fingerprint
from .profile import SearchProfile
from .report import regret_report


def action_agreement(reference_results, candidate_results):
    rows = []
    for reference, candidate in zip(reference_results, candidate_results):
        ref_action = (reference.best_action if hasattr(reference, "best_action")
                      else reference.get("best_action"))
        candidate_action = (candidate.best_action if hasattr(candidate, "best_action")
                            else candidate.get("best_action"))
        rows.append(ref_action == candidate_action)
    return {
        "count": len(rows),
        "agreement": (sum(rows) / len(rows) if rows else None),
    }


def seed_variance(values_by_seed: Mapping[object, Iterable[float]]):
    """Population variance of per-seed means, retaining missing seed rows."""
    means = {}
    for seed, values in values_by_seed.items():
        values = [float(value) for value in values]
        if any(not math.isfinite(value) for value in values):
            raise ValueError("seed values must be finite")
        means[str(seed)] = sum(values) / len(values) if values else None
    valid = [value for value in means.values() if value is not None]
    mean = sum(valid) / len(valid) if valid else None
    variance = (sum((value - mean) ** 2 for value in valid) / len(valid)
                if valid else None)
    return {"seed_means": means, "mean": mean, "variance": variance,
            "seed_count": len(valid)}


def high_budget_reference_profile(*, simulations=16000, seed=0,
                                  belief_profile_fingerprint="",
                                  actor_policy_version="heuristic-likelihood-v1",
                                  leaf_version="terminal-rollout-v1"):
    """Return a frozen, noise-free reference profile for an offline run."""
    simulations = int(simulations)
    if simulations not in (8000, 16000):
        raise ValueError("reference simulations must be 8000 or 16000")
    return SearchProfile(
        version=f"search-v1-reference-{simulations}",
        belief_profile_fingerprint=belief_profile_fingerprint,
        actor_policy_version=actor_policy_version, leaf_version=leaf_version,
        simulation_budget=simulations, seed=int(seed), root_noise=False,
        prior_source="actor-policy-v1", tie_break="legal-order-v1")


def search_quality_report(reference_results, candidate_results, *, seed_values=None):
    """Combine the declared high-budget regret and agreement diagnostics."""
    regret = regret_report(reference_results, candidate_results)
    result = {
        "schema": "search-quality-report-v1",
        "regret": regret,
        "action_agreement": action_agreement(reference_results,
                                               candidate_results),
        "seed_variance": seed_variance(seed_values or {}),
    }
    result["fingerprint"] = fingerprint(result, 24)
    return result


def paired_score_summary(scores, *, alpha=0.05):
    """Report paired score mean and a conservative normal CI.

    This helper is for compact offline reports; release evidence should use
    the source-game clustered bootstrap defined in the evaluation plan.
    """
    values = [float(value) for value in scores]
    if any(not math.isfinite(value) for value in values):
        raise ValueError("paired scores must be finite")
    if not values:
        return {"count": 0, "mean": None, "ci": None, "alpha": alpha}
    mean = sum(values) / len(values)
    if len(values) < 2:
        interval = (mean, mean)
    else:
        variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
        half = 1.96 * math.sqrt(variance / len(values))
        interval = (mean - half, mean + half)
    return {"count": len(values), "mean": mean, "ci": interval,
            "alpha": float(alpha), "metric": "hero_round_score_points"}
