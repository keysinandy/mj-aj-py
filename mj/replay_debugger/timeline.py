"""Causal timeline and dual-coordinate cursor helpers."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from .model import (
    Boundary,
    Cursor,
    EvidenceStrength,
    EventType,
    LocalStep,
    NormalizedEvent,
    SeqFrame,
    SourceRole,
    stable_id,
)
from .state import ReferenceReducer


def event_semantic_key(event: NormalizedEvent) -> tuple[Any, ...]:
    return (event.game_id, event.round_no, event.seq_no, str(event.type),
            event.seat, event.from_seat, event.tile, tuple(event.tiles))


def deduplicate_events(events: Iterable[NormalizedEvent]) -> tuple[list[NormalizedEvent], list[dict[str, Any]]]:
    """Deduplicate identical authoritative facts while retaining all raw refs."""
    result: list[NormalizedEvent] = []
    by_key: dict[tuple[Any, ...], NormalizedEvent] = {}
    conflicts: list[dict[str, Any]] = []
    for event in events:
        # Local receipt events remain independent evidence.  Only repeated
        # server facts may be collapsed into one reference fact.
        if event.source != SourceRole.SERVER_TIMELINE:
            result.append(event)
            continue
        key = event_semantic_key(event)
        previous = by_key.get(key)
        if previous is None:
            by_key[key] = event
            result.append(event)
            continue
        previous.raw_refs.extend(x for x in event.raw_refs if x not in previous.raw_refs)
        if event.payload != previous.payload:
            conflicts.append({"kind": "CONFLICTING_EVENT_PAYLOAD",
                              "eventId": previous.event_id,
                              "rawRefs": list(previous.raw_refs),
                              "key": list(key), "payloads": [previous.payload, event.payload]})
    return result, conflicts


def establish_causality(events: list[NormalizedEvent], steps: list[LocalStep]) -> None:
    """Attach obvious discard→claim and request lifecycle edges."""
    discards: dict[tuple[int | None, str | None], NormalizedEvent] = {}
    latest_request_step: dict[str, str] = {}
    for event in sorted(events, key=lambda e: (e.round_no or 0,
                                                e.seq_no if e.seq_no is not None else 10**18,
                                                e.event_id)):
        if event.type == EventType.DISCARD:
            discards[(event.seat, event.tile)] = event
        elif event.type in (EventType.CHI, EventType.PON,
                            EventType.KAN_OPEN):
            source_seat = event.from_seat
            candidate = discards.get((source_seat, event.tile))
            if candidate is None:
                candidates = [e for (seat, tile), e in discards.items()
                              if tile == event.tile and
                              (source_seat is None or seat == source_seat)]
                candidate = max(candidates, key=lambda e: (e.seq_no or -1, e.event_id),
                                default=None)
            if candidate is not None:
                event.caused_by = candidate.event_id
                if event.event_id not in candidate.related_events:
                    candidate.related_events.append(event.event_id)
        elif event.type == EventType.KAN_ADDED:
            parents = [e for e in events if e.type == EventType.PON and
                       e.seat == event.seat and e.tile == event.tile and
                       (event.seq_no is None or e.seq_no is None or e.seq_no <= event.seq_no)]
            if parents:
                parent = max(parents, key=lambda e: (e.seq_no or -1, e.event_id))
                event.caused_by = parent.event_id
                parent.related_events.append(event.event_id)
    for step in steps:
        if step.request_id:
            parent = latest_request_step.get(step.request_id)
            if parent and parent not in step.causal_parents:
                step.causal_parents.append(parent)
            latest_request_step[step.request_id] = step.step_id
        if step.related_event_id:
            event = next((e for e in events if e.event_id == step.related_event_id), None)
            if event and event.caused_by and event.caused_by not in step.causal_parents:
                step.causal_parents.append(event.caused_by)


def _event_dict(event: NormalizedEvent) -> dict[str, Any]:
    return event.as_dict()


def build_server_frames(events: Iterable[NormalizedEvent], *, game_id=None,
                        start_hands=None) -> tuple[list[SeqFrame], list[dict[str, Any]], list[dict[str, Any]]]:
    """Build BEFORE/event/AFTER frames from server evidence only."""
    server = [e for e in events if e.source == SourceRole.SERVER_TIMELINE and
              e.seq_no not in (None, 0)]
    server.sort(key=lambda e: (e.round_no or 0, e.seq_no, e.event_id))
    frames: list[SeqFrame] = []
    states: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    reducers: dict[int | None, ReferenceReducer] = {}
    last_state_by_round: dict[int | None, dict[str, Any]] = {}
    for event in server:
        rno = event.round_no
        reducer = reducers.get(rno)
        if reducer is None:
            reducer = ReferenceReducer(game_id=game_id, round_no=rno,
                                       source=SourceRole.SERVER_TIMELINE,
                                       evidence=EvidenceStrength.RECORDED)
            reducers[rno] = reducer
            hands = (start_hands.get(rno) if isinstance(start_hands, Mapping)
                     else start_hands if rno in (None, 1) else None)
            if hands is not None:
                reducer.initialize_hands(hands)
        before = reducer.as_state().as_dict()
        if event.type == EventType.ROUND_START and event.payload.get("start_hands"):
            reducer.initialize_hands(event.payload["start_hands"])
        reducer.apply_event(event, copy_result=False)
        after = reducer.as_state().as_dict()
        if reducer.issues:
            for issue in reducer.issues:
                issue = dict(issue)
                issue.setdefault("eventId", event.event_id)
                issues.append(issue)
            reducer.issues.clear()
        frame = next((f for f in frames if f.round_no == rno and f.seq_no == event.seq_no), None)
        if frame is None:
            frame = SeqFrame(event.seq_no, rno, before, _event_dict(event), after,
                             raw_refs=list(event.raw_refs))
            frames.append(frame)
        else:
            # Same sequence is only one frame when the payload is identical.
            if frame.server_event != _event_dict(event):
                frame.event_known = False
                issues.append({"kind": "CONFLICTING_EVENT_PAYLOAD",
                               "eventId": event.event_id, "seqNo": event.seq_no,
                               "rawRefs": list(event.raw_refs)})
            frame.raw_refs.extend(x for x in event.raw_refs if x not in frame.raw_refs)
        last_state_by_round[rno] = after
        states.append(after)
    frames.sort(key=lambda f: (f.round_no or 0, f.seq_no))
    return frames, states, issues


def add_snapshot_anchors(frames: list[SeqFrame], snapshots: Iterable[Mapping[str, Any]], *, round_no=None):
    for item in snapshots:
        seq = item.get("seq")
        if seq is None or int(seq) == 0:
            # seq=0 is a request mode, never a fabricated server event.
            continue
        seq = int(seq)
        existing = next((f for f in frames if f.seq_no == seq and f.round_no == round_no), None)
        if existing is not None:
            existing.raw_refs.extend(x for x in item.get("rawRefs", []) if x not in existing.raw_refs)
            continue
        frames.append(SeqFrame(seq_no=seq, round_no=round_no,
                               server_before=None, server_event=None,
                               server_after=None, snapshot_anchor=True,
                               event_known=False, raw_refs=list(item.get("rawRefs", []))))
    frames.sort(key=lambda f: (f.round_no or 0, f.seq_no))


def attach_steps(frames: list[SeqFrame], steps: Iterable[LocalStep]):
    by_seq = {(f.round_no, f.seq_no): f for f in frames}
    for step in steps:
        # A zero cursor is a FULL request mode, never a server event or
        # snapshot anchor.  Keep the request step itself in local order.
        if step.related_seq_no in (None, 0):
            continue
        frame = by_seq.get((step.round_no, step.related_seq_no))
        if frame is None:
            # A local step can refer to a response watermark that has no
            # independent event; preserve it as a snapshot-only anchor.
            frame = SeqFrame(step.related_seq_no, step.round_no,
                             server_before=None, server_event=None,
                             server_after=None, snapshot_anchor=True,
                             event_known=False)
            frames.append(frame)
            by_seq[(step.round_no, step.related_seq_no)] = frame
        if step.step_id not in frame.local_steps:
            frame.local_steps.append(step.step_id)
    frames.sort(key=lambda f: (f.round_no or 0, f.seq_no))


@dataclass
class TimelineIndex:
    frames: list[SeqFrame]
    steps: list[LocalStep]
    game_id: str | None = None

    def __post_init__(self):
        self._by_round = {}
        for frame in self.frames:
            self._by_round.setdefault(frame.round_no, []).append(frame)
        for values in self._by_round.values():
            values.sort(key=lambda f: f.seq_no)
        self._frame_by_round_seq = {
            (frame.round_no, frame.seq_no): frame
            for frame in self.frames
        }
        self._position_by_round_seq = {
            (round_no, frame.seq_no): index
            for round_no, values in self._by_round.items()
            for index, frame in enumerate(values)
        }
        self._step_by_id = {s.step_id: s for s in self.steps}

    @property
    def rounds(self) -> list[int | None]:
        return sorted(self._by_round, key=lambda x: (-1 if x is None else x))

    def seqs(self, round_no=None) -> list[int]:
        return [f.seq_no for f in self._by_round.get(round_no, [])]

    def frame(self, seq_no: int | None, round_no=None) -> SeqFrame | None:
        return self._frame_by_round_seq.get((round_no, seq_no))

    def first(self, round_no=None) -> Cursor | None:
        frames = self._by_round.get(round_no, [])
        if not frames:
            return None
        return Cursor(self.game_id, round_no, frames[0].seq_no, Boundary.BEFORE,
                      0 if frames[0].local_steps else None,
                      self._step_by_id[frames[0].local_steps[0]].local_ordinal
                      if frames[0].local_steps else None)

    def last(self, round_no=None) -> Cursor | None:
        frames = self._by_round.get(round_no, [])
        if not frames:
            return None
        frame = frames[-1]
        idx = len(frame.local_steps) - 1 if frame.local_steps else None
        step = self._step_by_id[frame.local_steps[idx]] if idx is not None else None
        return Cursor(self.game_id, round_no, frame.seq_no, Boundary.AFTER,
                      idx, step.local_ordinal if step else None)

    def move_seq(self, cursor: Cursor, delta: int) -> tuple[Cursor, str | None]:
        values = self._by_round.get(cursor.round_no, [])
        if not values:
            return cursor, "round is unavailable"
        pos = self._position_by_round_seq.get((cursor.round_no, cursor.seq_no))
        if pos is None:
            return cursor, f"seq {cursor.seq_no} is unavailable"
        target = max(0, min(len(values) - 1, pos + delta))
        frame = values[target]
        idx = 0 if frame.local_steps else None
        ordinal = self._step_by_id[frame.local_steps[0]].local_ordinal if idx is not None else None
        return Cursor(self.game_id, frame.round_no, frame.seq_no, cursor.phase, idx, ordinal), None

    def jump_seq(self, cursor: Cursor, seq_no: int) -> tuple[Cursor, str | None]:
        frame = self.frame(seq_no, cursor.round_no)
        if frame is None:
            return cursor, f"seq {seq_no} is unavailable in round {cursor.round_no}"
        idx = 0 if frame.local_steps else None
        step = self._step_by_id[frame.local_steps[0]] if idx is not None else None
        return Cursor(self.game_id, cursor.round_no, seq_no, Boundary.AFTER,
                      idx, step.local_ordinal if step else None), None

    def move_local(self, cursor: Cursor, delta: int) -> tuple[Cursor, str | None]:
        if not self.steps:
            return cursor, "no local steps are available"
        pos = cursor.local_step_index
        if pos is None:
            pos = 0 if delta >= 0 else len(self.steps) - 1
        target = max(0, min(len(self.steps) - 1, pos + delta))
        step = self.steps[target]
        return Cursor(self.game_id, step.round_no, step.related_seq_no,
                      Boundary.AFTER, target, step.local_ordinal), None

    def cursor_for_step(self, step_id: str) -> Cursor | None:
        step = self._step_by_id.get(step_id)
        if step is None:
            return None
        return Cursor(self.game_id, step.round_no, step.related_seq_no,
                      Boundary.AFTER, step.index, step.local_ordinal)

    def set_boundary(self, cursor: Cursor, boundary: Boundary | str) -> tuple[Cursor, str | None]:
        try:
            boundary = Boundary(boundary)
        except ValueError:
            return cursor, f"invalid boundary {boundary!r}"
        if self.frame(cursor.seq_no, cursor.round_no) is None:
            return cursor, f"seq {cursor.seq_no} is unavailable"
        return Cursor(cursor.game_id, cursor.round_no, cursor.seq_no, boundary,
                      cursor.local_step_index, cursor.local_ordinal), None

    def diagnostic_cursor(self, diagnostics: Iterable[Any], index: int,
                          *, kind: str | None = None) -> tuple[Cursor | None, str | None]:
        def dtype(item):
            if hasattr(item, "type"):
                return getattr(item, "type")
            return item.get("type") if isinstance(item, Mapping) else None
        values = [d for d in diagnostics if kind is None or str(dtype(d)) == kind]
        if not values:
            return None, "no matching diagnostics"
        item = values[max(0, min(len(values) - 1, index))]
        target = getattr(item, "navigation_target", None) or item.get("navigationTarget", {})
        round_no = getattr(item, "round_no", None)
        if round_no is None and isinstance(item, Mapping):
            round_no = item.get("roundNo")
        seq_no = getattr(item, "seq_no", None)
        if seq_no is None and isinstance(item, Mapping):
            seq_no = item.get("seqNo")
        local = target.get("localStepIndex") if isinstance(target, Mapping) else None
        if local is None:
            local = getattr(item, "local_step_index", None)
        frame = self.frame(seq_no, round_no)
        if frame is None:
            return None, "diagnostic destination is unavailable"
        return Cursor(self.game_id, round_no, seq_no, Boundary.AFTER, local,
                      self.steps[local].local_ordinal if isinstance(local, int) and local < len(self.steps) else None), None


@dataclass
class ReplayNavigator:
    """Single selection object used by non-HTML consumers and UI adapters."""

    index: TimelineIndex
    cursor: Cursor
    message: str | None = None

    def _move(self, result):
        self.cursor, self.message = result
        return self.cursor

    def first(self):
        value = self.index.first(self.cursor.round_no)
        if value is None:
            self.message = "round is unavailable"
            return self.cursor
        self.cursor, self.message = value, None
        return self.cursor

    def last(self):
        value = self.index.last(self.cursor.round_no)
        if value is None:
            self.message = "round is unavailable"
            return self.cursor
        self.cursor, self.message = value, None
        return self.cursor

    def previous_seq(self):
        return self._move(self.index.move_seq(self.cursor, -1))

    def next_seq(self):
        return self._move(self.index.move_seq(self.cursor, 1))

    def previous_local(self):
        return self._move(self.index.move_local(self.cursor, -1))

    def next_local(self):
        return self._move(self.index.move_local(self.cursor, 1))

    def jump_seq(self, seq_no: int):
        return self._move(self.index.jump_seq(self.cursor, seq_no))

    def boundary(self, boundary: Boundary | str):
        return self._move(self.index.set_boundary(self.cursor, boundary))

    def select_round(self, round_no):
        candidate = self.index.first(round_no)
        if candidate is None:
            self.message = f"round {round_no} is unavailable"
            return self.cursor
        self.cursor, self.message = candidate, None
        return self.cursor


def state_at(session, cursor: Cursor, *, world: str = "server") -> dict[str, Any] | None:
    """Return a detached state at a cursor without mutating replay data."""
    index = TimelineIndex(session.frames, session.local_steps, session.game_id)
    if world in ("expected", "observed") and cursor.local_step_index is not None:
        if 0 <= cursor.local_step_index < len(session.local_steps):
            step = session.local_steps[cursor.local_step_index]
            values = session.states.get("steps", {}).get(step.step_id, {})
            key = f"{world}{'Before' if cursor.phase == Boundary.BEFORE else 'After'}"
            value = values.get(key)
            return copy.deepcopy(value)
    frame = index.frame(cursor.seq_no, cursor.round_no)
    if frame is None:
        return None
    value = frame.server_before if cursor.phase == Boundary.BEFORE else frame.server_after
    return copy.deepcopy(value)


def verify_checkpoints(session) -> dict[str, Any]:
    """Check stored checkpoint snapshots against the compiled step states."""
    checked = 0
    failures = []
    for checkpoint in session.checkpoints:
        if checkpoint.local_step_index is None or not (
                0 <= checkpoint.local_step_index < len(session.local_steps)):
            continue
        step = session.local_steps[checkpoint.local_step_index]
        values = session.states.get("steps", {}).get(step.step_id, {})
        checked += 1
        if checkpoint.expected_state != values.get("expectedAfter"):
            failures.append(checkpoint.checkpoint_id)
            continue
        if checkpoint.observed_state != values.get("observedAfter"):
            failures.append(checkpoint.checkpoint_id)
    return {"checked": checked, "failures": failures, "passed": not failures}
