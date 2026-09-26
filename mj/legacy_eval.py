"""Public-information, one-draw lookahead for the legacy discard policy.

This module deliberately does not share the shape-v1/shape-v2 evaluator.  The
legacy V1 contract is much smaller: after a root discard, enumerate one public
draw and one legal discard, then stop.  All values are immutable and the
whole layer is transactional: an incomplete frontier never contributes a
partial score to the selected action.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, replace
import hashlib
import json
import math
import time
from typing import Callable, Iterable, Mapping, Sequence

from .shanten import (
    LEGACY_TWO_PLY_KERNEL_VERSION,
    WEIGHTED_TWO_PLY_KERNEL_REQUIRED,
    WEIGHTED_TWO_PLY_KERNEL_VERSION,
    legacy_two_ply_frontier,
    weighted_two_ply_frontier,
    shanten,
    ukeire,
)
from .big_hand_intent import (
    CHIITOI,
    INTENT_MEDIUM,
    INTENT_STRONG,
    LUXURY_CHIITOI,
    WHITE_RICH,
)
from .tiles import W
from .shape_quality import (
    SHAPE_QUALITY_VERSION,
    standing_shape_quality,
)


PROFILE_VERSION = "legacy-two-ply-v1"
FUTURE_MODEL = "uniform_unseen_one_draw_best_discard"
SORT_VERSION = "legacy-frontier-future-v1"
LEGACY_V2_PROFILE_VERSION = "legacyV2"
# ``WEIGHTED_PROFILE_VERSION`` remains as a source-compatible constant for
# callers that imported it during the rollout.  The serialized/profile name is
# now the shorter public ``legacyV2`` identifier.
WEIGHTED_PROFILE_VERSION = LEGACY_V2_PROFILE_VERSION
WEIGHTED_SORT_VERSION = "weighted-frontier-v1"
WEIGHTED_DEADLINE_RESERVE_MS = 2.0
# The weighted adapter passes the Stage B worker count, so it needs the kernel
# revision that accepts it. An older wheel falls back instead of raising a
# TypeError at the FFI boundary.
# 训练/离线标签生成使用:预算宽裕到不会因 deadline / work budget 回退,
# 使 legacyV2 的标签始终由搜索本身给出。
WEIGHTED_OFFLINE_PROFILE_VERSION = "legacyV2-offline"
LEGACY_V2_OFFLINE_EVALUATORS = (
    WEIGHTED_OFFLINE_PROFILE_VERSION,
    "legacy-v2-offline",
    "legacy_v2_offline",
)
LEGACY_V2_EVALUATORS = (
    LEGACY_V2_PROFILE_VERSION,
    "legacy-v2",
    "weighted-two-ply-frontier-v1",
    "weighted_two_ply",
    "weighted-two-ply",
)
LEGACY_V2_BASELINE_EVALUATORS = ("legacy-v2-baseline",)
LEGACY_V2_PHASE_A_EVALUATORS = ("legacy-v2-phase-a",)
LEGACY_V2_PHASE_B_EVALUATORS = ("legacy-v2-phase-b",)
LEGACY_V2_SHAPE_PHASE_A_EVALUATORS = ("legacy-v2-shape-phase-a",)
LEGACY_V2_SHAPE_PHASE_B_EVALUATORS = ("legacy-v2-shape-phase-b",)
LEGACY_V2_EXPERIMENT_EVALUATORS = (
    *LEGACY_V2_BASELINE_EVALUATORS,
    *LEGACY_V2_PHASE_A_EVALUATORS,
    *LEGACY_V2_PHASE_B_EVALUATORS,
    *LEGACY_V2_SHAPE_PHASE_A_EVALUATORS,
    *LEGACY_V2_SHAPE_PHASE_B_EVALUATORS,
)
SHAPE_QUALITY_GUARD_VERSION = "standing-shape-taatsu-gain-v1"
# Product default evaluator. ``legacy`` is kept as a compatibility alias for
# this v2 route; callers that need the frozen rollback oracle must explicitly
# request ``legacy-v1``.
DEFAULT_BOT_EVALUATOR = LEGACY_V2_PROFILE_VERSION


def canonical_evaluator(value):
    """Normalize default/legacy compatibility names to the online v2 route."""
    return (DEFAULT_BOT_EVALUATOR
            if value is None or value == "legacy"
            or value in LEGACY_V2_EVALUATORS else value)


def _canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


@dataclass(frozen=True)
class LegacyTwoPlyProfile:
    """Versioned knobs that affect the legacy weighted result or its cost."""

    name: str = PROFILE_VERSION
    version: str = PROFILE_VERSION
    model: str = FUTURE_MODEL
    rules_version: str = "hangzhou-platform-guide-v34"
    node_budget: int = 4096
    time_budget_ms: float = 8.0
    cache_capacity: int = 8192
    sort_version: str = SORT_VERSION
    kernel: str = "auto"
    mode: str = "exact"
    soft_budget_ms: float | None = None
    hard_budget_ms: float | None = None
    max_frontier_candidates: int = 0
    allow_partial: bool = False
    min_partial_coverage: float = 1.0
    lazy_child_ukeire: bool = True
    workers: int = 0
    shape_guard_enabled: bool = False
    shape_guard_ukeire_slack: int = 1
    shape_guard_shape_delta: int = 8
    shape_quality_enabled: bool = False
    shape_quality_version: str = SHAPE_QUALITY_VERSION
    shape_quality_stage: str = "diagnostic"
    shape_quality_guard_enabled: bool = False
    shape_quality_guard_version: str = SHAPE_QUALITY_GUARD_VERSION
    # Standing reaction callers may request a no-Stage-A-shortcut contract;
    # discard evaluators keep the historical default False.
    require_complete: bool = False
    enabled: bool = True
    big_hand_version: str = "legacy-big-hand-intent-v1"
    big_hand_enabled: bool = False
    big_hand_same_shanten_enabled: bool = True
    big_hand_plus_one_enabled: bool = False
    big_hand_min_live: int = 24
    big_hand_max_opponent_melds: int = 1
    big_hand_min_ukeire: int = 4
    big_hand_max_ukeire_loss: int = 4
    big_hand_min_pair_units: int = 4
    big_hand_min_luxury_upgrade_live: int = 1

    def __post_init__(self):
        if not self.name or not self.version or not self.model:
            raise ValueError("legacy-two-ply profile identifiers are required")
        if self.kernel not in {"auto", "python", "rust"}:
            raise ValueError("kernel must be auto, python, or rust")
        if self.mode not in {"exact", "weighted"}:
            raise ValueError("mode must be exact or weighted")
        if int(self.node_budget) < 0:
            raise ValueError("node_budget must be non-negative")
        if int(self.cache_capacity) < 0:
            raise ValueError("cache_capacity must be non-negative")
        value = float(self.time_budget_ms)
        if not math.isfinite(value) or value < 0:
            raise ValueError("time_budget_ms must be finite and non-negative")
        soft = value if self.soft_budget_ms is None else float(self.soft_budget_ms)
        hard = value if self.hard_budget_ms is None else float(self.hard_budget_ms)
        if (not math.isfinite(soft) or not math.isfinite(hard) or
                soft < 0 or hard < 0 or soft > hard):
            raise ValueError("soft/hard budgets must be finite and ordered")
        if int(self.max_frontier_candidates) < 0:
            raise ValueError("max_frontier_candidates must be non-negative")
        if int(self.workers) < 0:
            raise ValueError("workers must be non-negative")
        if int(self.shape_guard_ukeire_slack) < 0:
            raise ValueError("shape_guard_ukeire_slack must be non-negative")
        if int(self.shape_guard_shape_delta) < 0:
            raise ValueError("shape_guard_shape_delta must be non-negative")
        if self.shape_quality_stage not in {"diagnostic", "root", "full"}:
            raise ValueError(
                "shape_quality_stage must be diagnostic, root, or full")
        if not self.shape_quality_version:
            raise ValueError("shape_quality_version is required")
        if not self.shape_quality_guard_version:
            raise ValueError("shape_quality_guard_version is required")
        if not self.big_hand_version:
            raise ValueError("big_hand_version is required")
        for name in ("big_hand_min_live", "big_hand_max_opponent_melds",
                     "big_hand_min_ukeire", "big_hand_max_ukeire_loss",
                     "big_hand_min_pair_units",
                     "big_hand_min_luxury_upgrade_live"):
            if int(getattr(self, name)) < 0:
                raise ValueError(f"{name} must be non-negative")
        coverage = float(self.min_partial_coverage)
        if not math.isfinite(coverage) or not 0 <= coverage <= 1:
            raise ValueError("min_partial_coverage must be between 0 and 1")
        object.__setattr__(self, "node_budget", int(self.node_budget))
        object.__setattr__(self, "cache_capacity", int(self.cache_capacity))
        object.__setattr__(self, "time_budget_ms", value)
        object.__setattr__(self, "soft_budget_ms", soft)
        object.__setattr__(self, "hard_budget_ms", hard)
        object.__setattr__(self, "max_frontier_candidates",
                           int(self.max_frontier_candidates))
        object.__setattr__(self, "workers", int(self.workers))
        object.__setattr__(self, "shape_guard_enabled",
                           bool(self.shape_guard_enabled))
        object.__setattr__(self, "shape_guard_ukeire_slack",
                           int(self.shape_guard_ukeire_slack))
        object.__setattr__(self, "shape_guard_shape_delta",
                           int(self.shape_guard_shape_delta))
        object.__setattr__(self, "shape_quality_enabled",
                           bool(self.shape_quality_enabled))
        object.__setattr__(self, "shape_quality_guard_enabled",
                           bool(self.shape_quality_guard_enabled))
        object.__setattr__(self, "allow_partial", bool(self.allow_partial))
        object.__setattr__(self, "min_partial_coverage", coverage)
        object.__setattr__(self, "lazy_child_ukeire",
                           bool(self.lazy_child_ukeire))
        object.__setattr__(self, "require_complete",
                           bool(self.require_complete))
        object.__setattr__(self, "enabled", bool(self.enabled))
        object.__setattr__(self, "big_hand_enabled",
                           bool(self.big_hand_enabled))
        object.__setattr__(self, "big_hand_same_shanten_enabled",
                           bool(self.big_hand_same_shanten_enabled))
        object.__setattr__(self, "big_hand_plus_one_enabled",
                           bool(self.big_hand_plus_one_enabled))
        for name in ("big_hand_min_live", "big_hand_max_opponent_melds",
                     "big_hand_min_ukeire", "big_hand_max_ukeire_loss",
                     "big_hand_min_pair_units",
                     "big_hand_min_luxury_upgrade_live"):
            object.__setattr__(self, name, int(getattr(self, name)))

    @classmethod
    def default(cls, **overrides):
        return cls(**overrides)

    @classmethod
    def weighted_online(cls, **overrides):
        values = {
            "name": WEIGHTED_PROFILE_VERSION,
            "version": WEIGHTED_PROFILE_VERSION,
            "model": "weighted_unseen_one_draw_lazy_best_discard",
            "node_budget": 100000,
            "time_budget_ms": 50.0,
            "soft_budget_ms": 40.0,
            "hard_budget_ms": 50.0,
            "cache_capacity": 8192,
            "sort_version": WEIGHTED_SORT_VERSION,
            "kernel": "auto",
            "mode": "weighted",
            "max_frontier_candidates": 3,
            "allow_partial": True,
            "min_partial_coverage": 0.90,
            "lazy_child_ukeire": True,
            "workers": 0,
            "shape_guard_enabled": True,
            "shape_quality_enabled": True,
            "shape_quality_stage": "full",
            "shape_quality_guard_enabled": True,
            "big_hand_enabled": False,
            "big_hand_same_shanten_enabled": True,
            "big_hand_plus_one_enabled": False,
        }
        values.update(overrides)
        return cls(**values)

    @classmethod
    def weighted_offline(cls, **overrides):
        """训练/离线标签生成用的 legacyV2 profile。

        保留既有 shape-off 标签口径；前沿上限、部分接受规则与在线相同。
        时间与节点预算放大到不会触发 deadline / work budget / 不安全
        partial 回退,因此搜索标签不会悄悄退化成 legacy 启发式。需要生成
        与线上 shape-aware 默认一致的标签时，调用方需显式开启对应 shape 参数。
        """
        values = {
            "name": WEIGHTED_OFFLINE_PROFILE_VERSION,
            "version": WEIGHTED_OFFLINE_PROFILE_VERSION,
            "model": "weighted_unseen_one_draw_lazy_best_discard",
            "node_budget": 5_000_000,
            "time_budget_ms": 2000.0,
            "soft_budget_ms": 2000.0,
            "hard_budget_ms": 2000.0,
            "cache_capacity": 65536,
            "sort_version": WEIGHTED_SORT_VERSION,
            "kernel": "rust",
            "mode": "weighted",
            "max_frontier_candidates": 3,
            "allow_partial": True,
            "min_partial_coverage": 0.90,
            "lazy_child_ukeire": True,
            "workers": 0,
            "shape_guard_enabled": True,
            "big_hand_enabled": False,
            "big_hand_same_shanten_enabled": False,
            "big_hand_plus_one_enabled": False,
        }
        values.update(overrides)
        return cls(**values)

    def _payload(self):
        payload = {
            "name": self.name,
            "version": self.version,
            "model": self.model,
            "rules_version": self.rules_version,
            "node_budget": self.node_budget,
            "time_budget_ms": self.time_budget_ms,
            "cache_capacity": self.cache_capacity,
            "sort_version": self.sort_version,
            "kernel": self.kernel,
            "enabled": self.enabled,
            "big_hand": self.big_hand_config(),
        }
        legacy_compatible = (
            self.mode == "exact" and
            self.max_frontier_candidates == 0 and
            not self.allow_partial and
            self.min_partial_coverage == 1.0 and
            self.lazy_child_ukeire and
            not self.shape_guard_enabled and
            self.shape_guard_ukeire_slack == 1 and
            self.shape_guard_shape_delta == 8 and
            not self.require_complete and
            self.soft_budget_ms == self.time_budget_ms and
            self.hard_budget_ms == self.time_budget_ms
        )
        if not legacy_compatible:
            payload.update({
                "mode": self.mode,
                "soft_budget_ms": self.soft_budget_ms,
                "hard_budget_ms": self.hard_budget_ms,
                "max_frontier_candidates": self.max_frontier_candidates,
                "allow_partial": self.allow_partial,
                "min_partial_coverage": self.min_partial_coverage,
                "lazy_child_ukeire": self.lazy_child_ukeire,
                "shape_guard_enabled": self.shape_guard_enabled,
                "shape_guard_ukeire_slack": self.shape_guard_ukeire_slack,
                "shape_guard_shape_delta": self.shape_guard_shape_delta,
                "require_complete": self.require_complete,
            })
        if self.shape_quality_enabled or self.shape_quality_guard_enabled:
            payload.update({
                "shape_quality_enabled": self.shape_quality_enabled,
                "shape_quality_version": self.shape_quality_version,
                "shape_quality_stage": self.shape_quality_stage,
                "shape_quality_guard_enabled": (
                    self.shape_quality_guard_enabled),
                "shape_quality_guard_version": (
                    self.shape_quality_guard_version),
            })
        return payload

    def big_hand_config(self):
        """Return the complete, fingerprinted BigHandIntent profile payload."""
        return {
            "version": self.big_hand_version,
            "enabled": self.big_hand_enabled,
            "same_shanten_enabled": self.big_hand_same_shanten_enabled,
            "plus_one_enabled": self.big_hand_plus_one_enabled,
            "min_live": self.big_hand_min_live,
            "max_opponent_melds": self.big_hand_max_opponent_melds,
            "min_ukeire": self.big_hand_min_ukeire,
            "max_ukeire_loss": self.big_hand_max_ukeire_loss,
            "min_pair_units": self.big_hand_min_pair_units,
            "min_luxury_upgrade_live": self.big_hand_min_luxury_upgrade_live,
        }

    def as_json(self):
        result = self._payload()
        result.update({
            "mode": self.mode,
            "soft_budget_ms": self.soft_budget_ms,
            "hard_budget_ms": self.hard_budget_ms,
            "max_frontier_candidates": self.max_frontier_candidates,
            "allow_partial": self.allow_partial,
            "min_partial_coverage": self.min_partial_coverage,
            "lazy_child_ukeire": self.lazy_child_ukeire,
            "workers": self.workers,
            "shape_guard_enabled": self.shape_guard_enabled,
            "shape_guard_ukeire_slack": self.shape_guard_ukeire_slack,
            "shape_guard_shape_delta": self.shape_guard_shape_delta,
            "shape_quality_enabled": self.shape_quality_enabled,
            "shape_quality_version": self.shape_quality_version,
            "shape_quality_stage": self.shape_quality_stage,
            "shape_quality_guard_enabled": self.shape_quality_guard_enabled,
            "shape_quality_guard_version": self.shape_quality_guard_version,
            "require_complete": self.require_complete,
        })
        result["fingerprint"] = self.fingerprint
        return result

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(
            _canonical_json(self._payload()).encode("utf-8")
        ).hexdigest()[:16]


@dataclass(frozen=True)
class LegacyRootCandidate:
    """Immutable root candidate, including the post-discard concealed hand."""

    tile: int
    hand: tuple[int, ...]
    shanten: int
    shape_loss: float = 0.0
    feed_risk: float = 0.0
    standing_shape_quality: int | None = None
    standing_shape_signature: tuple[int, ...] = ()
    shape_quality_version: str | None = None
    current_ukeire: int | None = None
    current_ukeire_tiles: tuple[int, ...] = ()
    eligible: bool = True
    missing: tuple[str, ...] = ()
    shanten_verified: bool = False
    speed_eligible: bool = True
    intent_kinds: tuple[str, ...] = ()
    intent_strength: str = "NONE"
    chiitoi_shanten: int | None = None
    pair_units: int = 0
    luxury_groups: int = 0
    luxury_upgrade_tiles: tuple[int, ...] = ()
    luxury_upgrade_live: int = 0
    wild_count: int = 0
    wild_live: int = 0
    shanten_regression: int = 0
    admission_hint: str | None = None
    live_wall: int | None = None
    max_opponent_melds: int | None = None
    big_hand_gate_reason: str | None = None

    def as_json(self):
        result = {
            "tile": self.tile,
            "shanten": self.shanten,
            "ukeire": self.current_ukeire,
            "ukeire_tiles": list(self.current_ukeire_tiles),
            "ukeire_types": (len(self.current_ukeire_tiles)
                             if self.current_ukeire is not None else None),
            "shape_loss": self.shape_loss,
            "discard_shape_cost": self.shape_loss,
            "feed_risk": self.feed_risk,
            "eligible": self.eligible,
            "missing": list(self.missing),
        }
        if self.standing_shape_quality is not None:
            result.update({
                "standing_shape_quality": self.standing_shape_quality,
                "standing_shape_signature": list(
                    self.standing_shape_signature),
                "shape_quality_version": self.shape_quality_version,
            })
        has_intent = bool(self.intent_kinds or
                          self.intent_strength != "NONE")
        if (has_intent or self.admission_hint or self.big_hand_gate_reason):
            result.update({
                "intent_kinds": list(self.intent_kinds),
                "intent_strength": self.intent_strength,
                "chiitoi_shanten": self.chiitoi_shanten,
                "pair_units": self.pair_units,
                "luxury_groups": self.luxury_groups,
                "luxury_upgrade_tiles": list(self.luxury_upgrade_tiles),
                "luxury_upgrade_live": self.luxury_upgrade_live,
                "wild_count": self.wild_count,
                "wild_live": self.wild_live,
                "admission_hint": self.admission_hint,
                "live_wall": self.live_wall,
                "max_opponent_melds": self.max_opponent_melds,
                "big_hand_gate_reason": self.big_hand_gate_reason,
            })
            result["shanten_regression"] = self.shanten_regression
            result["speed_eligible"] = self.speed_eligible
        return result


@dataclass(frozen=True)
class FutureEvaluation:
    """Result of exactly one future draw and one best discard."""

    complete: bool
    root_shanten: int | None = None
    future_improve_weight: int | None = None
    future_improve_lower: int | None = None
    future_improve_upper: int | None = None
    future_ukeire: int | None = None
    future_ukeire_mean: float | None = None
    future_ukeire_mean_denominator: int | None = None
    future_ukeire_types: int | None = None
    future_ukeire_types_mean: float | None = None
    future_shape_quality_sum: int | None = None
    future_shape_quality_mean: float | None = None
    future_shape_denominator: int | None = None
    child_shape_quality_by_draw: tuple[tuple[int, int], ...] = ()
    best_discard_by_draw: tuple[tuple[int, int], ...] = ()
    future_ukeire_skipped: bool = False
    best_discards: tuple[tuple[int, int], ...] = ()
    nodes: int = 0
    cache_hits: int = 0
    elapsed_ms: float | None = None
    covered_weight: int | None = None
    total_weight: int | None = None
    coverage: float | None = None
    partial_accepted: bool = False
    search_metrics: Mapping | None = None
    missing: tuple[str, ...] = ()
    fallback_reason: str | None = None

    def as_json(self):
        result = {
            "complete": self.complete,
            "root_shanten": self.root_shanten,
            "future_improve_weight": self.future_improve_weight,
            "future_improve_lower": self.future_improve_lower,
            "future_improve_upper": self.future_improve_upper,
            "future_ukeire": self.future_ukeire,
            "future_ukeire_mean": self.future_ukeire_mean,
            "future_ukeire_mean_denominator": self.future_ukeire_mean_denominator,
            "future_ukeire_types": self.future_ukeire_types,
            "future_ukeire_types_mean": self.future_ukeire_types_mean,
            "future_shape_quality_sum": self.future_shape_quality_sum,
            "future_shape_quality_mean": self.future_shape_quality_mean,
            "future_shape_denominator": self.future_shape_denominator,
            "child_shape_quality_by_draw": {
                str(tile): quality
                for tile, quality in self.child_shape_quality_by_draw
            } if self.child_shape_quality_by_draw else None,
            "child_best_discards_by_draw": {
                str(tile): discard
                for tile, discard in self.best_discard_by_draw
            } if self.best_discard_by_draw else None,
            "future_ukeire_skipped": self.future_ukeire_skipped,
            "future_best_discards": {
                str(tile): weight for tile, weight in self.best_discards
            },
            "future_nodes": self.nodes,
            "future_cache_hits": self.cache_hits,
            "future_elapsed_ms": self.elapsed_ms,
            "covered_weight": self.covered_weight,
            "total_weight": self.total_weight,
            "coverage": self.coverage,
            "partial_accepted": self.partial_accepted,
            "search_metrics": (dict(self.search_metrics)
                                if self.search_metrics is not None else None),
            "missing": list(self.missing),
            "future_fallback_reason": self.fallback_reason,
        }
        return result


@dataclass(frozen=True)
class StandingRoot:
    """One stable public standing hand for shared future evaluation."""

    stable_id: str | int
    hand: tuple[int, ...]
    shanten: int

    def __post_init__(self):
        try:
            hash(self.stable_id)
        except TypeError as exc:
            raise ValueError("standing root id must be hashable") from exc
        object.__setattr__(self, "hand", tuple(int(value) for value in self.hand))
        object.__setattr__(self, "shanten", int(self.shanten))


@dataclass(frozen=True)
class LegacyDiscardEvaluation:
    """Complete explanation for one legacy V1 root decision."""

    version: str
    profile: str
    profile_fingerprint: str
    level: str
    complete: bool
    selected: int | None
    legacy_best: int | None
    candidates: tuple[Mapping, ...]
    future_model: str = FUTURE_MODEL
    mode: str = "exact"
    future_nodes: int = 0
    future_cache_hits: int = 0
    budget: Mapping[str, float | int | str] | None = None
    assumptions: tuple[str, ...] = (
        "public_visible_uniform_unseen",
        "one_future_draw_one_best_legal_discard",
        "no_wall_order_or_opponent_hidden_state",
    )
    fallback_reason: str | None = None
    missing: tuple[str, ...] = ()
    partial_accepted: bool = False
    covered_weight: int | None = None
    total_weight: int | None = None
    coverage: float | None = None
    search_metrics: Mapping | None = None
    requested_kernel: str = "python"
    actual_kernel: str = "python"
    kernel_version: str | None = None
    kernel_fallback_reason: str | None = None
    short_circuit_reason: str | None = None
    search_used: bool = False
    search_phase: str | None = None
    search_attempt_phase: str | None = None
    frontier_guard: Mapping | None = None
    speed_pool_tiles: tuple[int, ...] = ()
    speed_winner: int | None = None
    big_hand_challenger: int | None = None
    big_hand_override: bool = False
    big_hand_override_reason: str | None = None
    big_hand_phase: str = "disabled"
    frontier_cap_dropped: tuple[int, ...] = ()
    big_hand_guard: Mapping | None = None
    shape_quality_version: str | None = None
    shape_quality_used: bool = False
    shape_quality_stage: str | None = None
    shape_changed_winner: bool = False
    shape_baseline_selected: int | None = None
    decision_scope: str = "weighted_two_ply"
    stage_b_entered: bool = False

    def as_json(self):
        result = {
            "version": self.version,
            "profile": self.profile,
            "profile_fingerprint": self.profile_fingerprint,
            "level": self.level,
            "complete": self.complete,
            "selected": self.selected,
            "legacy_best": self.legacy_best,
            "future_model": self.future_model,
            "mode": self.mode,
            "future_nodes": self.future_nodes,
            "future_cache_hits": self.future_cache_hits,
            "partial_accepted": self.partial_accepted,
            "covered_weight": self.covered_weight,
            "total_weight": self.total_weight,
            "coverage": self.coverage,
            "search_metrics": (dict(self.search_metrics)
                                if self.search_metrics is not None else None),
            "budget": dict(self.budget or {}),
            "assumptions": list(self.assumptions),
            "candidates": [dict(item) for item in self.candidates],
            "fallback_reason": self.fallback_reason,
            "future_fallback_reason": self.fallback_reason,
            "missing": list(self.missing),
            "requested_kernel": self.requested_kernel,
            "actual_kernel": self.actual_kernel,
            "kernel_version": self.kernel_version,
            "kernel_fallback_reason": self.kernel_fallback_reason,
            "short_circuit_reason": self.short_circuit_reason,
            "search_used": self.search_used,
            "search_phase": self.search_phase,
            "search_attempt_phase": self.search_attempt_phase,
            "frontier_guard": (dict(self.frontier_guard)
                               if self.frontier_guard is not None else None),
            "speed_pool_tiles": list(self.speed_pool_tiles),
            "speed_winner": self.speed_winner,
            "big_hand_challenger": self.big_hand_challenger,
            "big_hand_override": self.big_hand_override,
            "big_hand_override_reason": self.big_hand_override_reason,
            "big_hand_phase": self.big_hand_phase,
            "frontier_cap_dropped": list(self.frontier_cap_dropped),
            "big_hand_guard": (dict(self.big_hand_guard)
                               if self.big_hand_guard is not None else None),
            "shape_quality_version": self.shape_quality_version,
            "shape_quality_used": self.shape_quality_used,
            "shape_quality_stage": self.shape_quality_stage,
            "shape_changed_winner": self.shape_changed_winner,
            "shape_baseline_selected": self.shape_baseline_selected,
            "decision_scope": self.decision_scope,
            "stage_b_entered": self.stage_b_entered,
        }
        return result


class _BudgetExceeded(RuntimeError):
    pass


class _InvalidPublicState(RuntimeError):
    pass


class _NativeKernelUnavailable(RuntimeError):
    pass


class _NativeKernelIncomplete(RuntimeError):
    pass


class _NativeKernelInvalid(RuntimeError):
    pass


class _FutureMemo:
    """Decision-local bounded memo; never shared across visible states."""

    def __init__(self, capacity: int):
        self.capacity = max(0, int(capacity))
        self.values = OrderedDict()
        self.hits = 0

    def get(self, key):
        if self.capacity == 0:
            return None
        value = self.values.get(key)
        if value is not None:
            self.values.move_to_end(key)
            self.hits += 1
        return value

    def put(self, key, value):
        if self.capacity == 0:
            return
        self.values[key] = value
        self.values.move_to_end(key)
        while len(self.values) > self.capacity:
            self.values.popitem(last=False)


class _Budget:
    def __init__(self, profile: LegacyTwoPlyProfile):
        self.profile = profile
        self.started = time.monotonic()
        self.nodes = 0

    @property
    def elapsed_ms(self):
        return (time.monotonic() - self.started) * 1000.0

    def check(self):
        if self.nodes >= self.profile.node_budget:
            raise _BudgetExceeded("node_budget_exceeded")
        if self.elapsed_ms >= self.profile.time_budget_ms:
            raise _BudgetExceeded("time_budget_exceeded")

    def node(self):
        self.check()
        self.nodes += 1


def _as_tuple(values, *, name, upper=4):
    try:
        result = tuple(int(value) for value in values)
    except (TypeError, ValueError) as exc:
        raise _InvalidPublicState(f"{name}_unknown") from exc
    if len(result) != 34 or any(value < 0 or value > upper for value in result):
        raise _InvalidPublicState(f"{name}_invalid")
    return result


def _validate_hand(hand, locked):
    result = _as_tuple(hand, name="hand")
    expected = 13 - 3 * int(locked)
    if sum(result) != expected:
        raise _InvalidPublicState("hand_size_invalid")
    return result


def _rule_context(game, profile, seat):
    phase = str(getattr(game, "phase", "discard"))
    frozen = bool(getattr(game, "freeze", 0) > 0 and
                  seat != getattr(game, "freezer", None))
    flags = ("frozen" if frozen else "free", "discard-only")
    rule = str(getattr(game, "rules_version", profile.rules_version))
    return rule, phase, flags, frozen


def _child_discards(game, seat, hand, drawn, frozen):
    if frozen:
        return (drawn,) if 0 <= drawn < 34 and hand[drawn] else ()
    return tuple(tile for tile, count in enumerate(hand) if count > 0)


def _normalise_roots(root_candidates: Iterable[LegacyRootCandidate | Sequence],
                     locked=0):
    roots = []
    for raw in root_candidates:
        if isinstance(raw, LegacyRootCandidate):
            roots.append(raw)
            continue
        try:
            if len(raw) == 4:
                tile, hand, shape, feed = raw
                root_s = shanten(hand, locked)
            else:
                tile, hand, root_s, shape, feed = raw
        except (TypeError, ValueError) as exc:
            raise _InvalidPublicState("root_candidate_invalid") from exc
        roots.append(LegacyRootCandidate(
            tile=int(tile), hand=tuple(int(v) for v in hand),
            shanten=int(root_s), shape_loss=float(shape),
            feed_risk=float(feed)))
    return tuple(roots)


def _root_features(roots, locked, visible, *, shape_quality_enabled=False):
    visible = _as_tuple(visible, name="visible")
    enriched = []
    for root in roots:
        hand = _validate_hand(root.hand, locked)
        if not 0 <= root.tile < 34:
            raise _InvalidPublicState("root_tile_invalid")
        if visible[root.tile] < hand[root.tile]:
            raise _InvalidPublicState("visible_missing_hand")
        s = (root.shanten if root.shanten_verified
             else shanten(hand, locked))
        shape = (standing_shape_quality(hand, locked=locked)
                 if shape_quality_enabled else None)
        if s != root.shanten:
            # The immutable root is a public contract, not a trust boundary;
            # recompute rather than letting a stale caller change ordering.
            root = replace(root, hand=hand, shanten=s)
        if shape is not None:
            root = replace(
                root,
                standing_shape_quality=shape.encoded,
                standing_shape_signature=shape.signature,
                shape_quality_version=shape.version,
            )
        else:
            root = replace(
                root, standing_shape_quality=None,
                standing_shape_signature=None, shape_quality_version=None)
        state = ukeire(hand, locked, visible)
        enriched.append(replace(
            root, hand=hand, shanten=s,
            current_ukeire=int(state[2]),
            current_ukeire_tiles=tuple(state[1])))
    if not enriched:
        raise _InvalidPublicState("no_root_candidates")
    speed_pool = [root for root in enriched if root.speed_eligible]
    if not speed_pool:
        speed_pool = list(enriched)
    min_s = min(root.shanten for root in speed_pool)
    speed_pool = [root for root in speed_pool if root.shanten == min_s]
    frontier = list(speed_pool)
    if any(root.tile != W for root in frontier):
        frontier = [root for root in frontier if root.tile != W]
    max_u = max(int(root.current_ukeire or 0) for root in frontier)
    frontier = [root for root in frontier if root.current_ukeire == max_u]
    frontier_tiles = {root.tile for root in frontier}
    diagnostics = []
    for root in enriched:
        if root.shanten != min_s or not root.speed_eligible:
            missing = ("shanten_regression",)
            diagnostics.append((root, False, missing))
        elif root.tile not in frontier_tiles:
            missing = ("current_ukeire_frontier",)
            diagnostics.append((root, False, missing))
        else:
            diagnostics.append((root, True, ()))
    return tuple(enriched), tuple(frontier), tuple(diagnostics)


def _legacy_speed_roots(roots):
    """Return only the frozen min-shanten speed pool for rollback semantics."""
    roots = tuple(roots)
    if not roots:
        return ()
    speed = tuple(root for root in roots if root.speed_eligible)
    if not speed:
        speed = roots
    best_s = min(root.shanten for root in speed)
    return tuple(root for root in speed if root.shanten == best_s)


def _legacy_speed_best(roots):
    speed = _legacy_speed_roots(roots)
    return min(speed, key=_legacy_key).tile if speed else None


def _weighted_native_ready(profile):
    """护栏是否具备"真实比较"的前提:原生 weighted 内核可用。

    内核不可用时扩围只会改变 legacy 键的候选集,违背回退语义,因此护栏
    必须跳过并记录 ``kernel_unavailable``。
    """
    if profile.kernel == "python":
        return False
    if (weighted_two_ply_frontier is None
            or WEIGHTED_TWO_PLY_KERNEL_VERSION is None):
        return False
    if WEIGHTED_TWO_PLY_KERNEL_VERSION != WEIGHTED_TWO_PLY_KERNEL_REQUIRED:
        return False
    return True


def _apply_shape_guard(frontier, diagnostics, profile):
    """形状护栏前沿(D1/D2/D3)。

    仅在 primary frontier 只有一个候选时扩围:纳入「直接进张差距 <=
    slack」且「结构损失比 primary 至少好 delta」的候选,合并后按声明顺序
    (-进张, 结构, 喂牌, 牌编号)受 max_frontier_candidates 截断。返回
    ``(frontier, diagnostics, frontier_guard, admitted_by)``;护栏关闭或
    未扩围时返回原值,保证零行为漂移。
    """
    guard = {
        "enabled": bool(profile.shape_guard_enabled),
        "shape_quality_enabled": bool(profile.shape_quality_guard_enabled),
        "shape_quality_guard_version": profile.shape_quality_guard_version,
        "policy": ({
            "slack_ukeire": int(profile.shape_guard_ukeire_slack),
            "taatsu_class_gain": 1,
            "max_frontier_candidates": int(profile.max_frontier_candidates),
        } if profile.shape_quality_enabled and
             profile.shape_quality_guard_enabled else {
                 "slack_ukeire": int(profile.shape_guard_ukeire_slack),
                 "legacy_shape_delta": int(profile.shape_guard_shape_delta),
                 "max_frontier_candidates": int(
                     profile.max_frontier_candidates),
             }),
        "primary_tiles": [root.tile for root in frontier],
        "admitted_tiles": [],
        "dropped_tiles": [],
        "skipped_reason": None,
    }
    admitted_by = {root.tile: "primary" for root in frontier}
    quality_guard = bool(profile.shape_quality_enabled and
                         profile.shape_quality_guard_enabled)
    if not profile.shape_guard_enabled:
        return frontier, diagnostics, guard, admitted_by
    if profile.shape_quality_enabled and not quality_guard:
        guard["skipped_reason"] = "shape_quality_guard_disabled"
        return frontier, diagnostics, guard, admitted_by
    if not _weighted_native_ready(profile):
        guard["skipped_reason"] = "kernel_unavailable"
        return frontier, diagnostics, guard, admitted_by
    if len(frontier) != 1:
        # primary 有多个候选时既有实现本来就会做加权比较(D2)。
        guard["skipped_reason"] = "primary_not_singleton"
        return frontier, diagnostics, guard, admitted_by
    slack = int(profile.shape_guard_ukeire_slack)
    if slack <= 0:
        guard["skipped_reason"] = "slack_zero"
        return frontier, diagnostics, guard, admitted_by
    primary = frontier[0]
    primary_ukeire = int(primary.current_ukeire or 0)
    primary_shape = float(primary.shape_loss)
    admitted = []
    for root, eligible, missing in diagnostics:
        if eligible or root.tile == primary.tile:
            continue
        if (not root.speed_eligible or
                root.shanten != primary.shanten):
            continue
        if "current_ukeire_frontier" not in missing:
            # 被前沿上限截断的候选不属于"进张接近"的护栏对象。
            continue
        if primary_ukeire - int(root.current_ukeire or 0) > slack:
            continue
        if quality_guard:
            if (primary.standing_shape_quality is None or
                    root.standing_shape_quality is None or
                    not _taatsu_class_improved(root, primary)):
                continue
        elif primary_shape - float(root.shape_loss) < int(
                profile.shape_guard_shape_delta):
            continue
        admitted.append(root)
    if not admitted:
        guard["skipped_reason"] = "no_candidate_admitted"
        return frontier, diagnostics, guard, admitted_by
    merged = [primary, *admitted]
    limit = int(profile.max_frontier_candidates or 0)
    if limit and len(merged) > limit:
        ranked = sorted(merged, key=lambda root: (
            -int(root.current_ukeire or 0),
            (-int(root.standing_shape_quality or 0)
             if quality_guard else float(root.shape_loss)),
            float(root.shape_loss) if quality_guard else 0.0,
            float(root.feed_risk), root.tile,
        ))
        kept = [root for root in ranked[:limit]]
        guard["dropped_tiles"] = [root.tile for root in ranked[limit:]]
        merged = kept
    merged_tiles = {root.tile for root in merged}
    updated = []
    for root, eligible, missing in diagnostics:
        if root.tile in merged_tiles:
            updated.append((root, True, ()))
            admitted_by[root.tile] = ("primary" if root.tile == primary.tile
                                      else "shape_guard")
        elif "current_ukeire_frontier" in missing:
            updated.append((root, False, missing))
        else:
            updated.append((root, eligible, missing))
    guard["admitted_tiles"] = [root.tile for root in merged
                               if root.tile != primary.tile]
    return tuple(merged), tuple(updated), guard, admitted_by


def _taatsu_class_improved(candidate, primary):
    """Require a strict improvement in the declared taatsu class vector."""
    left = tuple(candidate.standing_shape_signature[2:6])
    right = tuple(primary.standing_shape_signature[2:6])
    return left > right


def _big_hand_route_reason(root, profile, *, speed_winner=None, locked=0,
                           plus_one=False):
    """Return a stable admission rejection reason or ``None`` when eligible."""
    kinds = set(root.intent_kinds)
    if root.intent_strength != INTENT_STRONG:
        return "intent_not_strong"
    if locked != 0:
        return "locked_hand"
    if root.chiitoi_shanten is None or root.chiitoi_shanten > 1:
        return "chiitoi_distance"
    luxury_route = LUXURY_CHIITOI in kinds
    white_route = CHIITOI in kinds and WHITE_RICH in kinds
    if not plus_one:
        if luxury_route and (root.luxury_groups > 0 or
                             root.luxury_upgrade_live > 0):
            return None
        if white_route and root.wild_count >= 2 and (
                root.pair_units >= profile.big_hand_min_pair_units):
            return None
        return "no_same_shanten_strong_route"

    if speed_winner is None:
        return "speed_winner_unknown"
    if luxury_route:
        if root.luxury_groups > speed_winner.luxury_groups:
            return None
        if (root.luxury_upgrade_live >=
                profile.big_hand_min_luxury_upgrade_live and
                root.luxury_upgrade_live > speed_winner.luxury_upgrade_live):
            return None
    if white_route and root.wild_count >= 2 and (
            root.pair_units >= profile.big_hand_min_pair_units):
        return None
    return "no_plus_one_strong_route"


def _big_hand_candidate_key(root):
    """Deterministic route-first ordering; never an action value/bonus."""
    return (
        -(1 if LUXURY_CHIITOI in root.intent_kinds else 0),
        -(1 if root.luxury_groups else 0),
        -int(root.luxury_upgrade_live),
        int(root.chiitoi_shanten if root.chiitoi_shanten is not None else 99),
        -int(root.pair_units),
        -int(root.current_ukeire or 0),
        float(root.shape_loss), float(root.feed_risk), root.tile,
    )


def _apply_big_hand_guard(enriched, frontier, diagnostics, profile, locked,
                          admitted_by=None):
    """Admit at most one intent root while preserving one speed fallback slot.

    Same-shanten admission is preferred and remains inside the existing
    weighted comparator.  A plus-one root is only nominated when no Phase-A
    root is available and is returned separately for independent override.
    """
    speed_roots = _legacy_speed_roots(enriched)
    if not speed_roots:
        return (tuple(frontier), tuple(diagnostics),
                dict(admitted_by or {}), {}, None,
                "disabled", (), None)
    best_s = min(root.shanten for root in speed_roots)
    speed_winner = min(speed_roots, key=_legacy_key)
    speed_ukeire = max(int(root.current_ukeire or 0) for root in speed_roots)
    enabled = bool(profile.big_hand_enabled and
                   _weighted_native_ready(profile))
    phase = ("plus-one" if profile.big_hand_plus_one_enabled else
             "same-shanten" if profile.big_hand_same_shanten_enabled else
             "disabled") if enabled else "disabled"
    guard = {
        "enabled": enabled,
        "phase": phase,
        "policy": profile.big_hand_config(),
        "speed_pool_tiles": [root.tile for root in speed_roots],
        "speed_winner": speed_winner.tile,
        "admitted_tiles": [],
        "dropped_tiles": [],
        "candidate_gate_reasons": {},
        "skipped_reason": None,
    }
    admitted_by = dict(admitted_by or {
        root.tile: "primary" for root in frontier
    })
    plus_one_candidate = None
    gate_reasons = {}
    chosen = None
    chosen_reason = None

    if not enabled:
        guard["skipped_reason"] = (
            "feature_disabled" if not profile.big_hand_enabled else
            "kernel_unavailable")
    else:
        same_candidates = []
        initial_tiles = {root.tile for root in frontier}
        if profile.big_hand_same_shanten_enabled:
            for root in enriched:
                if root.speed_eligible and root.shanten == best_s:
                    if root.tile in initial_tiles:
                        continue
                    if not root.intent_kinds and root.intent_strength == "NONE":
                        continue
                    reason = _big_hand_route_reason(
                        root, profile, locked=locked)
                    if reason is None and (
                            speed_ukeire - int(root.current_ukeire or 0) >
                            profile.big_hand_max_ukeire_loss):
                        reason = "same_shanten_ukeire_loss"
                    gate_reasons[root.tile] = reason or "eligible_same_shanten"
                    if reason is None:
                        same_candidates.append(root)
        if same_candidates:
            chosen = min(same_candidates, key=_big_hand_candidate_key)
            chosen_reason = "same_shanten_intent"
            phase = "same-shanten"
        elif profile.big_hand_plus_one_enabled:
            plus_candidates = []
            for root in enriched:
                if root.shanten != best_s + 1:
                    if (root.shanten > best_s + 1 and
                            (root.intent_kinds or
                             root.intent_strength != "NONE")):
                        gate_reasons[root.tile] = "shanten_regression_too_large"
                    continue
                if not root.intent_kinds and root.intent_strength == "NONE":
                    continue
                reason = _big_hand_route_reason(
                    root, profile, speed_winner=speed_winner,
                    locked=locked, plus_one=True)
                if reason is None and (root.live_wall is None or
                        root.live_wall < profile.big_hand_min_live):
                    reason = "live_wall_guard"
                if reason is None and (root.max_opponent_melds is None or
                        root.max_opponent_melds >
                        profile.big_hand_max_opponent_melds):
                    reason = "opponent_meld_guard"
                gate_reasons[root.tile] = reason or "eligible_plus_one"
                if reason is None:
                    plus_candidates.append(root)
            if plus_candidates:
                chosen = min(plus_candidates, key=_big_hand_candidate_key)
                chosen_reason = "plus_one_intent"
                plus_one_candidate = chosen
                phase = "plus-one"
        else:
            guard["skipped_reason"] = "no_same_shanten_candidate"

    diagnostics = tuple(
        (replace(root, big_hand_gate_reason=gate_reasons[root.tile])
         if root.tile in gate_reasons else root,
         eligible, missing)
        for root, eligible, missing in diagnostics
    )
    enriched = tuple(
        replace(root, big_hand_gate_reason=gate_reasons[root.tile])
        if root.tile in gate_reasons else root for root in enriched
    )
    if chosen is None:
        if enabled and guard["skipped_reason"] is None:
            guard["skipped_reason"] = "no_candidate_admitted"
        guard["candidate_gate_reasons"] = {
            str(tile): reason for tile, reason in sorted(gate_reasons.items())
        }
        return (tuple(frontier), diagnostics, admitted_by, guard, None,
                phase, (), speed_winner.tile)

    limit = int(profile.max_frontier_candidates or 0)
    dropped = []
    kept_speed = list(frontier)
    # A Phase-B challenger is evaluated independently of the frozen speed
    # winner.  It must not evict a Phase-A root from a full frontier: doing so
    # would make the candidate profile change the speed comparator even when
    # the eventual +1 override is rejected.  Preserve the complete Phase-A
    # frontier and report the challenger as capacity-gated instead.
    if (plus_one_candidate is not None and limit and
            len(kept_speed) >= limit):
        reason = "frontier_cap_no_challenger_slot"
        gate_reasons[chosen.tile] = reason
        guard["skipped_reason"] = reason
        guard["candidate_gate_reasons"] = {
            str(tile): gate_reason
            for tile, gate_reason in sorted(gate_reasons.items())
        }
        updated_diagnostics = []
        for root, eligible, missing in diagnostics:
            if root.tile == chosen.tile:
                updated_diagnostics.append((
                    replace(root, big_hand_gate_reason=reason), False,
                    (reason,),
                ))
            else:
                updated_diagnostics.append((root, eligible, missing))
        return (tuple(frontier), tuple(updated_diagnostics), admitted_by,
                guard, None, phase, (), speed_winner.tile)
    if limit and len(kept_speed) >= limit:
        if limit <= 1:
            guard["skipped_reason"] = "frontier_cap_no_reserved_slot"
            guard["candidate_gate_reasons"] = {
                str(tile): reason for tile, reason in sorted(gate_reasons.items())
            }
            return (tuple(frontier), diagnostics, admitted_by, guard, None,
                    phase, (), speed_winner.tile)
        anchor = next((root for root in kept_speed
                       if root.tile == speed_winner.tile), kept_speed[0])
        others = [root for root in kept_speed if root.tile != anchor.tile]
        others.sort(key=lambda root: (
            0 if admitted_by.get(root.tile) == "shape_guard" else 1,
            -int(root.current_ukeire or 0), root.shape_loss,
            root.feed_risk, root.tile,
        ))
        speed_slots = limit - 1
        kept_speed = [anchor, *others[:max(0, speed_slots - 1)]]
        dropped = [root.tile for root in others[max(0, speed_slots - 1):]]
    merged = tuple([*kept_speed, chosen])
    admitted_by[chosen.tile] = "big_hand_guard"
    guard["admitted_tiles"] = [chosen.tile]
    guard["dropped_tiles"] = dropped
    guard["candidate_gate_reasons"] = {
        str(tile): reason for tile, reason in sorted(gate_reasons.items())
    }
    guard["selected_tile"] = chosen.tile
    guard["selected_reason"] = chosen_reason
    updated_diagnostics = []
    for root, eligible, missing in diagnostics:
        if root.tile == chosen.tile:
            updated_diagnostics.append((root, True, ()))
        elif root.tile in dropped:
            updated_diagnostics.append((root, False, ("frontier_cap",)))
        else:
            updated_diagnostics.append((root, eligible, missing))
    return (merged, tuple(updated_diagnostics), admitted_by, guard,
            plus_one_candidate.tile if plus_one_candidate is not None else None,
            phase, tuple(dropped), speed_winner.tile)


def _weighted_root_key(root, future_values, *, shape_quality_enabled=False,
                       future_shape_enabled=False):
    future = future_values[root.tile]
    key = (
        root.tile == W,
        -(root.current_ukeire or 0),
        -future.future_improve_weight,
        -(future.future_ukeire_mean or 0.0),
        -(future.future_ukeire_types_mean or 0.0),
    )
    if future_shape_enabled:
        if future.future_shape_quality_mean is None:
            raise ValueError("future shape quality is missing for a complete key")
        key += (-future.future_shape_quality_mean,)
    if shape_quality_enabled:
        if root.standing_shape_quality is None:
            raise ValueError("standing shape quality is missing for root")
        key += (-root.standing_shape_quality,)
    return key + (root.shape_loss, root.feed_risk, root.tile)


def _can_big_hand_override(challenger, speed_winner, future, profile, locked):
    """Independent conservative gate for a completed plus-one challenger."""
    if locked != 0:
        return False, "locked_hand"
    if challenger.intent_strength != INTENT_STRONG:
        return False, "intent_not_strong"
    if future is None or not future.complete:
        return False, "challenger_future_incomplete"
    if future.future_improve_weight is None or future.future_improve_weight <= 0:
        return False, "challenger_future_not_promising"
    if challenger.current_ukeire is None or speed_winner.current_ukeire is None:
        return False, "ukeire_unknown"
    if challenger.current_ukeire < profile.big_hand_min_ukeire:
        return False, "ukeire_absolute_floor"
    ukeire_loss = max(0, int(speed_winner.current_ukeire) -
                      int(challenger.current_ukeire))
    if ukeire_loss > profile.big_hand_max_ukeire_loss:
        return False, "ukeire_loss_guard"
    if (challenger.live_wall is None or
            challenger.live_wall < profile.big_hand_min_live):
        return False, "live_wall_guard"
    if (challenger.max_opponent_melds is None or
            challenger.max_opponent_melds >
            profile.big_hand_max_opponent_melds):
        return False, "opponent_meld_guard"

    kinds = set(challenger.intent_kinds)
    luxury_route = LUXURY_CHIITOI in kinds
    white_route = CHIITOI in kinds and WHITE_RICH in kinds
    luxury_advantage = (
        challenger.luxury_groups > speed_winner.luxury_groups or
        challenger.luxury_upgrade_live > speed_winner.luxury_upgrade_live
    )
    white_advantage = (
        challenger.wild_count > speed_winner.wild_count or
        challenger.pair_units > speed_winner.pair_units
    )
    if luxury_route and not luxury_advantage and not white_advantage:
        return False, "override_intent_tie"
    if white_route and not luxury_route and not white_advantage:
        return False, "override_intent_tie"
    if luxury_route and challenger.luxury_groups <= speed_winner.luxury_groups:
        if (challenger.luxury_upgrade_live <
                profile.big_hand_min_luxury_upgrade_live):
            return False, "luxury_upgrade_not_live"
    if white_route and challenger.wild_count < 2:
        return False, "white_rich_resource_lost"
    if not luxury_route and not white_route:
        return False, "no_override_route"
    return True, "strong_intent_override"


def _limit_weighted_frontier(frontier, diagnostics, limit, *,
                             shape_quality_enabled=False):
    """Apply the online cap only after minimum-shanten/current-ukeire filtering."""
    if not limit or len(frontier) <= limit:
        return tuple(frontier), tuple(diagnostics)
    if shape_quality_enabled:
        ranked = sorted(frontier, key=lambda root: (
            -int(root.current_ukeire or 0),
            -int(root.standing_shape_quality or 0),
            root.shape_loss, root.feed_risk, root.tile,
        ))
    else:
        ranked = sorted(
            frontier,
            key=lambda root: (
                root.shape_loss,
                root.feed_risk,
                -len(root.current_ukeire_tiles),
                root.tile,
            ),
        )
    kept = {root.tile for root in ranked[:limit]}
    updated = []
    for root, eligible, missing in diagnostics:
        if root.tile in {candidate.tile for candidate in frontier}:
            if root.tile in kept:
                updated.append((root, True, missing))
            else:
                updated.append((root, False, ("frontier_cap",)))
        else:
            updated.append((root, eligible, missing))
    return tuple(root for root in frontier if root.tile in kept), tuple(updated)


def _validate_root_legality(game, seat, roots):
    """Check that supplied roots are a projection of this game's legal set."""
    try:
        source = _as_tuple(game.hands[seat], name="source_hand", upper=4)
    except (AttributeError, IndexError, TypeError, _InvalidPublicState) as exc:
        raise _InvalidPublicState("root_legality_unknown") from exc
    frozen = bool(getattr(game, "freeze", 0) > 0 and
                  seat != getattr(game, "freezer", None))
    drawn = None
    if frozen:
        try:
            drawn = game.drawn[seat]
        except (AttributeError, IndexError, TypeError) as exc:
            raise _InvalidPublicState("root_legality_unknown") from exc
    seen = set()
    for root in roots:
        try:
            root_hand = _as_tuple(root.hand, name="root_hand", upper=4)
        except _InvalidPublicState as exc:
            raise _InvalidPublicState("root_legality_unknown") from exc
        if root.tile in seen:
            raise _InvalidPublicState("duplicate_root_candidate")
        if not 0 <= root.tile < 34:
            raise _InvalidPublicState("root_tile_invalid")
        seen.add(root.tile)
        if source[root.tile] <= root_hand[root.tile]:
            raise _InvalidPublicState("root_discard_not_in_hand")
        if frozen and root.tile != drawn:
            raise _InvalidPublicState("root_freeze_legality")


