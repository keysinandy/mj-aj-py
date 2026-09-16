"""Append-only public event history.

Only information available to the hero is represented here.  In particular,
draw events intentionally omit tile identity: a draw made by another seat is
not a public observation, and a hero draw belongs in the hero-private context.
Identity/transport fields are retained for auditability but excluded from the
semantic hash so a gid/seq/log-format change cannot manufacture a new belief
state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import copy
import hashlib
import json
from typing import Any, Iterable, Mapping

from ..decision.profile import canonical_json, fingerprint


EVENT_TYPES = (
    "DRAW_PUBLIC", "DISCARD", "PASS", "CHOW", "PONG", "KONG_OPEN",
    "KONG_CLOSED_PUBLIC", "KONG_ADD", "HU", "ROUND_START", "ROUND_END",
)
_EVENT_ALIASES = {
    "tile_drawn": "DRAW_PUBLIC", "draw": "DRAW_PUBLIC",
    "draw_public": "DRAW_PUBLIC", "discard": "DISCARD",
    "tile_discarded": "DISCARD", "pass": "PASS", "chi": "CHOW",
    "chow": "CHOW", "peng": "PONG", "pong": "PONG", "gang": "KONG_OPEN",
    "kong_open": "KONG_OPEN", "kong_closed": "KONG_CLOSED_PUBLIC",
    "gang_an": "KONG_CLOSED_PUBLIC", "kong_add": "KONG_ADD",
    "gang_bu": "KONG_ADD", "hu": "HU", "round_start": "ROUND_START",
    "round_started": "ROUND_START", "round_end": "ROUND_END",
    "round_ended": "ROUND_END",
}


def _event_type(value: Any) -> str:
    text = str(value)
    upper = text.upper()
    if upper in EVENT_TYPES:
        return upper
    result = _EVENT_ALIASES.get(text.lower())
    if result is None:
        raise ValueError(f"unknown public event type: {value!r}")
    return result


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_safe(item) for item in value]
    if isinstance(value, set):
        return sorted(_json_safe(item) for item in value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"public event payload is not JSON-compatible: {value!r}")


def _normalize_actor(value: Any) -> int | None:
    if value is None:
        return None
    value = int(value)
    if not 0 <= value < 4:
        raise ValueError(f"event actor out of range: {value}")
    return value


@dataclass(frozen=True)
class PublicEvent:
    """One semantic public event plus non-semantic provenance fields."""

    event_type: str
    actor: int | None = None
    phase: str | None = None
    public_payload: Mapping[str, Any] = field(default_factory=dict)
    legal_actions_before: tuple[int, ...] | None = None
    context_hash_before: str | None = None
    provenance: str | Mapping[str, Any] = "unknown"
    schema: str = "public-event-v1"
    gid: str | None = None
    round_no: int | None = None
    seq: int | None = None
    log_format: str | None = None
    event_hash: str = field(init=False)

    def __post_init__(self):
        if self.schema != "public-event-v1":
            raise ValueError(f"unsupported public event schema: {self.schema}")
        object.__setattr__(self, "event_type", _event_type(self.event_type))
        object.__setattr__(self, "actor", _normalize_actor(self.actor))
        if self.round_no is not None:
            object.__setattr__(self, "round_no", int(self.round_no))
        if self.seq is not None:
            object.__setattr__(self, "seq", int(self.seq))
        payload = _json_safe(dict(self.public_payload or {}))
        if not isinstance(payload, dict):
            raise ValueError("public_payload must be an object")
        object.__setattr__(self, "public_payload", payload)
        if self.legal_actions_before is not None:
            actions = tuple(int(action) for action in self.legal_actions_before)
            if len(set(actions)) != len(actions):
                raise ValueError("legal_actions_before must contain unique actions")
            object.__setattr__(self, "legal_actions_before", actions)
        object.__setattr__(self, "provenance", _json_safe(self.provenance))
        object.__setattr__(self, "event_hash", fingerprint(self.semantic_payload(), 24))

    def semantic_payload(self) -> dict[str, Any]:
        """Fields that can change observable semantics, excluding log identity."""
        return {
            "schema": self.schema,
            "event_type": self.event_type,
            "actor": self.actor,
            "phase": self.phase,
            "public_payload": self.public_payload,
            "legal_actions_before": self.legal_actions_before,
            "context_hash_before": self.context_hash_before,
        }

    @property
    def action(self):
        """Map an action-bearing public event to the engine action code."""
        from ..game import (CHOW_HIGH, CHOW_LOW, CHOW_MID, HU, KONG_ADD_BASE,
                            KONG_CLOSED_BASE, KONG_OPEN, PASS, PONG)

        if self.event_type == "PASS":
            return PASS
        if self.event_type == "HU":
            return HU
        if self.event_type == "DISCARD":
            return int(self.public_payload["tile"])
        if self.event_type == "PONG":
            return PONG
        if self.event_type == "KONG_OPEN":
            return KONG_OPEN
        if self.event_type == "KONG_CLOSED_PUBLIC":
            return KONG_CLOSED_BASE - int(self.public_payload["tile"])
        if self.event_type == "KONG_ADD":
            return KONG_ADD_BASE - int(self.public_payload["tile"])
        if self.event_type == "CHOW":
            position = self.public_payload.get("position", 0)
            return CHOW_LOW - int(position)
        return None

    def as_json(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "event_type": self.event_type,
            "actor": self.actor,
            "phase": self.phase,
            "public_payload": copy.deepcopy(dict(self.public_payload)),
            "legal_actions_before": (list(self.legal_actions_before)
                                      if self.legal_actions_before is not None
                                      else None),
            "context_hash_before": self.context_hash_before,
            "provenance": copy.deepcopy(self.provenance),
            "gid": self.gid, "round_no": self.round_no, "seq": self.seq,
            "log_format": self.log_format,
            "event_hash": self.event_hash,
        }

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> "PublicEvent":
        value = dict(data)
        supplied = value.pop("event_hash", None)
        if "payload" in value and "public_payload" not in value:
            value["public_payload"] = value.pop("payload")
        allowed = set(cls.__dataclass_fields__) - {"event_hash"}
        unknown = set(value) - allowed
        if unknown:
            raise ValueError("unknown public event fields: " +
                             ", ".join(sorted(unknown)))
        event = cls(**value)
        if supplied is not None and supplied != event.event_hash:
            raise ValueError(
                f"public event hash mismatch: supplied={supplied} "
                f"actual={event.event_hash}")
        return event

    @classmethod
    def from_action(cls, game, action: int, *, actor: int | None = None,
                    phase: str | None = None, provenance: str = "game",
                    legal_actions_before=None):
        """Describe an action before it is applied to ``game``."""
        from ..game import (CHOW_HIGH, CHOW_LOW, CHOW_MID, HU, KONG_ADD_BASE,
                            KONG_CLOSED_BASE, KONG_OPEN, PASS, PONG)

        actor = game.current_seat() if actor is None else actor
        phase = phase or getattr(game, "phase", None)
        if action == PASS:
            kind, payload = "PASS", {}
        elif action == HU:
            kind, payload = "HU", {}
        elif 0 <= action < 34:
            kind, payload = "DISCARD", {"tile": int(action)}
        elif action == PONG:
            pending = getattr(game, "pending", None)
            kind, payload = "PONG", {"tile": pending[1] if pending else None}
        elif action == KONG_OPEN:
            pending = getattr(game, "pending", None)
            kind, payload = "KONG_OPEN", {"tile": pending[1] if pending else None}
        elif CHOW_HIGH <= action <= CHOW_LOW:
            pending = getattr(game, "pending", None)
            kind, payload = "CHOW", {
                "tile": pending[1] if pending else None,
                "position": int(CHOW_LOW - action),
            }
        elif KONG_CLOSED_BASE - 33 <= action <= KONG_CLOSED_BASE:
            kind, payload = "KONG_CLOSED_PUBLIC", {
                "tile": int(KONG_CLOSED_BASE - action)}
        elif KONG_ADD_BASE - 33 <= action <= KONG_ADD_BASE:
            kind, payload = "KONG_ADD", {"tile": int(KONG_ADD_BASE - action)}
        else:
            raise ValueError(f"unsupported engine action for public history: {action}")
        payload = {key: value for key, value in payload.items() if value is not None}
        return cls(kind, actor=actor, phase=phase, public_payload=payload,
                   legal_actions_before=(
                       tuple(int(value) for value in legal_actions_before)
                       if legal_actions_before is not None else None),
                   context_hash_before=public_state_hash(game),
                   provenance=provenance)


@dataclass(frozen=True)
class InformationHistory:
    """Immutable append-only event sequence."""

    events: tuple[PublicEvent, ...] = ()
    history_incomplete: bool = False
    incomplete_reasons: tuple[str, ...] = ()
    schema: str = "information-history-v1"

    def __post_init__(self):
        if self.schema != "information-history-v1":
            raise ValueError(f"unsupported information history schema: {self.schema}")
        normalized = tuple(
            item if isinstance(item, PublicEvent) else PublicEvent.from_json(item)
            for item in (self.events or ())
        )
        object.__setattr__(self, "events", normalized)
        object.__setattr__(self, "history_incomplete", bool(self.history_incomplete))
        object.__setattr__(self, "incomplete_reasons", tuple(sorted({str(x) for x in
                                                                     (self.incomplete_reasons or ())})))

    @property
    def history_hash(self) -> str:
        # Event order is preserved by the list.  Do not sort this payload.
        return fingerprint({
            "schema": self.schema,
            "events": [event.semantic_payload() for event in self.events],
            "history_incomplete": self.history_incomplete,
            "incomplete_reasons": self.incomplete_reasons,
        }, 32)

    @property
    def fingerprint(self):
        return self.history_hash

    def append(self, event: PublicEvent | Mapping[str, Any]) -> "InformationHistory":
        value = event if isinstance(event, PublicEvent) else PublicEvent.from_json(event)
        return InformationHistory(
            events=self.events + (value,),
            history_incomplete=self.history_incomplete,
            incomplete_reasons=self.incomplete_reasons,
            schema=self.schema,
        )

    def mark_incomplete(self, reason: str) -> "InformationHistory":
        return InformationHistory(
            events=self.events, history_incomplete=True,
            incomplete_reasons=self.incomplete_reasons + (str(reason),),
            schema=self.schema,
        )

    def as_json(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "events": [event.as_json() for event in self.events],
            "history_incomplete": self.history_incomplete,
            "incomplete_reasons": list(self.incomplete_reasons),
            "history_hash": self.history_hash,
        }

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> "InformationHistory":
        value = dict(data)
        supplied = value.pop("history_hash", None)
        allowed = {"schema", "events", "history_incomplete", "incomplete_reasons"}
        unknown = set(value) - allowed
        if unknown:
            raise ValueError("unknown information history fields: " +
                             ", ".join(sorted(unknown)))
        history = cls(**value)
        if supplied is not None and supplied != history.history_hash:
            raise ValueError(
                f"information history hash mismatch: supplied={supplied} "
                f"actual={history.history_hash}")
        return history

    @classmethod
    def from_game(cls, game) -> "InformationHistory":
        return history_from_game(game)


@dataclass
class PublicReplayState:
    """Minimal public reducer used to audit history against a context."""

    discards: list[list[int]] = field(default_factory=lambda: [[], [], [], []])
    melds: list[list[tuple[str, int]]] = field(
        default_factory=lambda: [[], [], [], []])
    chows: list[int] = field(default_factory=lambda: [0, 0, 0, 0])
    pending: tuple[int, int] | None = None
    phase: str | None = None
    turn: int | None = None
    freeze: int = 0
    freezer: int | None = None
    passes: list[tuple[int | None, str | None]] = field(default_factory=list)
    chain_counts: list[int | None] = field(
        default_factory=lambda: [None, None, None, None])
    chain_piao_counts: list[int | None] = field(
        default_factory=lambda: [None, None, None, None])
    react_seq: list[int] = field(default_factory=list)
    react_idx: int | None = None
    react_claim_count: int | None = None
    incomplete: bool = False

    def apply(self, event: PublicEvent):
        self.phase = event.phase or self.phase
        self.turn = event.actor if event.actor is not None else self.turn
        payload = event.public_payload
        actor = event.actor
        if event.event_type == "DRAW_PUBLIC":
            # A completed response ring or kong transition leads to a new
            # draw.  The draw event is the first unambiguous public marker
            # that the pending discard is no longer claimable.
            self.pending = None
            self.react_seq = []
            self.react_idx = None
            self.react_claim_count = None
            self.phase = event.phase or "draw"
        elif event.event_type == "ROUND_START":
            self.chain_counts = [0, 0, 0, 0]
            self.chain_piao_counts = [0, 0, 0, 0]
        elif event.event_type == "DISCARD" and actor is not None:
            tile = int(payload["tile"])
            self.discards[actor].append(tile)
            self.pending = (actor, tile)
            if self.freeze > 0:
                self.freeze -= 1
            if tile == 33:
                self.freezer, self.freeze = actor, 3
                # A white discard can be a public piao action or an ordinary
                # break of the chain; the event alone does not reveal the
                # private standing-hand test.  Preserve an explicit value,
                # otherwise mark this actor's chain as unknown.
                if "chain_count" in payload or "chain" in payload:
                    self.chain_counts[actor] = int(
                        payload.get("chain_count", payload.get("chain")))
                    self.chain_piao_counts[actor] = int(
                        payload.get("chain_piao_count",
                                   payload.get("chain_piao", 0)))
                elif payload.get("piao") is True:
                    if self.chain_counts[actor] is None:
                        self.chain_counts[actor] = 0
                    if self.chain_piao_counts[actor] is None:
                        self.chain_piao_counts[actor] = 0
                    self.chain_counts[actor] += 1
                    self.chain_piao_counts[actor] += 1
                else:
                    self.chain_counts[actor] = None
                    self.chain_piao_counts[actor] = None
            elif self.chain_counts[actor] is not None:
                self.chain_counts[actor] = 0
                self.chain_piao_counts[actor] = 0
            claims = [(actor + offset) % 4 for offset in (1, 2, 3)
                      if not (self.freeze > 0 and (actor + offset) % 4 != self.freezer)]
            chow_seat = (actor + 1) % 4
            if self.freeze > 0 and chow_seat != self.freezer:
                chow_seat = None
            self.react_seq = claims + ([chow_seat] if chow_seat is not None else [])
            self.react_claim_count = len(claims)
            self.react_idx = 0 if self.react_seq else None
            self.phase = "react" if self.react_seq else "draw"
            if self.react_seq:
                self.turn = self.react_seq[0]
        elif event.event_type == "PASS":
            self.passes.append((actor, event.phase))
            if self.react_seq and self.react_idx is not None:
                if actor is not None and actor != self.react_seq[self.react_idx]:
                    self.incomplete = True
                next_index = self.react_idx + 1
                if next_index >= len(self.react_seq):
                    self.pending = None
                    self.react_seq = []
                    self.react_idx = None
                    self.react_claim_count = None
                    self.phase = "draw"
                else:
                    self.react_idx = next_index
                    self.turn = self.react_seq[next_index]
        elif event.event_type in ("PONG", "KONG_OPEN") and actor is not None:
            tile = int(payload.get("tile", self.pending[1] if self.pending else -1))
            self._consume_pending(tile)
            self.melds[actor].append(("pong" if event.event_type == "PONG"
                                      else "kong_open", tile))
            self.phase = "discard"
            self.turn = actor
            self.react_seq = []
            self.react_idx = None
            self.react_claim_count = None
            if event.event_type == "KONG_OPEN":
                self._increment_chain(actor, payload)
            if event.event_type == "KONG_OPEN":
                self.pending = None
        elif event.event_type == "CHOW" and actor is not None:
            tile = int(payload.get("tile", self.pending[1] if self.pending else -1))
            tiles = tuple(int(value) for value in payload.get("tiles", ()))
            position = payload.get("position")
            start = (min((tile,) + tiles) if tiles else
                     tile - int(position) if position is not None else tile)
            self._consume_pending(tile)
            self.melds[actor].append(("chow", start))
            self.chows[actor] += 1
            self.phase = "discard"
            self.turn = actor
            self.react_seq = []
            self.react_idx = None
            self.react_claim_count = None
        elif event.event_type == "KONG_CLOSED_PUBLIC" and actor is not None:
            self.melds[actor].append(("kong_closed", int(payload["tile"])))
            self.phase = "discard"
            self.turn = actor
            self.react_seq = []
            self.react_idx = None
            self.react_claim_count = None
            self._increment_chain(actor, payload)
        elif event.event_type == "KONG_ADD" and actor is not None:
            tile = int(payload["tile"])
            for index, meld in enumerate(self.melds[actor]):
                if meld == ("pong", tile):
                    self.melds[actor][index] = ("kong_add", tile)
                    break
            else:
                self.incomplete = True
            self.phase = "discard"
            self.turn = actor
            self.react_seq = []
            self.react_idx = None
            self.react_claim_count = None
            self._increment_chain(actor, payload)
        elif event.event_type == "ROUND_END":
            self.phase = "settled"
            self.react_seq = []
            self.react_idx = None
            self.react_claim_count = None
        return self

    def _consume_pending(self, tile):
        if self.pending is None:
            self.incomplete = True
            return
        owner, pending = self.pending
        if pending != tile or not self.discards[owner] or self.discards[owner][-1] != tile:
            self.incomplete = True
            return
        self.discards[owner].pop()
        self.pending = None

    def _increment_chain(self, actor, payload):
        if "chain_count" in payload or "chain" in payload:
            self.chain_counts[actor] = int(
                payload.get("chain_count", payload.get("chain")))
            if "chain_piao_count" in payload or "chain_piao" in payload:
                self.chain_piao_counts[actor] = int(
                    payload.get("chain_piao_count",
                               payload.get("chain_piao")))
        elif self.chain_counts[actor] is not None:
            self.chain_counts[actor] += 1

    def as_json(self):
        return {
            "discards": [list(row) for row in self.discards],
            "melds": [[list(meld) for meld in row] for row in self.melds],
            "chows": list(self.chows), "pending": self.pending,
            "phase": self.phase, "turn": self.turn,
            "freeze": self.freeze, "freezer": self.freezer,
            "passes": [list(value) for value in self.passes],
            "chain_counts": list(self.chain_counts),
            "chain_piao_counts": list(self.chain_piao_counts),
            "react_seq": list(self.react_seq), "react_idx": self.react_idx,
            "react_claim_count": self.react_claim_count,
            "incomplete": self.incomplete,
        }


def replay_public_history(history: InformationHistory | Iterable[PublicEvent]):
    """Reduce an ordered history without consulting a hidden Game."""
    if not isinstance(history, InformationHistory):
        history = InformationHistory(tuple(history))
    state = PublicReplayState()
    for event in history.events:
        state.apply(event)
    if history.history_incomplete:
        state.incomplete = True
    return state


def replay_against_context(history: InformationHistory, context) -> dict[str, Any]:
    """Compare replayed public rivers/melds/cursor fields with a context."""
    state = replay_public_history(history)
    expected_discards = [list(row) for row in context.discards]
    expected_melds = [[tuple(meld) for meld in row] for row in context.melds]
    mismatches = []
    if state.discards != expected_discards:
        mismatches.append("discards")
    if state.melds != expected_melds:
        mismatches.append("melds")
    if context.pending_owner is not None or state.pending is not None:
        expected_pending = (context.pending_owner, context.pending_tile)
        if state.pending != expected_pending:
            mismatches.append("pending")
    response_phases = {"react", "response_peng", "response_chi"}
    phase_match = (context.phase == state.phase or
                   (state.phase == "draw" and context.phase == "discard") or
                   (context.phase in response_phases and
                    state.phase in response_phases))
    if context.phase and state.phase and not phase_match:
        mismatches.append("phase")
    if tuple(state.chows) != tuple(context.chows):
        mismatches.append("chows")
    if int(state.freeze) != int(context.freeze):
        mismatches.append("freeze")
    if state.freezer != context.freezer:
        mismatches.append("freezer")
    if str(context.phase) in response_phases:
        if tuple(state.react_seq) != tuple(context.react_seq):
            mismatches.append("response_order")
        if (context.react_index is not None and
                state.react_idx != context.react_index):
            mismatches.append("response_cursor")
        if (context.react_claim_count is not None and
                state.react_claim_count != context.react_claim_count):
            mismatches.append("response_claim_boundary")
    known_chains = getattr(context, "chain_counts", ())
    if any(value is not None for value in known_chains):
        replay_chains = getattr(state, "chain_counts", ())
        for seat, value in enumerate(known_chains):
            if (value is not None and replay_chains and
                    replay_chains[seat] is not None and
                    replay_chains[seat] != value):
                mismatches.append("chain")
                break
    # A partial response ring does not expose the next responder in a
    # PASS-only public event.  Compare the turn whenever the cursor is not in
    # that intentionally under-specified window.
    response_phase = str(context.phase).startswith("response") or context.phase == "react"
    if not response_phase and context.turn is not None and state.turn != context.turn:
        mismatches.append("turn")
    return {
        "schema": "public-history-replay-report-v1",
        "history_hash": history.history_hash,
        "matched": not mismatches and not state.incomplete,
        "mismatches": mismatches,
        "history_incomplete": history.history_incomplete,
        "replay_incomplete": state.incomplete,
        "state": state.as_json(),
    }


def public_state_hash(game) -> str:
    """Hash a public Game projection without any hand or wall identity."""
    pending = getattr(game, "pending", None)
    payload = {
        "schema": "public-state-v1",
        "dealer": getattr(game, "dealer", None),
        "base": getattr(game, "base", None),
        "you_cai_bi_kao": bool(getattr(game, "you_cai_bi_kao", False)),
        "melds": tuple(tuple(tuple(m) for m in row)
                       for row in getattr(game, "melds", ((),) * 4)),
        "discards": tuple(tuple(row) for row in
                          getattr(game, "discards", ((),) * 4)),
        "chows": tuple(getattr(game, "chows", (0,) * 4)),
        "turn": getattr(game, "turn", None),
        "phase": getattr(game, "phase", None),
        "pending": tuple(pending) if pending is not None else None,
        "freeze": getattr(game, "freeze", 0),
        "freezer": getattr(game, "freezer", None),
        "live_wall": (game.live_wall_left()
                      if hasattr(game, "live_wall_left") else None),
        "react_seq": tuple(getattr(game, "react_seq", ())),
        "react_idx": getattr(game, "react_idx", None),
        "n_claim": getattr(game, "_n_claim", None),
    }
    return fingerprint(payload, 24)


def history_from_game(game) -> InformationHistory:
    """Return the instrumented game history, or a conservative fallback."""
    existing = getattr(game, "_public_history", None)
    if isinstance(existing, InformationHistory):
        return existing
    # A manually assembled Game has no reliable action order.  Preserve what
    # is knowable as a boundary and explicitly mark the missing history.
    history = InformationHistory().append(PublicEvent(
        "ROUND_START", actor=getattr(game, "dealer", None),
        phase=getattr(game, "phase", None),
        context_hash_before=public_state_hash(game), provenance="game_snapshot"))
    return history.mark_incomplete("action_history_unavailable")


def _platform_event(raw: Mapping[str, Any]) -> PublicEvent | None:
    if isinstance(raw, PublicEvent):
        return raw
    if not isinstance(raw, Mapping):
        return None
    etype = raw.get("type", raw.get("event_type", raw.get("eventType")))
    etype = str(etype).lower()
    data = raw.get("data") or {}
    if not isinstance(data, Mapping):
        data = {}
    actor = raw.get("seat", raw.get("actor", data.get("seat", data.get("actor"))))
    phase = raw.get("phase") or data.get("phase")
    legal_before = raw.get(
        "legal_actions_before",
        raw.get("legalActionsBefore", raw.get("legal")))
    if legal_before is None:
        legal_before = data.get("legal_actions_before", data.get("legal"))
    try:
        legal_before = (tuple(int(value) for value in legal_before)
                        if legal_before is not None else None)
    except (TypeError, ValueError):
        legal_before = None
    payload: dict[str, Any] = {}
    if etype in ("tile_drawn", "draw"):
        kind = "DRAW_PUBLIC"
    elif etype in ("tile_discarded", "discard"):
        kind = "DISCARD"
        if raw.get("tile") is not None:
            tile = raw.get("tile")
            if isinstance(tile, str):
                from ..platform.proto import tidx
                tile = tidx(tile)
            payload["tile"] = int(tile)
    elif etype in ("pass", "chi", "peng", "pong", "gang", "hu",
                   "round_start", "round_started", "round_end", "round_ended"):
        kind = _event_type(etype)
        tile = raw.get("tile")
        if isinstance(tile, str):
            from ..platform.proto import tidx
            tile = tidx(tile)
        if tile is not None:
            payload["tile"] = int(tile)
        if kind == "CHOW":
            tiles = data.get("tiles") or raw.get("tiles")
            if tiles:
                from ..platform.proto import tidx
                parsed = [tidx(item) if isinstance(item, str) else int(item)
                          for item in tiles]
                payload["tiles"] = parsed
                if payload.get("tile") is not None:
                    try:
                        payload["position"] = parsed.index(payload["tile"])
                    except ValueError:
                        pass
        if kind == "KONG_OPEN" and data.get("kind") in ("an", "gang_an"):
            kind = "KONG_CLOSED_PUBLIC"
        elif kind == "KONG_OPEN" and data.get("kind") in ("bu", "gang_bu"):
            kind = "KONG_ADD"
    else:
        return None
    return PublicEvent(kind, actor=actor, phase=phase, public_payload=payload,
                       legal_actions_before=legal_before,
                       context_hash_before=raw.get("context_hash_before"),
                       provenance="platform",
                       gid=raw.get("gid"), round_no=raw.get("round_no"),
                       seq=raw.get("seq"), log_format="platform-v1")


def history_from_records(records: Iterable[Mapping[str, Any]]) -> InformationHistory:
    """Build semantic history from Recorder/SSE records.

    Snapshot-only or gap records are intentionally represented as degraded;
    the function never reconstructs a missing PASS/response order from a
    final snapshot.
    """
    history = InformationHistory()
    saw_input = False
    last_sequence = None
    last_stream = None
    for record in records:
        if hasattr(record, "raw"):
            record = record.raw
        if not isinstance(record, Mapping):
            history = history.mark_incomplete("unknown_record_shape")
            continue
        kind = record.get("type")
        if kind == "events":
            batch = record.get("events") or ()
            for raw in batch:
                event = _platform_event(raw)
                if event is None:
                    history = history.mark_incomplete("unknown_or_unusable_event")
                    continue
                stream = (event.gid, event.round_no)
                if (event.seq is not None and last_sequence is not None and
                        stream == last_stream and event.seq != last_sequence + 1):
                    history = history.mark_incomplete("event_sequence_gap")
                if event.seq is not None:
                    last_sequence, last_stream = event.seq, stream
                history = history.append(event)
                saw_input = True
        elif kind == "snapshot":
            history = history.mark_incomplete("snapshot_without_event_history")
        elif kind == "req":
            result = record.get("res") or {}
            if not isinstance(result, Mapping):
                history = history.mark_incomplete("invalid_request_result")
            elif result.get("gap") or result.get("snapshot"):
                history = history.mark_incomplete("state_gap_or_full_snapshot")
        elif kind in ("reset", "end"):
            if kind == "reset":
                history = history.mark_incomplete("mirror_reset")
                last_sequence, last_stream = None, None
    if not saw_input:
        history = history.mark_incomplete("no_event_records")
    return history


def history_from_actions(game, actions: Iterable[int]) -> InformationHistory:
    """Replay an action trace through the authoritative Game state machine."""
    import copy as _copy

    replay = _copy.deepcopy(game)
    # The copy may carry an old history; start from its current public state.
    replay._public_history = InformationHistory().append(PublicEvent(
        "ROUND_START", actor=getattr(replay, "dealer", None),
        phase=getattr(replay, "phase", None),
        context_hash_before=public_state_hash(replay), provenance="game_trace"))
    for action in actions:
        replay.step(int(action))
    return history_from_game(replay)
