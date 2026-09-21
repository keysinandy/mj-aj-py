"""Public-information, one-draw lookahead for the legacy discard policy.

This module deliberately does not share the shape-v1/shape-v2 evaluator.  The
legacy V1 contract is much smaller: after a root discard, enumerate one public
draw and one legal discard, then stop.  All values are immutable and the
whole layer is transactional: an incomplete frontier never contributes a
partial score to the selected action.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import hashlib
import json
import math
import time
from typing import Callable, Iterable, Mapping, Sequence

from .shanten import (
    LEGACY_TWO_PLY_KERNEL_VERSION,
    WEIGHTED_TWO_PLY_KERNEL_VERSION,
    legacy_two_ply_frontier,
    weighted_two_ply_frontier,
    shanten,
    ukeire,
)
from .tiles import W


PROFILE_VERSION = "legacy-two-ply-v1"
FUTURE_MODEL = "uniform_unseen_one_draw_best_discard"
SORT_VERSION = "legacy-frontier-future-v1"
LEGACY_V2_PROFILE_VERSION = "legacyV2"
# ``WEIGHTED_PROFILE_VERSION`` remains as a source-compatible constant for
# callers that imported it during the rollout.  The serialized/profile name is
# now the shorter public ``legacyV2`` identifier.
WEIGHTED_PROFILE_VERSION = LEGACY_V2_PROFILE_VERSION
WEIGHTED_SORT_VERSION = "weighted-frontier-v1"
LEGACY_V2_EVALUATORS = (
    LEGACY_V2_PROFILE_VERSION,
    "legacy-v2",
    "weighted-two-ply-frontier-v1",
    "weighted_two_ply",
    "weighted-two-ply",
)
# Product default for the discard evaluator.  Callers that need the
# historical rollback oracle continue to pass ``evaluator="legacy"``
# explicitly; keeping the name centralized prevents CLI/clientd defaults from
# drifting apart.
DEFAULT_BOT_EVALUATOR = LEGACY_V2_PROFILE_VERSION


def canonical_evaluator(value):
    """Normalize public LegacyV2 aliases without touching explicit legacy."""
    return (DEFAULT_BOT_EVALUATOR
            if value in LEGACY_V2_EVALUATORS else value)


def _canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


@dataclass(frozen=True)
class LegacyTwoPlyProfile:
    """Versioned knobs that can affect the V1 result or its cost."""

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
    enabled: bool = True

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
        object.__setattr__(self, "allow_partial", bool(self.allow_partial))
        object.__setattr__(self, "min_partial_coverage", coverage)
        object.__setattr__(self, "lazy_child_ukeire",
                           bool(self.lazy_child_ukeire))
        object.__setattr__(self, "enabled", bool(self.enabled))

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
        }
        legacy_compatible = (
            self.mode == "exact" and
            self.max_frontier_candidates == 0 and
            not self.allow_partial and
            self.min_partial_coverage == 1.0 and
            self.lazy_child_ukeire and
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
            })
        return payload

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
    current_ukeire: int | None = None
    current_ukeire_tiles: tuple[int, ...] = ()
    eligible: bool = True
    missing: tuple[str, ...] = ()
    shanten_verified: bool = False

    def as_json(self):
        return {
            "tile": self.tile,
            "shanten": self.shanten,
            "ukeire": self.current_ukeire,
            "ukeire_tiles": list(self.current_ukeire_tiles),
            "ukeire_types": (len(self.current_ukeire_tiles)
                             if self.current_ukeire is not None else None),
            "shape_loss": self.shape_loss,
            "feed_risk": self.feed_risk,
            "eligible": self.eligible,
            "missing": list(self.missing),
        }


@dataclass(frozen=True)
class FutureEvaluation:
    """Result of exactly one future draw and one best discard."""

    complete: bool
    root_shanten: int | None = None
    future_improve_weight: int | None = None
    future_ukeire: int | None = None
    future_ukeire_mean: float | None = None
    future_ukeire_types: int | None = None
    future_ukeire_types_mean: float | None = None
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
            "future_ukeire": self.future_ukeire,
            "future_ukeire_mean": self.future_ukeire_mean,
            "future_ukeire_types": self.future_ukeire_types,
            "future_ukeire_types_mean": self.future_ukeire_types_mean,
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
    budget: Mapping[str, float | int] | None = None
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


def _root_features(roots, locked, visible):
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
        if s != root.shanten:
            # The immutable root is a public contract, not a trust boundary;
            # recompute rather than letting a stale caller change ordering.
            root = LegacyRootCandidate(
                tile=root.tile, hand=hand, shanten=s,
                shape_loss=root.shape_loss, feed_risk=root.feed_risk)
        state = ukeire(hand, locked, visible)
        enriched.append(LegacyRootCandidate(
            tile=root.tile, hand=hand, shanten=s,
            shape_loss=root.shape_loss, feed_risk=root.feed_risk,
            current_ukeire=int(state[2]),
            current_ukeire_tiles=tuple(state[1])))
    if not enriched:
        raise _InvalidPublicState("no_root_candidates")
    min_s = min(root.shanten for root in enriched)
    frontier = [root for root in enriched if root.shanten == min_s]
    if any(root.tile != W for root in frontier):
        frontier = [root for root in frontier if root.tile != W]
    max_u = max(int(root.current_ukeire or 0) for root in frontier)
    frontier = [root for root in frontier if root.current_ukeire == max_u]
    frontier_tiles = {root.tile for root in frontier}
    diagnostics = []
    for root in enriched:
        if root.tile not in frontier_tiles:
            missing = ("current_ukeire_frontier",)
            diagnostics.append((root, False, missing))
        else:
            diagnostics.append((root, True, ()))
    return tuple(enriched), tuple(frontier), tuple(diagnostics)


def _limit_weighted_frontier(frontier, diagnostics, limit):
    """Apply the online cap only after minimum-shanten/current-ukeire filtering."""
    if not limit or len(frontier) <= limit:
        return tuple(frontier), tuple(diagnostics)
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
        future_ukeire=weighted_ukeire,
        future_ukeire_mean=mean,
        best_discards=tuple(sorted(best_counts.items())),
        nodes=budget.nodes,
        cache_hits=memo.hits,
        elapsed_ms=budget.elapsed_ms,
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
    best_counts = {}
    child_nodes = 0
    ukeire_calls = 0
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
            child_shape = (float(shape_cost(next_hand, discard))
                           if shape_cost is not None else 0.0)
            child_feed = (float(feed_risk(game, seat, discard))
                          if feed_risk is not None else 0.0)
            key = (child_s, -child_u, -child_types, discard == W,
                   child_shape, child_feed, discard)
            if best is None or key < best[0]:
                best = (key, discard, child_u, child_types)
        if best is None:
            raise _InvalidPublicState("future_legal_discards_unknown")
        _key, discard, child_u, child_types = best
        best_counts[discard] = best_counts.get(discard, 0) + weight
        if best_s < root.shanten:
            improve += weight
        else:
            maintain += weight
        weighted_ukeire += weight * child_u
        weighted_types += weight * child_types
    elapsed_ms = (time.monotonic() - started) * 1000.0
    return FutureEvaluation(
        complete=True,
        root_shanten=root.shanten,
        future_improve_weight=improve,
        future_ukeire=weighted_ukeire,
        future_ukeire_mean=(float(weighted_ukeire) / float(total_weight)
                            if total_weight else None),
        future_ukeire_types=weighted_types,
        future_ukeire_types_mean=(float(weighted_types) / float(total_weight)
                                  if total_weight else None),
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
            future_ukeire=weighted_ukeire,
            future_ukeire_mean=(float(weighted_ukeire) / float(total_weight)
                                if total_weight else None),
            best_discards=tuple(sorted(best_counts.items())),
            nodes=total_nodes,
            cache_hits=total_cache_hits,
            elapsed_ms=elapsed_ms,
        )
    return values, total_nodes, total_cache_hits, elapsed_ms


def _weighted_native_future_for_frontier(
    game, seat, frontier, locked, visible, profile, frozen,
    shape_cost, feed_risk,
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
        if (not isinstance(metrics, (list, tuple)) or len(metrics) != 6 or
                not isinstance(counters, (list, tuple)) or len(counters) != 9):
            raise _NativeKernelInvalid("weighted_native_metrics_shape_invalid")
        (covered, total, native_improve, native_maintain,
         native_ukeire, native_types) = (int(value) for value in metrics)
        counter_names = (
            "root_candidates", "draw_nodes", "child_nodes", "shanten_calls",
            "ukeire_calls", "shanten_cache_hits", "shanten_cache_misses",
            "ukeire_cache_hits", "ukeire_cache_misses",
        )
        search_metrics = {
            name: int(value) for name, value in zip(counter_names, counters)
        }
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

        seen_draws = set()
        improve = 0
        maintain = 0
        weighted_ukeire = 0
        weighted_types = 0
        best_counts = {}
        for raw_draw in draw_rows:
            if not isinstance(raw_draw, (list, tuple)) or len(raw_draw) != 6:
                raise _NativeKernelInvalid("weighted_native_draw_row_shape_invalid")
            drawn, weight, child_s, child_u, child_types, tied = raw_draw
            drawn = int(drawn)
            weight = int(weight)
            child_s = int(child_s)
            child_u = int(child_u)
            child_types = int(child_types)
            if (drawn not in expected_draws or drawn in seen_draws or
                    weight != remaining[drawn] or child_u < 0 or
                    child_types < 0 or not isinstance(tied, (list, tuple)) or
                    not tied):
                raise _NativeKernelInvalid("weighted_native_draw_row_invalid")
            seen_draws.add(drawn)
            next_hand = list(root.hand)
            next_hand[drawn] += 1
            candidates = []
            for discard in tied:
                discard = int(discard)
                if not 0 <= discard < 34 or next_hand[discard] <= 0:
                    raise _NativeKernelInvalid(
                        "weighted_native_child_discard_invalid")
                child_shape = (float(shape_cost(next_hand, discard))
                               if shape_cost is not None else 0.0)
                child_feed = (float(feed_risk(game, seat, discard))
                              if feed_risk is not None else 0.0)
                candidates.append((
                    (child_s, -child_u, -child_types, discard == W,
                     child_shape, child_feed, discard),
                    discard,
                ))
            _key, selected = min(candidates, key=lambda item: item[0])
            best_counts[selected] = best_counts.get(selected, 0) + weight
            if child_s < root.shanten:
                improve += weight
            else:
                maintain += weight
            weighted_ukeire += weight * child_u
            weighted_types += weight * child_types

        if complete:
            if seen_draws != expected_draws or covered != total:
                raise _NativeKernelInvalid("weighted_native_complete_coverage")
        elif covered != sum(remaining[draw] for draw in seen_draws):
            raise _NativeKernelInvalid("weighted_native_partial_coverage")
        if (improve != native_improve or maintain != native_maintain or
                weighted_ukeire != native_ukeire or
                weighted_types != native_types):
            raise _NativeKernelInvalid("weighted_native_metric_mismatch")
        total_weight = total
        values[root.tile] = FutureEvaluation(
            complete=bool(complete),
            root_shanten=root.shanten,
            future_improve_weight=improve,
            future_ukeire=weighted_ukeire,
            future_ukeire_mean=(float(weighted_ukeire) / float(total_weight)
                                if total_weight else None),
            future_ukeire_types=weighted_types,
            future_ukeire_types_mean=(float(weighted_types) / float(total_weight)
                                      if total_weight else None),
            best_discards=tuple(sorted(best_counts.items())),
            nodes=search_metrics["child_nodes"],
            cache_hits=search_metrics["shanten_cache_hits"],
            elapsed_ms=float(elapsed_us) / 1000.0,
            covered_weight=covered,
            total_weight=total_weight,
            coverage=(float(covered) / float(total_weight)
                      if total_weight else 1.0),
            search_metrics=search_metrics,
            fallback_reason=(str(reason) if reason else None),
        )
        all_metrics = search_metrics
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
        enriched, frontier, diagnostics = _root_features(roots, locked, visible)
        visible_tuple = _as_tuple(visible, name="visible")
    except (TypeError, ValueError, _InvalidPublicState) as exc:
        roots = locals().get("roots", ())
        legacy = min(roots, key=_legacy_key).tile if roots else None
        evaluation = LegacyDiscardEvaluation(
            version=profile.version, profile=profile.name,
            profile_fingerprint=profile.fingerprint, level="legacy",
            complete=False, selected=legacy, legacy_best=legacy,
            candidates=tuple(root.as_json() for root in roots),
            future_model=profile.model, mode=profile.mode,
            budget={"node_budget": profile.node_budget,
                    "soft_budget_ms": profile.soft_budget_ms,
                    "hard_budget_ms": profile.hard_budget_ms},
            fallback_reason=str(exc), missing=(str(exc),),
            requested_kernel=requested_kernel, actual_kernel="legacy",
            kernel_fallback_reason="root_validation_failed",
        )
        return legacy, evaluation

    frontier, diagnostics = _limit_weighted_frontier(
        frontier, diagnostics, profile.max_frontier_candidates)
    legacy = min(enriched, key=_legacy_key).tile
    if not profile.enabled:
        evaluation = LegacyDiscardEvaluation(
            version=profile.version, profile=profile.name,
            profile_fingerprint=profile.fingerprint, level="legacy",
            complete=False, selected=legacy, legacy_best=legacy,
            candidates=tuple(root.as_json() for root, _, _ in diagnostics),
            future_model=profile.model, mode=profile.mode,
            budget={"node_budget": profile.node_budget,
                    "soft_budget_ms": profile.soft_budget_ms,
                    "hard_budget_ms": profile.hard_budget_ms},
            fallback_reason="profile_disabled", missing=("profile_disabled",),
            requested_kernel=requested_kernel, actual_kernel="legacy",
            kernel_fallback_reason="profile_disabled",
        )
        return legacy, evaluation

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
            }
            elapsed_ms = sum(value.elapsed_ms or 0 for value in future_values.values())
        except (_BudgetExceeded, _InvalidPublicState) as exc:
            fallback_reason = str(exc)
            kernel_fallback_reason = f"python_{exc}"
    else:
        available = (weighted_two_ply_frontier is not None and
                     WEIGHTED_TWO_PLY_KERNEL_VERSION is not None)
        if not available:
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
    if not fallback_reason and not complete and profile.allow_partial:
        partial_accepted = (
            bool(root_tiles) and committed_tiles == root_tiles and all(
                value.coverage is not None and
                value.coverage >= profile.min_partial_coverage
                for value in future_values.values()))
        if not partial_accepted:
            fallback_reason = "partial_not_acceptable"
    elif not fallback_reason and not complete:
        fallback_reason = "incomplete_weighted_frontier"

    accepted = not fallback_reason and (complete or partial_accepted)
    if accepted:
        selected_root = min(
            frontier,
            key=lambda root: (
                root.tile == W,
                -(root.current_ukeire or 0),
                -future_values[root.tile].future_improve_weight,
                -(future_values[root.tile].future_ukeire_mean or 0.0),
                -(future_values[root.tile].future_ukeire_types_mean or 0.0),
                root.shape_loss, root.feed_risk, root.tile,
            ),
        )
        selected = selected_root.tile
    else:
        selected = legacy
        actual_kernel = "legacy"

    candidate_json = []
    frontier_tiles = {root.tile for root in frontier}
    for root, eligible, missing in diagnostics:
        data = root.as_json()
        data["missing"] = list(missing)
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
                "soft_budget_ms": profile.soft_budget_ms,
                "hard_budget_ms": profile.hard_budget_ms},
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
        search_metrics={**search_metrics, "elapsed_ms": elapsed_ms},
        requested_kernel=requested_kernel, actual_kernel=actual_kernel,
        kernel_version=kernel_version,
        kernel_fallback_reason=kernel_fallback_reason,
    )
    return selected, evaluation


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
        enriched, frontier, diagnostics = _root_features(roots, locked, visible)
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
    "LEGACY_V2_EVALUATORS",
    "LEGACY_V2_PROFILE_VERSION", "PROFILE_VERSION", "WEIGHTED_PROFILE_VERSION",
    "LegacyDiscardEvaluation",
    "LegacyRootCandidate", "LegacyTwoPlyProfile", "FutureEvaluation",
    "evaluate_legacy_two_ply",
]
