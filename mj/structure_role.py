"""Public-information marginal structure roles for legacyV2 roots.

The role describes what is lost by discarding one tile from a standing hand.
It is deliberately a diagnostic/admission signal.  It is not a value bonus,
an opponent discard probability, or a hidden-wall estimate.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

MARGINAL_STRUCTURE_ROLE_VERSION = "marginal-structure-role-v1"


def _counts(values: Sequence[int], name: str) -> tuple[int, ...]:
    try:
        result = tuple(int(value) for value in values)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name}_invalid") from exc
    if len(result) != 34 or any(value < 0 or value > 4 for value in result):
        raise ValueError(f"{name}_invalid")
    return result


def _same_suit(left: int, right: int) -> bool:
    return left < 27 and right < 27 and left // 9 == right // 9


def _taatsu_routes(hand: tuple[int, ...], tile: int) -> tuple[tuple[int, int], ...]:
    """Return distinct two-tile sequence routes involving ``tile``."""
    if tile >= 27:
        return ()
    routes = []
    for gap in (1, 2):
        for other in (tile - gap, tile + gap):
            if (_same_suit(tile, other) and hand[tile] > 0 and
                    hand[other] > 0):
                routes.append((min(tile, other), max(tile, other)))
    return tuple(sorted(set(routes)))


def _meld_routes(hand: tuple[int, ...], tile: int) -> tuple[tuple[str, int], ...]:
    """Return distinct complete triplet/sequence routes involving ``tile``."""
    routes: list[tuple[str, int]] = []
    if hand[tile] >= 3:
        routes.append(("triplet", tile))
    if tile < 27:
        lo = tile - tile % 9
        for start in range(max(lo, tile - 2), min(lo + 6, tile) + 1):
            if all(hand[start + offset] > 0 for offset in range(3)):
                routes.append(("sequence", start))
    return tuple(sorted(set(routes)))


def _connectivity_tiles(tile: int) -> tuple[int, ...]:
    """Potential useful neighboring ranks for a singleton tile."""
    if tile >= 27:
        return ()
    lo = tile - tile % 9
    result = []
    for gap in (1, 2):
        for other in (tile - gap, tile + gap):
            if lo <= other < lo + 9:
                result.append(other)
    return tuple(sorted(set(result)))


@dataclass(frozen=True)
class MarginalStructureRole:
    """Versioned role of one root discard, computed from public material."""

    version: str
    lost_pair_option: bool
    same_tile_unseen: int
    lost_taatsu_option: bool
    lost_completed_meld: bool
    completed_meld_redundancy: bool
    alternative_route_count_before: int
    alternative_route_count_after: int
    singleton_connectivity: int
    singleton_live_connectivity: int
    critical_compound_break: bool
    loss_tier: str

    def as_json(self) -> dict:
        return {
            "marginal_role_version": self.version,
            "marginal_loss_tier": self.loss_tier,
            "lost_pair_option": self.lost_pair_option,
            "same_tile_unseen": self.same_tile_unseen,
            "lost_taatsu_option": self.lost_taatsu_option,
            "lost_completed_meld": self.lost_completed_meld,
            "completed_meld_redundancy": self.completed_meld_redundancy,
            "alternative_route_count_before": self.alternative_route_count_before,
            "alternative_route_count_after": self.alternative_route_count_after,
            "singleton_connectivity": self.singleton_connectivity,
            "singleton_live_connectivity": self.singleton_live_connectivity,
            "critical_compound_break": self.critical_compound_break,
        }


def marginal_structure_role(
        standing_hand: Sequence[int], discard: int, visible: Sequence[int] | None = None,
        *, locked: int = 0) -> MarginalStructureRole:
    """Compute the public-information marginal role for one discard.

    ``standing_hand`` is the complete concealed hand immediately before the
    discard (normally the 14-tile post-draw hand); ``visible`` must include
    that hand when supplied, matching :func:`mj.shanten.ukeire`.  The helper
    never reads opponent concealed hands or wall order.
    """
    hand = _counts(standing_hand, "standing_hand")
    if not 0 <= int(discard) < 34 or hand[int(discard)] <= 0:
        raise ValueError("discard_invalid")
    tile = int(discard)
    vis = hand if visible is None else _counts(visible, "visible")
    after = list(hand)
    after[tile] -= 1
    after_tuple = tuple(after)

    pair_before = hand[tile] >= 2
    pair_after = after_tuple[tile] >= 2
    taatsu_before = _taatsu_routes(hand, tile)
    taatsu_after = _taatsu_routes(after_tuple, tile)
    meld_before = _meld_routes(hand, tile)
    meld_after = _meld_routes(after_tuple, tile)

    lost_pair = pair_before and not pair_after
    lost_taatsu = len(taatsu_after) < len(taatsu_before)
    lost_meld = len(meld_after) < len(meld_before)
    # A complete route involving the tile remains available after removing
    # one copy.  This is the 7899s -> 789s anti-overfit case.
    redundant_meld = bool(meld_before and meld_after)
    route_before = int(pair_before) + len(taatsu_before) + len(meld_before)
    route_after = int(pair_after) + len(taatsu_after) + len(meld_after)

    connectivity_tiles = _connectivity_tiles(tile)
    live_connectivity = sum(max(0, 4 - vis[other])
                            for other in connectivity_tiles)
    singleton_connectivity = len(connectivity_tiles)
    critical = bool(
        route_before >= 2 and route_after < route_before and
        not redundant_meld and (lost_pair or lost_taatsu or lost_meld)
    )
    if critical:
        loss_tier = "critical_compound"
    elif redundant_meld:
        loss_tier = "redundant_completed_meld"
    elif lost_meld:
        loss_tier = "completed_meld"
    elif lost_pair and lost_taatsu:
        loss_tier = "pair_and_taatsu"
    elif lost_pair:
        loss_tier = "pair"
    elif lost_taatsu:
        loss_tier = "taatsu"
    elif live_connectivity:
        loss_tier = "connected_singleton"
    else:
        loss_tier = "isolated_singleton"

    return MarginalStructureRole(
        version=MARGINAL_STRUCTURE_ROLE_VERSION,
        lost_pair_option=lost_pair,
        same_tile_unseen=max(0, 4 - vis[tile]),
        lost_taatsu_option=lost_taatsu,
        lost_completed_meld=lost_meld,
        completed_meld_redundancy=redundant_meld,
        alternative_route_count_before=route_before,
        alternative_route_count_after=route_after,
        singleton_connectivity=singleton_connectivity,
        singleton_live_connectivity=live_connectivity,
        critical_compound_break=critical,
        loss_tier=loss_tier,
    )


__all__ = [
    "MARGINAL_STRUCTURE_ROLE_VERSION",
    "MarginalStructureRole",
    "marginal_structure_role",
]
