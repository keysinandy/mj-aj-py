"""Cheap public-information intent features for legacyV2 discard roots.

The module is deliberately a deterministic O(34) feature extractor, not a
future-search or hand-scoring implementation.  It never receives a Game
object, opponent concealed hands, or wall order.
"""

from __future__ import annotations

from dataclasses import dataclass

from .shanten import chiitoi_shanten, chiitoi_shanten_components
from .tiles import W


INTENT_NONE = "NONE"
INTENT_WEAK = "WEAK"
INTENT_MEDIUM = "MEDIUM"
INTENT_STRONG = "STRONG"

CHIITOI = "CHIITOI"
LUXURY_CHIITOI = "LUXURY_CHIITOI"
WHITE_RICH = "WHITE_RICH"


@dataclass(frozen=True)
class BigHandIntent:
    kinds: tuple[str, ...]
    strength: str
    chiitoi_shanten: int
    pair_units: int
    natural_pairs: int
    luxury_groups: int
    luxury_upgrade_tiles: tuple[int, ...]
    luxury_upgrade_live: int
    wild_count: int
    wild_live: int
    protected_tiles: tuple[int, ...]
    reasons: tuple[str, ...]
    locked: int = 0
    live_wall: int | None = None
    max_opponent_melds: int | None = None

    def as_json(self):
        return {
            "intent_kinds": list(self.kinds),
            "intent_strength": self.strength,
            "chiitoi_shanten": self.chiitoi_shanten,
            "pair_units": self.pair_units,
            "natural_pairs": self.natural_pairs,
            "luxury_groups": self.luxury_groups,
            "luxury_upgrade_tiles": list(self.luxury_upgrade_tiles),
            "luxury_upgrade_live": self.luxury_upgrade_live,
            "wild_count": self.wild_count,
            "wild_live": self.wild_live,
            "protected_tiles": list(self.protected_tiles),
            "intent_reasons": list(self.reasons),
            "locked": self.locked,
            "live_wall": self.live_wall,
            "max_opponent_melds": self.max_opponent_melds,
        }


def _validated_hand(hand):
    try:
        hand = tuple(int(value) for value in hand)
    except (TypeError, ValueError) as exc:
        raise ValueError("hand must be integer-like") from exc
    if len(hand) != 34 or any(value < 0 or value > 4 for value in hand):
        raise ValueError("hand must contain 34 counts in range 0..4")
    return hand


def _validated_visible(visible, hand):
    try:
        visible = tuple(int(value) for value in visible)
    except (TypeError, ValueError) as exc:
        raise ValueError("visible must be integer-like") from exc
    if (len(visible) != 34 or
            any(value < 0 or value > 4 for value in visible) or
            any(shown < own for shown, own in zip(visible, hand))):
        raise ValueError("visible must include hand and contain counts in 0..4")
    return visible


