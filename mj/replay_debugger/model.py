"""Versioned, JSON serialisable contracts for offline Mahjong replay.

The replay debugger deliberately keeps evidence and interpretation separate.
The small data classes in this module are also used by the HTML exporter, so
they contain no file, network, or game-engine side effects.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import copy
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping


SCHEMA_VERSION = "1.0"
ANALYZER_VERSION = "1.0"
TRACE_SCHEMA_VERSION = "1.0"


class _ValueEnum(str, Enum):
    def __str__(self) -> str:
        return self.value


class SourceRole(_ValueEnum):
    SERVER_TIMELINE = "SERVER_TIMELINE"
    SSE = "SSE"
    STATE_RESPONSE = "STATE_RESPONSE"
    LOCAL_TRACE = "LOCAL_TRACE"
    LOCAL_DERIVED = "LOCAL_DERIVED"


class EvidenceStrength(_ValueEnum):
    RECORDED = "RECORDED"
    DERIVED = "DERIVED"
    UNKNOWN = "UNKNOWN"


class KnowledgeState(_ValueEnum):
    KNOWN = "KNOWN"
    UNKNOWN = "UNKNOWN"
    HIDDEN = "HIDDEN"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class VisibilityMode(_ValueEnum):
    PLAYER_VIEW = "PLAYER_VIEW"
    LOCAL_KNOWLEDGE = "LOCAL_KNOWLEDGE"
    OMNISCIENT = "OMNISCIENT"


class Boundary(_ValueEnum):
    BEFORE = "BEFORE"
    AFTER = "AFTER"


class EventType(_ValueEnum):
    ROUND_START = "ROUND_START"
    DEAL = "DEAL"
    DRAW = "DRAW"
    DISCARD = "DISCARD"
    CHI = "CHI"
    PON = "PON"
    KAN_OPEN = "KAN_OPEN"
    KAN_CLOSED = "KAN_CLOSED"
    KAN_ADDED = "KAN_ADDED"
    PASS = "PASS"
    TIMEOUT = "TIMEOUT"
    READY = "READY"
    RIICHI = "RIICHI"
    WIN = "WIN"
    ROUND_END = "ROUND_END"
    GAME_END = "GAME_END"
    STATE_REQUEST = "STATE_REQUEST"
    STATE_RESPONSE = "STATE_RESPONSE"
    STATE_MERGE = "STATE_MERGE"
    SSE_CONNECT = "SSE_CONNECT"
    SSE_RECEIVED = "SSE_RECEIVED"
    SSE_PARSED = "SSE_PARSED"
    SSE_DISCONNECT = "SSE_DISCONNECT"
    SSE_RECONNECT = "SSE_RECONNECT"
    SSE_CLOSED = "SSE_CLOSED"
    UNKNOWN = "UNKNOWN"


class LocalStepType(_ValueEnum):
    INPUT = "INPUT"
    SSE_RECEIVED = "SSE_RECEIVED"
    SSE_PARSED = "SSE_PARSED"
    LOCAL_TRANSITION = "LOCAL_TRANSITION"
    PROCESSING_COMPLETE = "PROCESSING_COMPLETE"
    STATE_REQUEST = "STATE_REQUEST"
    STATE_ATTEMPT = "STATE_ATTEMPT"
    STATE_RESPONSE = "STATE_RESPONSE"
    STATE_MERGE = "STATE_MERGE"
    DECISION = "DECISION"
    ACTION_POST = "ACTION_POST"
    RESET = "RESET"
    ROUND_BOUNDARY = "ROUND_BOUNDARY"
    SSE_CONNECT = "SSE_CONNECT"
    SSE_DISCONNECT = "SSE_DISCONNECT"
    SSE_RECONNECT = "SSE_RECONNECT"
    UNKNOWN = "UNKNOWN"


class DiagnosticType(_ValueEnum):
    FIRST_DIVERGENCE = "FIRST_DIVERGENCE"
    MISSING_LOCAL_TRANSITION = "MISSING_LOCAL_TRANSITION"
    SERVER_LOCAL_STATE_MISMATCH = "SERVER_LOCAL_STATE_MISMATCH"
    LOCAL_EXPECTED_OBSERVED_MISMATCH = "LOCAL_EXPECTED_OBSERVED_MISMATCH"
    REDUNDANT_STATE_REQUEST = "REDUNDANT_STATE_REQUEST"
    STATE_RECOVERY = "STATE_RECOVERY"
    SSE_DELAY = "SSE_DELAY"
    SSE_GAP = "SSE_GAP"
    STATE_INVARIANT_ERROR = "STATE_INVARIANT_ERROR"
    UNEXPLAINED_STATE_CHANGE = "UNEXPLAINED_STATE_CHANGE"
    UNKNOWN_DATA = "UNKNOWN_DATA"


class RequestClassification(_ValueEnum):
    RECOVERY_CAUSED_BY_LOCAL_TRANSITION = "RECOVERY_CAUSED_BY_LOCAL_TRANSITION"
    RECOVERY_CAUSED_BY_RECONNECT = "RECOVERY_CAUSED_BY_RECONNECT"
    RECOVERY_CAUSED_BY_MISSED_EVENT = "RECOVERY_CAUSED_BY_MISSED_EVENT"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"
    VALIDATION_ONLY = "VALIDATION_ONLY"
    PROGRESS_UPDATE = "PROGRESS_UPDATE"
    REDUNDANT = "REDUNDANT"
    SUSPICIOUS = "SUSPICIOUS"
    UNCLASSIFIED = "UNCLASSIFIED"


class Avoidability(_ValueEnum):
    NECESSARY = "NECESSARY"
    AVOIDABLE = "AVOIDABLE"
    UNKNOWN = "UNKNOWN"


def _plain(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if dataclasses.is_dataclass(value):
        return {k: _plain(v) for k, v in dataclasses.asdict(value).items()}
    if isinstance(value, Mapping):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_plain(v) for v in value]
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(_plain(value), ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), default=str)


def stable_id(namespace: str, *parts: Any, length: int = 20) -> str:
    """Return a semantic ID independent of wall clock and absolute paths."""
    body = canonical_json([namespace, *parts]).encode("utf-8")
    return f"{namespace.lower()}_{hashlib.sha256(body).hexdigest()[:length]}"


@dataclass(frozen=True)
class KnownValue:
    """A value with explicit knowledge and evidence semantics.

    ``KNOWN + []`` is intentionally different from UNKNOWN, HIDDEN, and
    NOT_APPLICABLE.  The value is omitted from restricted projections by the
    visibility layer, never by mutating the preserved evidence.
    """

    status: KnowledgeState = KnowledgeState.UNKNOWN
    value: Any = None
    source: SourceRole | str | None = None
    evidence: EvidenceStrength = EvidenceStrength.UNKNOWN
    raw_refs: tuple[str, ...] = ()

    @classmethod
    def known(cls, value: Any, source=None,
              evidence: EvidenceStrength = EvidenceStrength.RECORDED,
              raw_refs: Iterable[str] = ()) -> "KnownValue":
        return cls(KnowledgeState.KNOWN, value, source, evidence,
                   tuple(raw_refs))

    @classmethod
    def unknown(cls, evidence=EvidenceStrength.UNKNOWN,
                raw_refs: Iterable[str] = ()) -> "KnownValue":
        return cls(KnowledgeState.UNKNOWN, None, None, evidence,
                   tuple(raw_refs))

    @classmethod
    def hidden(cls, raw_refs: Iterable[str] = ()) -> "KnownValue":
        return cls(KnowledgeState.HIDDEN, None, None,
                   EvidenceStrength.RECORDED, tuple(raw_refs))

    @classmethod
    def not_applicable(cls) -> "KnownValue":
        return cls(KnowledgeState.NOT_APPLICABLE, None, None,
                   EvidenceStrength.RECORDED, ())

    def as_dict(self) -> dict[str, Any]:
        out = {"status": self.status.value,
               "evidence": self.evidence.value}
        if self.value is not None and self.status == KnowledgeState.KNOWN:
            out["value"] = _plain(self.value)
        if self.source is not None:
            out["source"] = str(self.source)
        if self.raw_refs:
            out["rawRefs"] = list(self.raw_refs)
        return out


@dataclass
class RiverTile:
    tile: str | None = None
    discard_seq_no: int | None = None
    called: bool | None = False
    called_by: int | None = None
    call_type: str | None = None
    source_event_id: str | None = None
    raw_refs: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "tile": self.tile,
            "discardSeqNo": self.discard_seq_no,
            "called": self.called,
            "calledBy": self.called_by,
            "callType": self.call_type,
            "sourceEventId": self.source_event_id,
            "rawRefs": list(self.raw_refs),
        }


@dataclass
class Meld:
    meld_id: str
    type: str
    owner_seat: int | None = None
    from_seat: int | None = None
    tiles: list[str] = field(default_factory=list)
    source_discard_event_id: str | None = None
    created_seq_no: int | None = None
    updated_seq_no: int | None = None
    raw_refs: list[str] = field(default_factory=list)
    parent_meld_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "meldId": self.meld_id,
            "type": self.type,
            "ownerSeat": self.owner_seat,
            "fromSeat": self.from_seat,
            "tiles": list(self.tiles),
            "sourceDiscardEventId": self.source_discard_event_id,
            "createdSeqNo": self.created_seq_no,
            "updatedSeqNo": self.updated_seq_no,
            "rawRefs": list(self.raw_refs),
            "parentMeldId": self.parent_meld_id,
        }


@dataclass
class PlayerState:
    seat: int
    hand: KnownValue = field(default_factory=KnownValue.unknown)
    hand_count: KnownValue = field(default_factory=KnownValue.unknown)
    river: list[RiverTile] = field(default_factory=list)
    melds: list[Meld] = field(default_factory=list)
    flags: dict[str, KnownValue | Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        flags = {k: (v.as_dict() if isinstance(v, KnownValue) else _plain(v))
                 for k, v in self.flags.items()}
        return {
            "seat": self.seat,
            "hand": self.hand.as_dict(),
            "handCount": self.hand_count.as_dict(),
            "river": [r.as_dict() for r in self.river],
            "melds": [m.as_dict() for m in self.melds],
            "flags": flags,
        }


@dataclass
class GameState:
    round_id: str | None = None
    round_no: int | None = None
    dealer_seat: int | None = None
    current_turn: int | None = None
    next_seat: int | None = None
    phase: str | None = None
    remaining_tiles: KnownValue = field(default_factory=KnownValue.unknown)
    live_wall_left: KnownValue = field(default_factory=KnownValue.unknown)
    pending: KnownValue = field(default_factory=KnownValue.unknown)
    responding_seats: KnownValue = field(default_factory=KnownValue.unknown)
    deadline: KnownValue = field(default_factory=KnownValue.unknown)
    frozen: KnownValue = field(default_factory=KnownValue.unknown)
    flags: dict[str, KnownValue | Any] = field(default_factory=dict)
    players: list[PlayerState] = field(
        default_factory=lambda: [PlayerState(seat=i) for i in range(4)])
    last_action: dict[str, Any] | None = None
    evidence: EvidenceStrength = EvidenceStrength.UNKNOWN
    raw_refs: list[str] = field(default_factory=list)

    def clone(self) -> "GameState":
        return copy.deepcopy(self)

    def as_dict(self) -> dict[str, Any]:
        def field_dict(v):
            return v.as_dict() if isinstance(v, KnownValue) else _plain(v)
        return {
            "round": {
                "roundId": self.round_id,
                "roundNo": self.round_no,
                "dealerSeat": self.dealer_seat,
                "currentTurn": self.current_turn,
                "nextSeat": self.next_seat,
                "phase": self.phase,
                "remainingTiles": field_dict(self.remaining_tiles),
                "liveWallLeft": field_dict(self.live_wall_left),
                "pending": field_dict(self.pending),
                "respondingSeats": field_dict(self.responding_seats),
                "deadline": field_dict(self.deadline),
                "frozen": field_dict(self.frozen),
            },
            "players": [p.as_dict() for p in self.players],
            "lastAction": _plain(self.last_action),
            "flags": {k: field_dict(v) for k, v in self.flags.items()},
            "evidence": self.evidence.value,
            "rawRefs": list(self.raw_refs),
        }


@dataclass
class RawRecord:
    record_id: str
    source_role: SourceRole | str
    source_path: str | None = None
    line_no: int | None = None
    byte_start: int | None = None
    byte_end: int | None = None
    raw_text: str | None = None
    raw: Any = None
    content_hash: str = ""
    raw_id: str | None = None
    timestamp: Any = None
    clock_domain: str | None = None
    precision: str | None = None
    truncated: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def make(cls, source_role: SourceRole | str, raw: Any,
             *, source_path=None, line_no=None, byte_start=None,
             byte_end=None, raw_text=None, timestamp=None,
             clock_domain=None, precision=None, truncated=False,
             metadata=None) -> "RawRecord":
        text = raw_text if raw_text is not None else canonical_json(raw)
        digest = hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()
        rid = stable_id("raw", str(source_role), digest, line_no,
                        byte_start, raw_id_for(raw))
        return cls(rid, source_role, source_path, line_no, byte_start,
                   byte_end, raw_text, raw, digest,
                   raw_id_for(raw), timestamp, clock_domain, precision,
                   truncated, dict(metadata or {}))

    def as_dict(self) -> dict[str, Any]:
        return {
            "recordId": self.record_id,
            "sourceRole": str(self.source_role),
            "sourcePath": self.source_path,
            "line": self.line_no,
            "byteStart": self.byte_start,
            "byteEnd": self.byte_end,
            "rawText": self.raw_text,
            "raw": _plain(self.raw),
            "contentHash": self.content_hash,
            "rawId": self.raw_id,
            "timestamp": self.timestamp,
            "clockDomain": self.clock_domain,
            "precision": self.precision,
            "truncated": self.truncated,
            "metadata": _plain(self.metadata),
        }


def raw_id_for(raw: Any) -> str | None:
    if not isinstance(raw, Mapping):
        return None
    for key in ("id", "event_id", "eventId", "request_id",
                "logical_request_id", "transport_request_id", "seq"):
        if raw.get(key) is not None:
            return str(raw[key])
    return None


@dataclass
class NormalizedEvent:
    event_id: str
    type: EventType | str
    source: SourceRole | str
    evidence: EvidenceStrength = EvidenceStrength.RECORDED
    game_id: str | None = None
    round_no: int | None = None
    seq_no: int | None = None
    source_seq: Any = None
    server_timestamp: Any = None
    local_timestamp: Any = None
    clock_domain: str | None = None
    precision: str | None = None
    seat: int | None = None
    from_seat: int | None = None
    tile: str | None = None
    tiles: list[str] = field(default_factory=list)
    caused_by: str | None = None
    related_events: list[str] = field(default_factory=list)
    raw_refs: list[str] = field(default_factory=list)
    payload: dict[str, Any] = field(default_factory=dict)
    original_type: str | None = None
    visibility: KnowledgeState = KnowledgeState.KNOWN

    def as_dict(self) -> dict[str, Any]:
        return {
            "eventId": self.event_id,
            "type": str(self.type),
            "source": str(self.source),
            "evidence": self.evidence.value,
            "gameId": self.game_id,
            "roundNo": self.round_no,
            "seqNo": self.seq_no,
            "sourceSeq": self.source_seq,
            "serverTimestamp": self.server_timestamp,
            "localTimestamp": self.local_timestamp,
            "clockDomain": self.clock_domain,
            "precision": self.precision,
            "seat": self.seat,
            "fromSeat": self.from_seat,
            "tile": self.tile,
            "tiles": list(self.tiles),
            "causedBy": self.caused_by,
            "relatedEvents": list(self.related_events),
            "rawRefs": list(self.raw_refs),
            "payload": _plain(self.payload),
            "originalType": self.original_type,
            "visibility": (self.visibility.value
                           if isinstance(self.visibility, Enum)
                           else str(self.visibility)),
        }


@dataclass
class Cursor:
    game_id: str | None = None
    round_no: int | None = None
    seq_no: int | None = None
    phase: Boundary = Boundary.AFTER
    local_step_index: int | None = None
    local_ordinal: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"gameId": self.game_id, "roundNo": self.round_no,
                "seqNo": self.seq_no, "phase": self.phase.value,
                "localStepIndex": self.local_step_index,
                "localOrdinal": self.local_ordinal}


@dataclass
class LocalStep:
    step_id: str
    index: int
    type: LocalStepType | str
    evidence: EvidenceStrength = EvidenceStrength.RECORDED
    timestamp: Any = None
    clock_domain: str | None = None
    precision: str | None = None
    related_seq_no: int | None = None
    related_event_id: str | None = None
    request_id: str | None = None
    causal_parents: list[str] = field(default_factory=list)
    state_before: dict[str, Any] | None = None
    state_after: dict[str, Any] | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    outcome: str | None = None
    raw_refs: list[str] = field(default_factory=list)
    round_no: int | None = None
    local_ordinal: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "stepId": self.step_id,
            "index": self.index,
            "localOrdinal": self.local_ordinal,
            "type": str(self.type),
            "evidence": self.evidence.value,
            "timestamp": self.timestamp,
            "clockDomain": self.clock_domain,
            "precision": self.precision,
            "relatedSeqNo": self.related_seq_no,
            "relatedEventId": self.related_event_id,
            "requestId": self.request_id,
            "causalParents": list(self.causal_parents),
            "stateBefore": self.state_before,
            "stateAfter": self.state_after,
            "payload": _plain(self.payload),
            "outcome": self.outcome,
            "rawRefs": list(self.raw_refs),
            "roundNo": self.round_no,
        }


@dataclass
class SeqFrame:
    seq_no: int
    round_no: int | None = None
    server_before: dict[str, Any] | None = None
    server_event: dict[str, Any] | None = None
    server_after: dict[str, Any] | None = None
    local_steps: list[str] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)
    snapshot_anchor: bool = False
    event_known: bool = True
    raw_refs: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "seqNo": self.seq_no,
            "roundNo": self.round_no,
            "serverBefore": self.server_before,
            "serverEvent": self.server_event,
            "serverAfter": self.server_after,
            "localSteps": list(self.local_steps),
            "diagnostics": list(self.diagnostics),
            "snapshotAnchor": self.snapshot_anchor,
            "eventKnown": self.event_known,
            "rawRefs": list(self.raw_refs),
        }


@dataclass
class DiffResult:
    changes: list[dict[str, Any]] = field(default_factory=list)
    compared_fields: list[str] = field(default_factory=list)
    unknown_fields: list[str] = field(default_factory=list)
    unavailable_fields: list[str] = field(default_factory=list)
    complete: bool = False
    reason: str | None = None

    @property
    def equal(self) -> bool:
        return not self.changes and not self.unknown_fields and not self.unavailable_fields

    def as_dict(self) -> dict[str, Any]:
        return {"changes": _plain(self.changes),
                "comparedFields": list(self.compared_fields),
                "unknownFields": list(self.unknown_fields),
                "unavailableFields": list(self.unavailable_fields),
                "complete": self.complete, "equal": self.equal,
                "reason": self.reason}


@dataclass
class StateRequestRecord:
    request_id: str
    logical_request_id: str | None = None
    transport_request_id: str | None = None
    attempt_index: int | None = None
    round_no: int | None = None
    requested_seq: int | None = None
    response_seq: int | None = None
    requested_at: Any = None
    responded_at: Any = None
    applied_at: Any = None
    clock_domain: str | None = None
    trigger: dict[str, Any] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    request: dict[str, Any] = field(default_factory=dict)
    response: dict[str, Any] = field(default_factory=dict)
    attempts: list[dict[str, Any]] = field(default_factory=list)
    state_before: dict[str, Any] | None = None
    state_before_merge: dict[str, Any] | None = None
    state_after_merge: dict[str, Any] | None = None
    request_to_response_diff: DiffResult = field(default_factory=DiffResult)
    effective_merge_diff: DiffResult = field(default_factory=DiffResult)
    expected_observed_diff: DiffResult = field(default_factory=DiffResult)
    classification: RequestClassification = RequestClassification.UNCLASSIFIED
    contributing_causes: list[str] = field(default_factory=list)
    avoidability: Avoidability = Avoidability.UNKNOWN
    evidence: EvidenceStrength = EvidenceStrength.UNKNOWN
    raw_refs: list[str] = field(default_factory=list)
    association_status: str = "KNOWN"

    def as_dict(self) -> dict[str, Any]:
        return {
            "requestId": self.request_id,
            "logicalRequestId": self.logical_request_id,
            "transportRequestId": self.transport_request_id,
            "attemptIndex": self.attempt_index,
            "roundNo": self.round_no,
            "requestedSeq": self.requested_seq,
            "responseSeq": self.response_seq,
            "requestedAt": self.requested_at,
            "respondedAt": self.responded_at,
            "appliedAt": self.applied_at,
            "clockDomain": self.clock_domain,
            "trigger": _plain(self.trigger),
            "reasons": list(self.reasons),
            "request": _plain(self.request),
            "response": _plain(self.response),
            "attempts": _plain(self.attempts),
            "stateBefore": self.state_before,
            "stateBeforeMerge": self.state_before_merge,
            "stateAfterMerge": self.state_after_merge,
            "requestToResponseDiff": self.request_to_response_diff.as_dict(),
            "effectiveMergeDiff": self.effective_merge_diff.as_dict(),
            "expectedObservedDiff": self.expected_observed_diff.as_dict(),
            "classification": self.classification.value,
            "contributingCauses": list(self.contributing_causes),
            "avoidability": self.avoidability.value,
            "evidence": self.evidence.value,
            "rawRefs": list(self.raw_refs),
            "associationStatus": self.association_status,
        }


@dataclass
class Diagnostic:
    diagnostic_id: str
    type: DiagnosticType | str
    severity: str = "INFO"
    round_no: int | None = None
    seq_no: int | None = None
    local_step_index: int | None = None
    local_ordinal: int | None = None
    message: str = ""
    status: str | None = None
    expected: Any = None
    actual: Any = None
    affected_fields: list[str] = field(default_factory=list)
    probable_cause: str | None = None
    caused_by_event_id: str | None = None
    caused_by_step_id: str | None = None
    caused_by_diagnostic_id: str | None = None
    evidence: EvidenceStrength = EvidenceStrength.UNKNOWN
    raw_refs: list[str] = field(default_factory=list)
    recovered: bool = False
    recovered_by_request_id: str | None = None
    recovery_duration: dict[str, Any] | None = None
    unknown_interval: dict[str, Any] | None = None
    navigation_target: dict[str, Any] = field(default_factory=dict)
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "diagnosticId": self.diagnostic_id,
            "type": str(self.type),
            "severity": self.severity,
            "roundNo": self.round_no,
            "seqNo": self.seq_no,
            "localStepIndex": self.local_step_index,
            "localOrdinal": self.local_ordinal,
            "message": self.message,
            "status": self.status,
            "expected": _plain(self.expected),
            "actual": _plain(self.actual),
            "affectedFields": list(self.affected_fields),
            "probableCause": self.probable_cause,
            "causedByEventId": self.caused_by_event_id,
            "causedByStepId": self.caused_by_step_id,
            "causedByDiagnosticId": self.caused_by_diagnostic_id,
            "evidence": self.evidence.value,
            "rawRefs": list(self.raw_refs),
            "recovered": self.recovered,
            "recoveredByRequestId": self.recovered_by_request_id,
            "recoveryDuration": _plain(self.recovery_duration),
            "unknownInterval": _plain(self.unknown_interval),
            "navigationTarget": _plain(self.navigation_target),
            "details": _plain(self.details),
        }


@dataclass
class Checkpoint:
    checkpoint_id: str
    round_no: int | None
    seq_no: int | None
    local_step_index: int | None
    local_ordinal: int | None
    expected_state: dict[str, Any] | None = None
    observed_state: dict[str, Any] | None = None
    server_state: dict[str, Any] | None = None
    diagnostic_ids: list[str] = field(default_factory=list)
    unknown_intervals: list[dict[str, Any]] = field(default_factory=list)
    evidence: EvidenceStrength = EvidenceStrength.DERIVED
    raw_refs: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "checkpointId": self.checkpoint_id,
            "roundNo": self.round_no,
            "seqNo": self.seq_no,
            "localStepIndex": self.local_step_index,
            "localOrdinal": self.local_ordinal,
            "expectedState": self.expected_state,
            "observedState": self.observed_state,
            "serverState": self.server_state,
            "diagnosticIds": list(self.diagnostic_ids),
            "unknownIntervals": _plain(self.unknown_intervals),
            "evidence": self.evidence.value,
            "rawRefs": list(self.raw_refs),
        }


@dataclass
class ReplaySession:
    session_id: str
    game_id: str | None = None
    schema_version: str = SCHEMA_VERSION
    analyzer_version: str = ANALYZER_VERSION
    trace_schema_version: str | None = None
    rule_profile: str = "hangzhou"
    input_hashes: dict[str, str] = field(default_factory=dict)
    source_coverage: dict[str, Any] = field(default_factory=dict)
    raw_records: list[RawRecord] = field(default_factory=list)
    normalized_events: list[NormalizedEvent] = field(default_factory=list)
    frames: list[SeqFrame] = field(default_factory=list)
    local_steps: list[LocalStep] = field(default_factory=list)
    states: dict[str, Any] = field(default_factory=dict)
    requests: list[StateRequestRecord] = field(default_factory=list)
    checkpoints: list[Checkpoint] = field(default_factory=list)
    diagnostics: list[Diagnostic] = field(default_factory=list)
    cursor: Cursor = field(default_factory=Cursor)
    first_visible_divergence: dict[str, Any] | None = None
    first_confirmed_failure: dict[str, Any] | None = None
    view: dict[str, Any] = field(default_factory=lambda: {
        "handVisibility": VisibilityMode.LOCAL_KNOWLEDGE.value,
        "selectedPlayer": 0,
    })
    quality: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "analyzerVersion": self.analyzer_version,
            "traceSchemaVersion": self.trace_schema_version,
            "sessionId": self.session_id,
            "gameId": self.game_id,
            "ruleProfile": self.rule_profile,
            "inputHashes": dict(self.input_hashes),
            "sourceCoverage": _plain(self.source_coverage),
            "rawRecords": [r.as_dict() for r in self.raw_records],
            "normalizedEvents": [e.as_dict() for e in self.normalized_events],
            "seqFrames": [f.as_dict() for f in self.frames],
            "localSteps": [s.as_dict() for s in self.local_steps],
            "states": _plain(self.states),
            "requests": [r.as_dict() for r in self.requests],
            "checkpoints": [c.as_dict() for c in self.checkpoints],
            "diagnostics": [d.as_dict() for d in self.diagnostics],
            "cursor": self.cursor.as_dict(),
            "firstVisibleDivergence": _plain(self.first_visible_divergence),
            "firstConfirmedFailure": _plain(self.first_confirmed_failure),
            "view": _plain(self.view),
            "quality": _plain(self.quality),
            "metadata": _plain(self.metadata),
        }

    def to_json(self, *, indent: int | None = None) -> str:
        return json.dumps(self.as_dict(), ensure_ascii=False, sort_keys=True,
                          indent=indent, separators=None if indent else (",", ":"))


# Public aliases matching the design vocabulary.
Request = StateRequestRecord
ReplayState = GameState
