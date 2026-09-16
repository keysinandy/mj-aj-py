"""All-legal-discard frontier, with a Python semantic reference."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from ..shanten import shanten, ukeire

try:
    from ..shanten import discard_frontier as _discard_frontier_kernel
    from ..shanten import DISCARD_FRONTIER_KERNEL_VERSION
    from ..shanten import (discard_frontier_batch as
                           _discard_frontier_batch_kernel,
                           DISCARD_FRONTIER_BATCH_KERNEL_VERSION)
except ImportError:  # pragma: no cover - kept for rolling deployments
    _discard_frontier_kernel = None
    DISCARD_FRONTIER_KERNEL_VERSION = "python-frontier-v1"
    _discard_frontier_batch_kernel = None
    DISCARD_FRONTIER_BATCH_KERNEL_VERSION = None

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


def discard_frontier_batch(states, locked=0, visibles=None,
                           legal_discards=None, *, use_rust=True):
    """Return one all-legal frontier for each post-draw state.

    The Rust implementation shares its decomposition cache across states.
    When unavailable, this is a semantic-preserving Python/state fallback;
    callers can inspect each item's ``kernel`` field for the actual path.
    """
    states = tuple(tuple(int(x) for x in state) for state in states)
    if any(len(state) != 34 for state in states):
        raise FrontierError("every batch hand must have 34 entries")
    if any(any(x < 0 for x in state) for state in states):
        raise FrontierError("batch hand contains a negative count")
    locked = int(locked)
    if not 0 <= locked <= 4:
        raise FrontierError(f"invalid locked={locked}")
    if visibles is not None:
        visibles = tuple(tuple(int(x) for x in value) for value in visibles)
        if len(visibles) != len(states):
            raise FrontierError("visibles must have one entry per state")
        if any(len(value) != 34 for value in visibles):
            raise FrontierError("every batch visible vector must have 34 entries")
        if any(any(x < 0 or x > 4 for x in value) for value in visibles):
            raise FrontierError("batch visible counts must be in [0,4]")
        if any(any(state[t] > value[t] for t in range(34))
               for state, value in zip(states, visibles)):
            raise FrontierError("batch hand is not included in visible")
    if legal_discards is not None:
        legal_discards = tuple(
            tuple(sorted(set(int(x) for x in value)))
            for value in legal_discards)
        if len(legal_discards) != len(states):
            raise FrontierError("legal_discards must have one entry per state")
        if any(any(tile < 0 or tile >= 34 for tile in value)
               for value in legal_discards):
            raise FrontierError("batch legal discard out of range")
        if any(any(state[tile] <= 0 for tile in value)
               for state, value in zip(states, legal_discards)):
            raise FrontierError("batch legal discard is absent from hand")
    if use_rust and _discard_frontier_batch_kernel is not None:
        try:
            raw = _discard_frontier_batch_kernel(
                [list(state) for state in states], int(locked),
                [list(value) for value in visibles] if visibles is not None else None,
                [list(value) for value in legal_discards]
                if legal_discards is not None else None, True)
        except (TypeError, ValueError):
            raw = None
        if raw is not None and len(raw) == len(states):
            result = []
            for index, rows in enumerate(raw):
                visible = (visibles[index] if visibles is not None
                           else states[index])
                legal = (tuple(sorted(set(legal_discards[index])))
                         if legal_discards is not None else
                         tuple(t for t, n in enumerate(states[index]) if n > 0))
                unknown = sum(max(0, 4 - x) for x in visible)
                items = []
                for row in rows:
                    tile, sh, tiles, u1 = row
                    item = DiscardFrontierItem(
                        tile=int(tile), shanten=int(sh),
                        ukeire_tiles=tuple(int(t) for t in tiles),
                        u1=int(u1), ukeire_bitset=_bitset(tiles),
                        waits=tuple(int(t) for t in tiles) if int(sh) == 0 else (),
                        structure_ukeire=() if int(sh) == 0 else
                        tuple(int(t) for t in tiles),
                        visible_unknown=unknown,
                        kernel=DISCARD_FRONTIER_BATCH_KERNEL_VERSION or
                        DISCARD_FRONTIER_KERNEL_VERSION)
                    items.append(item)
                if tuple(item.tile for item in items) == legal:
                    result.append(tuple(items))
                else:
                    result = []
                    break
            if result:
                return tuple(result)
    result = []
    for index, state in enumerate(states):
        visible = visibles[index] if visibles is not None else None
        legal = (legal_discards[index]
                 if legal_discards is not None else None)
        result.append(discard_frontier(
            state, locked=locked, visible=visible,
            legal_discards=legal, use_rust=use_rust))
    return tuple(result)
