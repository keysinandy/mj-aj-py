"""Small deterministic evidence fixtures used by replay acceptance tests."""

from __future__ import annotations

import copy
import json
from typing import Any

from .model import EventType


def three_source_fixture(gid: str = "fixture-game") -> dict[str, Any]:
    """Return server/local/trace inputs covering a delayed PON and recovery."""
    hands = [
        ["1w", "1w", "5b", "5b", "5b", "2t", "3t", "4t", "7w", "8w", "9w", "东", "南", "中"],
        ["2w"] * 13,
        ["3w"] * 13,
        ["4w"] * 13,
    ]
    server_events = [
        {"seq": 180, "type": "tile_discarded", "seat": 2, "tile": "5b"},
        {"seq": 181, "type": "peng", "seat": 0, "tile": "5b",
         "data": {"fromSeat": 2}},
        {"seq": 182, "type": "tile_discarded", "seat": 0, "tile": "1w"},
    ]
    local = [
        {"type": "meta", "gid": gid, "name": "fixture", "seat": 0},
        {"type": "snapshot", "seq": 179, "snap": {"gid": gid, "seat": 0,
         "round_no": 1, "my_hand": hands[0], "discards": [[], [], [], []],
         "melds": [[], [], [], []], "hand_counts": [14, 13, 13, 13]}},
        {"type": "sse_frame", "seq": 181, "payload": {"seq": 181},
         "accepted": True, "wake_enqueued": True},
        {"type": "req", "seq": 179, "requested_seq": 179, "status": 200,
         "logical_request_id": "req-1", "reason": "SSE_DELTA",
         "res": {"seq": 181, "events": [server_events[0], server_events[1]]}},
        {"type": "state_reconcile", "logical_request_id": "req-1",
         "satisfied_reasons": ["SSE_DELTA"]},
    ]
    trace = [
        {"kind": "header", "traceSchemaVersion": "1.0", "sessionId": "fixture-session", "gid": gid},
        {"kind": "state_request", "traceSchemaVersion": "1.0", "sessionId": "fixture-session",
         "gid": gid, "recordId": "trace-1", "localOrdinal": 1,
         "logicalRequestId": "req-1", "seqNo": 179, "payload": {"seq": 179}},
        {"kind": "state_response", "traceSchemaVersion": "1.0", "sessionId": "fixture-session",
         "gid": gid, "recordId": "trace-2", "localOrdinal": 2,
         "logicalRequestId": "req-1", "seqNo": 181,
         "payload": {"events": [server_events[0], server_events[1]]}},
        {"kind": "transition", "traceSchemaVersion": "1.0", "sessionId": "fixture-session",
         "gid": gid, "recordId": "trace-3", "localOrdinal": 3, "seqNo": 181,
         "payload": {"eventType": "PON", "eventId": "missing-pon"},
         "outcome": "COMPLETED", "executionComplete": True},
        {"kind": "footer", "traceSchemaVersion": "1.0", "sessionId": "fixture-session", "gid": gid},
    ]
    server = {"gid": gid, "blocks": [{"round_no": 1, "start_hands": hands,
                                       "events": server_events}]}
    return {"server": server, "local": local, "trace": trace}


def jsonl(records: list[dict[str, Any]]) -> str:
    return "".join(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
                   for record in records)

