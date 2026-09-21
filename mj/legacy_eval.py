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
    legacy_two_ply_frontier,
    shanten,
    ukeire,
)
from .tiles import W


PROFILE_VERSION = "legacy-two-ply-v1"
FUTURE_MODEL = "uniform_unseen_one_draw_best_discard"
SORT_VERSION = "legacy-frontier-future-v1"


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
    enabled: bool = True

    def __post_init__(self):
        if not self.name or not self.version or not self.model:
            raise ValueError("legacy-two-ply profile identifiers are required")
        if self.kernel not in {"auto", "python", "rust"}:
            raise ValueError("kernel must be auto, python, or rust")
        if int(self.node_budget) < 0:
            raise ValueError("node_budget must be non-negative")
        if int(self.cache_capacity) < 0:
            raise ValueError("cache_capacity must be non-negative")
        value = float(self.time_budget_ms)
        if not math.isfinite(value) or value < 0:
            raise ValueError("time_budget_ms must be finite and non-negative")
        object.__setattr__(self, "node_budget", int(self.node_budget))
        object.__setattr__(self, "cache_capacity", int(self.cache_capacity))
        object.__setattr__(self, "time_budget_ms", value)
        object.__setattr__(self, "enabled", bool(self.enabled))

    @classmethod
    def default(cls, **overrides):
        return cls(**overrides)

    def _payload(self):
        return {
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

    def as_json(self):
        result = self._payload()
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

    def as_json(self):
        return {
            "tile": self.tile,
            "shanten": self.shanten,
            "ukeire": self.current_ukeire,
            "ukeire_tiles": list(self.current_ukeire_tiles),
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
    best_discards: tuple[tuple[int, int], ...] = ()
    nodes: int = 0
    cache_hits: int = 0
    elapsed_ms: float | None = None
    missing: tuple[str, ...] = ()
    fallback_reason: str | None = None

    def as_json(self):
        result = {
            "complete": self.complete,
            "root_shanten": self.root_shanten,
            "future_improve_weight": self.future_improve_weight,
            "future_ukeire": self.future_ukeire,
            "future_ukeire_mean": self.future_ukeire_mean,
            "future_best_discards": {
                str(tile): weight for tile, weight in self.best_discards
            },
            "future_nodes": self.nodes,
            "future_cache_hits": self.cache_hits,
            "future_elapsed_ms": self.elapsed_ms,
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
            "future_nodes": self.future_nodes,
            "future_cache_hits": self.future_cache_hits,
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
        s = shanten(hand, locked)
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
    "FUTURE_MODEL", "PROFILE_VERSION", "LegacyDiscardEvaluation",
    "LegacyRootCandidate", "LegacyTwoPlyProfile", "FutureEvaluation",
    "evaluate_legacy_two_ply",
]
