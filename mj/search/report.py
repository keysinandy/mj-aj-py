"""Search result and regret evidence helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any, Mapping

from ..decision.profile import fingerprint


@dataclass(frozen=True)
class SearchResult:
    status: str
    context_hash: str
    history_hash: str
    belief_fingerprint: str
    search_profile_fingerprint: str
    root_key: str
    legal_actions: tuple[int, ...]
    best_action: int | None
    visit_policy: Mapping[int, float]
    q_by_action: Mapping[int, float]
    visit_counts: Mapping[int, int]
    variance_by_action: Mapping[int, float]
    simulations: int
    requested_simulations: int
    terminal_simulations: int
    leaf_simulations: int
    failed_simulations: int
    max_depth: int
    incomplete_budget: bool
    ambiguous: bool
    failures: tuple = ()
    node_count: int = 0
    root_value: float | None = None
    leaf_fallback_reason: str | None = None
    leaf_version: str = "terminal-rollout-v1"
    fingerprint: str = ""
    report_schema: str = "search-report-v1"

    def __post_init__(self):
        if self.status not in ("ok", "incomplete_budget", "failed", "unsupported"):
            raise ValueError(f"unknown search result status: {self.status}")
        if self.best_action is not None and self.best_action not in self.legal_actions:
            raise ValueError("best_action is not in legal_actions")
        object.__setattr__(self, "legal_actions", tuple(int(x) for x in self.legal_actions))
        if not self.fingerprint:
            object.__setattr__(self, "fingerprint", fingerprint({
                "schema": self.report_schema,
                "context_hash": self.context_hash,
                "history_hash": self.history_hash,
                "belief_fingerprint": self.belief_fingerprint,
                "search_profile_fingerprint": self.search_profile_fingerprint,
                "legal_actions": self.legal_actions,
                "best_action": self.best_action,
                "visit_policy": dict(self.visit_policy),
                "q_by_action": dict(self.q_by_action),
                "visit_counts": dict(self.visit_counts),
                "simulations": self.simulations,
                "failed_simulations": self.failed_simulations,
                "incomplete_budget": self.incomplete_budget,
                "ambiguous": self.ambiguous,
                "leaf_fallback_reason": self.leaf_fallback_reason,
                "leaf_version": self.leaf_version,
            }, 32))

    @property
    def confidence(self):
        if not self.q_by_action:
            return 0.0
        values = sorted(self.q_by_action.values(), reverse=True)
        return 1.0 if len(values) < 2 else max(0.0, values[0] - values[1])

    def as_json(self):
        return {
            "schema": self.report_schema, "status": self.status,
            "context_hash": self.context_hash, "history_hash": self.history_hash,
            "belief_fingerprint": self.belief_fingerprint,
            "search_profile_fingerprint": self.search_profile_fingerprint,
            "root_key": self.root_key,
            "legal_actions": list(self.legal_actions), "best_action": self.best_action,
            "visit_policy": {str(k): v for k, v in self.visit_policy.items()},
            "q_by_action": {str(k): v for k, v in self.q_by_action.items()},
            "visit_counts": {str(k): v for k, v in self.visit_counts.items()},
            "variance_by_action": {str(k): v for k, v in self.variance_by_action.items()},
            "simulations": self.simulations,
            "requested_simulations": self.requested_simulations,
            "terminal_simulations": self.terminal_simulations,
            "leaf_simulations": self.leaf_simulations,
            "failed_simulations": self.failed_simulations,
            "max_depth": self.max_depth,
            "incomplete_budget": self.incomplete_budget,
            "ambiguous": self.ambiguous, "confidence": self.confidence,
            "failures": list(self.failures), "node_count": self.node_count,
            "root_value": self.root_value, "fingerprint": self.fingerprint,
            "leaf_fallback_reason": self.leaf_fallback_reason,
            "leaf_version": self.leaf_version,
            "oracle": False, "real_wall_optimal": False,
            "model_error_free": False,
        }


def regret(reference: SearchResult | Mapping[str, Any], action: int) -> float | None:
    """Reference-model value difference for a candidate action."""
    q = reference.q_by_action if isinstance(reference, SearchResult) else reference.get("q_by_action", {})
    q = {int(key): value for key, value in q.items()}
    if not q or int(action) not in q:
        return None
    best = max(q.values())
    value = q[int(action)]
    if value is None or not math.isfinite(float(value)):
        return None
    return float(best) - float(value)


def regret_report(reference_results, candidate_results):
    rows = []
    for reference, candidate in zip(reference_results, candidate_results):
        reference_context = (reference.context_hash
                             if isinstance(reference, SearchResult)
                             else reference.get("context_hash"))
        reference_ambiguous = (reference.ambiguous
                               if isinstance(reference, SearchResult)
                               else reference.get("ambiguous", False))
        action = (candidate.best_action if isinstance(candidate, SearchResult)
                  else candidate.get("best_action"))
        value = regret(reference, action) if action is not None else None
        rows.append({"context_hash": reference_context, "action": action,
                     "regret": value, "ambiguous": reference_ambiguous})
    values = [row["regret"] for row in rows if row["regret"] is not None]
    ordered = sorted(values)
    return {
        "schema": "search-regret-report-v1", "rows": rows,
        "count": len(values),
        "mean": sum(values) / len(values) if values else None,
        "p50": ordered[(len(ordered) - 1) // 2] if ordered else None,
        "p95": ordered[min(len(ordered) - 1, int(len(ordered) * .95))]
        if ordered else None,
        "ambiguous_coverage": (sum(bool(row["ambiguous"]) for row in rows) /
                                len(rows) if rows else None),
        "fingerprint": fingerprint(rows, 24),
    }
