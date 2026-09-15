"""Immutable public-information decision contexts.

``Game`` is a complete local simulation object, while ``Mirror`` is an online
hero projection.  Neither object is allowed to cross the decision/rollout
boundary directly.  This module copies only public material and hero-private
state into a value object; no source object reference, RNG, wall sequence, or
opponent concealed tile vector is retained.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace as dataclass_replace
import hashlib
import json
from typing import Any, Mapping, Optional

from ..game import DEAD_WALL


class ContextError(ValueError):
    """The public state cannot represent a supported Mahjong context."""


def _is_reaction_phase(phase):
    """Return whether a context is inside a pending claim response."""
    value = str(phase)
    return value == "react" or value.startswith("response")


def _tuple34(value, name):
    if value is None:
        value = (0,) * 34
    out = tuple(int(x) for x in value)
    if len(out) != 34:
        raise ContextError(f"{name} must have 34 entries")
    if any(x < 0 for x in out):
        raise ContextError(f"{name} contains a negative count")
    return out


def _tuple_optional4(value, name):
    if value is None:
        return (None,) * 4
    out = tuple(None if x is None else int(x) for x in value)
    if len(out) != 4:
        raise ContextError(f"{name} must have four entries")
    if any(x is not None and x < 0 for x in out):
        raise ContextError(f"{name} contains a negative count")
    return out


def _normalise_melds(value):
    result = []
    for seat in value or ():
        row = []
        for meld in seat or ():
            if len(meld) != 2:
                raise ContextError(f"invalid meld {meld!r}")
            kind, tile = str(meld[0]), int(meld[1])
            if kind == "chow" and not 0 <= tile < 25:
                raise ContextError(f"invalid chow start {tile}")
            if kind != "chow" and not 0 <= tile < 34:
                raise ContextError(f"invalid meld tile {tile}")
            row.append((kind, tile))
        result.append(tuple(row))
    if len(result) != 4:
        raise ContextError("melds must contain four seats")
    return tuple(result)


def _normalise_discards(value):
    result = [tuple(int(t) for t in (row or ())) for row in (value or ())]
    if len(result) != 4:
        raise ContextError("discards must contain four seats")
    if any(t < 0 or t >= 34 for row in result for t in row):
        raise ContextError("discard tile out of range")
    return tuple(result)


def _meld_counts(melds):
    out = [0] * 34
    for row in melds:
        for kind, tile in row:
            if kind == "chow":
                for t in (tile, tile + 1, tile + 2):
                    out[t] += 1
            elif kind.startswith("kong"):
                out[tile] += 4
            else:
                out[tile] += 3
    return tuple(out)


def _visible(hand, discards, melds):
    out = list(hand)
    for row in discards:
        for tile in row:
            out[tile] += 1
    material = _meld_counts(melds)
    return tuple(a + b for a, b in zip(out, material))


@dataclass(frozen=True)
class PublicDecisionContext:
    """A value-only, auditable snapshot for one hero decision.

    ``concealed_counts`` exposes only per-seat counts, never tile identities.
    ``None`` means the protocol did not provide enough public information.
    ``fast_valid`` and ``rollout_valid`` are independent: Fast EV can work
    with hero scoring fields while a complete four-player world remains
    unsupported.
    """

    schema: str = "public-decision-context-v1"
    hero_seat: int = 0
    dealer: Optional[int] = None
    base: Optional[int] = None
    you_cai_bi_kao: Optional[bool] = None
    hand: tuple = field(default_factory=lambda: (0,) * 34)
    drawn: Optional[int] = None
    kong_draw: bool = False
    locked: int = 0
    melds: tuple = field(default_factory=lambda: ((), (), (), ()))
    chows: tuple = field(default_factory=lambda: (0, 0, 0, 0))
    discards: tuple = field(default_factory=lambda: ((), (), (), ()))
    # ``None`` means "derive the public material from the supplied hero hand"
    # for direct value-object construction.  Projections pass an explicit
    # vector, so an intentionally inconsistent visible vector is still
    # rejected rather than silently repaired.
    visible: Optional[tuple] = None
    concealed_counts: tuple = field(default_factory=lambda: (None,) * 4)
    turn: Optional[int] = None
    phase: str = "discard"
    pending_owner: Optional[int] = None
    pending_tile: Optional[int] = None
    freeze: int = 0
    freezer: Optional[int] = None
    chain_counts: tuple = field(default_factory=lambda: (None,) * 4)
    chain_piao_counts: tuple = field(default_factory=lambda: (None,) * 4)
    chain_count: Optional[int] = None
    chain_piao: Optional[int] = None
    live_wall: Optional[int] = None
    dead_wall: int = DEAD_WALL
    react_seq: tuple = ()
    react_index: Optional[int] = None
    react_claim_count: Optional[int] = None
    legal_actions: tuple = ()
    gid: Optional[str] = None
    round_no: Optional[int] = None
    seq: Optional[int] = None
    decision_id: Optional[int] = None
    fast_valid: bool = True
    rollout_valid: bool = True
    missing_fields: tuple = ()
    unsupported: tuple = ()
    provenance: tuple = ()
    input_hash: str = field(init=False)

    def __post_init__(self):
        if not 0 <= int(self.hero_seat) < 4:
            raise ContextError(f"invalid hero_seat={self.hero_seat}")
        object.__setattr__(self, "hero_seat", int(self.hero_seat))
        for name in ("dealer", "turn", "pending_owner", "freezer"):
            value = getattr(self, name)
            if value is not None and not 0 <= int(value) < 4:
                raise ContextError(f"invalid {name}={value}")
            if value is not None:
                object.__setattr__(self, name, int(value))
        if self.base is not None and int(self.base) <= 0:
            raise ContextError("base must be positive")
        if self.drawn is not None and not 0 <= int(self.drawn) < 34:
            raise ContextError(f"invalid drawn={self.drawn}")
        if self.pending_tile is not None and not 0 <= int(self.pending_tile) < 34:
            raise ContextError(f"invalid pending_tile={self.pending_tile}")
        object.__setattr__(self, "hand", _tuple34(self.hand, "hand"))
        visible = self.hand if self.visible is None else self.visible
        object.__setattr__(self, "visible", _tuple34(visible, "visible"))
        object.__setattr__(self, "melds", _normalise_melds(self.melds))
        object.__setattr__(self, "discards", _normalise_discards(self.discards))
        chows = tuple(int(x) for x in (self.chows or (0, 0, 0, 0)))
        if len(chows) != 4 or any(x < 0 for x in chows):
            raise ContextError("chows must contain four non-negative counts")
        object.__setattr__(self, "chows", chows)
        locked = int(self.locked)
        if locked != len(self.melds[self.hero_seat]):
            raise ContextError("locked does not match hero meld count")
        if not 0 <= locked <= 4:
            raise ContextError(f"invalid locked={locked}")
        object.__setattr__(self, "locked", locked)
        if any(a > 4 for a in self.visible):
            raise ContextError("visible count exceeds four copies")
        if any(self.hand[t] > self.visible[t] for t in range(34)):
            raise ContextError("hero hand is not included in visible")
        object.__setattr__(self, "concealed_counts",
                           _tuple_optional4(self.concealed_counts,
                                            "concealed_counts"))
        for name in ("chain_counts", "chain_piao_counts"):
            object.__setattr__(self, name,
                               _tuple_optional4(getattr(self, name), name))
        if self.chain_count is not None and int(self.chain_count) < 0:
            raise ContextError("chain_count must be non-negative")
        if self.chain_piao is not None and int(self.chain_piao) < 0:
            raise ContextError("chain_piao must be non-negative")
        row_chain = self.chain_counts[self.hero_seat]
        row_piao = self.chain_piao_counts[self.hero_seat]
        if (self.chain_count is not None and row_chain is not None and
                int(self.chain_count) != row_chain):
            raise ContextError("hero chain_count disagrees with chain_counts")
        if (self.chain_piao is not None and row_piao is not None and
                int(self.chain_piao) != row_piao):
            raise ContextError(
                "hero chain_piao disagrees with chain_piao_counts")
        if self.live_wall is not None and int(self.live_wall) < 0:
            raise ContextError("live_wall must be non-negative")
        if int(self.dead_wall) < 0:
            raise ContextError("dead_wall must be non-negative")
        if _is_reaction_phase(self.phase):
            if ((self.pending_tile is None) !=
                    (self.pending_owner is None)):
                raise ContextError(
                    "pending owner and tile must be supplied together")
        object.__setattr__(self, "react_seq",
                           tuple(int(x) for x in (self.react_seq or ())))
        if any(x < 0 or x >= 4 for x in self.react_seq):
            raise ContextError("react_seq contains an invalid seat")
        if (self.react_claim_count is not None and
                not 0 <= int(self.react_claim_count) <= len(self.react_seq)):
            raise ContextError("invalid react_claim_count")
        if (self.react_index is not None and
                not 0 <= int(self.react_index) <= len(self.react_seq)):
            raise ContextError("invalid react_index")
        object.__setattr__(self, "legal_actions",
                           tuple(int(x) for x in (self.legal_actions or ())))
        object.__setattr__(self, "missing_fields",
                           tuple(sorted(set(str(x) for x in (self.missing_fields or ())))))
        object.__setattr__(self, "unsupported",
                           tuple(sorted(set(str(x) for x in (self.unsupported or ())))))
        if self.provenance:
            prov = tuple(sorted((str(k), str(v)) for k, v in self.provenance))
        else:
            prov = ()
        object.__setattr__(self, "provenance", prov)
        object.__setattr__(self, "input_hash", self._compute_hash())

    @property
    def context_hash(self):
        return self.input_hash

    @property
    def remaining(self):
        return tuple(4 - x for x in self.visible)

    @property
    def unknown_pool(self):
        return sum(self.remaining)

    @property
    def full_wall(self):
        if self.live_wall is None:
            return None
        return int(self.live_wall) + int(self.dead_wall)

    @property
    def legal_discards(self):
        values = tuple(a for a in self.legal_actions if 0 <= a < 34)
        if values:
            return values
        return tuple(t for t, n in enumerate(self.hand) if n > 0)

    @property
    def complete_public_material(self):
        """Whether visible plus count-only hidden material can close to 136."""
        if (self.live_wall is None or any(
                x is None for s, x in enumerate(self.concealed_counts)
                if s != self.hero_seat)):
            return False
        hidden = sum(x for s, x in enumerate(self.concealed_counts)
                     if s != self.hero_seat)
        return (sum(self.visible) + hidden
                + self.full_wall == 136)

    def material_errors(self):
        errors = []
        if any(x < 0 or x > 4 for x in self.visible):
            errors.append("visible_count")
        if self.live_wall is not None and self.live_wall < 0:
            errors.append("live_wall")
        if any(x is not None and x < 0 for x in self.concealed_counts):
            errors.append("concealed_count")
        if any(x is not None for x in self.concealed_counts):
            if not self.complete_public_material:
                errors.append("material_conservation")
        if self.pending_tile is not None:
            pending_row = (self.discards[self.pending_owner]
                           if self.pending_owner is not None else ())
            if (_is_reaction_phase(self.phase) and
                    (not pending_row or pending_row[-1] != self.pending_tile)):
                errors.append("pending_missing_from_river")
        return tuple(sorted(set(errors)))

    def validate_for(self, purpose: str = "fast"):
        errors = self.material_errors()
        if errors:
            raise ContextError("invalid public material: " + ", ".join(errors))
        if purpose == "rollout":
            if not self.rollout_valid:
                raise ContextError("unsupported rollout context: " +
                                   ", ".join(self.missing_fields + self.unsupported))
            if not self.complete_public_material:
                raise ContextError("rollout material is incomplete")
            required = {
                "dealer": self.dealer,
                "base": self.base,
                "you_cai_bi_kao": self.you_cai_bi_kao,
                "live_wall": self.live_wall,
            }
            missing = [name for name, value in required.items()
                       if value is None]
            missing += [f"chain_counts[{seat}]" for seat, value
                        in enumerate(self.chain_counts) if value is None]
            missing += [f"chain_piao_counts[{seat}]" for seat, value
                        in enumerate(self.chain_piao_counts) if value is None]
            if _is_reaction_phase(self.phase) and (
                    not self.react_seq or self.react_claim_count is None):
                missing.append("response_order")
            if missing:
                raise ContextError("rollout fields are missing: " +
                                   ", ".join(missing))
        elif purpose == "fast" and not self.fast_valid:
            raise ContextError("invalid fast context: " +
                               ", ".join(self.missing_fields + self.unsupported))
        return self

    def _semantic_payload(self):
        # Identity fields (gid/seq/decision id), provenance formatting, and the
        # derived hash intentionally do not change a decision's semantic input.
        return {
            "schema": self.schema,
            "hero_seat": self.hero_seat,
            "dealer": self.dealer,
            "base": self.base,
            "you_cai_bi_kao": self.you_cai_bi_kao,
            "hand": self.hand,
            "drawn": self.drawn,
            "kong_draw": self.kong_draw,
            "locked": self.locked,
            "melds": self.melds,
            "chows": self.chows,
            "discards": self.discards,
            "visible": self.visible,
            "concealed_counts": self.concealed_counts,
            "turn": self.turn,
            "phase": self.phase,
            "pending_owner": self.pending_owner,
            "pending_tile": self.pending_tile,
            "freeze": self.freeze,
            "freezer": self.freezer,
            "chain_counts": self.chain_counts,
            "chain_piao_counts": self.chain_piao_counts,
            "chain_count": self.chain_count,
            "chain_piao": self.chain_piao,
            "live_wall": self.live_wall,
            "dead_wall": self.dead_wall,
            "react_seq": self.react_seq,
            "react_index": self.react_index,
            "react_claim_count": self.react_claim_count,
            "legal_actions": self.legal_actions,
            "fast_valid": self.fast_valid,
            "rollout_valid": self.rollout_valid,
            "missing_fields": self.missing_fields,
            "unsupported": self.unsupported,
        }

    def _compute_hash(self):
        raw = json.dumps(self._semantic_payload(), sort_keys=True,
                         separators=(",", ":"), ensure_ascii=False).encode()
        return hashlib.sha256(raw).hexdigest()[:16]

    def as_json(self):
        return {
            "schema": self.schema, "hero_seat": self.hero_seat,
            "dealer": self.dealer, "base": self.base,
            "you_cai_bi_kao": self.you_cai_bi_kao,
            "hand": list(self.hand), "drawn": self.drawn,
            "kong_draw": self.kong_draw, "locked": self.locked,
            "melds": [[list(m) for m in row] for row in self.melds],
            "chows": list(self.chows),
            "discards": [list(row) for row in self.discards],
            "visible": list(self.visible),
            "concealed_counts": list(self.concealed_counts),
            "turn": self.turn, "phase": self.phase,
            "pending_owner": self.pending_owner,
            "pending_tile": self.pending_tile, "freeze": self.freeze,
            "freezer": self.freezer, "chain_counts": list(self.chain_counts),
            "chain_piao_counts": list(self.chain_piao_counts),
            "chain_count": self.chain_count, "chain_piao": self.chain_piao,
            "live_wall": self.live_wall, "dead_wall": self.dead_wall,
            "react_seq": list(self.react_seq), "react_index": self.react_index,
            "react_claim_count": self.react_claim_count,
            "legal_actions": list(self.legal_actions),
            "gid": self.gid, "round_no": self.round_no, "seq": self.seq,
            "decision_id": self.decision_id,
            "fast_valid": self.fast_valid, "rollout_valid": self.rollout_valid,
            "missing_fields": list(self.missing_fields),
            "unsupported": list(self.unsupported),
            "provenance": dict(self.provenance),
            "input_hash": self.input_hash,
        }

    def replace(self, **changes):
        return dataclass_replace(self, **changes)

    @classmethod
    def from_game(cls, game, seat: int, *, gid=None, round_no=None, seq=None,
                  decision_id=None, phase=None):
        """Project a ``Game`` without reading opponent tile identities."""
        phase = phase or getattr(game, "phase", "discard")
        melds = tuple(tuple((str(kind), int(tile)) for kind, tile in row)
                      for row in getattr(game, "melds", ((),) * 4))
        discards = tuple(tuple(int(t) for t in row)
                         for row in getattr(game, "discards", ((),) * 4))
        hand = tuple(int(x) for x in game.hands[seat])
        visible = _visible(hand, discards, melds)
        locked = len(melds[seat])
        turn = getattr(game, "turn", seat)
        # This is a phase/locked-count derivation, not a read of the other
        # hands.  The current actor may hold the extra post-draw/post-claim
        # tile; all other concealed sizes are standing counts.
        concealed = []
        for s in range(4):
            need = max(0, 13 - 3 * len(melds[s]))
            concealed.append(need + 1 if phase == "discard" and s == turn else need)
        pending = getattr(game, "pending", None)
        owner, tile = pending if pending is not None else (None, None)
        chains = [None] * 4
        chain_piao = [None] * 4
        chains[seat] = int(getattr(game, "chain", [0] * 4)[seat])
        chain_piao[seat] = int(getattr(game, "chain_piao", [0] * 4)[seat])
        missing = ["opponent_chain", "opponent_chain_piao"]
        unsupported = []
        if _is_reaction_phase(phase) and not getattr(game, "react_seq", None):
            missing.append("react_sequence")
            unsupported.append("response_order")
        # ``Game.legal_actions()`` reads the current actor's concealed hand.
        # If the requested hero is not that actor, do not accidentally make a
        # public context depend on another player's hidden tiles.
        if turn != seat:
            legal = ()
            missing.append("legal_actions_for_hero")
        else:
            try:
                legal = tuple(int(x) for x in game.legal_actions())
            except Exception:
                legal = ()
                missing.append("legal_actions")
        live = max(0, int(game.live_wall_left()))
        return cls(
            hero_seat=seat, dealer=getattr(game, "dealer", None),
            base=getattr(game, "base", None),
            you_cai_bi_kao=bool(getattr(game, "you_cai_bi_kao", False)),
            hand=hand, drawn=getattr(game, "drawn", [None] * 4)[seat],
            kong_draw=bool(getattr(game, "_kong_draw", False)),
            locked=locked, melds=melds, chows=tuple(getattr(game, "chows", (0,) * 4)),
            discards=discards, visible=visible,
            concealed_counts=tuple(concealed), turn=turn, phase=phase,
            pending_owner=owner, pending_tile=tile,
            freeze=int(getattr(game, "freeze", 0)),
            freezer=getattr(game, "freezer", None),
            chain_counts=tuple(chains), chain_piao_counts=tuple(chain_piao),
            chain_count=chains[seat], chain_piao=chain_piao[seat],
            live_wall=live, dead_wall=DEAD_WALL,
            react_seq=tuple(getattr(game, "react_seq", ())),
            react_index=getattr(game, "react_idx", None),
            react_claim_count=getattr(game, "_n_claim", None), legal_actions=legal,
            gid=gid, round_no=round_no, seq=seq, decision_id=decision_id,
            fast_valid=not any(x in missing for x in
                               ("legal_actions", "legal_actions_for_hero")),
            rollout_valid=False,
            missing_fields=tuple(missing), unsupported=tuple(unsupported),
            provenance=(
                ("hand", "hero_private_game_state"),
                ("visible", "derived_from_hero_hand_public_rivers_melds"),
                ("concealed_counts", "public_phase_and_meld_count_derivation"),
                ("live_wall", "public_wall_length"),
                ("chain_count", "hero_game_state"),
                ("opponent_chain", "unknown_not_read_from_game"),
            ),
        )

    @classmethod
    def from_mirror(cls, mirror, phase=None, *, gid=None, round_no=None,
                    seq=None, decision_id=None):
        """Project the online Mirror; its placeholder world stays excluded."""
        phase = phase or "draw"
        melds = tuple(tuple((str(kind), int(tile)) for kind, tile in row)
                      for row in mirror.melds)
        discards = tuple(tuple(int(t) for t in row) for row in mirror.discards)
        hand = tuple(int(x) for x in mirror.my_hand)
        visible = _visible(hand, discards, melds)
        locked = len(melds[mirror.me])
        turn = mirror.me
        concealed = []
        for s in range(4):
            need = max(0, 13 - 3 * len(melds[s]))
            concealed.append(need + 1 if s == mirror.me and phase == "draw" else need)
        owner, tile = mirror.pending if mirror.pending is not None else (None, None)
        chains = [None] * 4
        chain_piao = [None] * 4
        chains[mirror.me] = int(mirror.chain)
        chain_piao[mirror.me] = int(mirror.chain_piao)
        missing = ["opponent_chain", "opponent_chain_piao"]
        unsupported = []
        if _is_reaction_phase(phase) and mirror.pending is None:
            missing.append("pending")
            unsupported.append("response_order")
        legal = ()
        try:
            g = mirror.build_game(phase)
            legal = tuple(int(x) for x in g.legal_actions())
        except Exception:
            missing.append("legal_actions")
        live = max(0, int(mirror.live_wall_left()))
        return cls(
            hero_seat=mirror.me, dealer=mirror.dealer, base=mirror.base,
            you_cai_bi_kao=bool(mirror.you_cai_bi_kao), hand=hand,
            drawn=mirror.drawn, kong_draw=bool(mirror.kong_draw),
            locked=locked, melds=melds, chows=tuple(mirror.chows),
            discards=discards, visible=visible,
            concealed_counts=tuple(concealed), turn=turn, phase=phase,
            pending_owner=owner, pending_tile=tile,
            freeze=int(mirror.freeze), freezer=mirror.freezer,
            chain_counts=tuple(chains), chain_piao_counts=tuple(chain_piao),
            chain_count=chains[mirror.me], chain_piao=chain_piao[mirror.me],
            live_wall=live, dead_wall=DEAD_WALL,
            react_seq=(), react_index=None, legal_actions=legal,
            react_claim_count=None,
            gid=gid, round_no=round_no if round_no is not None else mirror.round_no,
            seq=seq, decision_id=decision_id, fast_valid="legal_actions" not in missing,
            rollout_valid=False, missing_fields=tuple(missing),
            unsupported=tuple(unsupported), provenance=(
                ("hand", "hero_private_mirror"),
                ("visible", "derived_from_hero_hand_public_rivers_melds"),
                ("wall_material", "placeholder_excluded"),
                ("opponent_chain", "unknown_not_read_from_mirror"),
            ),
        )

    @classmethod
    def from_public(cls, data: Mapping[str, Any]):
        """Build from a public JSON object, preserving explicit nulls."""
        value = dict(data)
        supplied = value.pop("input_hash", None)
        value.pop("context_hash", None)
        allowed = {name for name in cls.__dataclass_fields__ if name != "input_hash"}
        value = {k: v for k, v in value.items() if k in allowed}
        if "provenance" in value and isinstance(value["provenance"], Mapping):
            value["provenance"] = tuple(value["provenance"].items())
        context = cls(**value)
        if supplied is not None and supplied != context.input_hash:
            raise ContextError(
                f"context hash mismatch: supplied={supplied} actual={context.input_hash}")
        return context