def _legacy_key(root):
    return (root.tile == W, -(root.current_ukeire or 0),
            root.shape_loss, root.feed_risk, root.tile)


def _future_key(shanten_value, ukeire_value, tile, shape, feed):
    return (shanten_value, -ukeire_value, tile == W, shape, feed, tile)


def _future_for_root(game, seat, root, locked, visible, profile, budget,
                     memo, shape_cost: Callable | None,
                     feed_risk: Callable | None):
    rule, phase, flags, frozen = _rule_context(game, profile, seat)
    remaining = [max(0, 4 - count) for count in visible]
    total_weight = sum(remaining)
    improve = 0
    weighted_ukeire = 0
    best_counts = {}
    draw_best_discards = {}
    for drawn, weight in enumerate(remaining):
        if weight <= 0:
            continue
        budget.check()
        next_hand = list(root.hand)
        next_hand[drawn] += 1
        visible_after = list(visible)
        visible_after[drawn] += 1
        if visible_after[drawn] > 4:
            raise _InvalidPublicState("visible_after_draw_invalid")
        legal = _child_discards(game, seat, next_hand, drawn, frozen)
        if not legal:
            raise _InvalidPublicState("future_legal_discards_unknown")
        best = None
        for discard in legal:
            after = list(next_hand)
            after[discard] -= 1
            cache_key = (
                tuple(after), drawn, int(locked), tuple(visible_after),
                rule, phase, (flags, legal), profile.fingerprint,
            )
            value = memo.get(cache_key)
            if value is None:
                budget.node()
                child_s = shanten(after, locked)
                child_u = int(ukeire(after, locked, visible_after)[2])
                if shape_cost is None:
                    child_shape = 0.0
                else:
                    child_shape = float(shape_cost(next_hand, discard))
                if feed_risk is None:
                    child_feed = 0.0
                else:
                    child_feed = float(feed_risk(game, seat, discard))
                value = (child_s, child_u, child_shape, child_feed)
                memo.put(cache_key, value)
            else:
                child_s, child_u, child_shape, child_feed = value
            key = _future_key(child_s, child_u, discard,
                              child_shape, child_feed)
            if best is None or key < best[0]:
                best = (key, discard, child_s, child_u)
        _key, discard, child_s, child_u = best
        draw_best_discards[drawn] = discard
        best_counts[discard] = best_counts.get(discard, 0) + weight
        if child_s < root.shanten:
            improve += weight
        weighted_ukeire += weight * child_u
        budget.check()
    mean = (float(weighted_ukeire) / float(total_weight)
            if total_weight else None)
    return FutureEvaluation(
        complete=True,
        root_shanten=root.shanten,
        future_improve_weight=improve,
        future_improve_lower=improve,
        future_improve_upper=improve,
        future_ukeire=weighted_ukeire,
        future_ukeire_mean=mean,
        future_ukeire_mean_denominator=total_weight,
        best_discards=tuple(sorted(best_counts.items())),
        best_discard_by_draw=tuple(sorted(draw_best_discards.items())),
        nodes=budget.nodes,
        cache_hits=memo.hits,
        elapsed_ms=budget.elapsed_ms,
        covered_weight=total_weight,
        total_weight=total_weight,
        coverage=1.0,
    )


