"""动作提交传输与 match runner 对照开关回归。

动作 POST 的结果在网络异常后可能未知，因此不允许复用普通 GET/POST
的重试策略。测试全部使用独立 mock，不访问平台。
"""

import io
import json
import os
import tempfile
import time
import urllib.error
import unittest
from contextlib import redirect_stdout
from unittest import mock

from mj.platform.api import (
    ActionDeadlineExceeded,
    ActionSubmissionError,
    Api,
)
from mj.platform.match_runner import bot_transport_options, main
from mj.platform.runner import DumpingApi


def _http_error(req, status, body=b'{"code":"RATE_LIMITED"}'):
    return urllib.error.HTTPError(
        req.full_url, status, "error", {}, io.BytesIO(body))


class TestActionTransport(unittest.TestCase):
    def test_429_action_is_rejected_without_retry(self):
        calls = []

        def urlopen(req, timeout=None, context=None):
            calls.append((req.full_url, timeout))
            raise _http_error(req, 429)

        api = Api("https://x", "tok")
        with mock.patch("urllib.request.urlopen", urlopen), \
                self.assertRaises(ActionSubmissionError) as cm:
            api.game_action("g1", {"action": "pass"})

        self.assertEqual(len(calls), 1)
        self.assertEqual(cm.exception.status, 429)
        self.assertFalse(cm.exception.uncertain)
        self.assertFalse(cm.exception.timed_out)
        self.assertEqual(cm.exception.attempts, 1)

    def test_gateway_action_is_uncertain_and_not_retried(self):
        calls = []

        def urlopen(req, timeout=None, context=None):
            calls.append(req.full_url)
            raise _http_error(req, 502, b'{"code":"BAD_GATEWAY"}')

        api = Api("https://x", "tok")
        with mock.patch("urllib.request.urlopen", urlopen), \
                self.assertRaises(ActionSubmissionError) as cm:
            api.game_action("g1", {"action": "discard", "tile": "1w"})

        self.assertEqual(calls, ["https://x/api/games/g1/action"])
        self.assertEqual(cm.exception.status, 502)
        self.assertTrue(cm.exception.uncertain)
        self.assertTrue(cm.exception.result_unknown)
        self.assertEqual(cm.exception.attempts, 1)

    def test_lost_action_response_is_uncertain_and_not_retried(self):
        calls = []

        def urlopen(req, timeout=None, context=None):
            calls.append(req.full_url)
            raise urllib.error.URLError("connection reset after write")

        api = Api("https://x", "tok")
        with mock.patch("urllib.request.urlopen", urlopen), \
                self.assertRaises(ActionSubmissionError) as cm:
            api.game_action("g1", {"action": "peng", "tile": "5b"})

        self.assertEqual(len(calls), 1)
        self.assertEqual(cm.exception.status, 0)
        self.assertTrue(cm.exception.uncertain)
        self.assertFalse(cm.exception.timed_out)

    def test_action_timeout_is_uncertain_and_single_request_is_bounded(self):
        calls = []

        def urlopen(req, timeout=None, context=None):
            calls.append(timeout)
            raise TimeoutError("request deadline")

        api = Api("https://x", "tok", timeout=30.0)
        deadline = time.monotonic() + 0.25
        with mock.patch("urllib.request.urlopen", urlopen), \
                self.assertRaises(ActionSubmissionError) as cm:
            api.game_action("g1", {"action": "discard", "tile": "2w"},
                            deadline=deadline)

        self.assertEqual(len(calls), 1)
        self.assertLessEqual(calls[0], 0.25)
        self.assertTrue(cm.exception.uncertain)
        self.assertTrue(cm.exception.timed_out)

    def test_expired_action_deadline_sends_nothing(self):
        calls = []

        def urlopen(*args, **kwargs):
            calls.append(1)
            return io.BytesIO(b"{}")

        api = Api("https://x", "tok")
        with mock.patch("urllib.request.urlopen", urlopen), \
                self.assertRaises(ActionDeadlineExceeded) as cm:
            api.game_action("g1", {"action": "pass"},
                            deadline=time.monotonic() - 1)

        self.assertEqual(calls, [])
        self.assertTrue(cm.exception.deadline_exceeded)
        self.assertFalse(cm.exception.uncertain)
        self.assertEqual(cm.exception.attempts, 0)

    def test_seq_zero_snapshot_has_explicit_single_request_bound(self):
        calls = []

        def urlopen(req, timeout=None, context=None):
            calls.append((req.full_url, timeout))
            return io.BytesIO(b'{"snapshot": {"phase": "response_chi"},'
                              b'"seq": 7}')

        api = Api("https://x", "tok", timeout=35.0, state_rate=None)
        with mock.patch("urllib.request.urlopen", urlopen):
            result = api.game_snapshot("g1", timeout=0.2)

        self.assertEqual(result["seq"], 7)
        self.assertEqual(calls[0][0], "https://x/api/games/g1/state?seq=0")
        self.assertLessEqual(calls[0][1], 0.2)

    def test_dumping_api_records_uncertain_action_error(self):
        with tempfile.TemporaryDirectory() as dump_dir:
            api = DumpingApi("https://x", "tok", "bot", dump_dir)

            def urlopen(req, timeout=None, context=None):
                raise urllib.error.URLError("response lost")

            with mock.patch("urllib.request.urlopen", urlopen), \
                    self.assertRaises(ActionSubmissionError):
                api.game_action("g1", {"action": "pass"})

            paths = [os.path.join(dump_dir, p)
                     for p in os.listdir(dump_dir)]
            self.assertEqual(len(paths), 1)
            with open(paths[0], encoding="utf-8") as f:
                dumped = json.load(f)
            self.assertEqual(dumped["payload"]["action"], "pass")
            self.assertTrue(dumped["error"]["uncertain"])
            self.assertEqual(dumped["error"]["attempts"], 1)


