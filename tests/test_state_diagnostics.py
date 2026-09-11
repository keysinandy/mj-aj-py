"""Focused checks for state transport diagnostics and recorder extensions."""

import io
import json
import os
import tempfile
import urllib.error
from unittest import mock

import pytest

from mj.platform.api import ActionSubmissionError, Api, _TLS
from mj.platform.recorder import Recorder


class _Response(io.BytesIO):
    def __init__(self, payload, status=200):
        super().__init__(payload)
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False


def _http_response(payload, status=200):
    response = _Response(json.dumps(payload).encode(), status=status)
    return response


def _read_log(recorder, gid, name="bot"):
    import time

    path = os.path.join(recorder.root, time.strftime("%Y%m%d"),
                        f"{name}_{gid}.jsonl")
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def test_state_attempts_keep_429_and_final_200_without_url_or_token():
    api = Api("https://state.example", "secret-token", state_rate=None)
    busy = mock.patch(
        "urllib.request.urlopen",
        side_effect=[
            urllib.error.HTTPError(
                "https://state.example/api/games/g/state?seq=3", 429,
                "busy", {}, io.BytesIO(b"{}")),
            _http_response({"seq": 3, "events": []}),
        ],
    )
    with busy, mock.patch("mj.platform.api.time.sleep"):
        assert api.game_state("g", 3) == {"seq": 3, "events": []}

    meta = _TLS.request_meta
    assert meta["attempts"] == 2
    assert meta["retry_429"] == 1
    assert meta["state_physical_attempts"] == 2
    assert meta["state_429"] == 1
    assert [a["status"] for a in meta["state_attempts"]] == [429, 200]
    assert all("started_epoch" in a and "latency_ms" in a
               for a in meta["state_attempts"])
    first, final = meta["state_attempts"]
    assert first.get("retry_after_s") is None
    assert first["timed_out"] is False
    assert first["timing"]["dns_ms"] is None
    assert first["timing"]["connect_ms"] is None
    assert first["timing"]["tls_ms"] is None
    assert first["timing"]["send_ms"] is None
    assert first["timing"]["pre_read_ms"] >= 0
    assert first["timing"]["read_ms"] is None
    assert final["timing"]["read_ms"] >= 0
    assert meta["diagnostic_capabilities"]["measured"] == [
        "total_ms", "pre_read_ms", "read_ms"]
    encoded = json.dumps(meta)
    assert "secret-token" not in encoded
    assert "state.example" not in encoded


def test_state_429_notifies_shared_throttle_before_retry():
    class Throttle:
        def __init__(self):
            self.acquires = []
            self.feedback = 0

        def acquire(self, gid, deadline):
            self.acquires.append((gid, deadline))

        def note_429(self):
            self.feedback += 1

    throttle = Throttle()
    api = Api("https://state.example", "secret-token",
              state_throttle=throttle)
    with mock.patch(
        "urllib.request.urlopen",
        side_effect=[
            urllib.error.HTTPError("https://state.example/state", 429,
                                   "busy", {}, io.BytesIO(b"{}")),
            _http_response({"seq": 1}),
        ],
    ), mock.patch("mj.platform.api.time.sleep"):
        assert api.game_state("g", 1) == {"seq": 1}
    assert throttle.feedback == 1
    assert throttle.acquires == [("g", None), ("g", None)]


def test_non_state_request_keeps_existing_transport_shape():
    api = Api("https://state.example", "secret-token", state_rate=None)
    with mock.patch("urllib.request.urlopen",
                    return_value=_http_response({"me": 1})):
        assert api.me() == {"me": 1}
    assert "state_attempts" not in _TLS.request_meta
    assert "state_physical_attempts" not in _TLS.request_meta


def test_state_status_from_response_double_is_json_safe():
    api = Api("https://state.example", "secret-token", state_rate=None)
    response = mock.MagicMock()
    response.__enter__.return_value = response
    response.status = mock.MagicMock()
    response.read.return_value = b'{}'
    with mock.patch("urllib.request.urlopen", return_value=response):
        assert api.game_state("g", 1) == {}
    attempt = _TLS.request_meta["state_attempts"][0]
    assert attempt["status"] == 200
    json.dumps(_TLS.request_meta)


def test_failed_state_permit_does_not_reuse_previous_attempt_metadata():
    api = Api("https://state.example", "secret-token", state_rate=None)
    with mock.patch("urllib.request.urlopen",
                    return_value=_http_response({"seq": 1})):
        assert api.game_state("g", 1) == {"seq": 1}
    assert _TLS.request_meta["state_physical_attempts"] == 1

    class FailingThrottle:
        def acquire(self, gid, deadline):
            raise RuntimeError("permit unavailable")

    api.state_throttle = FailingThrottle()
    with mock.patch("urllib.request.urlopen") as send:
        with mock.patch.object(api, "state_throttle", FailingThrottle()):
            try:
                api.game_state("g", 2)
            except RuntimeError:
                pass
            else:
                raise AssertionError("state permit failure was swallowed")
    send.assert_not_called()
    assert _TLS.attempts == 0
    assert _TLS.request_meta["state_attempts"] == []
    assert _TLS.request_meta["state_physical_attempts"] == 0