def _weighted_future_for_root(game, seat, root, locked, visible, profile,
                              shape_cost, feed_risk, frozen=False):
    """Python parity implementation for explicit kernel=python diagnostics."""
    started = time.monotonic()
    remaining = [max(0, 4 - count) for count in visible]
    draw_order = sorted(
        (tile for tile, weight in enumerate(remaining) if weight > 0),
        key=lambda tile: (-remaining[tile], tile),
    )
    total_weight = sum(remaining)
    improve = 0
    maintain = 0
    weighted_ukeire = 0
    weighted_types = 0
    shape_enabled = (profile.shape_quality_enabled and
                     profile.shape_quality_stage == "full")
    weighted_shape = 0
    draw_shapes = {}
    best_counts = {}
    child_nodes = 0
    ukeire_calls = 0
    draw_best_discards = {}
    for drawn in draw_order:
        if ((time.monotonic() - started) * 1000.0 >=
                profile.hard_budget_ms):
            raise _BudgetExceeded("hard_deadline")
        weight = remaining[drawn]
        next_hand = list(root.hand)
        next_hand[drawn] += 1
        visible_after = list(visible)
        visible_after[drawn] += 1
        legal = _child_discards(game, seat, next_hand, drawn, frozen)
        if not legal:
            raise _InvalidPublicState("future_legal_discards_unknown")
        children = []
        best_s = None
        for discard in legal:
            if child_nodes >= profile.node_budget:
                raise _BudgetExceeded("node_budget_exceeded")
            after = list(next_hand)
            after[discard] -= 1
            child_s = shanten(after, locked)
            child_nodes += 1
            if best_s is None or child_s < best_s:
                best_s = child_s
            children.append((discard, after, child_s))
        best = None
        for discard, after, child_s in children:
            if child_s != best_s:
                continue
            ukeire_calls += 1
            child_state = ukeire(after, locked, visible_after)
            child_u = int(child_state[2])
            child_types = len(child_state[1])
            child_standing_shape = (
                standing_shape_quality(after, locked=locked).encoded
                if shape_enabled else None)
            child_shape = (float(shape_cost(next_hand, discard))
                           if shape_cost is not None else 0.0)
            child_feed = (float(feed_risk(game, seat, discard))
                          if feed_risk is not None else 0.0)
            if shape_enabled:
                key = (child_s, -child_u, -child_types,
                       -int(child_standing_shape), discard == W,
                       child_shape, child_feed, discard)
            else:
                key = (child_s, -child_u, -child_types, discard == W,
                       child_shape, child_feed, discard)
            if best is None or key < best[0]:
                best = (key, discard, child_u, child_types,
                        child_standing_shape)
        if best is None:
            raise _InvalidPublicState("future_legal_discards_unknown")
        _key, discard, child_u, child_types, child_standing_shape = best
        draw_best_discards[drawn] = discard
        best_counts[discard] = best_counts.get(discard, 0) + weight
        if best_s < root.shanten:
            improve += weight
        else:
            maintain += weight
        weighted_ukeire += weight * child_u
        weighted_types += weight * child_types
        if shape_enabled:
            draw_shapes[drawn] = int(child_standing_shape)
            weighted_shape += weight * int(child_standing_shape)
    elapsed_ms = (time.monotonic() - started) * 1000.0
    return FutureEvaluation(
        complete=True,
        root_shanten=root.shanten,
        future_improve_weight=improve,
        future_improve_lower=improve,
        future_improve_upper=improve,
        future_ukeire=weighted_ukeire,
        future_ukeire_mean=(float(weighted_ukeire) / float(total_weight)
                            if total_weight else None),
        future_ukeire_mean_denominator=total_weight,
        future_ukeire_types=weighted_types,
        future_ukeire_types_mean=(float(weighted_types) / float(total_weight)
                                  if total_weight else None),
        future_shape_quality_sum=(weighted_shape if shape_enabled else None),
        future_shape_quality_mean=(
            float(weighted_shape) / float(total_weight)
            if shape_enabled and total_weight else None),
        future_shape_denominator=(total_weight if shape_enabled else None),
        child_shape_quality_by_draw=(
            tuple(sorted(draw_shapes.items())) if shape_enabled else ()),
        best_discard_by_draw=tuple(sorted(draw_best_discards.items())),
        best_discards=tuple(sorted(best_counts.items())),
        nodes=child_nodes,
        cache_hits=0,
        elapsed_ms=elapsed_ms,
        covered_weight=total_weight,
        total_weight=total_weight,
        coverage=1.0,
        search_metrics={
            "root_candidates": 1,
            "draw_nodes": len(draw_order),
            "child_nodes": child_nodes,
            "shanten_calls": child_nodes,
            "ukeire_calls": ukeire_calls,
            "shanten_cache_hits": 0,
            "shanten_cache_misses": child_nodes,
            "ukeire_cache_hits": 0,
            "ukeire_cache_misses": ukeire_calls,
        },
    )