def _evaluate_validated(hand, locked, visible, live_wall,
                        max_opponent_melds, features=None):
    """Feature calculation after one-time public material validation."""
    if features is None:
        chiitoi = chiitoi_shanten(hand, locked)
        natural_pairs = sum(count // 2 for count in hand[:33])
        natural_singles = sum(count % 2 for count in hand[:33])
        luxury_groups = sum(count == 4 for count in hand[:33])
        luxury_upgrade_tiles = tuple(
            tile for tile, count in enumerate(hand[:33])
            if count == 3 and 4 - visible[tile] > 0
        )
        luxury_upgrade_live = sum(4 - visible[tile]
                                  for tile in luxury_upgrade_tiles)
        protected = {tile for tile, count in enumerate(hand[:33])
                     if count >= 2}
    else:
        (chiitoi, natural_pairs, natural_singles, luxury_groups,
         luxury_upgrade_tiles, luxury_upgrade_live,
         protected_tiles, wild_count) = features
        protected = set(protected_tiles)
    if features is None:
        wild_count = hand[W]
    wild_to_singles = min(natural_singles, wild_count)
    remaining_wilds = wild_count - wild_to_singles
    pair_units = (natural_pairs + wild_to_singles + remaining_wilds // 2)
    wild_live = max(0, 4 - visible[W])

    if (not (locked == 0 and chiitoi <= 2) and
            not (wild_count >= 2 and natural_pairs >= 3)):
        return BigHandIntent(
            kinds=(), strength=INTENT_NONE,
            chiitoi_shanten=chiitoi, pair_units=pair_units,
            natural_pairs=natural_pairs, luxury_groups=luxury_groups,
            luxury_upgrade_tiles=luxury_upgrade_tiles,
            luxury_upgrade_live=luxury_upgrade_live,
            wild_count=wild_count, wild_live=wild_live,
            protected_tiles=(), reasons=("no_big_hand_pattern",),
            locked=locked, live_wall=live_wall,
            max_opponent_melds=max_opponent_melds,
        )

    kinds = []
    reasons = []
    if locked == 0 and chiitoi <= 2:
        kinds.append(CHIITOI)
        reasons.append("chiitoi_distance_at_most_2")
    if (locked == 0 and chiitoi <= 2 and
            (luxury_groups > 0 or luxury_upgrade_live > 0)):
        kinds.append(LUXURY_CHIITOI)
        if luxury_groups:
            reasons.append("natural_luxury_group")
        if luxury_upgrade_live:
            reasons.append("live_natural_luxury_upgrade")
    if (wild_count >= 2 and
            (natural_pairs >= 3 or (locked == 0 and chiitoi <= 2))):
        kinds.append(WHITE_RICH)
        reasons.append("multiple_wilds_with_pair_structure")
        if wild_count:
            protected.add(W)

    strong_luxury = (
        LUXURY_CHIITOI in kinds and chiitoi <= 1 and
        (luxury_groups > 0 or luxury_upgrade_live > 0)
    )
    strong_white = (
        WHITE_RICH in kinds and wild_count >= 2 and chiitoi <= 1 and
        pair_units >= 4
    )
    medium = (
        (CHIITOI in kinds and chiitoi <= 1) or
        (WHITE_RICH in kinds and wild_count >= 2 and pair_units >= 3)
    )
    if strong_luxury or strong_white:
        strength = INTENT_STRONG
        reasons.append("strong_intent_gate")
    elif medium:
        strength = INTENT_MEDIUM
        reasons.append("medium_intent_gate")
    elif kinds:
        strength = INTENT_WEAK
    else:
        strength = INTENT_NONE

    return BigHandIntent(
        kinds=tuple(kinds), strength=strength,
        chiitoi_shanten=chiitoi, pair_units=pair_units,
        natural_pairs=natural_pairs, luxury_groups=luxury_groups,
        luxury_upgrade_tiles=luxury_upgrade_tiles,
        luxury_upgrade_live=luxury_upgrade_live,
        wild_count=wild_count, wild_live=wild_live,
        protected_tiles=tuple(sorted(protected)),
        reasons=tuple(reasons), locked=locked, live_wall=live_wall,
        max_opponent_melds=max_opponent_melds,
    )


def evaluate_big_hand_intents(hands, locked=0, visible=None, *, live_wall=None,
                              max_opponent_melds=None):
    """Evaluate several roots while validating shared public state once."""
    try:
        locked = int(locked)
    except (TypeError, ValueError) as exc:
        raise ValueError("locked must be integer-like") from exc
    if locked < 0 or locked > 4:
        raise ValueError("locked must be in range 0..4")
    if live_wall is not None:
        live_wall = int(live_wall)
        if live_wall < 0:
            raise ValueError("live_wall must be non-negative")
    if max_opponent_melds is not None:
        max_opponent_melds = int(max_opponent_melds)
        if max_opponent_melds < 0:
            raise ValueError("max_opponent_melds must be non-negative")
    try:
        hands = tuple(_validated_hand(hand) for hand in hands)
    except TypeError as exc:
        raise ValueError("hands must be an iterable of 34-count vectors") from exc
    if visible is None:
        visible = tuple(max((hand[tile] for hand in hands), default=0)
                        for tile in range(34))
    else:
        # Multiple post-discard hands may differ from visible only by one
        # concealed tile, so validate against the largest per-tile root count.
        maximum_hand = tuple(max((hand[tile] for hand in hands), default=0)
                             for tile in range(34))
        visible = _validated_visible(visible, maximum_hand)
    return tuple(_evaluate_validated(
        hand, locked, visible, live_wall, max_opponent_melds)
        for hand in hands)


def evaluate_big_hand_discard_intents(base_hand, discards, locked=0,
                                      visible=None, *, live_wall=None,
                                      max_opponent_melds=None):
    """Batch the roots formed by discarding one distinct tile from one hand.

    Shared natural-pair/single/luxury counts are computed once.  Each root
    applies the one-tile delta and calls the same chiitoi component rule used
    by :func:`mj.shanten.chiitoi_shanten`.
    """
    base = _validated_hand(base_hand)
    try:
        locked = int(locked)
        discards = tuple(int(tile) for tile in discards)
    except (TypeError, ValueError) as exc:
        raise ValueError("locked and discards must be integer-like") from exc
    if locked < 0 or locked > 4:
        raise ValueError("locked must be in range 0..4")
    if len(set(discards)) != len(discards):
        raise ValueError("discard roots must use distinct tiles")
    if any(not 0 <= tile < 34 or base[tile] <= 0 for tile in discards):
        raise ValueError("discard tile must be present in base hand")
    if visible is None:
        visible = base
    visible = _validated_visible(visible, base)
    if live_wall is not None:
        live_wall = int(live_wall)
        if live_wall < 0:
            raise ValueError("live_wall must be non-negative")
    if max_opponent_melds is not None:
        max_opponent_melds = int(max_opponent_melds)
        if max_opponent_melds < 0:
            raise ValueError("max_opponent_melds must be non-negative")

    natural_pairs = 0
    natural_singles = 0
    luxury_groups = 0
    upgrades = []
    protected = set()
    for tile, count in enumerate(base[:33]):
        natural_pairs += count // 2
        natural_singles += count % 2
        luxury_groups += int(count == 4)
        if count == 3 and 4 - visible[tile] > 0:
            upgrades.append(tile)
        if count >= 2:
            protected.add(tile)
    upgrades = tuple(upgrades)
    upgrade_live = sum(4 - visible[tile] for tile in upgrades)
    results = []
    for tile in discards:
        old_count = base[tile]
        pairs = natural_pairs
        singles = natural_singles
        groups = luxury_groups
        root_upgrades = upgrades
        root_upgrade_live = upgrade_live
        root_protected = protected
        if tile < 33:
            remaining_count = old_count - 1
            pairs += remaining_count // 2 - old_count // 2
            singles += remaining_count % 2 - old_count % 2
            if old_count == 4:
                groups -= 1
            if old_count == 3:
                root_upgrades = tuple(value for value in upgrades
                                      if value != tile)
                if tile in upgrades:
                    root_upgrade_live -= 4 - visible[tile]
            if old_count == 2:
                root_protected = protected - {tile}
        wilds = base[W] - int(tile == W)
        chiitoi = chiitoi_shanten_components(
            pairs, singles, wilds, locked)
        features = (
            chiitoi, pairs, singles, groups, root_upgrades,
            root_upgrade_live, root_protected, wilds,
        )
        results.append(_evaluate_validated(
            base, locked, visible, live_wall,
            max_opponent_melds, features=features))
    return tuple(results)


def evaluate_big_hand_intent(hand, locked=0, visible=None, *, live_wall=None,
                             max_opponent_melds=None) -> BigHandIntent:
    """Build deterministic BigHandIntent from one public standing hand.

    ``visible`` follows the evaluator convention and includes the hero's hand.
    Natural luxury only examines suit/honor tile identities 0..32; 财神 can
    provide pair resources but never counts as a natural luxury tile.
    """
    return evaluate_big_hand_intents(
        (hand,), locked, visible, live_wall=live_wall,
        max_opponent_melds=max_opponent_melds)[0]


__all__ = [
    "BigHandIntent", "evaluate_big_hand_intent", "evaluate_big_hand_intents",
    "evaluate_big_hand_discard_intents",
    "CHIITOI",
    "LUXURY_CHIITOI", "WHITE_RICH", "INTENT_NONE", "INTENT_WEAK",
    "INTENT_MEDIUM", "INTENT_STRONG",
]
