"""All-legal-discard frontier, with a Python semantic reference."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from ..shanten import shanten, ukeire

try:
    from ..shanten import discard_frontier as _discard_frontier_kernel
    from ..shanten import DISCARD_FRONTIER_KERNEL_VERSION
except ImportError:  # pragma: no cover - kept for rolling deployments
    _discard_frontier_kernel = None
    DISCARD_FRONTIER_KERNEL_VERSION = "python-frontier-v1"

from .context import PublicDecisionContext


class FrontierError(ValueError):
    """The requested frontier is not a legal hand-state operation."""


@dataclass(frozen=True)
class DiscardFrontierItem:
    tile: int
    shanten: int
    ukeire_tiles: tuple
    u1: int
    ukeire_bitset: int
    waits: tuple = ()
    structure_ukeire: tuple = ()
    legal: bool = True
    visible_unknown: int = 0
    kernel: str = "python-frontier-v1"

    @property
    def p1(self):
        return self.u1 / self.visible_unknown if self.visible_unknown else 0.0

    def as_json(self):
        return {
            "tile": self.tile, "shanten": self.shanten,
            "ukeire_tiles": list(self.ukeire_tiles), "U1": self.u1,
            "p1": self.p1, "ukeire_bitset": self.ukeire_bitset,
            "waits": list(self.waits),
            "structure_ukeire": list(self.structure_ukeire),
            "legal": self.legal,
            "unknown_pool": self.visible_unknown, "kernel": self.kernel,
        }


def _bitset(values):
    result = 0
    for tile in values:
        result |= 1 << int(tile)
    return result


def _normalise_inputs(hand_or_context, locked, visible, legal_discards):
    if isinstance(hand_or_context, PublicDecisionContext):
        context = hand_or_context
        if locked is None:
            locked = context.locked
        if visible is None:
            visible = context.visible
        if legal_discards is None:
            legal_discards = context.legal_discards
        hand = context.hand
    else:
        context = None
        hand = tuple(int(x) for x in hand_or_context)
    if len(hand) != 34:
        raise FrontierError("hand must have 34 entries")
    if any(x < 0 for x in hand):
        raise FrontierError("hand contains a negative count")
    if locked is None:
        locked = 0
    locked = int(locked)
    if not 0 <= locked <= 4:
        raise FrontierError(f"invalid locked={locked}")
    if visible is None:
        visible = hand
    visible = tuple(int(x) for x in visible)
    if len(visible) != 34 or any(x < 0 or x > 4 for x in visible):
        raise FrontierError("visible must be 34 counts in [0,4]")
    if any(hand[t] > visible[t] for t in range(34)):
        raise FrontierError("hand is not included in visible")
    if legal_discards is None:
        legal_discards = tuple(t for t, n in enumerate(hand) if n > 0)
    legal = tuple(sorted(set(int(t) for t in legal_discards)))
    if any(t < 0 or t >= 34 for t in legal):
        raise FrontierError("legal discard out of range")
    if any(hand[t] <= 0 for t in legal):
        raise FrontierError("legal discard is absent from hand")
    return hand, locked, visible, legal, context


def _python_frontier(hand, locked, visible, legal):
    unknown = sum(max(0, 4 - x) for x in visible)
    out = []
    for tile in legal:
        child = list(hand)
        child[tile] -= 1
        child_s, uke_tiles, u1 = ukeire(child, locked, visible)
        # Compute through the public dispatcher above, so a Python fallback
        # and a Rust shanten/ukeire installation share the same contract.
        if child_s != shanten(child, locked):
            raise FrontierError("shanten/ukeire disagree for discard frontier")
        uke_tiles = tuple(int(t) for t in uke_tiles)
        waits = uke_tiles if child_s == 0 else ()
        structure = () if child_s == 0 else uke_tiles
        out.append(DiscardFrontierItem(
            tile=tile, shanten=int(child_s), ukeire_tiles=uke_tiles,
            u1=int(u1), ukeire_bitset=_bitset(uke_tiles), waits=tuple(waits),
            structure_ukeire=tuple(structure), visible_unknown=unknown,
            kernel="python-frontier-v1"))
    return tuple(out)


def discard_frontier(hand_or_context, locked=None, visible=None,
                     legal_discards: Iterable[int] | None = None,
                     *, use_rust=True):
    """Return every different legal discard in stable tile order.

    The Python path is the semantic reference.  A Rust batch result is used
    only when it advertises the same all-candidate API; a missing or malformed
    extension falls back to the reference rather than the old min-shanten
    ``best_future_discard`` helper.
    """
    hand, locked, visible, legal, context = _normalise_inputs(
        hand_or_context, locked, visible, legal_discards)
    if use_rust and _discard_frontier_kernel is not None:
        try:
            raw = _discard_frontier_kernel(
                list(hand), locked, list(visible), list(legal), True)
        except (AttributeError, TypeError, ValueError):
            raw = None
        if raw is not None:
            try:
                unknown = sum(max(0, 4 - x) for x in visible)
                items = []
                for row in raw:
                    tile, s, tiles, u1 = row
                    tile, s, u1 = int(tile), int(s), int(u1)
                    if tile not in legal:
                        raise FrontierError("Rust frontier returned illegal tile")
                    tiles = tuple(int(t) for t in tiles)
                    items.append(DiscardFrontierItem(
                        tile=tile, shanten=s, ukeire_tiles=tiles, u1=u1,
                        ukeire_bitset=_bitset(tiles),
                        waits=tiles if s == 0 else (),
                        structure_ukeire=() if s == 0 else tiles,
                        visible_unknown=unknown,
                        kernel=DISCARD_FRONTIER_KERNEL_VERSION))
                if tuple(item.tile for item in items) == legal:
                    return tuple(items)
            except (TypeError, ValueError, FrontierError):
                pass
    return _python_frontier(hand, locked, visible, legal)