def _native_legal_masks(frontier, visible, frozen):
    """Build one 34-bit child-discard mask for every possible draw."""
    masks = []
    for root in frontier:
        root_masks = []
        for drawn, count in enumerate(visible):
            if count >= 4:
                root_masks.append(0)
                continue
            next_hand = list(root.hand)
            next_hand[drawn] += 1
            if frozen:
                mask = (1 << drawn) if next_hand[drawn] else 0
            else:
                mask = sum(1 << tile for tile, value in enumerate(next_hand)
                            if value > 0)
            root_masks.append(mask)
        masks.append(root_masks)
    return masks


def _native_kernel_choice(profile):
    """Return (selected implementation, optional immediate fallback reason)."""
    if profile.kernel == "python":
        return "python", None
    available = (legacy_two_ply_frontier is not None and
                 LEGACY_TWO_PLY_KERNEL_VERSION is not None)
    if available:
        return "rust", None
    if profile.kernel == "rust":
        return "legacy", "native_kernel_unavailable"
    return "python", None


def _native_future_for_frontier(
    game, seat, frontier, locked, visible, profile, frozen,
    shape_cost, feed_risk,
):
    """Run the native frontier and map rows back to Python V1 values.

    The native side returns all children tied on efficiency.  Selecting one of
    those ties remains Python policy work because shape/feed callbacks are
    intentionally not callable from Rust.
    """
    roots = [list(root.hand) for root in frontier]
    root_shantens = [int(root.shanten) for root in frontier]
    legal_masks = _native_legal_masks(frontier, visible, frozen)
    started = time.monotonic()
    rows = legacy_two_ply_frontier(
        roots, root_shantens, list(visible), legal_masks, locked, frozen,
        profile.node_budget, profile.time_budget_ms, True,
    )
    elapsed_ms = (time.monotonic() - started) * 1000.0
    if elapsed_ms >= profile.time_budget_ms:
        raise _NativeKernelIncomplete("time_budget_exceeded")
    if not isinstance(rows, (list, tuple)) or len(rows) != len(frontier):
        raise _NativeKernelInvalid("native_row_count_invalid")

    remaining = [max(0, 4 - count) for count in visible]
    expected_draws = {tile for tile, weight in enumerate(remaining)
                      if weight > 0}
    values = {}
    total_nodes = 0
    total_cache_hits = 0
    for expected_index, raw in enumerate(rows):
        if not isinstance(raw, (list, tuple)) or len(raw) != 8:
            raise _NativeKernelInvalid("native_row_shape_invalid")
        (index, complete, native_improve, native_ukeire, draw_rows,
         nodes, cache_hits, reason) = raw
        if int(index) != expected_index:
            raise _NativeKernelInvalid("native_root_order_invalid")
        total_nodes = max(total_nodes, int(nodes))
        total_cache_hits = max(total_cache_hits, int(cache_hits))
        if not complete:
            raise _NativeKernelIncomplete(str(reason or "native_incomplete"))
        if int(native_improve) < 0 or int(native_ukeire) < 0:
            raise _NativeKernelInvalid("native_metric_negative")
        if not isinstance(draw_rows, (list, tuple)):
            raise _NativeKernelInvalid("native_draw_rows_invalid")

        root = frontier[expected_index]
        seen_draws = set()
        improve = 0
        weighted_ukeire = 0
        best_counts = {}
        for raw_draw in draw_rows:
            if not isinstance(raw_draw, (list, tuple)) or len(raw_draw) != 5:
                raise _NativeKernelInvalid("native_draw_row_shape_invalid")
            drawn, weight, child_s, child_u, tied = raw_draw
            drawn = int(drawn)
            weight = int(weight)
            child_s = int(child_s)
            child_u = int(child_u)
            if (drawn not in expected_draws or drawn in seen_draws or
                    weight != remaining[drawn] or child_u < 0 or
                    not isinstance(tied, (list, tuple)) or not tied):
                raise _NativeKernelInvalid("native_draw_row_invalid")
            seen_draws.add(drawn)
            next_hand = list(root.hand)
            next_hand[drawn] += 1
            candidates = []
            for discard in tied:
                discard = int(discard)
                if not 0 <= discard < 34 or next_hand[discard] <= 0:
                    raise _NativeKernelInvalid("native_child_discard_invalid")
                child_shape = (float(shape_cost(next_hand, discard))
                               if shape_cost is not None else 0.0)
                child_feed = (float(feed_risk(game, seat, discard))
                              if feed_risk is not None else 0.0)
                candidates.append((_future_key(
                    child_s, child_u, discard, child_shape, child_feed),
                    discard))
            _key, selected = min(candidates, key=lambda item: item[0])
            best_counts[selected] = best_counts.get(selected, 0) + weight
            if child_s < root.shanten:
                improve += weight
            weighted_ukeire += weight * child_u

        if seen_draws != expected_draws:
            raise _NativeKernelInvalid("native_draw_coverage_invalid")
        if improve != int(native_improve) or weighted_ukeire != int(native_ukeire):
            raise _NativeKernelInvalid("native_metric_mismatch")
        total_weight = sum(remaining)
        values[root.tile] = FutureEvaluation(
            complete=True,
            root_shanten=root.shanten,
            future_improve_weight=improve,
            future_improve_lower=improve,
            future_improve_upper=improve,
            future_ukeire=weighted_ukeire,
            future_ukeire_mean=(float(weighted_ukeire) / float(total_weight)
                                if total_weight else None),
            future_ukeire_mean_denominator=total_weight,
            best_discards=tuple(sorted(best_counts.items())),
            nodes=total_nodes,
            cache_hits=total_cache_hits,
            elapsed_ms=elapsed_ms,
            covered_weight=total_weight,
            total_weight=total_weight,
            coverage=1.0,
        )
    return values, total_nodes, total_cache_hits, elapsed_ms


