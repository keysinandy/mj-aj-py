"""Versioned profiles and helpers for the legacy reaction policy.

The reaction profile is deliberately separate from the discard profile.  A
reaction can keep the frozen v1 gate while independently enabling the shared
standing-hand future layer, its transactional coverage rules, and the tempo
guard.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import hashlib
import json
import math

from .game import CHOW_HIGH, CHOW_LOW, CHOW_MID, KONG_OPEN, PASS, PONG
from .legacy_eval import (
    FutureEvaluation,
    LegacyTwoPlyProfile,
    StandingRoot,
    evaluate_standing_frontier,
)
from .legacy_kong import (
    progress_not_worse_v2,
    public_score_continuation,
    select_pong_kong_same_unit,
)
from .shanten import BAOTOU_UKEIRE_RUST, baotou_ukeire, shanten, ukeire
from .tiles import W
from .win import is_baotou, is_baotou_wait, is_win


LEGACY_REACTION_V1 = "legacy-shape-progress-v1"
LEGACY_REACTION_V2 = "legacy-react-v2"
LEGACY_REACTION_V2_OFFLINE = "legacy-react-v2-offline"
DEFAULT_HU_DISCARD_DELAY_MIN_GAIN_RATIO = 1.10
PONG_MIN_ABS_GAIN = 4
CHOW_MIN_ABS_GAIN = 6
LEGACY_MIN_GAIN_RATIO = 1.50

_LEGACY_PROGRESS_STATS = {
    "progress_calls": 0,
    "cache_hits": 0,
    "ukeire_calls": 0,
    "baotou_ukeire_calls": 0,
    "piao_enumerations": 0,
    # KONG structure decomposition still increments this through the
    # compatibility counter owned by bot.py until the KONG module split.
    "decomposition_calls": 0,
}


def _canonical_json(value) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    )


@dataclass(frozen=True)
class LegacyReactionProfile:
    """All knobs that can change legacy reaction behaviour or search cost."""

    name: str = LEGACY_REACTION_V1
    version: str = LEGACY_REACTION_V1
    future_enabled: bool = False
    future_mode: str = "disabled"
    future_node_budget: int = 0
    future_soft_budget_ms: float = 0.0
    future_hard_budget_ms: float = 0.0
    allow_partial: bool = False
    min_partial_coverage: float = 1.0
    require_complete: bool = False
    tempo_guard: bool = False
    continuation_node_budget: int = 0
    continuation_soft_budget_ms: float = 0.0
    continuation_hard_budget_ms: float = 0.0
    enabled: bool = True
    hu_discard_delay_min_gain_ratio: float = 1.0

    def __post_init__(self):
        hu_ratio = float(self.hu_discard_delay_min_gain_ratio)
        if not math.isfinite(hu_ratio) or hu_ratio < 1:
            raise ValueError("hu_discard_delay_min_gain_ratio must be finite and >= 1")
        object.__setattr__(self, "hu_discard_delay_min_gain_ratio", hu_ratio)
        if not self.name or not self.version:
            raise ValueError("legacy reaction profile identifiers are required")
        if self.future_mode not in {"disabled", "weighted"}:
            raise ValueError("future_mode must be disabled or weighted")
        if int(self.future_node_budget) < 0:
            raise ValueError("future_node_budget must be non-negative")
        if int(self.continuation_node_budget) < 0:
            raise ValueError("continuation_node_budget must be non-negative")
        soft = float(self.future_soft_budget_ms)
        hard = float(self.future_hard_budget_ms)
        if (not math.isfinite(soft) or not math.isfinite(hard)
                or soft < 0 or hard < 0 or soft > hard):
            raise ValueError("future budgets must be finite and ordered")
        continuation_soft = float(self.continuation_soft_budget_ms)
        continuation_hard = float(self.continuation_hard_budget_ms)
        if (not math.isfinite(continuation_soft)
                or not math.isfinite(continuation_hard)
                or continuation_soft < 0 or continuation_hard < 0
                or continuation_soft > continuation_hard):
            raise ValueError(
                "continuation budgets must be finite, non-negative and ordered")
        coverage = float(self.min_partial_coverage)
        if not math.isfinite(coverage) or not 0 <= coverage <= 1:
            raise ValueError("min_partial_coverage must be between 0 and 1")
        if self.future_enabled and self.future_mode == "disabled":
            raise ValueError("enabled future evaluation requires a future mode")
        if not self.future_enabled and self.future_mode != "disabled":
            raise ValueError("disabled future evaluation must use disabled mode")
        if self.require_complete and self.allow_partial:
            raise ValueError("require_complete cannot allow partial results")

        object.__setattr__(self, "future_enabled", bool(self.future_enabled))
        object.__setattr__(self, "future_node_budget",
                           int(self.future_node_budget))
        object.__setattr__(self, "future_soft_budget_ms", soft)
        object.__setattr__(self, "future_hard_budget_ms", hard)
        object.__setattr__(self, "allow_partial", bool(self.allow_partial))
        object.__setattr__(self, "min_partial_coverage", coverage)
        object.__setattr__(self, "require_complete", bool(self.require_complete))
        object.__setattr__(self, "tempo_guard", bool(self.tempo_guard))
        object.__setattr__(self, "continuation_node_budget",
                           int(self.continuation_node_budget))
        object.__setattr__(self, "continuation_soft_budget_ms",
                           continuation_soft)
        object.__setattr__(self, "continuation_hard_budget_ms",
                           continuation_hard)
        object.__setattr__(self, "enabled", bool(self.enabled))

    @classmethod
    def v1(cls, **overrides):
        values = {
            "name": LEGACY_REACTION_V1,
            "version": LEGACY_REACTION_V1,
            "future_enabled": False,
            "future_mode": "disabled",
            "future_node_budget": 0,
            "future_soft_budget_ms": 0.0,
            "future_hard_budget_ms": 0.0,
            "allow_partial": False,
            "min_partial_coverage": 1.0,
            "require_complete": False,
            "tempo_guard": False,
            "continuation_node_budget": 0,
            "continuation_soft_budget_ms": 0.0,
            "continuation_hard_budget_ms": 0.0,
            "enabled": True,
        }
        values.update(overrides)
        return cls(**values)

    @classmethod
    def v2_online(cls, **overrides):
        values = {
            "name": LEGACY_REACTION_V2,
            "version": LEGACY_REACTION_V2,
            "future_enabled": True,
            "future_mode": "weighted",
            "future_node_budget": 100_000,
            "future_soft_budget_ms": 6.0,
            "future_hard_budget_ms": 10.0,
            "allow_partial": True,
            "min_partial_coverage": 0.90,
            "require_complete": False,
            "tempo_guard": True,
            # Bound the hot path independently from the reaction U2 budget.
            # Soft is diagnostic only; hard triggers one transactional v1
            # fallback in the caller.
            "continuation_node_budget": 512,
            "continuation_soft_budget_ms": 8.0,
            "continuation_hard_budget_ms": 15.0,
            # This is the online production profile. Per-window U2/KONG
            # incompleteness still triggers the transactional v1 fallback.
            "enabled": True,
        }
        values.update(overrides)
        return cls(**values)

    @classmethod
    def v2_offline(cls, **overrides):
        values = {
            "name": LEGACY_REACTION_V2_OFFLINE,
            "version": LEGACY_REACTION_V2_OFFLINE,
            "future_enabled": True,
            "future_mode": "weighted",
            "future_node_budget": 5_000_000,
            "future_soft_budget_ms": 2_000.0,
            "future_hard_budget_ms": 2_000.0,
            "allow_partial": False,
            "min_partial_coverage": 1.0,
            "require_complete": True,
            "tempo_guard": True,
            "continuation_node_budget": 1_000_000,
            "continuation_soft_budget_ms": 2_000.0,
            "continuation_hard_budget_ms": 2_000.0,
            "enabled": True,
        }
        values.update(overrides)
        return cls(**values)

    def _payload(self):
        payload = {
            "name": self.name,
            "version": self.version,
            "future_enabled": self.future_enabled,
            "future_mode": self.future_mode,
            "future_node_budget": self.future_node_budget,
            "future_soft_budget_ms": self.future_soft_budget_ms,
            "future_hard_budget_ms": self.future_hard_budget_ms,
            "allow_partial": self.allow_partial,
            "min_partial_coverage": self.min_partial_coverage,
            "require_complete": self.require_complete,
            "tempo_guard": self.tempo_guard,
            "continuation_node_budget": self.continuation_node_budget,
            "continuation_soft_budget_ms": self.continuation_soft_budget_ms,
            "continuation_hard_budget_ms": self.continuation_hard_budget_ms,
            "enabled": self.enabled,
        }
        if self.hu_discard_delay_min_gain_ratio != 1.0:
            payload.update(hu_discard_delay_min_gain_ratio=self.hu_discard_delay_min_gain_ratio,
                           hu_discard_delay_version="minimum-public-reward-margin-v1")
        return payload

    def as_json(self):
        payload = self._payload()
        payload["fingerprint"] = self.fingerprint
        return payload

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(
            _canonical_json(self._payload()).encode("utf-8")
        ).hexdigest()[:16]

    def future_profile(self) -> LegacyTwoPlyProfile:
        """Build the shared weighted-kernel profile for reaction standing U2."""
        profile_factory = (LegacyTwoPlyProfile.weighted_offline
                           if self.require_complete
                           else LegacyTwoPlyProfile.weighted_online)
        return profile_factory(
            name=self.name,
            version=self.version,
            node_budget=self.future_node_budget,
            time_budget_ms=self.future_hard_budget_ms,
            soft_budget_ms=self.future_soft_budget_ms,
            hard_budget_ms=self.future_hard_budget_ms,
            max_frontier_candidates=0,
            allow_partial=self.allow_partial,
            min_partial_coverage=self.min_partial_coverage,
            shape_guard_enabled=False,
            kernel="rust" if self.require_complete else "auto",
            # Offline labels need the complete Stage-B metrics; a lazy
            # Stage-A winner is a valid online short-circuit but not a
            # complete offline reaction label.
            lazy_child_ukeire=(False if self.require_complete else True),
            require_complete=self.require_complete,
            enabled=self.future_enabled,
        )


@dataclass(frozen=True)
class ReactionTempo:
    pass_draw_index: int
    claim_draw_index: int
    tempo_cost: int

    def as_json(self):
        return {
            "pass_draw_index": self.pass_draw_index,
            "claim_draw_index": self.claim_draw_index,
            "tempo_cost": self.tempo_cost,
        }


class IncompleteReactionFuture(RuntimeError):
    """Raised when an offline v2 label cannot obtain a complete U2 layer."""


def reaction_tempo(hero_seat, pending_owner):
    pass_draw_index = (int(hero_seat) - int(pending_owner)) % 4
    if pass_draw_index not in (1, 2, 3):
        raise ValueError("reaction seats must be distinct")
    claim_draw_index = 4
    return ReactionTempo(
        pass_draw_index=pass_draw_index,
        claim_draw_index=claim_draw_index,
        tempo_cost=claim_draw_index - pass_draw_index,
    )


def significant_progress_detail(before, after, action):
    """Return the v2 reason taxonomy without changing frozen v1 reasons."""
    base = significant_progress(before, after, action)
    if base == "baotou_progress":
        if not before.baotou_ready and after.baotou_ready:
            return "baotou_ready_upgrade"
        return "baotou_ukeire_gain"
    if base == "piao_progress":
        if before.piao_draw_live == 0 and after.piao_draw_live > 0:
            return "piao_ready_upgrade"
        return "piao_mass_gain"
    return base


def future_stage(value: FutureEvaluation):
    if value.future_ukeire_skipped:
        return "stage_a"
    if value.future_ukeire_mean is not None:
        return "stage_b"
    return None


def future_usable(value: FutureEvaluation):
    return bool(value.complete or value.partial_accepted)


def ordinary_u2_gate(pass_future, claim_future, tempo_cost, *,
                     tempo_guard=True):
    """Return ``(accepted, reason, strict_gain)`` for an ordinary v2 claim."""
    if not future_usable(pass_future) or not future_usable(claim_future):
        return False, "u2_incomplete", False
    pass_stage = future_stage(pass_future)
    claim_stage = future_stage(claim_future)
    if pass_stage is None or pass_stage != claim_stage:
        return False, "u2_stage_mismatch", False
    if (pass_future.future_improve_weight is None
            or claim_future.future_improve_weight is None):
        return False, "u2_incomplete", False
    improve_gain = (
        claim_future.future_improve_weight
        - pass_future.future_improve_weight)
    if improve_gain < 0:
        return False, "u2_future_worse", False
    ukeire_gain = None
    if pass_stage == "stage_b":
        if (pass_future.future_ukeire_mean is None
                or claim_future.future_ukeire_mean is None):
            return False, "u2_incomplete", False
        ukeire_gain = (
            claim_future.future_ukeire_mean
            - pass_future.future_ukeire_mean)
        if ukeire_gain < 0:
            return False, "u2_future_worse", False
    strict_gain = improve_gain > 0 or (ukeire_gain is not None
                                       and ukeire_gain > 0)
    if tempo_guard and int(tempo_cost) >= 2 and not strict_gain:
        return False, "tempo_no_strict_future_gain", False
    return True, "u2_not_worse", strict_gain


def evaluate_future_group(roots, locked, visible, profile, *,
                          shape_cost=None):
    """Evaluate one common-locked group and enforce offline fail-loud."""
    values = evaluate_standing_frontier(
        roots, locked, visible, profile.future_profile(),
        shape_cost=shape_cost)
    unusable = [stable_id for stable_id, value in values.items()
                if not future_usable(value)]
    if unusable and profile.require_complete:
        reasons = sorted({values[stable_id].fallback_reason
                          for stable_id in unusable})
        raise IncompleteReactionFuture(
            "reaction U2 incomplete: " + ",".join(
                str(reason) for reason in reasons))
    return values


@dataclass(frozen=True)
class LegacyShapeProgress:
    """Public-information summary of one standing legacy hand."""

    shanten: int
    ukeire_types: int
    ukeire_live: int
    baotou_ready: bool
    baotou_ukeire_types: int | None
    baotou_ukeire_live: int | None
    piao_draw_types: int
    piao_draw_live: int

    def as_json(self):
        return {
            "shanten": self.shanten,
            "ukeire_types": self.ukeire_types,
            "ukeire_live": self.ukeire_live,
            "baotou_ready": self.baotou_ready,
            "baotou_ukeire_types": self.baotou_ukeire_types,
            "baotou_ukeire_live": self.baotou_ukeire_live,
            "piao_draw_types": self.piao_draw_types,
            "piao_draw_live": self.piao_draw_live,
        }


def legacy_progress_diagnostics():
    return dict(_LEGACY_PROGRESS_STATS)


@lru_cache(maxsize=512)
def _cached_legacy_shape_progress(standing, locked, visible,
                                  include_baotou, piao_allowed):
    value, wait_types, ukeire_live = ukeire(standing, locked, visible)
    _LEGACY_PROGRESS_STATS["ukeire_calls"] += 1
    baotou_ready = is_baotou_wait(standing, locked)
    baotou_types = None
    baotou_live = None
    if include_baotou and not baotou_ready:
        wait_tiles, baotou_live = baotou_ukeire(standing, locked, visible)
        baotou_types = len(wait_tiles)
        _LEGACY_PROGRESS_STATS["baotou_ukeire_calls"] += 1

    piao_types = 0
    piao_live = 0
    if piao_allowed and standing[W] > 0 and value == 0:
        remaining = [max(0, 4 - visible[tile]) for tile in range(34)]
        for tile, mass in enumerate(remaining):
            if mass <= 0:
                continue
            drawn = list(standing)
            drawn[tile] += 1
            if not is_win(drawn, locked):
                continue
            drawn[W] -= 1
            if is_baotou(drawn, locked):
                piao_types += 1
                piao_live += mass
        _LEGACY_PROGRESS_STATS["piao_enumerations"] += 1

    return LegacyShapeProgress(
        shanten=value,
        ukeire_types=len(wait_types),
        ukeire_live=ukeire_live,
        baotou_ready=baotou_ready,
        baotou_ukeire_types=baotou_types,
        baotou_ukeire_live=baotou_live,
        piao_draw_types=piao_types,
        piao_draw_live=piao_live,
    )


def legacy_shape_progress(standing, locked, visible, *,
                          include_baotou=False, piao_allowed=False,
                          baotou_kernel_available=None):
    """Evaluate a standing hand with lazy, cached expensive signals."""
    _LEGACY_PROGRESS_STATS["progress_calls"] += 1
    if baotou_kernel_available is None:
        baotou_kernel_available = BAOTOU_UKEIRE_RUST
    key = (
        tuple(standing), int(locked), tuple(visible),
        bool(include_baotou), bool(baotou_kernel_available),
        bool(piao_allowed),
    )
    before = dict(_LEGACY_PROGRESS_STATS)
    result = _cached_legacy_shape_progress(
        key[0], key[1], key[2], key[3] and key[4], key[5])
    if dict(_LEGACY_PROGRESS_STATS) == before:
        _LEGACY_PROGRESS_STATS["cache_hits"] += 1
    return result


def significant_live_gain(before, after, minimum):
    delta = after - before
    if delta < minimum:
        return False
    return before == 0 or after * 2 >= before * 3


def claim_gain_threshold(action):
    return PONG_MIN_ABS_GAIN if action == PONG else CHOW_MIN_ABS_GAIN


def significant_progress(before, after, action):
    minimum = claim_gain_threshold(action)
    if not before.baotou_ready and after.baotou_ready:
        return "baotou_progress"
    if (before.baotou_ukeire_live is not None
            and after.baotou_ukeire_live is not None
            and significant_live_gain(
                before.baotou_ukeire_live,
                after.baotou_ukeire_live, minimum)):
        return "baotou_progress"
    if before.piao_draw_live == 0 and after.piao_draw_live >= 2:
        return "piao_progress"
    if (before.piao_draw_live > 0
            and significant_live_gain(
                before.piao_draw_live, after.piao_draw_live, minimum)):
        return "piao_progress"
    if before.shanten == 0 and after.shanten == 0:
        if (after.ukeire_types - before.ukeire_types >= 2
                and after.ukeire_live >= before.ukeire_live):
            return "wait_expansion"
        if significant_live_gain(
                before.ukeire_live, after.ukeire_live, minimum):
            return "wait_expansion"
    if (before.shanten > 0 and after.shanten > 0
            and significant_live_gain(
                before.ukeire_live, after.ukeire_live, minimum)):
        return "ukeire_expansion"
    return None


def progress_sort_key(progress, shape, action):
    baotou_live = progress.baotou_ukeire_live
    if baotou_live is None:
        baotou_live = 0
    return (
        progress.shanten,
        not progress.baotou_ready,
        -progress.piao_draw_live,
        -baotou_live,
        -progress.ukeire_live,
        -progress.ukeire_types,
        shape,
        action,
    )


def progress_not_worse(left, right):
    left_baotou = left.baotou_ukeire_live or 0
    right_baotou = right.baotou_ukeire_live or 0
    return (
        left.shanten <= right.shanten
        and left.baotou_ready >= right.baotou_ready
        and left.piao_draw_live >= right.piao_draw_live
        and left_baotou >= right_baotou
        and left.ukeire_live >= right.ukeire_live
        and left.ukeire_types >= right.ukeire_types
    )


def evaluate_standing(hand, locked, visible):
    """Return the frozen v1 ``(shanten, live ukeire)`` standing score."""
    return shanten(hand, locked), ukeire(hand, locked, visible)[2]


def post_claim_min_shanten(hand, locked):
    best_shanten = None
    candidates = []
    for discard, count in enumerate(hand):
        if count <= 0:
            continue
        standing = list(hand)
        standing[discard] -= 1
        value = shanten(standing, locked)
        if best_shanten is None or value < best_shanten:
            best_shanten = value
            candidates = [(discard, standing)]
        elif value == best_shanten:
            candidates.append((discard, standing))
    return best_shanten, candidates


def best_standing(candidates, locked, visible, post_hand, *, shape_cost):
    best = None
    for discard, standing in candidates:
        live = ukeire(standing, locked, visible)[2]
        cost = shape_cost(post_hand, discard)
        key = (discard == W, -live, cost, discard)
        if best is None or key < best[0]:
            best = key, live, cost, discard
    return best[1], best[2], best[3]


def best_post_claim_discard(hand, locked, visible, *, shape_cost):
    best_shanten, candidates = post_claim_min_shanten(hand, locked)
    live, cost, discard = best_standing(
        candidates, locked, visible, hand, shape_cost=shape_cost)
    return best_shanten, live, cost, discard


def best_post_claim_state(hand, locked, visible, *, include_baotou,
                          piao_allowed, shape_progress, shape_cost):
    _best_shanten, candidates = post_claim_min_shanten(hand, locked)
    best = None
    for discard, standing in candidates:
        progress = shape_progress(
            standing, locked, visible,
            include_baotou=include_baotou,
            piao_allowed=piao_allowed,
        )
        cost = shape_cost(hand, discard)
        key = progress_sort_key(progress, cost, discard)
        if best is None or key < best[0]:
            best = key, progress, cost, discard, standing
    _, progress, cost, discard, standing = best
    return progress, cost, discard, standing


def choose_reaction_v1(
        game, seat, acts, *, piao_allowed, shape_progress,
        post_claim_min_shanten, best_post_claim_state, evaluate_kong_open,
        kong_public_result, baotou_kernel_available, return_evaluation=True):
    """Evaluate a frozen v1 reaction window without importing ``mj.bot``."""
    _owner, tile = game.pending
    hand = game.hands[seat]
    locked = len(game.melds[seat])
    visible = game.visible_counts(seat)
    pass_progress = None
    pass_shanten = shanten(hand, locked)
    candidates = []
    stats_before = legacy_progress_diagnostics() if return_evaluation else None

    def consider(action, remove):
        nonlocal pass_progress
        post_hand = list(hand)
        for removed_tile, count in remove:
            post_hand[removed_tile] -= count
        best_shanten, post_candidates = post_claim_min_shanten(
            post_hand, locked + 1)
        progress = shape = discard = standing = None
        multiple_discards = len(post_candidates) > 1
        if best_shanten < pass_shanten:
            reason = "shanten_drop"
        elif best_shanten > pass_shanten:
            reason = None
        else:
            if pass_progress is None:
                pass_progress = shape_progress(
                    hand, locked, visible, include_baotou=False,
                    piao_allowed=piao_allowed)
            progress, shape, discard, standing = best_post_claim_state(
                post_hand, locked + 1, visible,
                include_baotou=multiple_discards,
                piao_allowed=piao_allowed)
            reason = significant_progress(pass_progress, progress, action)
            if (reason is None and baotou_kernel_available
                    and pass_progress.baotou_ukeire_live is None):
                pass_progress = shape_progress(
                    hand, locked, visible, include_baotou=True,
                    piao_allowed=piao_allowed)
                if not multiple_discards:
                    progress, shape, discard, standing = \
                        best_post_claim_state(
                            post_hand, locked + 1, visible,
                            include_baotou=True, piao_allowed=piao_allowed)
                reason = significant_progress(pass_progress, progress, action)
        candidates.append({
            "action": action,
            "accepted": reason is not None,
            "reason": reason or "pass",
            "shanten": best_shanten,
            "post_cands": post_candidates,
            "post_hand": post_hand,
            "discard": discard,
            "progress": progress,
            "shape_cost": shape,
            "standing": standing,
        })

    if PONG in acts:
        consider(PONG, [(tile, 2)])
    for action in (CHOW_LOW, CHOW_MID, CHOW_HIGH):
        if action not in acts:
            continue
        position = CHOW_LOW - action
        start = tile - position
        remove = [
            (candidate, 1)
            for candidate in (start, start + 1, start + 2)
            if candidate != tile
        ]
        consider(action, remove)

    kong = None
    if KONG_OPEN in acts:
        if pass_progress is None:
            pass_progress = shape_progress(
                hand, locked, visible, include_baotou=True,
                piao_allowed=piao_allowed)
        kong = evaluate_kong_open(
            game, seat, tile, pass_progress, visible)

    accepted = [candidate for candidate in candidates
                if candidate["accepted"]]
    if accepted:
        best_shanten = min(candidate["shanten"] for candidate in accepted)
        top = [candidate for candidate in accepted
               if candidate["shanten"] == best_shanten]
        need_progress = (
            len(top) > 1
            or (kong is not None and kong["gate_passed"])
            or return_evaluation
        )
        if need_progress:
            for candidate in top:
                if candidate["progress"] is None:
                    (candidate["progress"], candidate["shape_cost"],
                     candidate["discard"], candidate["standing"]) = \
                        best_post_claim_state(
                            candidate["post_hand"], locked + 1, visible,
                            include_baotou=(best_shanten == pass_shanten),
                            piao_allowed=piao_allowed)
    else:
        top = []
    if len(top) == 1:
        best_claim = top[0]
    else:
        best_claim = min(
            top,
            key=lambda candidate: progress_sort_key(
                candidate["progress"], candidate["shape_cost"],
                candidate["action"]),
            default=None,
        )

    if kong is not None and kong["gate_passed"]:
        if best_claim is None:
            selected = kong
        elif kong["progress"].shanten < best_claim["progress"].shanten:
            selected = kong
        elif kong["progress"].shanten > best_claim["progress"].shanten:
            selected = best_claim
        elif progress_not_worse(kong["progress"], best_claim["progress"]):
            selected = kong
        else:
            selected = best_claim
    else:
        selected = best_claim

    action = selected["action"] if selected is not None else PASS
    reason = selected["reason"] if selected is not None else "pass"
    if not return_evaluation:
        return action, None

    if pass_progress is None:
        pass_progress = shape_progress(
            hand, locked, visible, include_baotou=True,
            piao_allowed=piao_allowed)
    elif (pass_progress.baotou_ukeire_live is None
          and baotou_kernel_available):
        pass_progress = shape_progress(
            hand, locked, visible, include_baotou=True,
            piao_allowed=piao_allowed)
    stats_after = legacy_progress_diagnostics()
    evaluation = {
        "version": LEGACY_REACTION_V1,
        "reason": reason,
        "before_progress": pass_progress.as_json(),
        "thresholds": {
            "pong_absolute": PONG_MIN_ABS_GAIN,
            "chow_absolute": CHOW_MIN_ABS_GAIN,
            "ratio": LEGACY_MIN_GAIN_RATIO,
        },
        "candidates": [{
            "action": candidate["action"],
            "accepted": candidate["accepted"],
            "reason": candidate["reason"],
            "discard": candidate["discard"],
            "progress": (
                candidate["progress"].as_json()
                if candidate["progress"] is not None else None),
            "shape_cost": candidate["shape_cost"],
        } for candidate in candidates],
        "kong": kong_public_result(kong) if kong is not None else None,
        "progress_diagnostics": {
            key: stats_after[key] - stats_before[key]
            for key in stats_before
        },
    }
    if selected is None:
        evaluation["after_progress"] = pass_progress.as_json()
    elif selected is not kong:
        evaluation["after_progress"] = selected["progress"].as_json()
    elif kong is not None:
        evaluation["after_progress"] = kong["progress"].as_json()
    return action, evaluation


def _claim_remove(action, tile):
    if action == PONG:
        return ((tile, 2),)
    position = CHOW_LOW - action
    start = tile - position
    return tuple((candidate, 1)
                 for candidate in (start, start + 1, start + 2)
                 if candidate != tile)


def _future_json(value):
    return {
        "complete": value.complete,
        "partial_accepted": value.partial_accepted,
        "stage": future_stage(value),
        "future_improve_weight": value.future_improve_weight,
        "future_ukeire_mean": value.future_ukeire_mean,
        "future_ukeire_types_mean": value.future_ukeire_types_mean,
        "coverage": value.coverage,
        "elapsed_ms": value.elapsed_ms,
        "nodes": value.nodes,
        "fallback_reason": value.fallback_reason,
    }


def _future_attempt_diagnostics(groups):
    """Summarise one transaction without double-counting batched roots."""
    elapsed = 0.0
    coverages = []
    fallback_reasons = set()
    partial_accepted = False
    for values in groups:
        batch = tuple(values.values())
        elapsed += max((value.elapsed_ms or 0.0 for value in batch),
                       default=0.0)
        coverages.extend(value.coverage for value in batch
                         if value.coverage is not None)
        fallback_reasons.update(
            value.fallback_reason for value in batch
            if value.fallback_reason)
        partial_accepted = partial_accepted or any(
            value.partial_accepted for value in batch)
    return {
        "u2_extra_elapsed_ms": elapsed,
        "u2_coverage": min(coverages) if coverages else None,
        "u2_partial_accepted": partial_accepted,
        "u2_attempt_fallback_reasons": sorted(fallback_reasons),
    }


def choose_reaction_v2(
        game, seat, acts, *, profile, piao_allowed, shape_progress,
        post_claim_min_shanten, best_post_claim_state, evaluate_kong_open,
        kong_public_result, baotou_kernel_available, shape_cost,
        post_discard_chain=None, return_evaluation=True):
    """Apply transactional standing U2 as a veto over the frozen v1 gate."""
    v1_action, evaluation = choose_reaction_v1(
        game,
        seat,
        acts,
        piao_allowed=piao_allowed,
        shape_progress=shape_progress,
        post_claim_min_shanten=post_claim_min_shanten,
        best_post_claim_state=best_post_claim_state,
        evaluate_kong_open=evaluate_kong_open,
        kong_public_result=kong_public_result,
        baotou_kernel_available=baotou_kernel_available,
        return_evaluation=True,
    )
    evaluation["reaction_profile"] = profile.as_json()
    evaluation["v1_action"] = v1_action
    if not profile.enabled or not profile.future_enabled:
        evaluation["u2_fallback_reason"] = "profile_disabled"
        return (v1_action, evaluation) if return_evaluation else (v1_action, None)

    owner, tile = game.pending
    tempo = reaction_tempo(seat, owner)
    evaluation.update(tempo.as_json())
    hand = game.hands[seat]
    locked = len(game.melds[seat])
    visible = game.visible_counts(seat)
    before = shape_progress(
        hand, locked, visible, include_baotou=True,
        piao_allowed=piao_allowed)
    rows = {row["action"]: row for row in evaluation["candidates"]}
    ordinary = []
    retained = []

    for action, row in rows.items():
        if not row["accepted"]:
            continue
        post_hand = list(hand)
        for removed_tile, count in _claim_remove(action, tile):
            post_hand[removed_tile] -= count
        best_shanten, standing_candidates = post_claim_min_shanten(
            post_hand, locked + 1)
        progress, current_shape, current_discard, current_standing = \
            best_post_claim_state(
                post_hand, locked + 1, visible,
                include_baotou=(best_shanten == before.shanten),
                piao_allowed=piao_allowed)
        detail = ("shanten_drop" if best_shanten < before.shanten else
                  significant_progress_detail(before, progress, action))
        record = {
            "action": action,
            "row": row,
            "post_hand": post_hand,
            "shanten": best_shanten,
            "standing_candidates": standing_candidates,
            "progress": progress,
            "shape": current_shape,
            "discard": current_discard,
            "standing": current_standing,
            "detail": detail,
            "future": None,
        }
        row["v2_reason"] = detail
        if detail == "shanten_drop":
            row["u2_exempt"] = "shanten_drop"
            retained.append(record)
        elif detail in {
                "baotou_ready_upgrade", "baotou_ukeire_gain",
                "piao_ready_upgrade", "piao_mass_gain"}:
            row["u2_exempt"] = "special_progress"
            row["u2_special_metric_missing"] = True
            retained.append(record)
        elif detail in {"wait_expansion", "ukeire_expansion"}:
            ordinary.append(record)
        else:
            row["v2_accepted"] = False
            row["v2_reason"] = "v1_gate_missing"

    evaluation.update({
        "u2_eligible": bool(ordinary),
        "u2_complete_or_safe_partial": False,
        "u2_extra_elapsed_ms": 0.0,
        "u2_coverage": None,
        "u2_partial_accepted": False,
        "u2_attempt_fallback_reasons": [],
    })
    if ordinary:
        pass_values = evaluate_future_group((StandingRoot(
            "pass", tuple(hand), before.shanten),), locked, visible, profile,
            shape_cost=shape_cost)
        pass_future = pass_values["pass"]
        future_groups = [pass_values]
        all_values = [pass_future]
        for record in ordinary:
            roots = tuple(StandingRoot(
                f"{record['action']}:{discard}",
                tuple(standing),
                record["shanten"],
            ) for discard, standing in record["standing_candidates"])
            values = evaluate_future_group(
                roots, locked + 1, visible, profile, shape_cost=shape_cost)
            record["future_values"] = values
            future_groups.append(values)
            all_values.extend(values.values())

        evaluation.update(_future_attempt_diagnostics(future_groups))

        stages = {future_stage(value) for value in all_values
                  if future_usable(value)}
        unusable = [value for value in all_values if not future_usable(value)]
        if unusable or None in stages or len(stages) != 1:
            if profile.require_complete:
                raise IncompleteReactionFuture(
                    "reaction U2 candidates do not share one complete stage")
            evaluation["u2_fallback_reason"] = (
                "u2_incomplete" if unusable else "u2_stage_mismatch")
            return ((v1_action, evaluation) if return_evaluation
                    else (v1_action, None))

        evaluation["u2_complete_or_safe_partial"] = True
        evaluation["pass_future"] = _future_json(pass_future)
        for record in ordinary:
            candidates = []
            for discard, standing in record["standing_candidates"]:
                stable_id = f"{record['action']}:{discard}"
                value = record["future_values"][stable_id]
                progress = shape_progress(
                    standing, locked + 1, visible,
                    include_baotou=True, piao_allowed=piao_allowed)
                cost = shape_cost(record["post_hand"], discard)
                key = (
                    -(value.future_improve_weight or 0),
                    -(value.future_ukeire_mean or 0.0),
                    -(value.future_ukeire_types_mean or 0.0),
                    progress_sort_key(progress, cost, discard),
                )
                candidates.append((key, discard, standing, progress, cost,
                                   value))
            (_key, record["discard"], record["standing"],
             record["progress"], record["shape"], record["future"]) = min(
                candidates, key=lambda candidate: candidate[0])
            accepted, reason, strict_gain = ordinary_u2_gate(
                pass_future, record["future"], tempo.tempo_cost,
                tempo_guard=profile.tempo_guard)
            record["row"].update({
                "v2_accepted": accepted,
                "v2_reason": reason,
                "discard": record["discard"],
                "future": _future_json(record["future"]),
                "strict_future_gain": strict_gain,
                **tempo.as_json(),
            })
            if accepted:
                retained.append(record)

    retained_actions = {record["action"] for record in retained}
    pong_record = next((record for record in retained
                        if record["action"] == PONG), None)
    kong = evaluation.get("kong")
    slow_path_action = None
    if (pong_record is not None and kong is not None
            and kong.get("gate_passed")):
        kong_progress = LegacyShapeProgress(**kong["post_kong_progress"])
        slow_path = {
            "entered": False,
            "q_pong": None,
            "q_kong": kong.get("total_reward_ev"),
            "delta": None,
            "nodes": 0,
            "elapsed_ms": 0.0,
            "fallback_reason": None,
        }
        if kong_progress.shanten < pong_record["progress"].shanten:
            slow_path_action = KONG_OPEN
            slow_path["fallback_reason"] = "kong_shanten_better"
        elif kong_progress.shanten > pong_record["progress"].shanten:
            slow_path_action = PONG
            slow_path["fallback_reason"] = "pong_shanten_better"
        elif not progress_not_worse_v2(
                kong_progress, pong_record["progress"]):
            slow_path_action = PONG
            slow_path["fallback_reason"] = "kong_progress_worse"
        else:
            slow_path["entered"] = True
            if post_discard_chain is None:
                pong_score = {"complete": False,
                              "fallback_reason": "score_callback_missing",
                              "continuation_nodes": 0,
                              "elapsed_ms": 0.0}
            else:
                chain, chain_piao = post_discard_chain(
                    game, seat, pong_record["discard"],
                    pong_record["standing"], locked + 1)
                remaining = [max(0, 4 - count) for count in visible]
                pong_score = public_score_continuation(
                    game, seat, pong_record["standing"], locked + 1,
                    chain, chain_piao, False, remaining,
                    first_draw_delay=4,
                    post_discard_chain=post_discard_chain,
                    node_budget=profile.continuation_node_budget,
                    soft_budget_ms=profile.continuation_soft_budget_ms,
                    hard_budget_ms=profile.continuation_hard_budget_ms,
                )
            slow_path["nodes"] = pong_score.get("continuation_nodes", 0)
            slow_path["elapsed_ms"] = pong_score.get("elapsed_ms", 0.0)
            kong_complete = kong.get("continuation_complete", True)
            if not pong_score.get("complete") or not kong_complete:
                if profile.require_complete:
                    raise IncompleteReactionFuture(
                        "PONG/KONG score continuation incomplete")
                slow_path_action = PONG
                slow_path["fallback_reason"] = "same_unit_incomplete"
            else:
                q_pong = pong_score["total_reward_ev"]
                q_kong = kong.get("total_reward_ev", 0.0)
                delta = q_kong - q_pong
                slow_path.update({
                    "q_pong": q_pong,
                    "q_kong": q_kong,
                    "delta": delta,
                })
                slow_path_action, comparison_reason = \
                    select_pong_kong_same_unit(q_pong, q_kong)
                if slow_path_action == PONG:
                    slow_path["fallback_reason"] = comparison_reason
        evaluation["pong_kong_slow_path"] = slow_path

    if slow_path_action is not None:
        selected_action = slow_path_action
    elif v1_action in retained_actions:
        selected_action = v1_action
    elif retained:
        selected_action = min(
            retained,
            key=lambda record: (
                record["shanten"],
                progress_sort_key(
                    record["progress"], record["shape"], record["action"]),
            ),
        )["action"]
    elif v1_action == KONG_OPEN:
        # KONG v2 is added by the dedicated KONG stages; do not let ordinary
        # reaction U2 pre-empt its frozen hard-gate result here.
        selected_action = v1_action
    else:
        selected_action = PASS

    selected_row = rows.get(selected_action)
    evaluation["reason"] = (
        selected_row.get("v2_reason", selected_row["reason"])
        if selected_row is not None else "pass")
    evaluation["selected"] = selected_action
    evaluation["u2_fallback_reason"] = None
    return ((selected_action, evaluation) if return_evaluation
            else (selected_action, None))


__all__ = [
    "LEGACY_REACTION_V1",
    "LEGACY_REACTION_V2",
    "LEGACY_REACTION_V2_OFFLINE",
    "CHOW_MIN_ABS_GAIN",
    "LEGACY_MIN_GAIN_RATIO",
    "PONG_MIN_ABS_GAIN",
    "IncompleteReactionFuture",
    "LegacyReactionProfile",
    "LegacyShapeProgress",
    "ReactionTempo",
    "best_post_claim_discard",
    "best_post_claim_state",
    "best_standing",
    "choose_reaction_v1",
    "choose_reaction_v2",
    "evaluate_standing",
    "evaluate_future_group",
    "future_stage",
    "future_usable",
    "legacy_progress_diagnostics",
    "legacy_shape_progress",
    "post_claim_min_shanten",
    "ordinary_u2_gate",
    "reaction_tempo",
    "progress_not_worse",
    "progress_sort_key",
    "significant_progress",
    "significant_progress_detail",
]