def test_action_attempt_has_safe_phase_timing_and_no_payload_metadata():
    api = Api("https://state.example", "secret-token", state_rate=None)
    payload = {"action": "discard", "tile": "1w"}
    with mock.patch("urllib.request.urlopen",
                    return_value=_http_response({"ok": True})):
        assert api.game_action("g", payload) == {"ok": True}

    meta = _TLS.request_meta
    assert meta["diagnostic_kind"] == "action"
    assert meta["action_physical_attempts"] == 1
    attempt = meta["action_attempts"][0]
    assert attempt["status"] == 200
    assert attempt["timed_out"] is False
    assert attempt["timeout_s"] == 35.0
    assert attempt["timing"]["total_ms"] >= 0
    assert attempt["timing"]["read_ms"] >= 0
    encoded = json.dumps(meta)
    assert "secret-token" not in encoded
    assert "discard" not in encoded
    assert "1w" not in encoded
    assert "state.example" not in encoded


def test_action_error_keeps_retry_after_and_timeout_diagnostics_without_retry():
    error = urllib.error.HTTPError(
        "https://state.example/api/games/g/action", 429, "busy",
        {"Retry-After": "1.25"}, io.BytesIO(b'{"code":"RATE_LIMITED"}'))
    api = Api("https://state.example", "secret-token", state_rate=None)
    with mock.patch("urllib.request.urlopen", side_effect=error) as send:
        with pytest.raises(ActionSubmissionError) as raised:
            api.game_action("g", {"action": "pass"})

    assert raised.value.status == 429
    send.assert_called_once()
    meta = _TLS.request_meta
    assert meta["diagnostic_kind"] == "action"
    assert meta["action_physical_attempts"] == 1
    attempt = meta["action_attempts"][0]
    assert attempt["status"] == 429
    assert attempt["retry_after_s"] == 1.25
    assert attempt["timed_out"] is False
    assert attempt["timing"]["read_ms"] is None


def test_http_error_records_header_and_body_boundaries_and_http_date_headers():
    error = urllib.error.HTTPError(
        "https://state.example/api/games/g/state", 503, "busy",
        {
            "Retry-After": "Wed, 21 Oct 2015 07:28:10 GMT",
            "Date": "Wed, 21 Oct 2015 07:28:00 GMT",
        }, io.BytesIO(b'{"message":"gateway"}'))
    api = Api("https://state.example", "secret-token", state_rate=None)
    with mock.patch("urllib.request.urlopen", side_effect=error), \
            mock.patch("mj.platform.api.time.time", return_value=1445412480.0), \
            mock.patch("mj.platform.api.time.sleep"):
        with pytest.raises(Exception):
            api.game_state("g", 1)

    attempt = _TLS.request_meta["state_attempts"][0]
    assert attempt["headers_received_mono"] is not None
    assert attempt["body_finished_mono"] is not None
    assert attempt["retry_after_raw"] == "Wed, 21 Oct 2015 07:28:10 GMT"
    assert attempt["server_date_raw"] == "Wed, 21 Oct 2015 07:28:00 GMT"
    assert attempt["retry_after_seconds"] == 10.0
    assert attempt["server_date_epoch"] == 1445412480.0
    assert attempt["deadline_left_at_send"] is None


def test_action_attempt_marks_state_throttle_not_applicable():
    api = Api("https://state.example", "secret-token", state_rate=None)
    with mock.patch("urllib.request.urlopen",
                    return_value=_http_response({"ok": True})):
        api.game_action("g", {"action": "pass"})
    attempt = _TLS.request_meta["action_attempts"][0]
    assert attempt["throttle"]["status"] == "not_applicable"
    assert attempt["queue_wait_ms"] is None


def test_recorder_request_kind_and_window_confirmation_are_optional_records():
    with tempfile.TemporaryDirectory() as root:
        recorder = Recorder(root=root)
        recorder.req("g", 4, 200, 12.5, attempts=1,
                     request_kind="WINDOW_PENG")
        recorder.window_confirm(
            "g", phase="response_peng", window_id="w1", seq=4,
            status="open", request_kind="WINDOW_PENG",
            responding_seats=[0], legal=[-1, 7], chosen=-1,
            deadline_at=123.4, outcome="awaiting_snapshot", reason=None)
        records = _read_log(recorder, "g")

    req = records[0]
    assert req["type"] == "req"
    assert req["request_kind"] == "WINDOW_PENG"
    confirm = records[1]
    assert confirm["type"] == "window_confirm"
    assert confirm["phase"] == "response_peng"
    assert confirm["window_id"] == "w1"
    assert confirm["responding_seats"] == [0]
    assert "reason" not in confirm


def test_recorder_req_without_request_kind_keeps_old_shape():
    with tempfile.TemporaryDirectory() as root:
        recorder = Recorder(root=root)
        recorder.req("g", 0, 200, 1.0)
        records = _read_log(recorder, "g")
    assert "request_kind" not in records[0]