def _weighted_native_future_for_frontier(
    game, seat, frontier, locked, visible, profile, frozen,
    shape_cost, feed_risk, *, stage_a_only=False,
):
    """Map the weighted native rows while preserving committed root rows."""
    roots = [list(root.hand) for root in frontier]
    root_shantens = [int(root.shanten) for root in frontier]
    legal_masks = _native_legal_masks(frontier, visible, frozen)
    started = time.monotonic()
    rows = weighted_two_ply_frontier(
        roots, root_shantens, list(visible), legal_masks, locked, frozen,
        profile.node_budget, profile.soft_budget_ms, profile.hard_budget_ms,
        profile.cache_capacity, profile.min_partial_coverage, True,
        profile.workers, stage_a_only=stage_a_only,
        shape_quality_enabled=(profile.shape_quality_enabled and
                               profile.shape_quality_stage == "full"),
    )
    elapsed_ms = (time.monotonic() - started) * 1000.0
    if rows is None:
        raise _NativeKernelUnavailable("native_weighted_kernel_unavailable")
    if not isinstance(rows, (list, tuple)) or len(rows) != len(frontier):
        raise _NativeKernelInvalid("weighted_native_row_count_invalid")

    remaining = [max(0, 4 - count) for count in visible]
    expected_draws = {tile for tile, weight in enumerate(remaining)
                      if weight > 0}
    values = {}
    all_metrics = {}
    for expected_index, raw in enumerate(rows):
        if not isinstance(raw, (list, tuple)) or len(raw) != 8:
            raise _NativeKernelInvalid("weighted_native_row_shape_invalid")
        (index, committed, complete, metrics, draw_rows, counters,
         elapsed_us, reason) = raw
        if int(index) != expected_index:
            raise _NativeKernelInvalid("weighted_native_root_order_invalid")
        if (not isinstance(metrics, (list, tuple)) or len(metrics) != 7 or
                not isinstance(counters, (list, tuple)) or len(counters) != 11):
            raise _NativeKernelInvalid("weighted_native_metrics_shape_invalid")
        (covered, total, native_improve, native_maintain,
         native_ukeire, native_types, native_shape_sum) = (
             int(value) for value in metrics)
        counter_names = (
            "root_candidates", "draw_nodes", "child_nodes", "shanten_calls",
            "ukeire_calls", "shanten_cache_hits", "shanten_cache_misses",
            "ukeire_cache_hits", "ukeire_cache_misses", "workers",
            "stage_b_entered",
        )
        search_metrics = {
            name: int(value) for name, value in zip(counter_names, counters)
        }
        stage_b_entered = bool(search_metrics["stage_b_entered"])
        if any(value < 0 for value in (
                covered, total, native_improve, native_maintain,
                native_ukeire, native_types,
                *search_metrics.values())):
            raise _NativeKernelInvalid("weighted_native_metric_negative")
        root = frontier[expected_index]
        if total != sum(remaining) or covered > total:
            raise _NativeKernelInvalid("weighted_native_weight_invalid")
        if not committed:
            all_metrics = search_metrics
            continue
        if not isinstance(draw_rows, (list, tuple)):
            raise _NativeKernelInvalid("weighted_native_draw_rows_invalid")

        # ``stage_a_only`` means Stage A proved the winner and Stage B was
        # skipped on purpose.  ``stage_a_shaped`` only says the draw rows carry
        # the sentinel columns, which is also what an aborted Stage B returns.
        stage_a_only = str(reason or "") == "future_ukeire_skipped"
        shape_enabled = (profile.shape_quality_enabled and
                         profile.shape_quality_stage == "full")
        sentinel_flags = []
        for raw_draw in draw_rows:
            if not isinstance(raw_draw, (list, tuple)) or len(raw_draw) != 7:
                raise _NativeKernelInvalid("weighted_native_draw_row_shape_invalid")
            sentinel_flags.append(
                int(raw_draw[3]) == -1 and int(raw_draw[4]) == -1 and
                int(raw_draw[5]) == -1)
        if sentinel_flags and any(flag != sentinel_flags[0] for flag in sentinel_flags):
            raise _NativeKernelInvalid("weighted_native_draw_stage_mixed")
        stage_a_shaped = bool(sentinel_flags) and all(sentinel_flags)
        if stage_a_only and sentinel_flags and not stage_a_shaped:
            raise _NativeKernelInvalid("weighted_native_stage_a_row_invalid")
        # 证明性 Stage-A-only 只可能出现在"Stage A 直接判胜"的分支,此时
        # 内核根本没有进入 Stage B;反过来,已进入 Stage B 的运行不允许把
        # 哨兵行当成 Stage-A-only 证明。
        if stage_a_only and stage_b_entered:
            raise _NativeKernelInvalid("weighted_native_stage_a_phase_invalid")
        if complete and stage_a_shaped:
            raise _NativeKernelInvalid("weighted_native_stage_a_marked_complete")

        seen_draws = set()
        improve = 0
        maintain = 0
        weighted_ukeire = 0
        weighted_types = 0
        weighted_shape = 0
        best_counts = {}
        draw_shapes = {}
        draw_best_discards = {}
        for raw_draw in draw_rows:
            (drawn, weight, child_s, child_u, child_types,
             child_shape, tied) = raw_draw
            drawn = int(drawn)
            weight = int(weight)
            child_s = int(child_s)
            child_u = int(child_u)
            child_types = int(child_types)
            child_shape_quality = int(child_shape)
            if (drawn not in expected_draws or drawn in seen_draws or
                    weight != remaining[drawn] or
                    (stage_a_shaped and (child_u != -1 or child_types != -1 or
                                         child_shape != -1)) or
                    (not stage_a_shaped and (child_u < 0 or child_types < 0)) or
                    (not stage_a_shaped and shape_enabled and
                     child_shape_quality < 0) or
                    (not stage_a_shaped and not shape_enabled and
                     child_shape_quality != -1) or
                    not isinstance(tied, (list, tuple)) or not tied):
                raise _NativeKernelInvalid("weighted_native_draw_row_invalid")
            seen_draws.add(drawn)
            next_hand = list(root.hand)
            next_hand[drawn] += 1
            if stage_a_shaped:
                if child_s < root.shanten:
                    improve += weight
                else:
                    maintain += weight
                continue
            candidates = []
            for discard in tied:
                discard = int(discard)
                if not 0 <= discard < 34 or next_hand[discard] <= 0:
                    raise _NativeKernelInvalid(
                        "weighted_native_child_discard_invalid")
                discard_shape_cost = (
                    float(shape_cost(next_hand, discard))
                    if shape_cost is not None else 0.0)
                child_feed = (float(feed_risk(game, seat, discard))
                              if feed_risk is not None else 0.0)
                if (shape_enabled and
                        not 0 <= child_shape_quality < (1 << 32)):
                    raise _NativeKernelInvalid(
                        "weighted_native_child_shape_invalid")
                candidates.append((
                    (child_s, -child_u, -child_types, discard == W,
                     discard_shape_cost, child_feed, discard),
                    discard,
                ))
            _key, selected = min(candidates, key=lambda item: item[0])
            draw_best_discards[drawn] = selected
            best_counts[selected] = best_counts.get(selected, 0) + weight
            if child_s < root.shanten:
                improve += weight
            else:
                maintain += weight
            weighted_ukeire += weight * child_u
            weighted_types += weight * child_types
            if shape_enabled:
                weighted_shape += weight * child_shape_quality
                draw_shapes[drawn] = child_shape_quality

        complete_value = bool(complete) and not stage_a_shaped
        if complete_value:
            if seen_draws != expected_draws or covered != total:
                raise _NativeKernelInvalid("weighted_native_complete_coverage")
        elif covered != sum(remaining[draw] for draw in seen_draws):
            raise _NativeKernelInvalid("weighted_native_partial_coverage")
        if (improve != native_improve or maintain != native_maintain or
                (not stage_a_shaped and weighted_ukeire != native_ukeire) or
                (not stage_a_shaped and weighted_types != native_types) or
                (stage_a_shaped and (native_ukeire != 0 or native_types != 0)) or
                (shape_enabled and not stage_a_shaped and
                 native_shape_sum != weighted_shape) or
                ((not shape_enabled or stage_a_shaped) and
                 native_shape_sum != -1)):
            raise _NativeKernelInvalid("weighted_native_metric_mismatch")
        total_weight = total
        mean_denominator = total if complete_value else covered
        values[root.tile] = FutureEvaluation(
            complete=complete_value,
            root_shanten=root.shanten,
            future_improve_weight=improve,
            future_improve_lower=improve,
            future_improve_upper=improve + max(0, total - covered),
            future_ukeire=(None if stage_a_shaped else weighted_ukeire),
            future_ukeire_mean=(
                None if stage_a_shaped else
                (float(weighted_ukeire) / float(mean_denominator)
                 if mean_denominator else None)),
            future_ukeire_mean_denominator=(
                None if stage_a_shaped else mean_denominator),
            future_ukeire_types=(None if stage_a_shaped else weighted_types),
            future_ukeire_types_mean=(
                None if stage_a_shaped else
                (float(weighted_types) / float(mean_denominator)
                 if mean_denominator else None)),
            future_shape_quality_sum=(
                weighted_shape if shape_enabled and not stage_a_shaped else None),
            future_shape_quality_mean=(
                float(weighted_shape) / float(total_weight)
                if shape_enabled and not stage_a_shaped and total_weight else None),
            future_shape_denominator=(
                total_weight if shape_enabled and not stage_a_shaped else None),
            child_shape_quality_by_draw=(
                tuple(sorted(draw_shapes.items())) if draw_shapes else ()),
            best_discard_by_draw=(
                tuple(sorted(draw_best_discards.items()))
                if draw_best_discards else ()),
            future_ukeire_skipped=stage_a_only,
            best_discards=(
                () if stage_a_shaped else tuple(sorted(best_counts.items()))),
            nodes=search_metrics["child_nodes"],
            cache_hits=search_metrics["shanten_cache_hits"],
            elapsed_ms=float(elapsed_us) / 1000.0,
            covered_weight=covered,
            total_weight=total_weight,
            coverage=(float(covered) / float(total_weight)
                      if total_weight else 1.0),
            search_metrics={
                **search_metrics,
                "search_phase": ("two_ply" if stage_b_entered
                                 else "future_shanten"),
            },
            missing=("future_ukeire_not_evaluated",) if stage_a_shaped else (),
            fallback_reason=(str(reason) if reason else None),
        )
        all_metrics = {
            **search_metrics,
            "search_phase": ("two_ply" if stage_b_entered
                             else "future_shanten"),
        }
    return values, all_metrics, elapsed_ms