class TestMatchTransportSwitches(unittest.TestCase):
    def test_transport_options_cover_all_cli_combinations(self):
        self.assertEqual(
            bot_transport_options(), {"use_notify": True, "long_poll": False})
        self.assertEqual(
            bot_transport_options(no_long_poll=True),
            {"use_notify": True, "long_poll": False})
        self.assertEqual(
            bot_transport_options(no_notify=True),
            {"use_notify": False, "long_poll": False})
        self.assertEqual(
            bot_transport_options(no_long_poll=True, no_notify=True),
            {"use_notify": False, "long_poll": False})

    def test_no_long_poll_cli_really_selects_sse(self):
        seen = {}

        class FakeApi:
            def __init__(self, *args, **kwargs):
                pass

            def me(self):
                return {"user_id": "u1"}

        class FakeBot:
            def __init__(self, api, name, decide, **kwargs):
                seen.update(kwargs)

            def run_match(self, **kwargs):
                return {"games": 0, "scores": []}

        with mock.patch("mj.platform.match_runner.load_match_config",
                        return_value={"server": "https://x",
                                      "match_token": "tok"}), \
                mock.patch("mj.platform.match_runner.make_decide",
                           return_value=lambda *_: 0), \
                mock.patch("mj.platform.match_runner.Api", FakeApi), \
                mock.patch("mj.platform.match_runner.BotClient", FakeBot), \
                mock.patch("mj.platform.match_runner.Recorder"), \
                redirect_stdout(io.StringIO()):
            main(["--games", "1", "--no-long-poll"])

        self.assertTrue(seen["use_notify"])
        self.assertFalse(seen["long_poll"])

    def test_no_notify_overrides_sse_and_reports_feature_disabled(self):
        seen = {}

        class FakeApi:
            def __init__(self, *args, **kwargs):
                pass

            def me(self):
                return {"user_id": "u1"}

        class FakeBot:
            def __init__(self, api, name, decide, **kwargs):
                seen.update(kwargs)

            def run_match(self, **kwargs):
                from mj.platform.api import ApiError
                raise ApiError(403, '{"code":"FEATURE_DISABLED",'
                                      '"message":"off"}')

        with mock.patch("mj.platform.match_runner.load_match_config",
                        return_value={"server": "https://x",
                                      "match_token": "tok"}), \
                mock.patch("mj.platform.match_runner.make_decide",
                           return_value=lambda *_: 0), \
                mock.patch("mj.platform.match_runner.Api", FakeApi), \
                mock.patch("mj.platform.match_runner.BotClient", FakeBot), \
                mock.patch("mj.platform.match_runner.Recorder"), \
                redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as cm:
                main(["--games", "1", "--no-long-poll", "--no-notify"])

        self.assertFalse(seen["use_notify"])
        self.assertFalse(seen["long_poll"])
        self.assertIn("FEATURE_DISABLED", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
