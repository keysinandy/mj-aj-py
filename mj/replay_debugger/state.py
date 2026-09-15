"""Pure reference state reconstruction for the replay debugger.

This reducer intentionally has no dependency on ``Mirror`` or ``Game``.  It
models only facts present in the replay evidence and leaves unavailable
private information unknown.
"""

from __future__ import annotations

import copy
from collections import Counter
from typing import Any, Iterable, Mapping

from .adapters import canonical_tile
from .model import (
    EvidenceStrength,
    EventType,
    GameState,
    KnownValue,
    Meld,
    NormalizedEvent,
    PlayerState,
    RiverTile,
    SourceRole,
    stable_id,
)


def _type(event: NormalizedEvent | Mapping[str, Any]) -> str:
    value = event.type if isinstance(event, NormalizedEvent) else event.get("type")
    return str(value).upper()


def _value(event: NormalizedEvent | Mapping[str, Any], key: str, default=None):
    if isinstance(event, NormalizedEvent):
        return getattr(event, key, default)
    return event.get(key, default)


def _payload(event: NormalizedEvent | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(event, NormalizedEvent):
        return event.payload
    return event if isinstance(event, Mapping) else {}


def _tiles(values: Iterable[Any]) -> list[str]:
    return [t for x in values if (t := canonical_tile(x)) is not None]


def _field(value, *, source=None, evidence=EvidenceStrength.RECORDED):
    return KnownValue.known(copy.deepcopy(value), source=source, evidence=evidence)


def _known(value: KnownValue) -> bool:
    return isinstance(value, KnownValue) and value.status.value == "KNOWN"


def _set_hand(player: PlayerState, values: list[str], source, evidence):
    values = list(values)
    player.hand = _field(values, source=source, evidence=evidence)
    player.hand_count = _field(len(values), source=source, evidence=evidence)


def _adjust_hand(player: PlayerState, remove: Iterable[str] = (), add: Iterable[str] = (),
                 source=None, evidence=EvidenceStrength.RECORDED):
    if not _known(player.hand):
        # Count is independent.  A hidden hand may still have a known count.
        if _known(player.hand_count):
            count = int(player.hand_count.value) - len(list(remove)) + len(list(add))
            player.hand_count = _field(max(count, 0), source=source, evidence=evidence)
        return
    values = list(player.hand.value or [])
    for tile in remove:
        try:
            values.remove(tile)
        except ValueError:
            # A bad event should remain visible to diagnostics; do not invent
            # a missing card in order to make the state look consistent.
            continue
    values.extend(add)
    _set_hand(player, values, source, evidence)


class ReferenceReducer:
    """Side-effect-free reducer with an inspectable ``issues`` collection."""

    def __init__(self, *, game_id=None, round_no=None, source=SourceRole.SERVER_TIMELINE,
                 evidence=EvidenceStrength.RECORDED, state: GameState | None = None):
        self.game_id = game_id
        self.source = source
        self.evidence = evidence
        self.state = state.clone() if state is not None else GameState(
            round_id=game_id, round_no=round_no, evidence=evidence)
        self._copy_results = True
        self.issues: list[dict[str, Any]] = []
        self.applied_event_ids: list[str] = []

    def clone(self) -> "ReferenceReducer":
        out = ReferenceReducer(game_id=self.game_id, source=self.source,
                               evidence=self.evidence, state=self.state)
        out.issues = copy.deepcopy(self.issues)
        out.applied_event_ids = list(self.applied_event_ids)
        return out

    def _raw_refs(self, event):
        refs = _value(event, "raw_refs", None)
        return list(refs or [])

    def _record(self, message, event=None, **details):
        item = {"message": message, "rawRefs": self._raw_refs(event) if event else [], **details}
        self.issues.append(item)
        return item

    def initialize_hands(self, hands: list[list[Any]] | None,
                         source=None, evidence=None):
        if not isinstance(hands, list):
            return
        src = source or self.source
        evd = evidence or self.evidence
        for seat, hand in enumerate(hands[:4]):
            if isinstance(hand, list):
                _set_hand(self.state.players[seat], _tiles(hand), src, evd)
        self.state.evidence = evd

    def apply_snapshot(self, snapshot: Mapping[str, Any], *, raw_refs=(),
                       source=None, evidence=None):
        """Apply a complete or partial state response as an observed anchor."""
        snap = snapshot or {}
        src = source or self.source
        evd = evidence or self.evidence
        s = self.state
        s.evidence = evd
        s.raw_refs.extend(x for x in raw_refs if x not in s.raw_refs)
        for key, attr in (("round_id", "round_id"), ("roundId", "round_id"),
                          ("roundNo", "round_no"), ("dealer", "dealer_seat"),
                          ("dealerSeat", "dealer_seat"), ("currentTurn", "current_turn"),
                          ("turn", "current_turn"), ("next_seat", "next_seat"),
                          ("nextSeat", "next_seat"),
                          ("phase", "phase")):
            if snap.get(key) is not None:
                setattr(s, attr, snap[key])
        if snap.get("round_no") is not None:
            s.round_no = snap["round_no"]
        responding = snap.get("responding_seats", snap.get("respondingSeats"))
        if responding is not None:
            s.responding_seats = _field(list(responding), source=src, evidence=evd)
        if snap.get("pending") is not None:
            s.pending = _field(copy.deepcopy(snap["pending"]), source=src, evidence=evd)
        elif snap.get("last_discard") is not None and snap.get("turn") is not None:
            s.pending = _field({"seat": snap["turn"], "tile": canonical_tile(snap["last_discard"])},
                               source=src, evidence=evd)
        wall_remaining = snap.get("wall_remaining", snap.get("wallRemaining"))
        live_wall_left = snap.get("live_wall_left", snap.get("liveWallLeft"))
        if wall_remaining is not None:
            s.remaining_tiles = _field(wall_remaining, source=src, evidence=evd)
        if live_wall_left is not None:
            s.live_wall_left = _field(live_wall_left, source=src, evidence=evd)
        elif wall_remaining is not None:
            # Preserve the raw wall metric as received; profile-specific
            # conversion is intentionally exposed in flags rather than hidden.
            s.live_wall_left = _field(wall_remaining, source=src, evidence=evd)
        deadline = snap.get("window_deadline_ms", snap.get("windowDeadlineMs"))
        if deadline is not None:
            s.deadline = _field(deadline, source=src, evidence=evd)
        god = snap.get("god") if isinstance(snap.get("god"), Mapping) else {}
        for key, val in god.items():
            s.flags[key] = _field(val, source=src, evidence=evd)
        for key in ("freeze", "freezer", "pending_discard", "catch_play",
                    "drawn_tile", "scores", "last_discard"):
            if key in snap:
                s.flags[key] = _field(copy.deepcopy(snap[key]), source=src, evidence=evd)
        seat = snap.get("seat")
        if isinstance(seat, int) and 0 <= seat < 4 and isinstance(snap.get("my_hand"), list):
            _set_hand(s.players[seat], _tiles(snap["my_hand"]), src, evd)
        hands = snap.get("hands")
        if isinstance(hands, list):
            self.initialize_hands(hands, source=src, evidence=evd)
        counts = snap.get("hand_counts") or snap.get("handCounts")
        if isinstance(counts, list):
            for p, count in enumerate(counts[:4]):
                if count is not None:
                    pstate = s.players[p]
                    pstate.hand_count = _field(count, source=src, evidence=evd)
                    if not _known(pstate.hand):
                        pstate.hand = KnownValue.hidden(raw_refs)
        rivers = snap.get("discards")
        if isinstance(rivers, list):
            for seat, river in enumerate(rivers[:4]):
                entries: list[RiverTile] = []
                for pos, item in enumerate(river or []):
                    if isinstance(item, Mapping):
                        tile = canonical_tile(item.get("tile"))
                        entries.append(RiverTile(tile=tile,
                            discard_seq_no=item.get("seq", item.get("discardSeqNo")),
                            called=item.get("called", False), called_by=item.get("calledBy"),
                            call_type=item.get("callType"),
                            source_event_id=item.get("sourceEventId"), raw_refs=list(raw_refs)))
                    else:
                        entries.append(RiverTile(tile=canonical_tile(item), raw_refs=list(raw_refs)))
                s.players[seat].river = entries
        melds = snap.get("melds")
        if isinstance(melds, list):
            for seat, raw_melds in enumerate(melds[:4]):
                s.players[seat].melds = [self._meld_from_snapshot(seat, m, pos, raw_refs)
                                         for pos, m in enumerate(raw_melds or [])]
        s.last_action = copy.deepcopy(snap.get("last_action", s.last_action))

    def _meld_from_snapshot(self, seat, raw, pos, raw_refs):
        if isinstance(raw, Mapping):
            kind = str(raw.get("kind", raw.get("type", "UNKNOWN"))).upper()
            kind = {"PONG": "PON", "CHOW": "CHI", "GANG_MING": "KAN_OPEN",
                    "MING": "KAN_OPEN", "GANG_AN": "KAN_CLOSED", "AN": "KAN_CLOSED",
                    "GANG_BU": "KAN_ADDED", "BU": "KAN_ADDED"}.get(kind, kind)
            tiles = _tiles(raw.get("tiles") or ([raw.get("tile")] if raw.get("tile") else []))
            return Meld(str(raw.get("meldId") or stable_id("meld", seat, pos, kind, tuple(tiles))),
                        kind, seat, raw.get("fromSeat"), tiles,
                        raw.get("sourceDiscardEventId"), raw.get("createdSeqNo"),
                        raw.get("updatedSeqNo"), list(raw_refs))
        if isinstance(raw, (list, tuple)) and raw:
            kind = str(raw[0]).upper()
            tiles = _tiles(raw[1:])
            return Meld(stable_id("meld", seat, pos, kind, tuple(tiles)), kind, seat,
                        None, tiles, raw_refs=list(raw_refs))
        return Meld(stable_id("meld", seat, pos, "UNKNOWN"), "UNKNOWN", seat,
                    raw_refs=list(raw_refs))

    def _result(self):
        return self.state.clone() if self._copy_results else self.state

    def apply_event(self, event: NormalizedEvent | Mapping[str, Any], *,
                    copy_result: bool | None = None):
        et = _type(event)
        previous_copy_mode = self._copy_results
        if copy_result is not None:
            self._copy_results = bool(copy_result)
        try:
            return self._apply_event(event, et)
        finally:
            self._copy_results = previous_copy_mode

    def _apply_event(self, event, et):
        if et in (EventType.ROUND_START.value, "ROUND_START"):
            payload = _payload(event)
            hands = payload.get("start_hands") or payload.get("startHands")
            self.initialize_hands(hands)
            if _value(event, "round_no") is not None:
                self.state.round_no = _value(event, "round_no")
        elif et == EventType.DEAL.value:
            payload = _payload(event)
            self.initialize_hands(payload.get("hands") or payload.get("start_hands"))
            seat = _value(event, "seat")
            if seat is not None and payload.get("tiles"):
                _adjust_hand(self.state.players[seat], add=_tiles(payload["tiles"]),
                             source=self.source, evidence=self.evidence)
        elif et in (EventType.DRAW.value, EventType.DISCARD.value,
                     EventType.CHI.value, EventType.PON.value,
                     EventType.KAN_OPEN.value, EventType.KAN_CLOSED.value,
                     EventType.KAN_ADDED.value, EventType.WIN.value,
                     EventType.ROUND_END.value, EventType.GAME_END.value,
                     EventType.PASS.value, EventType.TIMEOUT.value):
            self._apply_mahjong_event(event, et)
        elif et in (EventType.SSE_RECEIVED.value, EventType.SSE_CLOSED.value,
                    EventType.SSE_CONNECT.value, EventType.SSE_DISCONNECT.value,
                    EventType.SSE_RECONNECT.value, EventType.STATE_REQUEST.value,
                    EventType.STATE_RESPONSE.value, EventType.STATE_MERGE.value,
                    EventType.UNKNOWN.value, EventType.READY.value,
                    EventType.RIICHI.value):
            # Notifications and unsupported protocol records have no implicit
            # Mahjong transition.
            return self._result()
        self.state.raw_refs.extend(x for x in self._raw_refs(event)
                                   if x not in self.state.raw_refs)
        event_id = _value(event, "event_id")
        if event_id:
            self.applied_event_ids.append(event_id)
        return self._result()

    def _apply_mahjong_event(self, event, et):
        seat = _value(event, "seat")
        seq = _value(event, "seq_no")
        tile = canonical_tile(_value(event, "tile"))
        payload = _payload(event)
        if seat is not None and not (0 <= int(seat) < 4):
            self._record("seat outside four-player table", event, seat=seat)
            seat = None
        s = self.state
        if et == EventType.DRAW.value:
            if seat is None or tile is None:
                self._record("draw lacks seat or tile", event)
                return
            _adjust_hand(s.players[seat], add=[tile], source=self.source, evidence=self.evidence)
            s.current_turn, s.next_seat, s.phase = seat, seat, "DISCARD"
            s.last_action = {"eventId": _value(event, "event_id"), "type": "DRAW",
                             "seat": seat, "tile": tile}
            return
        if et == EventType.DISCARD.value:
            if seat is None or tile is None:
                self._record("discard lacks seat or tile", event)
                return
            p = s.players[seat]
            _adjust_hand(p, remove=[tile], source=self.source, evidence=self.evidence)
            river = RiverTile(tile=tile, discard_seq_no=seq,
                              source_event_id=_value(event, "event_id"),
                              raw_refs=self._raw_refs(event))
            p.river.append(river)
            s.pending = _field({"seat": seat, "tile": tile,
                                "eventId": _value(event, "event_id")},
                               source=self.source, evidence=self.evidence)
            s.current_turn, s.next_seat, s.phase = seat, (seat + 1) % 4, "RESPONSE"
            s.last_action = {"eventId": _value(event, "event_id"), "type": "DISCARD",
                             "seat": seat, "tile": tile}
            return
        if et in (EventType.CHI.value, EventType.PON.value,
                   EventType.KAN_OPEN.value):
            self._apply_claim(event, et)
            return
        if et in (EventType.KAN_CLOSED.value, EventType.KAN_ADDED.value):
            self._apply_self_kan(event, et)
            return
        if et in (EventType.PASS.value, EventType.TIMEOUT.value):
            s.last_action = {"eventId": _value(event, "event_id"), "type": et,
                             "seat": seat}
            return
        s.last_action = {"eventId": _value(event, "event_id"), "type": et,
                         "seat": seat, "tile": tile}
        if et == EventType.ROUND_END.value:
            s.phase = "ROUND_END"

    def _pending_river(self, tile, from_seat=None):
        s = self.state
        candidates = []
        seats = [from_seat] if from_seat is not None else range(4)
        for owner in seats:
            if owner is None or not (0 <= owner < 4):
                continue
            for river in reversed(s.players[owner].river):
                if river.tile == tile and not river.called:
                    candidates.append((owner, river))
                    break
        return candidates[0] if candidates else (None, None)

    def _apply_claim(self, event, et):
        seat = _value(event, "seat")
        tile = canonical_tile(_value(event, "tile"))
        payload = _payload(event)
        from_seat = _value(event, "from_seat")
        if from_seat is None:
            from_seat = payload.get("fromSeat", payload.get("sourceSeat"))
        from_seat = int(from_seat) if from_seat is not None else None
        owner, river = self._pending_river(tile, from_seat)
        if river is None:
            self._record("claim has no evidenced eligible source discard", event,
                         eventType=et, tile=tile, fromSeat=from_seat,
                         historyUnknown=not bool(self.state.raw_refs))
        else:
            river.called = True
            river.called_by = seat
            river.call_type = et
        raw_tiles = _value(event, "tiles") or payload.get("tiles") or []
        claim_tiles = _tiles(raw_tiles)
        if et == EventType.PON.value and tile:
            claim_tiles = [tile, tile, tile]
        elif et == EventType.KAN_OPEN.value and tile:
            claim_tiles = [tile] * 4
        elif et == EventType.CHI.value and tile:
            claim_tiles = claim_tiles + [tile]
        meld = Meld(
            meld_id=stable_id("meld", _value(event, "event_id"), seat, et, tile),
            type=et, owner_seat=seat, from_seat=owner if owner is not None else from_seat,
            tiles=claim_tiles, source_discard_event_id=river.source_event_id if river else None,
            created_seq_no=_value(event, "seq_no"), updated_seq_no=_value(event, "seq_no"),
            raw_refs=self._raw_refs(event))
        if seat is not None and 0 <= int(seat) < 4:
            player = self.state.players[seat]
            if not any(m.meld_id == meld.meld_id for m in player.melds):
                player.melds.append(meld)
            hand_remove = claim_tiles[:]
            if tile and hand_remove:
                try:
                    hand_remove.remove(tile)
                except ValueError:
                    pass
            _adjust_hand(player, remove=hand_remove, source=self.source, evidence=self.evidence)
        self.state.pending = KnownValue.unknown(self.evidence, self._raw_refs(event))
        self.state.current_turn = seat
        self.state.next_seat = seat
        self.state.phase = "DISCARD"
        self.state.last_action = {"eventId": _value(event, "event_id"), "type": et,
                                  "seat": seat, "tile": tile,
                                  "fromSeat": owner if owner is not None else from_seat}

    def _apply_self_kan(self, event, et):
        seat = _value(event, "seat")
        tile = canonical_tile(_value(event, "tile"))
        if seat is None or tile is None:
            self._record("kan lacks seat or tile", event)
            return
        p = self.state.players[seat]
        if et == EventType.KAN_ADDED.value:
            parent = next((m for m in p.melds if m.type == EventType.PON.value and tile in m.tiles), None)
            if parent is None:
                self._record("added kan has no evidenced pon parent", event, tile=tile)
            else:
                parent.type = et
                parent.tiles = [tile] * 4
                parent.updated_seq_no = _value(event, "seq_no")
                parent.raw_refs.extend(x for x in self._raw_refs(event) if x not in parent.raw_refs)
                parent.parent_meld_id = parent.meld_id
            _adjust_hand(p, remove=[tile], source=self.source, evidence=self.evidence)
        else:
            _adjust_hand(p, remove=[tile] * 4, source=self.source, evidence=self.evidence)
            p.melds.append(Meld(
                stable_id("meld", _value(event, "event_id"), seat, et, tile), et,
                seat, None, [tile] * 4, created_seq_no=_value(event, "seq_no"),
                updated_seq_no=_value(event, "seq_no"), raw_refs=self._raw_refs(event)))
        self.state.current_turn = seat
        self.state.next_seat = seat
        self.state.phase = "DISCARD"
        self.state.last_action = {"eventId": _value(event, "event_id"), "type": et,
                                  "seat": seat, "tile": tile}

    def as_state(self) -> GameState:
        return self.state.clone()


def validate_state(state: GameState | Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """Validate only evidenced business fields and leave unknowns untouched."""
    if state is None:
        return [{"kind": "STATE_INVARIANT_ERROR", "message": "state is unavailable"}]
    value = state.as_dict() if isinstance(state, GameState) else dict(state)
    issues: list[dict[str, Any]] = []
    players = value.get("players") or []
    if len(players) != 4:
        issues.append({"kind": "STATE_INVARIANT_ERROR",
                       "message": f"expected four players, found {len(players)}",
                       "field": "players"})
    meld_ids: set[str] = set()
    all_hands_known = len(players) == 4
    tile_counts: Counter[str] = Counter()
    for index, player in enumerate(players):
        seat = player.get("seat") if isinstance(player, Mapping) else None
        if seat not in (0, 1, 2, 3):
            issues.append({"kind": "STATE_INVARIANT_ERROR",
                           "message": "player seat is outside a four-player table",
                           "field": f"players[{index}].seat"})
        hand = player.get("hand", {}) if isinstance(player, Mapping) else {}
        count = player.get("handCount", {}) if isinstance(player, Mapping) else {}
        hand_known = isinstance(hand, Mapping) and hand.get("status") == "KNOWN"
        count_known = isinstance(count, Mapping) and count.get("status") == "KNOWN"
        if not hand_known:
            all_hands_known = False
        if hand_known and count_known and isinstance(hand.get("value"), list):
            if len(hand["value"]) != count.get("value"):
                issues.append({"kind": "STATE_INVARIANT_ERROR",
                               "message": "known hand length differs from known hand count",
                               "field": f"players[{index}].hand"})
            tile_counts.update(x for x in hand["value"] if x is not None)
        for river in player.get("river", []) if isinstance(player, Mapping) else []:
            if not isinstance(river, Mapping):
                continue
            if river.get("called"):
                if river.get("calledBy") is None or not river.get("callType"):
                    issues.append({"kind": "STATE_INVARIANT_ERROR",
                                   "message": "called river lacks caller or call type",
                                   "field": f"players[{index}].river"})
                # A claimed discard is represented by its meld, not counted
                # a second time as an available physical tile.
            elif river.get("tile") is not None:
                tile_counts[river["tile"]] += 1
        for meld in player.get("melds", []) if isinstance(player, Mapping) else []:
            if not isinstance(meld, Mapping):
                continue
            meld_id = meld.get("meldId")
            if meld_id in meld_ids:
                issues.append({"kind": "STATE_INVARIANT_ERROR",
                               "message": "duplicate meld identity",
                               "field": "melds", "meldId": meld_id})
            if meld_id is not None:
                meld_ids.add(meld_id)
            tile_counts.update(x for x in meld.get("tiles", []) if x is not None)
    if all_hands_known:
        for tile, count in tile_counts.items():
            if count > 4:
                issues.append({"kind": "STATE_INVARIANT_ERROR",
                               "message": f"known tile multiplicity exceeds four: {tile}",
                               "field": "tileMultiplicity", "tile": tile,
                               "count": count})
    round_data = value.get("round") if isinstance(value.get("round"), Mapping) else {}
    for field in ("currentTurn", "nextSeat"):
        field_value = round_data.get(field)
        if field_value is not None and field_value not in (0, 1, 2, 3):
            issues.append({"kind": "STATE_INVARIANT_ERROR",
                           "message": f"{field} is outside a four-player table",
                           "field": f"round.{field}"})
    for field in ("remainingTiles", "liveWallLeft"):
        metric = round_data.get(field)
        if isinstance(metric, Mapping) and metric.get("status") == "KNOWN":
            if not isinstance(metric.get("value"), (int, float)) or metric["value"] < 0:
                issues.append({"kind": "STATE_INVARIANT_ERROR",
                               "message": f"{field} has an invalid known value",
                               "field": f"round.{field}"})
    return issues


validate_invariants = validate_state


def replay_events(events: Iterable[NormalizedEvent], *, initial: GameState | None = None,
                  game_id=None, source=SourceRole.SERVER_TIMELINE,
                  evidence=EvidenceStrength.RECORDED) -> list[GameState]:
    reducer = ReferenceReducer(game_id=game_id, source=source, evidence=evidence,
                               state=initial)
    states: list[GameState] = [reducer.as_state()]
    for event in events:
        reducer.apply_event(event)
        states.append(reducer.as_state())
    return states


def state_from_dict(value: Mapping[str, Any] | None) -> GameState | None:
    """Restore a trace checkpoint without treating it as a server truth."""
    if not isinstance(value, Mapping):
        return None
    round_data = value.get("round") if isinstance(value.get("round"), Mapping) else {}
    state = GameState(round_id=round_data.get("roundId"),
                      round_no=round_data.get("roundNo"),
                      dealer_seat=round_data.get("dealerSeat"),
                      current_turn=round_data.get("currentTurn"),
                      next_seat=round_data.get("nextSeat"),
                      phase=round_data.get("phase"),
                      evidence=EvidenceStrength(value.get("evidence", "UNKNOWN"))
                      if value.get("evidence") in [x.value for x in EvidenceStrength]
                      else EvidenceStrength.UNKNOWN)
    def kv(v):
        if not isinstance(v, Mapping):
            return KnownValue.unknown()
        status = v.get("status", "UNKNOWN")
        try:
            from .model import KnowledgeState
            ks = KnowledgeState(status)
        except ValueError:
            from .model import KnowledgeState
            ks = KnowledgeState.UNKNOWN
        return KnownValue(ks, copy.deepcopy(v.get("value")), v.get("source"),
                          EvidenceStrength(v.get("evidence", "UNKNOWN"))
                          if v.get("evidence") in [x.value for x in EvidenceStrength]
                          else EvidenceStrength.UNKNOWN,
                          tuple(v.get("rawRefs") or []))
    state.remaining_tiles = kv(round_data.get("remainingTiles"))
    state.live_wall_left = kv(round_data.get("liveWallLeft"))
    state.pending = kv(round_data.get("pending"))
    state.responding_seats = kv(round_data.get("respondingSeats"))
    state.deadline = kv(round_data.get("deadline"))
    state.frozen = kv(round_data.get("frozen"))
    state.last_action = copy.deepcopy(value.get("lastAction"))
    state.players = []
    for i, raw_player in enumerate(value.get("players") or []):
        raw_player = raw_player if isinstance(raw_player, Mapping) else {}
        p = PlayerState(int(raw_player.get("seat", i)),
                        hand=kv(raw_player.get("hand")),
                        hand_count=kv(raw_player.get("handCount")))
        for river in raw_player.get("river") or []:
            if isinstance(river, Mapping):
                p.river.append(RiverTile(river.get("tile"), river.get("discardSeqNo"),
                                         river.get("called"), river.get("calledBy"),
                                         river.get("callType"), river.get("sourceEventId"),
                                         list(river.get("rawRefs") or [])))
        for meld in raw_player.get("melds") or []:
            if isinstance(meld, Mapping):
                p.melds.append(Meld(str(meld.get("meldId")), str(meld.get("type")),
                                    meld.get("ownerSeat"), meld.get("fromSeat"),
                                    list(meld.get("tiles") or []),
                                    meld.get("sourceDiscardEventId"),
                                    meld.get("createdSeqNo"), meld.get("updatedSeqNo"),
                                    list(meld.get("rawRefs") or []), meld.get("parentMeldId")))
        p.flags = {k: kv(v) if isinstance(v, Mapping) and "status" in v else copy.deepcopy(v)
                   for k, v in (raw_player.get("flags") or {}).items()}
        state.players.append(p)
    while len(state.players) < 4:
        state.players.append(PlayerState(len(state.players)))
    state.flags = {k: kv(v) if isinstance(v, Mapping) and "status" in v else copy.deepcopy(v)
                   for k, v in (value.get("flags") or {}).items()}
    state.raw_refs = list(value.get("rawRefs") or [])
    return state


def business_projection(state: GameState | Mapping[str, Any] | None) -> dict[str, Any] | None:
    if state is None:
        return None
    value = state.as_dict() if isinstance(state, GameState) else dict(state)
    # Keep business fields; raw references and evidence metadata are useful in
    # inspectors but should not create a false state difference.
    out = copy.deepcopy(value)
    out.pop("rawRefs", None)
    out.pop("evidence", None)
    for player in out.get("players", []):
        player.pop("flags", None)
        for river in player.get("river", []):
            river.pop("rawRefs", None)
        for meld in player.get("melds", []):
            meld.pop("rawRefs", None)
    return out


def project_visibility(state: GameState | Mapping[str, Any] | None, mode: str,
                       selected_player: int = 0, *, server_state=None,
                       local_state=None) -> dict[str, Any] | None:
    """Project every hand-bearing surface through one visibility function."""
    if state is None:
        return None
    value = copy.deepcopy(state.as_dict() if isinstance(state, GameState) else dict(state))
    mode = str(mode).upper()
    players = value.get("players") or []
    for player in players:
        seat = player.get("seat")
        if mode == "OMNISCIENT":
            continue
        if mode == "PLAYER_VIEW" and seat == selected_player:
            continue
        hand = player.get("hand") or {}
        if hand.get("status") == "KNOWN":
            player["hand"] = {"status": "HIDDEN", "evidence": hand.get("evidence", "RECORDED"),
                              "source": hand.get("source")}
    value["visibilityMode"] = mode
    value["selectedPlayer"] = selected_player
    return value