def _weighted_evaluation(
    game, seat, root_candidates, locked, visible, profile,
    *, shape_cost=None, feed_risk=None,
):
    """Evaluate the weighted online profile (canonical name: ``legacyV2``)."""
    requested_kernel = profile.kernel
    actual_kernel = "legacy"
    kernel_version = None
    kernel_fallback_reason = None
    try:
        roots = _normalise_roots(root_candidates, locked)
        _validate_root_legality(game, seat, roots)
        enriched, frontier, diagnostics = _root_features(
            roots, locked, visible,
            shape_quality_enabled=profile.shape_quality_enabled)
        visible_tuple = _as_tuple(visible, name="visible")
    except (TypeError, ValueError, _InvalidPublicState) as exc:
        roots = locals().get("roots", ())
        legacy = _legacy_speed_best(roots)
        evaluation = LegacyDiscardEvaluation(
            version=profile.version, profile=profile.name,
            profile_fingerprint=profile.fingerprint, level="legacy",
            complete=False, selected=legacy, legacy_best=legacy,
            candidates=tuple(root.as_json() for root in roots),
            future_model=profile.model, mode=profile.mode,
            budget={"node_budget": profile.node_budget,
                    "work_budget": profile.node_budget,
                    "work_budget_metric": "shanten_cache_misses",
                    "soft_budget_ms": profile.soft_budget_ms,
                    "hard_budget_ms": profile.hard_budget_ms,
                    "internal_hard_budget_ms": max(
                        0.0, profile.hard_budget_ms - WEIGHTED_DEADLINE_RESERVE_MS),
                    "deadline_reserve_ms": WEIGHTED_DEADLINE_RESERVE_MS},
            fallback_reason=str(exc), missing=(str(exc),),
            requested_kernel=requested_kernel, actual_kernel="legacy",
            kernel_fallback_reason="root_validation_failed",
        )
        return legacy, evaluation

    frontier, diagnostics = _limit_weighted_frontier(
        frontier, diagnostics, profile.max_frontier_candidates,
        shape_quality_enabled=profile.shape_quality_enabled)
    legacy = _legacy_speed_best(enriched)
    speed_pool = _legacy_speed_roots(enriched)
    speed_pool_tiles = tuple(root.tile for root in speed_pool)
    if not profile.enabled:
        evaluation = LegacyDiscardEvaluation(
            version=profile.version, profile=profile.name,
            profile_fingerprint=profile.fingerprint, level="legacy",
            complete=False, selected=legacy, legacy_best=legacy,
            candidates=tuple(root.as_json() for root, _, _ in diagnostics),
            future_model=profile.model, mode=profile.mode,
            budget={"node_budget": profile.node_budget,
                    "work_budget": profile.node_budget,
                    "work_budget_metric": "shanten_cache_misses",
                    "soft_budget_ms": profile.soft_budget_ms,
                    "hard_budget_ms": profile.hard_budget_ms},
            fallback_reason="profile_disabled", missing=("profile_disabled",),
            requested_kernel=requested_kernel, actual_kernel="legacy",
            kernel_fallback_reason="profile_disabled",
            speed_pool_tiles=speed_pool_tiles,
            speed_winner=legacy,
            big_hand_phase="disabled",
        )
        return legacy, evaluation

    # D1/D2/D3: 结构护栏。默认关闭时原样返回,零行为漂移。
    frontier, diagnostics, frontier_guard, admitted_by = _apply_shape_guard(
        frontier, diagnostics, profile)
    (frontier, diagnostics, admitted_by, big_hand_guard,
     big_hand_challenger, big_hand_phase, frontier_cap_dropped,
     speed_winner_hint) = _apply_big_hand_guard(
         enriched, frontier, diagnostics, profile, int(locked), admitted_by)
    speed_shanten = min((root.shanten for root in speed_pool), default=None)
    speed_frontier = tuple(
        root for root in frontier
        if root.speed_eligible and root.shanten == speed_shanten
    )
    if not speed_frontier:
        speed_frontier = tuple(root for root in frontier
                               if root.tile != big_hand_challenger)
    speed_pool_tiles = tuple(root.tile for root in speed_pool)

    if len(frontier) == 1:
        singleton = frontier[0]
        candidate_json = []
        frontier_tiles = {singleton.tile}
        for root, eligible, missing in diagnostics:
            data = root.as_json()
            data["missing"] = list(missing)
            data["admitted_by"] = admitted_by.get(root.tile, "primary")
            if root.tile in frontier_tiles and eligible:
                data["missing"] = ["future_not_evaluated_short_circuit"]
                data["future_short_circuit_reason"] = "frontier_singleton"
            candidate_json.append(data)
        evaluation = LegacyDiscardEvaluation(
            version=profile.version, profile=profile.name,
            profile_fingerprint=profile.fingerprint,
            level="legacy-one-ply", complete=False,
            selected=singleton.tile, legacy_best=legacy,
            candidates=tuple(candidate_json), future_model=profile.model,
            mode=profile.mode,
            budget={"node_budget": profile.node_budget,
                    "work_budget": profile.node_budget,
                    "work_budget_metric": "shanten_cache_misses",
                    "soft_budget_ms": profile.soft_budget_ms,
                    "hard_budget_ms": profile.hard_budget_ms},
            missing=("future_not_evaluated_short_circuit",),
            search_metrics={
                "root_candidates": len(frontier),
                "draw_nodes": 0,
                "child_nodes": 0,
                "shanten_calls": 0,
                "ukeire_calls": 0,
                "work_budget": profile.node_budget,
                "work_budget_metric": "shanten_cache_misses",
                "short_circuit": "frontier_singleton",
            },
            requested_kernel=requested_kernel, actual_kernel="legacy",
            short_circuit_reason="frontier_singleton",
            frontier_guard=frontier_guard,
            speed_pool_tiles=speed_pool_tiles,
            speed_winner=singleton.tile,
            big_hand_challenger=big_hand_challenger,
            big_hand_override=False,
            big_hand_override_reason=(
                "frontier_singleton" if big_hand_challenger is not None else None),
            big_hand_phase=big_hand_phase,
            frontier_cap_dropped=frontier_cap_dropped,
            big_hand_guard=big_hand_guard,
            shape_quality_version=profile.shape_quality_version,
            shape_quality_used=profile.shape_quality_enabled,
            shape_quality_stage=(profile.shape_quality_stage
                                 if profile.shape_quality_enabled else None),
            shape_changed_winner=(profile.shape_quality_enabled and
                                  singleton.tile != legacy),
            shape_baseline_selected=(
                legacy if profile.shape_quality_enabled else None),
            decision_scope=("weighted_two_ply"
                            if profile.shape_quality_enabled else "legacy"),
            stage_b_entered=False,
        )
        return singleton.tile, evaluation

    frozen = _rule_context(game, profile, seat)[3]
    future_values = {}
    search_metrics = {}
    fallback_reason = None
    elapsed_ms = 0.0
    if profile.kernel == "python":
        actual_kernel = "python"
        try:
            for root in frontier:
                future_values[root.tile] = _weighted_future_for_root(
                    game, seat, root, locked, visible_tuple, profile,
                    shape_cost, feed_risk, frozen)
            search_metrics = {
                "root_candidates": len(frontier),
                "draw_nodes": sum(
                    len(tuple(tile for tile, weight in enumerate(
                        [max(0, 4 - count) for count in visible_tuple])
                        if weight > 0)) for _ in frontier),
                "child_nodes": sum(value.nodes for value in future_values.values()),
                "shanten_calls": sum(value.nodes for value in future_values.values()),
                "ukeire_calls": None,
                "shanten_cache_hits": 0,
                "shanten_cache_misses": sum(value.nodes for value in future_values.values()),
                "ukeire_cache_hits": 0,
                "ukeire_cache_misses": None,
                "stage_b_entered": int(
                    profile.shape_quality_stage == "full"),
            }
            elapsed_ms = sum(value.elapsed_ms or 0 for value in future_values.values())
        except (_BudgetExceeded, _InvalidPublicState) as exc:
            fallback_reason = str(exc)
            kernel_fallback_reason = f"python_{exc}"
    else:
        available = (weighted_two_ply_frontier is not None and
                     WEIGHTED_TWO_PLY_KERNEL_VERSION is not None)
        if (available and WEIGHTED_TWO_PLY_KERNEL_VERSION
                != WEIGHTED_TWO_PLY_KERNEL_REQUIRED):
            available = False
            fallback_reason = "native_weighted_kernel_version_mismatch"
            kernel_fallback_reason = fallback_reason
        if not available:
            if fallback_reason is None:
                fallback_reason = "native_weighted_kernel_unavailable"
                kernel_fallback_reason = fallback_reason
        else:
            actual_kernel = "rust"
            kernel_version = WEIGHTED_TWO_PLY_KERNEL_VERSION
            try:
                future_values, search_metrics, elapsed_ms = (
                    _weighted_native_future_for_frontier(
                        game, seat, frontier, locked, visible_tuple, profile,
                        frozen, shape_cost, feed_risk))
            except (_NativeKernelUnavailable, _NativeKernelInvalid) as exc:
                fallback_reason = str(exc)
                actual_kernel = "legacy"
                kernel_fallback_reason = f"native_{exc}"

    root_tiles = {root.tile for root in frontier}
    committed_tiles = set(future_values)
    complete = bool(root_tiles) and committed_tiles == root_tiles and all(
        value.complete and (value.coverage is None or value.coverage >= 1.0)
        for value in future_values.values())
    partial_accepted = False
    partial_winner = None
    if (not fallback_reason and not complete and
            big_hand_challenger is not None):
        # A cross-shanten challenger can only reach the independent override
        # gate on complete future rows. Partial comparison is meaningful only
        # inside the same-shanten speed pool.
        fallback_reason = "big_hand_challenger_incomplete"
    elif not fallback_reason and not complete and profile.allow_partial:
        if bool(root_tiles) and committed_tiles == root_tiles:
            bounds = {
                root.tile: (
                    future_values[root.tile].future_improve_lower,
                    future_values[root.tile].future_improve_upper,
                )
                for root in frontier
            }
            for root in frontier:
                lower, _upper = bounds[root.tile]
                if lower is None:
                    continue
                other_uppers = [bounds[other.tile][1]
                                for other in frontier
                                if other.tile != root.tile]
                if (other_uppers and all(value is not None and lower > value
                                         for value in other_uppers)):
                    partial_winner = root
                    break
            partial_accepted = partial_winner is not None
        if not partial_accepted:
            fallback_reason = "partial_not_acceptable"
    elif not fallback_reason and not complete:
        fallback_reason = "incomplete_weighted_frontier"

    accepted = not fallback_reason and (complete or partial_accepted)
    speed_winner_root = None
    big_hand_override = False
    big_hand_override_reason = None
    if accepted:
        if partial_accepted:
            speed_winner_root = partial_winner
        else:
            speed_winner_root = min(
                speed_frontier,
                key=lambda root: _weighted_root_key(
                    root, future_values,
                    shape_quality_enabled=profile.shape_quality_enabled,
                    future_shape_enabled=(
                        profile.shape_quality_enabled and
                        profile.shape_quality_stage == "full" and
                        all(value.future_shape_quality_mean is not None
                            for value in future_values.values())),
                ),
            )
        selected = speed_winner_root.tile
        if big_hand_challenger is not None and not partial_accepted:
            challenger_root = next(
                (root for root in frontier
                 if root.tile == big_hand_challenger), None)
            challenger_future = future_values.get(big_hand_challenger)
            big_hand_override, big_hand_override_reason = (
                _can_big_hand_override(
                    challenger_root, speed_winner_root, challenger_future,
                    profile, int(locked))
                if challenger_root is not None else
                (False, "challenger_unknown"))
            if big_hand_override:
                selected = big_hand_challenger
    else:
        selected = legacy
        speed_winner_root = next(
            (root for root in speed_pool if root.tile == legacy), None)
        if big_hand_challenger is not None:
            big_hand_override_reason = fallback_reason or "weighted_incomplete"
        actual_kernel = "legacy"

    stage_a_only = any(
        value.future_ukeire_skipped for value in future_values.values())
    shape_changed_winner = False
    shape_baseline_selected = None
    if (profile.shape_quality_enabled and accepted and not partial_accepted
            and future_values):
        future_shape_enabled = (
            profile.shape_quality_stage == "full" and
            all(value.future_shape_quality_mean is not None
                for value in future_values.values()))
        old_shape_winner = min(
            speed_frontier,
            key=lambda root: _weighted_root_key(
                root, future_values, shape_quality_enabled=False,
                future_shape_enabled=False),
        )
        shape_baseline_selected = old_shape_winner.tile
        shape_changed_winner = (
            speed_winner_root is not None and
            old_shape_winner.tile != speed_winner_root.tile)
    search_used = bool(accepted)
    search_attempt_phase = search_metrics.get("search_phase")
    search_phase = (
        ("future_shanten" if stage_a_only else "two_ply")
        if accepted else None)

    candidate_json = []
    frontier_tiles = {root.tile for root in frontier}
    for root, eligible, missing in diagnostics:
        data = root.as_json()
        data["missing"] = list(missing)
        data["admitted_by"] = admitted_by.get(root.tile, "primary")
        if root.tile in frontier_tiles and accepted:
            future = future_values[root.tile]
            future = FutureEvaluation(
                **{**future.__dict__, "partial_accepted": partial_accepted})
            data.update(future.as_json())
        elif root.tile in frontier_tiles:
            data.update(FutureEvaluation(
                complete=False, root_shanten=root.shanten,
                missing=("weighted_incomplete",),
                fallback_reason=fallback_reason).as_json())
        candidate_json.append(data)

    evaluation = LegacyDiscardEvaluation(
        version=profile.version, profile=profile.name,
        profile_fingerprint=profile.fingerprint,
        level=("weighted-two-ply-v1" if complete else
               "weighted-two-ply-partial" if partial_accepted else "legacy"),
        complete=complete, selected=selected, legacy_best=legacy,
        candidates=tuple(candidate_json), future_model=profile.model,
        mode=profile.mode, future_nodes=int(search_metrics.get("child_nodes", 0)
                                            or 0),
        future_cache_hits=int(search_metrics.get("shanten_cache_hits", 0)
                              or 0),
        budget={"node_budget": profile.node_budget,
                "work_budget": profile.node_budget,
                "work_budget_metric": "shanten_cache_misses",
                "soft_budget_ms": profile.soft_budget_ms,
                "hard_budget_ms": profile.hard_budget_ms,
                "internal_hard_budget_ms": max(
                    0.0, profile.hard_budget_ms - WEIGHTED_DEADLINE_RESERVE_MS),
                "deadline_reserve_ms": WEIGHTED_DEADLINE_RESERVE_MS},
        fallback_reason=fallback_reason,
        missing=(() if accepted else ("weighted_incomplete",)),
        partial_accepted=partial_accepted,
        covered_weight=(min((future.covered_weight for future in
                             future_values.values() if future.covered_weight is not None),
                            default=None) if accepted else None),
        total_weight=(min((future.total_weight for future in
                           future_values.values() if future.total_weight is not None),
                          default=None) if accepted else None),
        coverage=(min((future.coverage for future in future_values.values()
                       if future.coverage is not None), default=None)
                  if accepted else None),
        search_metrics={
            **search_metrics,
            "elapsed_ms": elapsed_ms,
            "work_budget": profile.node_budget,
            "work_budget_metric": "shanten_cache_misses",
            "search_used": search_used,
            "search_phase": search_phase,
            "search_attempt_phase": search_attempt_phase,
            "internal_hard_budget_ms": max(
                0.0, profile.hard_budget_ms - WEIGHTED_DEADLINE_RESERVE_MS),
            "deadline_reserve_ms": WEIGHTED_DEADLINE_RESERVE_MS,
        },
        requested_kernel=requested_kernel, actual_kernel=actual_kernel,
        kernel_version=kernel_version,
        kernel_fallback_reason=kernel_fallback_reason,
        search_used=search_used,
        search_phase=search_phase,
        search_attempt_phase=search_attempt_phase,
        frontier_guard=frontier_guard,
        speed_pool_tiles=speed_pool_tiles,
        speed_winner=(speed_winner_root.tile if speed_winner_root is not None
                      else legacy),
        big_hand_challenger=big_hand_challenger,
        big_hand_override=big_hand_override,
        big_hand_override_reason=big_hand_override_reason,
        big_hand_phase=big_hand_phase,
        frontier_cap_dropped=frontier_cap_dropped,
        big_hand_guard=big_hand_guard,
        shape_quality_version=profile.shape_quality_version,
        shape_quality_used=bool(profile.shape_quality_enabled and accepted),
        shape_quality_stage=(
            "stage_b" if profile.shape_quality_enabled and
            profile.shape_quality_stage == "full" and
            search_metrics.get("stage_b_entered") else
            "root" if profile.shape_quality_enabled and accepted else None),
        shape_changed_winner=bool(shape_changed_winner),
        shape_baseline_selected=shape_baseline_selected,
        decision_scope=("weighted_two_ply" if accepted else "legacy"),
        stage_b_entered=bool(search_metrics.get("stage_b_entered", 0)),
    )
    return selected, evaluation


