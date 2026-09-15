"""Read-only adapters for server exports, recorder JSONL, dumps and traces."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from ..platform.proto import API_NAME, NAME_TO_IDX
from .model import (
    EvidenceStrength,
    EventType,
    KnowledgeState,
    LocalStep,
    LocalStepType,
    NormalizedEvent,
    RawRecord,
    Request,
    RequestClassification,
    SourceRole,
    stable_id,
)


_TYPE_ALIASES = {
    "round_start": EventType.ROUND_START,
    "round_started": EventType.ROUND_START,
    "deal": EventType.DEAL,
    "dealt": EventType.DEAL,
    "tile_drawn": EventType.DRAW,
    "draw": EventType.DRAW,
    "tile_discarded": EventType.DISCARD,
    "discard": EventType.DISCARD,
    "chi": EventType.CHI,
    "chow": EventType.CHI,
    "peng": EventType.PON,
    "pon": EventType.PON,
    "pong": EventType.PON,
    "gang": EventType.KAN_OPEN,
    "kan": EventType.KAN_OPEN,
    "kan_open": EventType.KAN_OPEN,
    "gang_ming": EventType.KAN_OPEN,
    "open_kan": EventType.KAN_OPEN,
    "kan_closed": EventType.KAN_CLOSED,
    "gang_an": EventType.KAN_CLOSED,
    "closed_kan": EventType.KAN_CLOSED,
    "kan_added": EventType.KAN_ADDED,
    "gang_bu": EventType.KAN_ADDED,
    "added_kan": EventType.KAN_ADDED,
    "pass": EventType.PASS,
    "timeout": EventType.TIMEOUT,
    "ready": EventType.READY,
    "riichi": EventType.RIICHI,
    "reach": EventType.RIICHI,
    "hu": EventType.WIN,
    "win": EventType.WIN,
    "round_ended": EventType.ROUND_END,
    "round_end": EventType.ROUND_END,
    "game_ended": EventType.GAME_END,
    "game_end": EventType.GAME_END,
    "state_request": EventType.STATE_REQUEST,
    "state_response": EventType.STATE_RESPONSE,
    "state_merge": EventType.STATE_MERGE,
    "sse_frame": EventType.SSE_RECEIVED,
    "sse_received": EventType.SSE_RECEIVED,
    "sse_connect": EventType.SSE_CONNECT,
    "sse_disconnect": EventType.SSE_DISCONNECT,
    "sse_reconnect": EventType.SSE_RECONNECT,
    "sse_closed": EventType.SSE_CLOSED,
}


def canonical_tile(value: Any) -> str | None:
    """Map platform and documentation spellings to the platform 34-tile name.

    The raw spelling is retained in every normalized payload.  ``m/p/s`` map
    to the project's ``w/b/t`` API suits; honors and Chinese names pass
    through unchanged.
    """
    if value is None or value == "":
        return None
    if isinstance(value, int):
        return API_NAME[value] if 0 <= value < len(API_NAME) else None
    value = str(value)
    if value in NAME_TO_IDX:
        return value
    if value in "东南西北中发白":
        return value
    if len(value) >= 2 and value[0] in "123456789" and value[1].lower() in "mpswbt":
        suit = {"m": "w", "p": "b", "s": "t"}.get(value[1].lower(), value[1].lower())
        candidate = value[0] + suit
        if candidate in NAME_TO_IDX:
            return candidate
    # Common Chinese tile spellings used in hand-written fixtures.
    for prefix, suit in (("万", "w"), ("筒", "b"), ("饼", "b"), ("条", "t"), ("索", "t")):
        if value.endswith(prefix) and value[:-1] in "123456789":
            return value[:-1] + suit
    return None


def _event_type(raw_type: Any, data: Mapping[str, Any] | None = None) -> tuple[EventType, str | None]:
    original = None if raw_type is None else str(raw_type)
    key = (original or "").lower()
    kind = str((data or {}).get("kind") or "").lower()
    if key in ("gang", "kan"):
        if kind in ("an", "closed", "gang_an", "kan_closed"):
            return EventType.KAN_CLOSED, original
        if kind in ("bu", "added", "gang_bu", "kan_added"):
            return EventType.KAN_ADDED, original
        return EventType.KAN_OPEN, original
    return _TYPE_ALIASES.get(key, EventType.UNKNOWN), original


def _first(mapping: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if mapping.get(key) is not None:
            return mapping[key]
    return None


def _as_int(value: Any) -> int | None:
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None


def normalize_event(raw: Mapping[str, Any], *, source: SourceRole | str,
                    raw_ref: str | None = None, game_id: str | None = None,
                    round_no: int | None = None,
                    local_timestamp: Any = None,
                    evidence: EvidenceStrength = EvidenceStrength.RECORDED,
                    ordinal: int | None = None) -> NormalizedEvent:
    """Normalize one protocol event without dropping unsupported semantics."""
    data = raw.get("data") if isinstance(raw.get("data"), Mapping) else {}
    type_data = dict(raw)
    type_data.update(data)
    etype, original = _event_type(raw.get("type"), type_data)
    rno = _as_int(_first(raw, "round_no", "roundNo"))
    if rno is None:
        rno = _as_int(_first(data, "round_no", "roundNo")) or round_no
    seq = _as_int(_first(raw, "seq", "seqNo", "source_seq"))
    seat = _as_int(_first(raw, "seat", "player", "owner", "ownerSeat"))
    from_seat = _as_int(_first(raw, "from_seat", "fromSeat", "source_seat", "sourceSeat"))
    if from_seat is None:
        from_seat = _as_int(_first(data, "from_seat", "fromSeat", "source_seat", "sourceSeat"))
    tile_raw = _first(raw, "tile", "tile_name")
    tile = canonical_tile(tile_raw)
    if tile is None:
        tile = canonical_tile(_first(data, "tile", "tile_name"))
    raw_tiles = _first(raw, "tiles")
    if raw_tiles is None:
        raw_tiles = _first(data, "tiles")
    if not isinstance(raw_tiles, list):
        raw_tiles = []
    tiles = [t for x in raw_tiles if (t := canonical_tile(x)) is not None]
    if etype == EventType.CHI and tile and len(tiles) == 3:
        # Platform chi payload includes the claimed discard.  The normalized
        # tiles are the two cards taken from the local hand.
        removed = False
        hand_tiles = []
        for item in tiles:
            if item == tile and not removed:
                removed = True
            else:
                hand_tiles.append(item)
        if removed:
            tiles = sorted(hand_tiles)
    payload = copy.deepcopy(dict(raw))
    payload.setdefault("rawTile", tile_raw)
    if raw_tiles:
        payload.setdefault("rawTiles", copy.deepcopy(raw_tiles))
    event_parts = [game_id, rno, seq, str(etype), seat, tile,
                   tuple(tiles), raw.get("id"), raw.get("event_id"), ordinal]
    event_id = stable_id("event", *event_parts)
    return NormalizedEvent(
        event_id=event_id,
        type=etype,
        source=source,
        evidence=evidence,
        game_id=game_id,
        round_no=rno,
        seq_no=seq,
        source_seq=_first(raw, "source_seq", "sourceSeq", "seq"),
        server_timestamp=_first(raw, "server_ts", "serverTimestamp", "ts"),
        local_timestamp=local_timestamp,
        clock_domain=_first(raw, "clock_domain", "clockDomain"),
        precision=_first(raw, "precision", "timestamp_precision"),
        seat=seat,
        from_seat=from_seat,
        tile=tile,
        tiles=tiles,
        raw_refs=[raw_ref] if raw_ref else [],
        payload=payload,
        original_type=original,
        visibility=(KnowledgeState.UNKNOWN if etype == EventType.UNKNOWN
                    else KnowledgeState.KNOWN),
    )


@dataclass
class EvidenceBundle:
    game_id: str | None = None
    seat: int | None = None
    raw_records: list[RawRecord] = field(default_factory=list)
    events: list[NormalizedEvent] = field(default_factory=list)
    local_steps: list[LocalStep] = field(default_factory=list)
    requests: list[Request] = field(default_factory=list)
    snapshots: list[dict[str, Any]] = field(default_factory=list)
    server_start_hands: list[list[str]] | None = None
    server_start_hands_by_round: dict[int | None, list[list[str]]] = field(default_factory=dict)
    server_rounds: list[dict[str, Any]] = field(default_factory=list)
    input_hashes: dict[str, str] = field(default_factory=dict)
    quality: list[dict[str, Any]] = field(default_factory=list)
    identities: dict[str, set[str]] = field(default_factory=dict)
    trace_metadata: dict[str, Any] = field(default_factory=dict)

    def add_raw(self, record: RawRecord) -> str:
        self.raw_records.append(record)
        if record.source_path and record.content_hash:
            self.input_hashes[record.source_path] = record.content_hash
        return record.record_id

    def add_quality(self, kind: str, message: str, *, raw_refs=(), **details):
        self.quality.append({"kind": kind, "message": message,
                             "rawRefs": list(raw_refs), **details})


def _file_text(path: str | os.PathLike[str]) -> tuple[str, str]:
    data = Path(path).read_bytes()
    return data.decode("utf-8", "replace"), hashlib.sha256(data).hexdigest()


def read_jsonl(path: str | os.PathLike[str], role: SourceRole | str) -> tuple[list[dict[str, Any]], list[RawRecord], list[dict[str, Any]]]:
    """Read all valid lines while retaining malformed lines and byte offsets."""
    path = str(path)
    data = Path(path).read_bytes()
    text = data.decode("utf-8", "replace")
    records: list[dict[str, Any]] = []
    raws: list[RawRecord] = []
    quality: list[dict[str, Any]] = []
    offset = 0
    for line_no, line in enumerate(text.splitlines(True), 1):
        content = line.rstrip("\r\n")
        start, offset = offset, offset + len(line.encode("utf-8", "replace"))
        if not content.strip():
            continue
        try:
            value = json.loads(content)
        except json.JSONDecodeError as exc:
            raw = RawRecord.make(role, None, source_path=path,
                                 line_no=line_no, byte_start=start,
                                 byte_end=offset, raw_text=content,
                                 metadata={"parseError": str(exc)})
            raws.append(raw)
            item = {"kind": "MALFORMED_JSONL", "message": str(exc),
                    "rawRefs": [raw.record_id], "line": line_no,
                    "sourcePath": path}
            quality.append(item)
            continue
        raw = RawRecord.make(role, value, source_path=path, line_no=line_no,
                             byte_start=start, byte_end=offset,
                             raw_text=content, timestamp=value.get("ts")
                             if isinstance(value, Mapping) else None,
                             clock_domain="epoch" if isinstance(value, Mapping) and value.get("ts") is not None else None)
        raws.append(raw)
        if isinstance(value, Mapping):
            records.append(dict(value))
        else:
            quality.append({"kind": "INVALID_JSONL_RECORD",
                            "message": "JSONL value is not an object",
                            "rawRefs": [raw.record_id], "line": line_no})
    return records, raws, quality


def _extract_game_id(doc: Mapping[str, Any]) -> str | None:
    value = _first(doc, "gid", "game_id", "gameId", "id")
    if value is not None and not isinstance(value, (dict, list)):
        return str(value)
    for key in ("game", "metadata", "meta"):
        nested = doc.get(key)
        if isinstance(nested, Mapping):
            value = _first(nested, "gid", "game_id", "gameId", "id")
            if value is not None:
                return str(value)
    return None


def _round_events(doc: Mapping[str, Any]) -> Iterable[tuple[int | None, list[dict[str, Any]], dict[str, Any], list[list[str]] | None]]:
    blocks = doc.get("blocks")
    if isinstance(blocks, list):
        current: dict[str, Any] | None = None
        for block in blocks:
            if not isinstance(block, Mapping):
                continue
            hands = block.get("start_hands")
            is_start = isinstance(hands, list) and any(x is not None for x in hands)
            if is_start or current is None:
                if current is not None:
                    yield current["round_no"], current["events"], current["meta"], current["hands"]
                current = {"round_no": _as_int(_first(block, "round_no", "roundNo")),
                           "events": [], "meta": {}, "hands": hands if is_start else None}
            current["events"].extend(x for x in block.get("events", []) if isinstance(x, Mapping))
            if isinstance(block.get("round_no"), int):
                current["round_no"] = block["round_no"]
        if current is not None:
            yield current["round_no"], current["events"], current["meta"], current["hands"]
        return
    rounds = doc.get("rounds")
    if isinstance(rounds, list):
        for index, round_doc in enumerate(rounds):
            if not isinstance(round_doc, Mapping):
                continue
            yield (_as_int(_first(round_doc, "round_no", "roundNo")) or index + 1,
                   [x for x in round_doc.get("events", []) if isinstance(x, Mapping)],
                   dict(round_doc),
                   round_doc.get("start_hands")
                   if isinstance(round_doc.get("start_hands"), list) else None)
        return
    events = doc.get("events")
    if isinstance(events, list):
        yield _as_int(_first(doc, "round_no", "roundNo")), \
            [x for x in events if isinstance(x, Mapping)], \
            {}, doc.get("start_hands") if isinstance(doc.get("start_hands"), list) else None


def import_server_timeline(path: str | os.PathLike[str], bundle: EvidenceBundle | None = None) -> EvidenceBundle:
    bundle = bundle or EvidenceBundle()
    path = str(path)
    text, digest = _file_text(path)
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as exc:
        raw = RawRecord.make(SourceRole.SERVER_TIMELINE, None,
                             source_path=path, raw_text=text, truncated=True,
                             metadata={"parseError": str(exc)})
        bundle.add_raw(raw)
        bundle.add_quality("MALFORMED_SERVER", str(exc), raw_refs=[raw.record_id])
        return bundle
    root = RawRecord.make(SourceRole.SERVER_TIMELINE, doc,
                          source_path=path, raw_text=text)
    bundle.add_raw(root)
    gid = _extract_game_id(doc) if isinstance(doc, Mapping) else None
    if gid:
        bundle.identities.setdefault("game", set()).add(gid)
        if bundle.game_id is None:
            bundle.game_id = gid
        elif bundle.game_id != gid:
            bundle.add_quality("IDENTITY_CONFLICT", "server game identity conflicts with another source",
                               raw_refs=[root.record_id], expected=bundle.game_id, actual=gid)
    if not isinstance(doc, Mapping):
        bundle.add_quality("INVALID_SERVER", "server timeline root is not an object",
                           raw_refs=[root.record_id])
        return bundle
    rounds_meta = doc.get("rounds") if isinstance(doc.get("rounds"), list) else []
    for idx, (round_no, events, meta, hands) in enumerate(_round_events(doc)):
        if hands is not None:
            converted = [[canonical_tile(x) for x in hand or [] if canonical_tile(x)]
                         for hand in hands]
            actual_round = round_no or (idx + 1)
            bundle.server_start_hands_by_round[actual_round] = converted
            if bundle.server_start_hands is None or actual_round == 1:
                bundle.server_start_hands = converted
        if idx < len(rounds_meta) and isinstance(rounds_meta[idx], Mapping):
            meta = dict(rounds_meta[idx])
        bundle.server_rounds.append(dict(meta or {}, round_no=round_no or (idx + 1)))
        if hands is not None:
            start = {"type": "round_start", "round_no": round_no or (idx + 1),
                     "start_hands": hands}
            start_raw = RawRecord.make(SourceRole.SERVER_TIMELINE, start,
                                       source_path=path,
                                       metadata={"parent": root.record_id})
            bundle.add_raw(start_raw)
            event = normalize_event(start, source=SourceRole.SERVER_TIMELINE,
                                    raw_ref=start_raw.record_id,
                                    game_id=gid or bundle.game_id,
                                    round_no=round_no or (idx + 1), ordinal=len(bundle.events))
            event.payload["start_hands"] = converted
            bundle.events.append(event)
        for ordinal, raw_event in enumerate(events):
            raw = RawRecord.make(SourceRole.SERVER_TIMELINE, raw_event,
                                 source_path=path,
                                 metadata={"parent": root.record_id,
                                           "ordinal": ordinal})
            bundle.add_raw(raw)
            event = normalize_event(raw_event, source=SourceRole.SERVER_TIMELINE,
                                    raw_ref=raw.record_id, game_id=gid or bundle.game_id,
                                    round_no=round_no or (idx + 1), ordinal=ordinal)
            if event.type == EventType.UNKNOWN:
                bundle.add_quality("UNKNOWN_EVENT", f"unknown server event type {raw_event.get('type')!r}",
                                   raw_refs=[raw.record_id], originalType=raw_event.get("type"))
            bundle.events.append(event)
    bundle.input_hashes[path] = digest
    return bundle


def _step_for_record(rec: Mapping[str, Any], raw_ref: str, index: int,
                     *, round_no=None, related_seq=None, request_id=None,
                     step_type=LocalStepType.UNKNOWN, outcome=None,
                     payload=None, causal_parents=()) -> LocalStep:
    timestamp = rec.get("ts")
    ordinal = index
    sid = stable_id("step", raw_ref, rec.get("id"), rec.get("type"), index)
    return LocalStep(step_id=sid, index=index, local_ordinal=ordinal,
                     type=step_type, evidence=EvidenceStrength.RECORDED,
                     timestamp=timestamp, clock_domain="epoch" if timestamp is not None else None,
                     related_seq_no=_as_int(related_seq), request_id=request_id,
                     causal_parents=list(causal_parents), payload=copy.deepcopy(dict(payload or rec)),
                     outcome=outcome, raw_refs=[raw_ref], round_no=_as_int(round_no))


def _request_from_record(rec: Mapping[str, Any], raw_ref: str, index: int,
                         game_id: str | None) -> Request:
    logical = _first(rec, "logical_request_id", "request_id", "id")
    if logical is None:
        logical = stable_id("logical-request", game_id, rec.get("seq"), index)
    logical = str(logical)
    rid = stable_id("request", logical, _first(rec, "transport_request_id"),
                    _first(rec, "attempt_index"), raw_ref)
    response = rec.get("res") if isinstance(rec.get("res"), Mapping) else {}
    req = Request(
        request_id=rid,
        logical_request_id=logical,
        transport_request_id=(str(rec["transport_request_id"])
                              if rec.get("transport_request_id") is not None else None),
        attempt_index=_as_int(rec.get("attempt_index")) or 0,
        round_no=_as_int(rec.get("round_no")),
        requested_seq=_as_int(_first(rec, "requested_seq", "seq")),
        response_seq=_as_int(response.get("seq")),
        requested_at=rec.get("started_epoch", rec.get("ts")),
        responded_at=rec.get("ts"),
        applied_at=rec.get("response_applied_at"),
        clock_domain="epoch" if rec.get("ts") is not None else None,
        trigger={"type": rec.get("reason") or rec.get("request_kind") or "UNKNOWN",
                 "confidence": "KNOWN" if rec.get("reason") or rec.get("request_kind") else "UNKNOWN"},
        reasons=[x for x in [rec.get("reason"), rec.get("request_kind")] if x],
        request={"method": "GET", "endpoint": "/api/games/{gid}/state",
                 "seq": _first(rec, "requested_seq", "seq"),
                 "requestedSeq": rec.get("requested_seq")},
        response=copy.deepcopy(dict(response)),
        attempts=[{"attemptIndex": _as_int(rec.get("attempt_index")) or 0,
                   "transportRequestId": rec.get("transport_request_id"),
                   "status": rec.get("status"),
                   "latencyMs": rec.get("latency_ms"),
                   "transport": copy.deepcopy(rec.get("transport"))}],
        evidence=EvidenceStrength.RECORDED,
        raw_refs=[raw_ref],
        association_status="KNOWN" if rec.get("logical_request_id") else "DERIVED",
    )
    return req


def import_local_jsonl(path: str | os.PathLike[str], bundle: EvidenceBundle | None = None) -> EvidenceBundle:
    bundle = bundle or EvidenceBundle()
    records, raws, quality = read_jsonl(path, SourceRole.STATE_RESPONSE)
    # A recorder file is a mixed source: preserve the line verbatim while
    # assigning the role that describes the fact carried by that line.
    converted_raws = []
    for raw in raws:
        if isinstance(raw.raw, Mapping):
            typ = str(raw.raw.get("type", "")).lower()
            role = (SourceRole.SSE if typ == "sse_frame" else
                    SourceRole.STATE_RESPONSE if typ in ("req", "snapshot", "events", "state_reconcile")
                    else SourceRole.LOCAL_DERIVED)
            raw = RawRecord.make(role, raw.raw, source_path=raw.source_path,
                                 line_no=raw.line_no, byte_start=raw.byte_start,
                                 byte_end=raw.byte_end, raw_text=raw.raw_text,
                                 timestamp=raw.timestamp, clock_domain=raw.clock_domain,
                                 precision=raw.precision, metadata=raw.metadata)
        converted_raws.append(raw)
    raws = converted_raws
    bundle.raw_records.extend(raws)
    bundle.quality.extend(quality)
    bundle.input_hashes[str(path)] = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    valid_raws = [r for r in raws if isinstance(r.raw, Mapping)]
    request_by_logical: dict[str, Request] = {}
    last_event_id = None
    for index, rec in enumerate(records):
        typ = str(rec.get("type", "")).lower()
        raw = valid_raws[index] if index < len(valid_raws) else None
        if raw is None:
            raw = RawRecord.make(SourceRole.STATE_RESPONSE, rec, source_path=str(path), line_no=index + 1)
            bundle.raw_records.append(raw)
        if typ == "meta":
            gid = _first(rec, "gid", "game_id", "gameId")
            if gid is not None:
                gid = str(gid)
                bundle.identities.setdefault("game", set()).add(gid)
                bundle.game_id = bundle.game_id or gid
            if rec.get("seat") is not None:
                bundle.seat = _as_int(rec.get("seat"))
            bundle.metadata = getattr(bundle, "metadata", {}) if hasattr(bundle, "metadata") else {}
            continue
        if typ == "snapshot":
            snap = copy.deepcopy(rec.get("snap") if isinstance(rec.get("snap"), Mapping) else {})
            bundle.snapshots.append({"seq": _as_int(rec.get("seq")), "snap": snap,
                                     "rawRefs": [raw.record_id]})
            step = _step_for_record(rec, raw.record_id, len(bundle.local_steps),
                                    related_seq=rec.get("seq"),
                                    step_type=LocalStepType.STATE_RESPONSE,
                                    payload=snap)
            bundle.local_steps.append(step)
            if bundle.seat is None:
                bundle.seat = _as_int(snap.get("seat"))
            last_event_id = None
            continue
        if typ == "events":
            received = rec.get("received_epoch", rec.get("ts"))
            for event_index, event_raw in enumerate(rec.get("events") or []):
                if not isinstance(event_raw, Mapping):
                    bundle.add_quality("INVALID_EVENT", "events item is not an object",
                                       raw_refs=[raw.record_id])
                    continue
                ev = normalize_event(event_raw, source=SourceRole.STATE_RESPONSE,
                                     raw_ref=raw.record_id, game_id=bundle.game_id,
                                     local_timestamp=received,
                                     evidence=EvidenceStrength.RECORDED,
                                     ordinal=len(bundle.events))
                bundle.events.append(ev)
                step = _step_for_record(event_raw, raw.record_id,
                                        len(bundle.local_steps),
                                        related_seq=ev.seq_no,
                                        step_type=(LocalStepType.INPUT if ev.type not in
                                                   (EventType.UNKNOWN,) else LocalStepType.UNKNOWN),
                                        payload=event_raw)
                step.related_event_id = ev.event_id
                if last_event_id:
                    step.causal_parents.append(last_event_id)
                bundle.local_steps.append(step)
                last_event_id = ev.event_id
            continue
        if typ == "sse_frame":
            payload = rec.get("payload") if isinstance(rec.get("payload"), Mapping) else {}
            ev_raw = {"type": "sse_frame", "seq": rec.get("seq"),
                      "data": payload, "closed": rec.get("closed"),
                      "connection_id": rec.get("connection_id")}
            event = normalize_event(ev_raw, source=SourceRole.SSE,
                                    raw_ref=raw.record_id, game_id=bundle.game_id,
                                    local_timestamp=rec.get("received_monotonic", rec.get("ts")),
                                    ordinal=len(bundle.events))
            event.type = (EventType.SSE_CLOSED if rec.get("closed") else EventType.SSE_RECEIVED)
            event.payload["watermark"] = rec.get("seq")
            event.payload["wake_enqueued"] = rec.get("wake_enqueued")
            bundle.events.append(event)
            bundle.local_steps.append(_step_for_record(
                rec, raw.record_id, len(bundle.local_steps),
                related_seq=rec.get("seq"),
                step_type=LocalStepType.SSE_DISCONNECT if rec.get("closed") else LocalStepType.SSE_RECEIVED,
                payload=rec))
            continue
        if typ == "req":
            request = _request_from_record(rec, raw.record_id, index, bundle.game_id)
            existing = request_by_logical.get(request.logical_request_id or request.request_id)
            if existing is not None:
                existing.attempts.extend(request.attempts)
                existing.raw_refs.extend(request.raw_refs)
                existing.response.update(request.response)
                existing.response_seq = request.response_seq or existing.response_seq
                request = existing
            else:
                request_by_logical[request.logical_request_id or request.request_id] = request
                bundle.requests.append(request)
            bundle.local_steps.append(_step_for_record(
                rec, raw.record_id, len(bundle.local_steps),
                related_seq=rec.get("seq"), request_id=request.request_id,
                step_type=LocalStepType.STATE_REQUEST, payload=rec))
            if isinstance(rec.get("res"), Mapping):
                response = rec["res"]
                bundle.local_steps.append(_step_for_record(
                    response, raw.record_id, len(bundle.local_steps),
                    related_seq=response.get("seq", rec.get("seq")),
                    request_id=request.request_id,
                    step_type=LocalStepType.STATE_RESPONSE, payload=response))
            continue
        if typ == "state_reconcile":
            logical = rec.get("logical_request_id")
            request = request_by_logical.get(str(logical)) if logical is not None else None
            bundle.local_steps.append(_step_for_record(
                rec, raw.record_id, len(bundle.local_steps),
                related_seq=(request.response_seq if request else None),
                request_id=request.request_id if request else None,
                step_type=LocalStepType.STATE_MERGE,
                outcome="FAILED" if rec.get("error") else "COMPLETED",
                payload=rec))
            continue
        if typ == "reset":
            bundle.local_steps.append(_step_for_record(
                rec, raw.record_id, len(bundle.local_steps),
                step_type=LocalStepType.RESET, outcome="RESET", payload=rec))
            continue
        if typ in ("decision", "action", "claim_miss", "window_confirm",
                   "window_lifecycle", "window_authorization", "window_terminal"):
            step_type = LocalStepType.DECISION if typ in ("decision", "claim_miss") else LocalStepType.ACTION_POST if typ == "action" else LocalStepType.UNKNOWN
            bundle.local_steps.append(_step_for_record(
                rec, raw.record_id, len(bundle.local_steps),
                related_seq=rec.get("seq"), request_id=rec.get("logical_request_id"),
                step_type=step_type, outcome=rec.get("outcome"), payload=rec))
            continue
        if typ == "end":
            bundle.local_steps.append(_step_for_record(
                rec, raw.record_id, len(bundle.local_steps),
                step_type=LocalStepType.ROUND_BOUNDARY, outcome=rec.get("reason"), payload=rec))
            continue
        # Trace records may be embedded in an existing JSONL by future clients.
        if typ in ("trace", "trace_record") or rec.get("traceSchemaVersion"):
            _import_trace_record(rec, raw.record_id, bundle)
            continue
        bundle.add_quality("UNKNOWN_RECORD", f"unknown local record type {rec.get('type')!r}",
                           raw_refs=[raw.record_id])
    return bundle


def _import_trace_record(rec: Mapping[str, Any], raw_ref: str, bundle: EvidenceBundle):
    kind = str(rec.get("kind") or rec.get("event") or rec.get("type") or "unknown").lower()
    payload = rec.get("payload") if isinstance(rec.get("payload"), Mapping) else dict(rec)
    ordinal = _as_int(rec.get("localOrdinal"))
    index = len(bundle.local_steps)
    type_map = {
        "sse_received": LocalStepType.SSE_RECEIVED,
        "sse_parse_error": LocalStepType.SSE_PARSED,
        "sse_closed": LocalStepType.SSE_DISCONNECT,
        "sse_connect": LocalStepType.SSE_CONNECT,
        "sse_disconnect": LocalStepType.SSE_DISCONNECT,
        "sse_reconnect": LocalStepType.SSE_RECONNECT,
        "state_request": LocalStepType.STATE_REQUEST,
        "state_attempt": LocalStepType.STATE_ATTEMPT,
        "state_response": LocalStepType.STATE_RESPONSE,
        "state_merge": LocalStepType.STATE_MERGE,
        "input": LocalStepType.INPUT,
        "transition": LocalStepType.LOCAL_TRANSITION,
        "processing_complete": LocalStepType.PROCESSING_COMPLETE,
        "action_post": LocalStepType.ACTION_POST,
        "decision": LocalStepType.DECISION,
        "round_boundary": LocalStepType.ROUND_BOUNDARY,
        "checkpoint": LocalStepType.STATE_RESPONSE,
        "reset": LocalStepType.RESET,
    }
    step = _step_for_record(rec, raw_ref, index,
                            related_seq=rec.get("seqNo", rec.get("seq")),
                            request_id=rec.get("logicalRequestId", rec.get("request_id")),
                            step_type=type_map.get(kind, LocalStepType.UNKNOWN),
                            outcome=rec.get("outcome"), payload=payload,
                            causal_parents=rec.get("causalParents") or [])
    step.local_ordinal = ordinal if ordinal is not None else step.local_ordinal
    step.state_before = copy.deepcopy(rec.get("stateBefore"))
    step.state_after = copy.deepcopy(rec.get("stateAfter"))
    step.evidence = EvidenceStrength.RECORDED
    bundle.local_steps.append(step)
    _update_trace_request(rec, raw_ref, kind, payload, bundle)
    if rec.get("traceSchemaVersion"):
        bundle.trace_metadata.update({k: rec.get(k) for k in
                                      ("traceSchemaVersion", "sessionId", "gid", "seat", "roundNo")
                                      if rec.get(k) is not None})


def _update_trace_request(rec: Mapping[str, Any], raw_ref: str, kind: str,
                          payload: Mapping[str, Any], bundle: EvidenceBundle):
    """Promote trace request boundaries into first-class request objects."""
    logical = rec.get("logicalRequestId", rec.get("logical_request_id"))
    if logical is None:
        return
    logical = str(logical)
    request = next((item for item in bundle.requests
                    if item.logical_request_id == logical), None)
    if request is None:
        request = Request(
            request_id=stable_id("request", logical,
                                 rec.get("transportRequestId"),
                                 rec.get("attemptIndex")),
            logical_request_id=logical,
            transport_request_id=(str(rec["transportRequestId"])
                                  if rec.get("transportRequestId") is not None else None),
            attempt_index=_as_int(rec.get("attemptIndex")),
            round_no=_as_int(rec.get("roundNo")),
            requested_seq=_as_int(rec.get("seqNo", rec.get("seq"))),
            requested_at=rec.get("captureTimestamp"),
            clock_domain=rec.get("clockDomain"),
            trigger={"type": "TRACE", "confidence": "KNOWN"},
            evidence=EvidenceStrength.RECORDED,
            raw_refs=[raw_ref], association_status="KNOWN")
        bundle.requests.append(request)
    elif raw_ref not in request.raw_refs:
        request.raw_refs.append(raw_ref)
    if kind in ("state_request", "state_attempt"):
        attempt = {
            "attemptIndex": _as_int(rec.get("attemptIndex")),
            "transportRequestId": rec.get("transportRequestId"),
            "status": rec.get("status"),
            "payload": dict(payload or {}),
            "captureTimestamp": rec.get("captureTimestamp"),
        }
        if not any(a.get("transportRequestId") == attempt["transportRequestId"]
                   and a.get("attemptIndex") == attempt["attemptIndex"]
                   for a in request.attempts):
            request.attempts.append(attempt)
        if kind == "state_request":
            request.request = copy.deepcopy(dict(payload or {}))
            request.requested_seq = request.requested_seq
    elif kind == "state_response":
        request.response = copy.deepcopy(dict(payload or {}))
        request.response_seq = _as_int((payload or {}).get("seq", rec.get("seqNo")))
        request.responded_at = rec.get("captureTimestamp")
    elif kind == "state_merge":
        request.applied_at = rec.get("captureTimestamp")
        request.state_after_merge = copy.deepcopy(rec.get("stateAfter"))


def import_trace(path: str | os.PathLike[str], bundle: EvidenceBundle | None = None) -> EvidenceBundle:
    bundle = bundle or EvidenceBundle()
    records, raws, quality = read_jsonl(path, SourceRole.LOCAL_TRACE)
    bundle.raw_records.extend(raws)
    bundle.quality.extend(quality)
    bundle.input_hashes[str(path)] = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    has_footer = False
    valid_raws = [r for r in raws if isinstance(r.raw, Mapping)]
    for idx, rec in enumerate(records):
        raw = valid_raws[idx] if idx < len(valid_raws) else RawRecord.make(SourceRole.LOCAL_TRACE, rec, source_path=str(path), line_no=idx + 1)
        kind = str(rec.get("kind", rec.get("type", ""))).lower()
        if kind in ("footer", "trace_footer"):
            bundle.trace_metadata.update(dict(rec))
            dropped = _as_int(rec.get("dropped")) or 0
            if dropped:
                bundle.add_quality(
                    "TRACE_DROPPED",
                    "bounded replay trace dropped records",
                    dropped=dropped,
                    firstOrdinal=rec.get("droppedFirstOrdinal"),
                    lastOrdinal=rec.get("droppedLastOrdinal"),
                    sourcePath=str(path))
            has_footer = True
            continue
        if kind in ("header", "trace_header"):
            bundle.trace_metadata.update(dict(rec))
            continue
        _import_trace_record(rec, raw.record_id, bundle)
    if records and not has_footer:
        bundle.add_quality("INCOMPLETE_TRACE", "trace has no completion footer",
                           sourcePath=str(path), tailIncomplete=True)
    return bundle


def import_http_dump(path: str | os.PathLike[str], bundle: EvidenceBundle | None = None) -> EvidenceBundle:
    """Import DumpingApi JSON files without assuming a request association."""
    bundle = bundle or EvidenceBundle()
    root = Path(path)
    paths = [root] if root.is_file() else sorted(root.rglob("*.json"))
    for file in paths:
        try:
            text, digest = _file_text(file)
            doc = json.loads(text)
        except (OSError, json.JSONDecodeError) as exc:
            bundle.add_quality("MALFORMED_HTTP_DUMP", str(exc), sourcePath=str(file))
            continue
        raw = RawRecord.make(SourceRole.STATE_RESPONSE, doc, source_path=str(file), raw_text=text)
        bundle.add_raw(raw)
        if isinstance(doc, Mapping):
            req = doc.get("request") if isinstance(doc.get("request"), Mapping) else doc
            res = doc.get("response", doc.get("res"))
            if isinstance(res, Mapping):
                if "snapshot" in res and isinstance(res["snapshot"], Mapping):
                    bundle.snapshots.append({"seq": res.get("seq"), "snap": copy.deepcopy(res["snapshot"]),
                                             "rawRefs": [raw.record_id]})
                for event_raw in res.get("events", []) if isinstance(res.get("events"), list) else []:
                    if isinstance(event_raw, Mapping):
                        bundle.events.append(normalize_event(
                            event_raw, source=SourceRole.STATE_RESPONSE,
                            raw_ref=raw.record_id, game_id=bundle.game_id,
                            ordinal=len(bundle.events)))
            logical = _first(doc, "logical_request_id", "logicalRequestId")
            if logical is not None:
                request = _request_from_record(dict(doc, res=res or {}), raw.record_id,
                                               len(bundle.requests), bundle.game_id)
                request.association_status = "KNOWN"
                bundle.requests.append(request)
        bundle.input_hashes[str(file)] = digest
    return bundle


def import_sources(local_path: str | os.PathLike[str], *, server_path=None,
                   trace_path=None, http_dump_path=None) -> EvidenceBundle:
    bundle = import_local_jsonl(local_path)
    for path, loader in ((server_path, import_server_timeline),
                         (trace_path, import_trace),
                         (http_dump_path, import_http_dump)):
        if path:
            loader(path, bundle)
    games = bundle.identities.get("game", set())
    if len(games) > 1:
        bundle.add_quality("IDENTITY_CONFLICT",
                           "input sources contain conflicting game identities",
                           identities=sorted(games))
    return bundle


def find_local_logs(target: str, root: str = "local/games") -> list[str]:
    path = Path(target)
    if path.exists():
        return [str(path)]
    if os.sep in target or target.endswith(".jsonl"):
        return []
    return sorted(str(p) for p in Path(root).rglob(f"*_{target}.jsonl"))
