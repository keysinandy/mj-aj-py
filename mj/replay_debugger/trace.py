"""Opt-in, bounded local execution trace writer.

The hot path only snapshots an enabled payload and performs ``put_nowait``.
It never waits for disk capacity and never records authentication material.
"""

from __future__ import annotations

import copy
import json
import os
import queue
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Mapping

from .model import TRACE_SCHEMA_VERSION, canonical_json, stable_id


_SENSITIVE_KEYS = {"authorization", "cookie", "cookies", "token", "access_token",
                   "refresh_token", "password", "secret", "api_key", "apikey"}


def scrub_credentials(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): ("[REDACTED]" if str(k).lower() in _SENSITIVE_KEYS
                         else scrub_credentials(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub_credentials(v) for v in value]
    if isinstance(value, tuple):
        return [scrub_credentials(v) for v in value]
    return value


class ReplayTraceWriter:
    """Thread-safe trace capture with durable loss and metric reporting."""

    def __init__(self, path: str | os.PathLike[str], *, gid=None, seat=None,
                 session_id=None, queue_size: int = 512, enabled: bool = True,
                 clock=time.monotonic, epoch=time.time, metadata=None):
        self.path = str(path)
        self.enabled = bool(enabled)
        self.gid = gid
        self.seat = seat
        self.session_id = session_id or str(uuid.uuid4())
        self.clock = clock
        self.epoch = epoch
        self.metadata = dict(metadata or {})
        self._ordinal = 0
        self._dropped = 0
        self._drop_first = None
        self._drop_last = None
        self._high_water = 0
        self._capture_ns: list[int] = []
        self._lock = threading.Lock()
        self._closed = False
        self._queue = queue.Queue(maxsize=max(1, int(queue_size)))
        self._thread = None
        if self.enabled:
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
            self._thread = threading.Thread(target=self._persist, name="replay-trace", daemon=True)
            self._thread.start()

    @property
    def dropped(self) -> int:
        return self._dropped

    @property
    def high_water_mark(self) -> int:
        return self._high_water

    @property
    def capture_p95_ms(self) -> float:
        values = sorted(self._capture_ns)
        if not values:
            return 0.0
        return values[min(len(values) - 1, int(len(values) * 0.95))] / 1_000_000

    def _header(self):
        return {"kind": "header", "traceSchemaVersion": TRACE_SCHEMA_VERSION,
                "sessionId": self.session_id, "gid": self.gid, "seat": self.seat,
                "metadata": scrub_credentials(self.metadata),
                "captureClock": "monotonic+epoch"}

    def _persist(self):
        try:
            with open(self.path, "w", encoding="utf-8") as handle:
                handle.write(json.dumps(self._header(), ensure_ascii=False,
                                        sort_keys=True) + "\n")
                while True:
                    item = self._queue.get()
                    if item is None:
                        break
                    try:
                        handle.write(json.dumps(scrub_credentials(item), ensure_ascii=False,
                                                sort_keys=True, default=str) + "\n")
                        handle.flush()
                    except (OSError, ValueError) as exc:
                        with self._lock:
                            self._dropped += 1
                            self._drop_first = self._drop_first or item.get("localOrdinal")
                            self._drop_last = item.get("localOrdinal")
                        # Keep the game path independent from trace I/O.
                        self._write_error = type(exc).__name__
                footer = {"kind": "footer", "traceSchemaVersion": TRACE_SCHEMA_VERSION,
                          "sessionId": self.session_id, "gid": self.gid,
                          "lastOrdinal": self._ordinal, "dropped": self._dropped,
                          "highWaterMark": self._high_water,
                          "captureP95Ms": self.capture_p95_ms,
                          "writeError": getattr(self, "_write_error", None)}
                handle.write(json.dumps(footer, ensure_ascii=False, sort_keys=True) + "\n")
                handle.flush()
        except OSError:
            self._write_error = "OSError"

    def capture(self, kind: str, payload: Any = None, *, gid=None, round_no=None,
                seat=None, seq_no=None, request_id=None, causal_parents=(),
                state_before=None, state_after=None, outcome=None,
                timestamp=None, clock_domain="monotonic", precision="ns",
                transport_request_id=None, attempt_index=None,
                event_id=None) -> str | None:
        """Capture one observation; disabled mode does not copy ``payload``."""
        if not self.enabled:
            return None
        started = time.perf_counter_ns()
        with self._lock:
            if self._closed:
                return None
            ordinal = self._ordinal
            self._ordinal += 1
        record_id = stable_id("trace", self.session_id, ordinal, kind)
        # Deep copy belongs strictly after the enabled guard.  It prevents
        # callers mutating a live Mirror from rewriting queued evidence.
        safe_payload = scrub_credentials(copy.deepcopy(payload))
        before = scrub_credentials(copy.deepcopy(state_before)) if state_before is not None else None
        after = scrub_credentials(copy.deepcopy(state_after)) if state_after is not None else None
        capture_monotonic = self.clock()
        capture_epoch = self.epoch() if timestamp is None else timestamp
        record = {"kind": kind, "traceSchemaVersion": TRACE_SCHEMA_VERSION,
                  "sessionId": self.session_id, "gid": gid if gid is not None else self.gid,
                  "roundNo": round_no, "seat": seat if seat is not None else self.seat,
                  "recordId": record_id, "localOrdinal": ordinal,
                  "captureMonotonic": capture_monotonic,
                  "captureTimestamp": capture_epoch, "clockDomain": clock_domain,
                  "precision": precision, "causalParents": list(causal_parents),
                  "logicalRequestId": request_id, "payload": safe_payload,
                  "transportRequestId": transport_request_id,
                  "attemptIndex": attempt_index, "eventId": event_id,
                  "stateBefore": before, "stateAfter": after,
                  "outcome": outcome}
        try:
            self._queue.put_nowait(record)
            self._high_water = max(self._high_water, self._queue.qsize())
        except queue.Full:
            with self._lock:
                self._dropped += 1
                self._drop_first = self._drop_first if self._drop_first is not None else ordinal
                self._drop_last = ordinal
        self._capture_ns.append(time.perf_counter_ns() - started)
        return record_id

    def checkpoint(self, state: Any, **fields):
        return self.capture("checkpoint", state, state_after=state, **fields)

    def flush(self, timeout: float = 5.0):
        if not self.enabled:
            return
        end = time.monotonic() + timeout
        while not self._queue.empty() and time.monotonic() < end:
            time.sleep(0.001)

    def close(self, *, timeout: float = 5.0):
        if not self.enabled or self._closed:
            return
        self._closed = True
        # close is outside the game scheduling path; waiting here lets the
        # footer describe the final observed ordinal.
        self.flush(timeout)
        try:
            self._queue.put(None, timeout=max(0.0, timeout))
        except queue.Full:
            self._write_error = "shutdown_queue_full"
        if self._thread is not None:
            self._thread.join(timeout=max(0.0, timeout))


TraceRecorder = ReplayTraceWriter