def evaluate_standing_frontier(
        standings: Sequence[StandingRoot], locked: int,
        visible: Sequence[int], profile: LegacyTwoPlyProfile, *,
        shape_cost: Callable | None = None,
) -> Mapping[str | int, FutureEvaluation]:
    """Evaluate public standing hands through the shared weighted Rust kernel.

    Every supplied root is already a standing candidate, so this entrypoint
    does not apply the discard evaluator's current-ukeire frontier or inspect a
    ``Game``.  No wall order or opponent concealed hand is accepted.
    """
    roots = tuple(standings)
    ids = [root.stable_id for root in roots]
    if not roots:
        return OrderedDict()
    if len(set(ids)) != len(ids):
        raise ValueError("standing root ids must be unique")
    if profile.mode != "weighted":
        raise ValueError("standing frontier requires a weighted profile")

    # Offline ``require_complete`` labels need Stage-B metrics even when the
    # native kernel can stop at a strict Stage-A winner. Same-hand duplicates
    # cannot be strict winners, so private tie guards force the native
    # frontier to finish Stage A and enter Stage B. They are removed from the
    # returned stable-id mapping below. Online reaction frontiers instead
    # explicitly request a common Stage-A safe partial.
    kernel_roots = roots
    if profile.require_complete:
        kernel_roots = roots + tuple(
            StandingRoot(
                f"{root.stable_id}::__offline_stage_b_guard",
                root.hand, root.shanten,
            ) for root in roots
        )
    if len(kernel_roots) > 68:
        raise ValueError("standing frontier supports at most 68 roots")

    try:
        visible_tuple = _as_tuple(visible, name="visible")
        synthetic = []
        for index, standing in enumerate(kernel_roots):
            hand = _validate_hand(standing.hand, locked)
            if any(visible_tuple[tile] < count
                   for tile, count in enumerate(hand)):
                raise _InvalidPublicState("visible_missing_hand")
            actual_shanten = shanten(hand, locked)
            if actual_shanten != standing.shanten:
                raise _InvalidPublicState("standing_shanten_mismatch")
            synthetic.append(LegacyRootCandidate(
                tile=index,
                hand=hand,
                shanten=actual_shanten,
                shanten_verified=True,
            ))
    except (TypeError, ValueError, _InvalidPublicState) as exc:
        reason = str(exc)
        return OrderedDict((root.stable_id, FutureEvaluation(
            complete=False,
            root_shanten=root.shanten,
            missing=(reason,),
            fallback_reason=reason,
            search_metrics={"profile_fingerprint": profile.fingerprint},
        )) for root in roots)

    fallback_reason = None
    values = {}
    if not profile.enabled:
        fallback_reason = "profile_disabled"
    elif profile.kernel == "python":
        # The reaction layer must not grow a second Python draw-discard DFS.
        fallback_reason = "standing_python_dfs_disallowed"
    elif (weighted_two_ply_frontier is None
          or WEIGHTED_TWO_PLY_KERNEL_VERSION is None):
        fallback_reason = "native_weighted_kernel_unavailable"
    elif WEIGHTED_TWO_PLY_KERNEL_VERSION != WEIGHTED_TWO_PLY_KERNEL_REQUIRED:
        fallback_reason = "native_weighted_kernel_version_mismatch"
    else:
        try:
            values, _metrics, _elapsed = _weighted_native_future_for_frontier(
                None, 0, tuple(synthetic), int(locked), visible_tuple,
                profile, False, shape_cost, None,
                stage_a_only=(profile.allow_partial
                              and not profile.require_complete))
        except (_NativeKernelUnavailable, _NativeKernelInvalid,
                _BudgetExceeded, _InvalidPublicState) as exc:
            fallback_reason = str(exc)

    all_present = len(values) == len(kernel_roots)
    all_complete = all_present and all(
        value.complete and (value.coverage is None or value.coverage >= 1.0)
        for value in values.values()
    )
    stages = {
        "stage_a" if value.future_ukeire_skipped else "stage_b"
        for value in values.values()
    }
    safe_partial = (
        not fallback_reason
        and not all_complete
        and profile.allow_partial
        and all_present
        and len(stages) == 1
        and all(
            value.coverage is not None
            and value.coverage >= profile.min_partial_coverage
            for value in values.values()
        )
    )
    accepted = all_complete or safe_partial
    if not fallback_reason and not accepted:
        if not all_present:
            fallback_reason = "standing_root_incomplete"
        elif len(stages) > 1:
            fallback_reason = "standing_stage_mismatch"
        elif not profile.allow_partial:
            fallback_reason = "standing_partial_disallowed"
        else:
            fallback_reason = "standing_coverage_insufficient"

    result = OrderedDict()
    for index, root in enumerate(roots):
        value = values.get(index)
        if value is None or not accepted:
            # Do not commit incomplete decision metrics, but retain bounded
            # search diagnostics so shadow/performance audits can account for
            # the cost and coverage of transactional fallbacks.
            result[root.stable_id] = FutureEvaluation(
                complete=False,
                root_shanten=root.shanten,
                nodes=(value.nodes if value is not None else 0),
                cache_hits=(value.cache_hits if value is not None else 0),
                elapsed_ms=(value.elapsed_ms if value is not None else None),
                covered_weight=(value.covered_weight
                                if value is not None else None),
                total_weight=(value.total_weight
                              if value is not None else None),
                coverage=(value.coverage if value is not None else None),
                missing=(fallback_reason or "standing_incomplete",),
                fallback_reason=fallback_reason or "standing_incomplete",
                search_metrics={
                    **(dict(value.search_metrics or {})
                       if value is not None else {}),
                    "profile_fingerprint": profile.fingerprint,
                },
            )
            continue
        metrics = dict(value.search_metrics or {})
        metrics["profile_fingerprint"] = profile.fingerprint
        result[root.stable_id] = FutureEvaluation(**{
            **value.__dict__,
            "partial_accepted": safe_partial,
            "search_metrics": metrics,
        })
    return result


