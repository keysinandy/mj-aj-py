"""Deterministic, non-overlapping standing-hand shape quality.

This metric describes the natural tiles left in a standing hand.  It is kept
separate from ``mj.bot._discard_shape_cost``, which describes local damage
caused by one discard.  White dragons are wildcards in Hangzhou rules and are
excluded here: their substitution value belongs to shanten/ukeire, not to a
fabricated virtual taatsu.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Sequence

from .tiles import W


SHAPE_QUALITY_VERSION = "standing-shape-v1"
_MAX_TILES = 14


@dataclass(frozen=True, slots=True)
class StandingShapeQuality:
    """Versioned signature for one natural standing hand.

    ``encoded`` packs the lexicographic comparison fields into eight
    four-bit digits.  It is deterministic and collision-free for legal
    14-tile hands; callers should compare ``sort_key`` or ``encoded`` and
    retain the named fields for explanations.
    """

    complete_meld_count: int
    taatsu_count: int
    ryanmen_count: int
    central_kanchan_count: int
    edge_kanchan_count: int
    penchan_count: int
    pair_units: int
    isolated_count: int
    encoded: int
    version: str = SHAPE_QUALITY_VERSION

    @property
    def sort_key(self) -> tuple[int, ...]:
        return (
            self.complete_meld_count,
            self.taatsu_count,
            self.ryanmen_count,
            self.central_kanchan_count,
            self.edge_kanchan_count,
            self.penchan_count,
            self.pair_units,
            -self.isolated_count,
        )

    @property
    def signature(self) -> tuple[int, ...]:
        return (
            self.complete_meld_count,
            self.taatsu_count,
            self.ryanmen_count,
            self.central_kanchan_count,
            self.edge_kanchan_count,
            self.penchan_count,
            self.pair_units,
            self.isolated_count,
        )

    def as_json(self) -> dict:
        return {
            "version": self.version,
            "complete_meld_count": self.complete_meld_count,
            "taatsu_count": self.taatsu_count,
            "ryanmen_count": self.ryanmen_count,
            "central_kanchan_count": self.central_kanchan_count,
            "edge_kanchan_count": self.edge_kanchan_count,
            "penchan_count": self.penchan_count,
            "pair_units": self.pair_units,
            "isolated_count": self.isolated_count,
            "signature": list(self.signature),
            "quality": self.encoded,
            "encoded": self.encoded,
        }


# Internal tuple order is deliberately the same as sort_key, with isolated
# tiles negated so ordinary tuple comparison selects fewer isolated tiles.
_ZERO = (0, 0, 0, 0, 0, 0, 0, 0)


def _plus(left: tuple[int, ...], right: tuple[int, ...]) -> tuple[int, ...]:
    return tuple(a + b for a, b in zip(left, right))


def _tile_unit_quality(group: str) -> tuple[int, ...]:
    if group == "meld":
        return (1, 0, 0, 0, 0, 0, 0, 0)
    if group == "pair":
        return (0, 0, 0, 0, 0, 0, 1, 0)
    if group == "isolated":
        return (0, 0, 0, 0, 0, 0, 0, -1)
    raise ValueError(f"unknown shape unit: {group}")


def _taatsu_unit_quality(rank: int, gap: int) -> tuple[int, ...]:
    if gap == 1:
        if rank == 0 or rank == 7:  # 12 / 89 are penchan
            slot = 5
        else:
            slot = 2
    elif gap == 2:
        if rank == 0 or rank == 6:  # 13 / 79 are edge kanchan
            slot = 4
        else:
            slot = 3
    else:
        raise ValueError("suited taatsu gap must be one or two")
    values = [0] * 8
    values[1] = 1
    values[slot] = 1
    return tuple(values)


@lru_cache(maxsize=1 << 15)
def _best_suit(counts: tuple[int, ...]) -> tuple[int, ...]:
    """Best exact partition of one nine-rank suit, without tile reuse."""
    try:
        rank = next(i for i, count in enumerate(counts) if count)
    except StopIteration:
        return _ZERO

    best: tuple[int, ...] | None = None

    def consider(consumed: tuple[int, ...], unit: tuple[int, ...]) -> None:
        nonlocal best
        rest = list(counts)
        for index, amount in enumerate(consumed):
            rest[index] -= amount
        candidate = _plus(unit, _best_suit(tuple(rest)))
        if best is None or candidate > best:
            best = candidate

    # All units consume the first remaining rank, so every decomposition is
    # explored while the tile copies remain disjoint.
    if counts[rank] >= 3:
        consumed = [0] * 9
        consumed[rank] = 3
        consider(tuple(consumed), _tile_unit_quality("meld"))
    if rank <= 6 and counts[rank + 1] and counts[rank + 2]:
        consumed = [0] * 9
        consumed[rank:rank + 3] = [1, 1, 1]
        consider(tuple(consumed), _tile_unit_quality("meld"))
    if counts[rank] >= 2:
        consumed = [0] * 9
        consumed[rank] = 2
        consider(tuple(consumed), _tile_unit_quality("pair"))
    for gap in (1, 2):
        other = rank + gap
        if other < 9 and counts[other]:
            consumed = [0] * 9
            consumed[rank] = consumed[other] = 1
            consider(tuple(consumed), _taatsu_unit_quality(rank, gap))
    consumed = [0] * 9
    consumed[rank] = 1
    consider(tuple(consumed), _tile_unit_quality("isolated"))
    assert best is not None
    return best


@lru_cache(maxsize=1 << 12)
def _best_honor(count: int) -> tuple[int, ...]:
    if count <= 0:
        return _ZERO
    choices = []
    if count >= 3:
        choices.append(_plus(_tile_unit_quality("meld"),
                             _best_honor(count - 3)))
    if count >= 2:
        choices.append(_plus(_tile_unit_quality("pair"),
                             _best_honor(count - 2)))
    choices.append(_plus(_tile_unit_quality("isolated"),
                         _best_honor(count - 1)))
    return max(choices)


def standing_shape_quality(counts: Sequence[int], *, locked: int = 0,
                           wildcard: int = W) -> StandingShapeQuality:
    """Return standing shape for a 34-count hand.

    ``locked`` is accepted to make the call contract explicit at decision
    sites; locked melds are not in the concealed standing counts and do not
    change this public tile-count feature.  The wildcard count is validated
    then omitted from the natural decomposition.
    """
    if len(counts) != 34:
        raise ValueError("standing shape requires 34 tile counts")
    if not 0 <= int(locked) <= 4:
        raise ValueError("locked meld count must be between 0 and 4")
    if not 0 <= int(wildcard) < 34:
        raise ValueError("wildcard tile must be in range 0..33")
    normalized = tuple(int(value) for value in counts)
    if any(value < 0 or value > 4 for value in normalized):
        raise ValueError("tile counts must be between 0 and 4")

    natural = list(normalized)
    natural[int(wildcard)] = 0
    quality = _ZERO
    for suit_start in (0, 9, 18):
        quality = _plus(
            quality, _best_suit(tuple(natural[suit_start:suit_start + 9])))
    for tile in range(27, 34):
        if tile != int(wildcard):
            quality = _plus(quality, _best_honor(natural[tile]))

    complete_melds, taatsu, ryanmen, central, edge, penchan, pairs, neg_iso = quality
    isolated = -neg_iso
    encoded_fields = (complete_melds, taatsu, ryanmen, central, edge,
                      penchan, pairs, _MAX_TILES - isolated)
    if any(value < 0 or value > 15 for value in encoded_fields):
        raise ValueError("shape signature exceeds the versioned encoding range")
    encoded = 0
    for value in encoded_fields:
        encoded = (encoded << 4) | value
    return StandingShapeQuality(
        complete_meld_count=complete_melds,
        taatsu_count=taatsu,
        ryanmen_count=ryanmen,
        central_kanchan_count=central,
        edge_kanchan_count=edge,
        penchan_count=penchan,
        pair_units=pairs,
        isolated_count=isolated,
        encoded=encoded,
    )


def rust_standing_shape_quality(counts: Sequence[int], *,
                                wildcard: int = W):
    """Return the native ``(version, signature, encoded)`` contract.

    Importing the kernel lazily keeps the Python reference usable without the
    optional extension and avoids a module import cycle.
    """
    from .shanten import _rust_standing_shape_quality

    if _rust_standing_shape_quality is None:
        raise RuntimeError("native standing shape kernel is unavailable")
    version, signature, encoded = _rust_standing_shape_quality(
        list(counts), int(wildcard))
    return str(version), tuple(int(value) for value in signature), int(encoded)


__all__ = ["SHAPE_QUALITY_VERSION", "StandingShapeQuality",
           "standing_shape_quality", "rust_standing_shape_quality"]
