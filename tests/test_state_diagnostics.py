"""Focused checks for state transport diagnostics and recorder extensions."""

import io
import json
import os
import tempfile
import urllib.error
from unittest import mock

from mj.platform.api import Api, _TLS
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