def evaluate_legacy_two_ply(game, seat, root_candidates, locked, visible,
                             profile: LegacyTwoPlyProfile | None = None,
                             *, shape_cost: Callable | None = None,
                             feed_risk: Callable | None = None):
    """Evaluate a root frontier and return ``(tile, explanation)``.

    ``root_candidates`` may contain :class:`LegacyRootCandidate` values or the
    existing bot tuples ``(tile, post_hand, shanten, shape, feed)``.  The
    returned action is always legal from the supplied root set.  On any
    incomplete/unknown state, all partial future values are discarded and the
    complete legacy selection is returned with an explicit fallback reason.
    """
    profile = profile or LegacyTwoPlyProfile.default()
    if profile.mode == "weighted":
        return _weighted_evaluation(
            game, seat, root_candidates, locked, visible, profile,
            shape_cost=shape_cost, feed_risk=feed_risk)
    kernel_choice, kernel_select_reason = _native_kernel_choice(profile)
    requested_kernel = profile.kernel
    actual_kernel = ("rust" if kernel_choice == "rust"
                     else "legacy" if kernel_choice == "legacy"
                     else "python")
    kernel_version = (LEGACY_TWO_PLY_KERNEL_VERSION
                      if kernel_choice == "rust" else None)
    kernel_fallback_reason = kernel_select_reason
    try:
        roots = _normalise_roots(root_candidates, locked)
    except (TypeError, ValueError, _InvalidPublicState) as exc:
        roots = ()
        root_error = f"root_candidate_{type(exc).__name__}"
    else:
        root_error = None
    try:
        if root_error:
            raise _InvalidPublicState(root_error)
        _validate_root_legality(game, seat, roots)
        enriched, frontier, diagnostics = _root_features(
            roots, locked, visible,
            shape_quality_enabled=profile.shape_quality_enabled)
        visible_tuple = _as_tuple(visible, name="visible")
    except _InvalidPublicState as exc:
        legacy = min(roots, key=_legacy_key).tile if roots else None
        evaluation = LegacyDiscardEvaluation(
            version=PROFILE_VERSION, profile=profile.name,
            profile_fingerprint=profile.fingerprint, level="legacy",
            complete=False, selected=legacy, legacy_best=legacy,
            candidates=tuple(root.as_json() for root in roots),
            budget={"node_budget": profile.node_budget,
                    "time_budget_ms": profile.time_budget_ms},
            fallback_reason=str(exc),
            missing=(str(exc),),
            requested_kernel=requested_kernel,
            actual_kernel="legacy",
            kernel_version=None,
            kernel_fallback_reason="root_validation_failed",
        )
        return legacy, evaluation

    legacy = min(enriched, key=_legacy_key).tile
    if not profile.enabled:
        evaluation = LegacyDiscardEvaluation(
            version=PROFILE_VERSION, profile=profile.name,
            profile_fingerprint=profile.fingerprint, level="legacy",
            complete=False, selected=legacy, legacy_best=legacy,
            candidates=tuple(root.as_json() for root, _, _ in diagnostics),
            budget={"node_budget": profile.node_budget,
                    "time_budget_ms": profile.time_budget_ms},
            fallback_reason="profile_disabled",
            missing=("profile_disabled",),
            requested_kernel=requested_kernel,
            actual_kernel="legacy",
            kernel_version=None,
            kernel_fallback_reason="profile_disabled",
        )
        return legacy, evaluation

    budget = _Budget(profile)
    memo = _FutureMemo(profile.cache_capacity)
    future_values = {}
    future_cache_hits = 0
    fallback_reason = kernel_select_reason
    if fallback_reason is None:
        try:
            if kernel_choice == "rust":
                (future_values, native_nodes, native_cache_hits,
                 _native_elapsed) = _native_future_for_frontier(
                    game, seat, frontier, locked, visible_tuple, profile,
                    _rule_context(game, profile, seat)[3], shape_cost,
                    feed_risk)
                budget.nodes = native_nodes
                future_cache_hits = native_cache_hits
                actual_kernel = "rust"
            else:
                for root in frontier:
                    future_values[root.tile] = _future_for_root(
                        game, seat, root, locked, visible_tuple, profile,
                        budget, memo, shape_cost, feed_risk)
                future_cache_hits = memo.hits
        except (_NativeKernelUnavailable, _NativeKernelIncomplete,
                _NativeKernelInvalid) as exc:
            fallback_reason = str(exc)
            actual_kernel = "legacy"
            kernel_fallback_reason = f"native_{exc}"
        except _BudgetExceeded as exc:
            fallback_reason = str(exc)
        except _InvalidPublicState as exc:
            fallback_reason = str(exc)
        except (AttributeError, TypeError, ValueError) as exc:
            fallback_reason = f"future_input_{type(exc).__name__}"
            if kernel_choice == "rust":
                actual_kernel = "legacy"
                kernel_fallback_reason = f"native_{type(exc).__name__}"

    complete = fallback_reason is None and len(future_values) == len(frontier)
    if complete:
        selected_root = min(
            frontier,
            key=lambda root: (
                root.tile == W,
                -(root.current_ukeire or 0),
                -future_values[root.tile].future_improve_weight,
                -future_values[root.tile].future_ukeire,
                root.shape_loss, root.feed_risk, root.tile,
            ),
        )
        selected = selected_root.tile
    else:
        selected = legacy

    candidate_json = []
    frontier_tiles = {root.tile for root in frontier}
    for root, eligible, missing in diagnostics:
        data = root.as_json()
        data["missing"] = list(missing)
        if root.tile in frontier_tiles and complete:
            data.update(future_values[root.tile].as_json())
        elif root.tile in frontier_tiles:
            data.update(FutureEvaluation(
                complete=False, root_shanten=root.shanten,
                nodes=budget.nodes, cache_hits=future_cache_hits,
                missing=("future_incomplete",),
                fallback_reason=fallback_reason).as_json())
        candidate_json.append(data)
    evaluation = LegacyDiscardEvaluation(
        version=PROFILE_VERSION, profile=profile.name,
        profile_fingerprint=profile.fingerprint,
        level="legacy-v1" if complete else "legacy",
        complete=complete, selected=selected, legacy_best=legacy,
        candidates=tuple(candidate_json),
        future_nodes=budget.nodes,
        future_cache_hits=future_cache_hits,
        budget={"node_budget": profile.node_budget,
                "time_budget_ms": profile.time_budget_ms},
        fallback_reason=fallback_reason,
        missing=(() if complete else ("future_incomplete",)),
        requested_kernel=requested_kernel,
        actual_kernel=actual_kernel if complete else "legacy",
        kernel_version=kernel_version,
        kernel_fallback_reason=kernel_fallback_reason,
    )
    return selected, evaluation


__all__ = [
    "canonical_evaluator", "DEFAULT_BOT_EVALUATOR", "FUTURE_MODEL",
    "LEGACY_V2_BASELINE_EVALUATORS", "LEGACY_V2_PHASE_A_EVALUATORS",
    "LEGACY_V2_PHASE_B_EVALUATORS", "LEGACY_V2_EXPERIMENT_EVALUATORS",
    "LEGACY_V2_EVALUATORS",
    "LEGACY_V2_PROFILE_VERSION", "PROFILE_VERSION", "WEIGHTED_PROFILE_VERSION",
    "LegacyDiscardEvaluation",
    "LegacyRootCandidate", "LegacyTwoPlyProfile", "FutureEvaluation",
    "StandingRoot", "evaluate_legacy_two_ply", "evaluate_standing_frontier",
]
