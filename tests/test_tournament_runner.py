"""Formal-tournament runner tests.

All tests use deterministic local doubles.  In particular, no registration
token in this file is valid and no test opens a network connection.
"""

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

from mj.platform.api import Api, ApiError
from mj.platform.bot_client import BotClient, FORMAL_POLL_INTERVAL
from mj.platform.config import TournamentConfigError, load_tournament_config
from mj.platform.recorder import Recorder
from mj.platform.runner import DumpingApi
from mj.platform.security import redact_text, redact_value
from mj.platform.tournament import (TournamentContext, TournamentResult,
                                    TournamentRules)
from mj.platform.tournament_runner import (TournamentWorker, build_parser,
                                           _is_auth_error, _is_transient_error,
                                           main, run_tournament)


def _api_error(status, code, message=""):
    return ApiError(status, json.dumps({"code": code, "message": message}))


class FakeTournamentApi:
    """Small scoped API double with independently scripted lifecycle facts."""

    instances = []
    plans = {}

    def __init__(self, server, token, state_rate=15.0):
        self.base = server.rstrip("/")
        self.token = token
        self.state_rate = state_rate
        self.state_throttle = object()
        self.plan = self.plans[token]
        self.tournament_id = self.plan.get("tournament_id", f"tid-{token}")
        self.me_calls = 0
        self.tournament_calls = 0
        self.register_calls = []
        self.ready_calls = []
        self.statuses = list(self.plan.get("statuses", []))
        self.active_sequence = list(self.plan.get("active_sequence", []))
        self.tournament_failures = list(
            self.plan.get("tournament_failures", []))
        self.instances.append(self)

    def me(self):
        self.me_calls += 1
        exc = self.plan.get("me_error")
        if exc is not None:
            raise exc
        active = self.plan.get("active_games", [])
        if self.active_sequence:
            active = self.active_sequence.pop(0)
        return {
            "user_id": self.plan.get("user_id", f"user-{self.token}"),
            "tournament_id": self.tournament_id,
            "active_games": active,
        }

    def rules(self):
        self.plan.setdefault("rules_calls", 0)
        self.plan["rules_calls"] += 1
        exc = self.plan.get("rules_error")
        if exc is not None:
            raise exc
        return {"config": dict(self.plan.get("rules", {
            "M": 10, "Rounds": 8, "BaseScore": 1,
            "YouCaiBiKao": False,
        }))}

    def tournament(self, tid):
        self.tournament_calls += 1
        if self.tournament_failures:
            failure = self.tournament_failures.pop(0)
            if isinstance(failure, BaseException):
                raise failure
        if not self.statuses:
            raise AssertionError("fake tournament status script exhausted")
        status = self.statuses.pop(0)
        if isinstance(status, BaseException):
            raise status
        if isinstance(status, dict):
            return dict(status)
        return {"status": status, "stage_id": self.plan.get("stage_id", "s1"),
                "qualified": True,
                "my_games": self.plan.get("my_games", [])}

    def register(self, tid):
        self.register_calls.append(tid)
        failures = self.plan.get("register_errors", [])
        if failures:
            failure = failures.pop(0)
            raise failure
        return {}

    def ready(self, tid):
        self.ready_calls.append(tid)
        failures = self.plan.get("ready_errors", [])
        if failures:
            failure = failures.pop(0)
            raise failure
        return {}


def _context(api, label="main"):
    return TournamentContext.from_me(
        token_label=label,
        server=api.base,
        response={"user_id": f"user-{label}",
                  "tournament_id": api.tournament_id,
                  "active_games": []},
        rules=TournamentRules.from_response(api.rules()))


def _formal_bot(api, label="main"):
    bot = BotClient(api, label, lambda *_: -1, mode="tournament",
                    use_notify=False)
    bot.configure_tournament(_context(api, label))
    bot._formal_poll_interval = 0
    return bot


class TestContractsAndConfig(unittest.TestCase):
    def test_rules_are_scoped_and_unknown_fields_are_ignored(self):
        rules = TournamentRules.from_response({
            "config": {
                "M": 12, "Rounds": 3, "BaseScore": 2,
                "YouCaiBiKao": "true", "WindowSec": 1.25,
                "unknown_secret": "ignored",
            }
        })
        self.assertEqual(rules.m, 12)
        self.assertEqual(rules.rounds, 3)
        self.assertEqual(rules.base_score, 2)
        self.assertTrue(rules.you_cai_bi_kao)
        self.assertNotIn("unknown_secret", rules.config)
        self.assertEqual(rules.timeout_window["WindowSec"], 1.25)

    def test_result_is_json_safe_and_has_stable_reason(self):
        result = TournamentResult(
            "main", final_stage=("stage", 2), termination_reason="bogus",
            diagnostics={"set": {"g1", "g2"}})
        encoded = json.dumps(result.as_dict(), ensure_ascii=False)
        self.assertIn('"termination_reason": "PROTOCOL_FATAL"', encoded)
        self.assertIn("g1", encoded)

    def test_loader_accepts_one_token_and_rejects_unsafe_labels(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "tournament.json")
            with open(path, "w", encoding="utf-8") as stream:
                json.dump({"server": "https://server", "tokens":
                           {"main": "secret"}}, stream)
            cfg = load_tournament_config(path)
            self.assertEqual(list(cfg["tokens"]), ["main"])

            for label in ("", ".", "..", "a/b", "a\\b"):
                with open(path, "w", encoding="utf-8") as stream:
                    json.dump({"server": "https://server", "tokens":
                               {label: "secret"}}, stream)
                with self.assertRaises(TournamentConfigError):
                    load_tournament_config(path)

    def test_dedicated_tournament_token_takes_precedence_over_room_tokens(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "tournament.json")
            with open(path, "w", encoding="utf-8") as stream:
                json.dump({
                    "server": "https://server",
                    "tournament_token": "formal-secret",
                    "tokens": {"青龙": "test-room-secret"},
                }, stream)

            cfg = load_tournament_config(path)
            self.assertEqual(cfg["tokens"], {"tournament": "formal-secret"})

            for invalid in ("", "   ", None, 123):
                with open(path, "w", encoding="utf-8") as stream:
                    json.dump({"server": "https://server",
                               "tournament_token": invalid}, stream)
                with self.assertRaises(TournamentConfigError):
                    load_tournament_config(path)

    def test_legacy_config_loaders_are_not_replaced(self):
        from mj.platform.config import load_config, load_match_config
        self.assertTrue(callable(load_config))
        self.assertTrue(callable(load_match_config))

    def test_api_wrappers_and_error_classes_cover_tournament_surface(self):
        api = Api("https://server", "secret")
        with mock.patch.object(api, "get", return_value={}) as get, \
                mock.patch.object(api, "post", return_value={}) as post:
            api.me()
            api.rules()
            api.tournament("tid")
            api.register("tid")
            api.ready("tid")
        self.assertEqual([call.args[0] for call in get.call_args_list],
                         ["/api/me", "/api/tournaments/me/rules",
                          "/api/tournaments/tid"])
        self.assertEqual([call.args[0] for call in post.call_args_list],
                         ["/api/tournaments/tid/register",
                          "/api/tournaments/tid/ready"])
        self.assertTrue(_is_auth_error(_api_error(401, "INVALID_TOKEN")))
        self.assertTrue(_is_auth_error(_api_error(403, "FORBIDDEN")))
        self.assertTrue(_is_transient_error(_api_error(503, "TEMPORARY")))
        self.assertTrue(_is_transient_error(TimeoutError("timeout")))
        self.assertFalse(_is_transient_error(_api_error(400, "BAD_REQUEST")))


class TestFormalLifecycle(unittest.TestCase):
    def setUp(self):
        FakeTournamentApi.instances = []
        FakeTournamentApi.plans = {}

    def _make_api(self, token="secret", **plan):
        FakeTournamentApi.plans[token] = plan
        return FakeTournamentApi("https://server", token)

    def test_multistage_presence_and_repeated_stage_attendance(self):
        api = self._make_api(
            statuses=[
                {"status": "registering", "stage_id": "s1"},
                {"status": "stage_open", "stage_id": "s1",
                 "qualified": True},
                {"status": "running", "stage_id": "s1",
                 "qualified": True, "my_games": []},
                {"status": "stage_done", "stage_id": "s1",
                 "qualified": True},
                {"status": "stage_done", "stage_id": "s1",
                 "qualified": True},
                {"status": "stage_open", "stage_id": "s2",
                 "qualified": True},
                {"status": "running", "stage_id": "s2",
                 "qualified": True, "my_games": []},
                {"status": "finished", "stage_id": "s2",
                 "qualified": True},
            ])
        bot = _formal_bot(api)
        sleeps = []
        with mock.patch("mj.platform.bot_client.time.sleep",
                        side_effect=lambda value: sleeps.append(value)):
            stats = bot.run()

        self.assertEqual(stats["termination_reason"], "FINISHED")
        self.assertEqual(stats["final_status"], "finished")
        self.assertTrue(stats["qualified"])
        self.assertEqual(len(api.register_calls), 2)
        self.assertEqual(len(api.ready_calls), 2)
        self.assertEqual(stats["stage_transitions"][0]["stage"],
                         ("top", "stage_id", "s1"))
        self.assertEqual(stats["stage_transitions"][-1]["status"],
                         "finished")
        # The default formal poll is deliberately much shorter than the
        # platform's presence window, including during stage_done.
        self.assertEqual(FORMAL_POLL_INTERVAL, 1.0)
        self.assertTrue(all(value <= 60 for value in sleeps))

    def test_direct_terminal_states(self):
        for status, reason in (("finished", "FINISHED"),
                               ("closed", "CLOSED"),
                               ("void", "VOID")):
            with self.subTest(status=status):
                api = self._make_api(token=f"tok-{status}",
                                     statuses=[{"status": status,
                                                "stage_id": "final"}])
                stats = _formal_bot(api).run()
                self.assertEqual(stats["termination_reason"], reason)
                self.assertEqual(stats["final_status"], status)

    def test_not_qualified_is_normal_elimination(self):
        api = self._make_api(
            statuses=[{"status": "stage_open", "stage_id": "s1",
                       "qualified": False}])
        stats = _formal_bot(api).run()
        self.assertEqual(stats["termination_reason"], "ELIMINATED")
        self.assertEqual(api.ready_calls, [])

    def test_started_race_and_not_qualified_ready_are_bounded(self):
        api = self._make_api(
            statuses=[
                {"status": "stage_open", "stage_id": "s1",
                 "qualified": True},
                {"status": "stage_open", "stage_id": "s1",
                 "qualified": False},
            ],
            register_errors=[_api_error(409, "TOURNAMENT_STARTED")],
            ready_errors=[_api_error(409, "NOT_QUALIFIED")])
        stats = _formal_bot(api).run()
        self.assertEqual(stats["termination_reason"], "ELIMINATED")
        self.assertEqual(len(api.register_calls), 1)
        self.assertEqual(len(api.ready_calls), 1)
        self.assertTrue(any(item["reason"] == "TOURNAMENT_STARTED"
                            for item in stats["tournament_warnings"]))

    def test_stage_crash_waits_and_does_not_finish(self):
        api = self._make_api(
            statuses=[
                {"status": "stage_done", "stage_id": "s1",
                 "stage_crashed": True},
                {"status": "stage_done", "stage_id": "s1",
                 "stage_crashed": True},
                {"status": "stage_open", "stage_id": "s2",
                 "qualified": True},
                {"status": "finished", "stage_id": "s2"},
            ])
        stats = _formal_bot(api).run()
        reasons = [item["reason"] for item in stats["tournament_warnings"]]
        self.assertEqual(reasons, ["STAGE_CRASHED_WAITING"])
        self.assertEqual(stats["termination_reason"], "FINISHED")
        self.assertGreaterEqual(api.tournament_calls, 4)

    def test_transient_polling_backoff_recovers_before_deciding(self):
        api = self._make_api(
            statuses=[{"status": "stage_done", "stage_id": "s1"},
                      {"status": "finished", "stage_id": "s1"}],
            tournament_failures=[OSError("connection reset"),
                                 TimeoutError("read timed out")])
        bot = _formal_bot(api)
        sleeps = []
        with mock.patch.object(
                BotClient, "_sleep_stop",
                side_effect=lambda seconds, stop: (sleeps.append(seconds)
                                                    or False)):
            stats = bot.run()
        self.assertEqual(stats["termination_reason"], "FINISHED")
        self.assertEqual(api.tournament_calls, 4)
        self.assertEqual(sleeps[:2], [0.5, 1.0])
        self.assertTrue(all(value <= 8.0 for value in sleeps))

    def test_fallback_stage_key_spans_registering_and_stage_open(self):
        api = self._make_api(
            statuses=["registering", "stage_open", "stage_done",
                      "stage_open", "finished"], stage_id=None)
        bot = _formal_bot(api)
        stats = bot.run()
        self.assertEqual(stats["termination_reason"], "FINISHED")
        self.assertEqual(len(api.ready_calls), 2)
        stages = [item["stage"] for item in stats["stage_transitions"]
                  if item["status"] == "stage_open"]
        self.assertEqual(stages, [("edge", 1), ("edge", 2)])

    def test_empty_active_games_and_late_tiebreak_are_not_completion(self):
        api = self._make_api(
            active_sequence=[
                [{"game_id": "g1"}, {"game_id": "foreign"}],
                [{"game_id": "g1"}],
                [{"game_id": "g1"}, {"game_id": "g2"}],
            ],
            statuses=[
                {"status": "running", "stage_id": "s1",
                 "my_games": ["g1"]},
                {"status": "running", "stage_id": "s1",
                 "my_games": ["g1"]},
                {"status": "running", "stage_id": "s1",
                 "my_games": ["g1", "g2"]},
                {"status": "finished", "stage_id": "s1"},
            ])
        bot = _formal_bot(api)
        started = []

        def play(gid):
            started.append(gid)
            bot._done_games.add(gid)

        bot._play_game_safe = play
        stats = bot.run()
        self.assertEqual(stats["termination_reason"], "FINISHED")
        self.assertEqual(started, ["g1", "g2"])
        self.assertNotIn("foreign", started)

    def test_multiple_games_share_the_same_api_throttle(self):
        api = self._make_api(
            active_sequence=[[{"game_id": "g1"}, {"game_id": "g2"}]],
            statuses=[{"status": "running", "stage_id": "s1",
                       "my_games": ["g1", "g2"]},
                      {"status": "finished", "stage_id": "s1"}])
        bot = _formal_bot(api)
        started = []

        def play(gid):
            started.append(gid)
            bot._done_games.add(gid)

        bot._play_game_safe = play
        bot.run()
        self.assertCountEqual(started, ["g1", "g2"])
        self.assertIs(api.state_throttle, api.state_throttle)

        real_api = Api("https://server", "token", state_rate=15.0)
        real_bot = BotClient(real_api, "main", lambda *_: -1,
                             mode="tournament")
        self.assertIs(real_bot._state_scheduler.throttle,
                      real_api.state_throttle)

    def test_formal_game_metadata_carries_the_token_rule_snapshot(self):
        api = self._make_api(
            rules={"M": 12, "Rounds": 3, "BaseScore": 2,
                   "YouCaiBiKao": True, "WindowSec": 1.5},
            statuses=["finished"])
        with tempfile.TemporaryDirectory() as root:
            recorder = Recorder(root=root, default_name="main",
                                redact_secrets=(api.token,))
            bot = _formal_bot(api)
            bot.recorder = recorder
            bot._play_loop = lambda gid, wake, sse: None
            bot.play_game("g1")
            recorder.close_all()
            day = __import__("time").strftime("%Y%m%d")
            with open(os.path.join(root, day, "main_g1.jsonl"),
                      encoding="utf-8") as stream:
                meta = json.loads(stream.readline())
        self.assertEqual(meta["mode"], "tournament")
        self.assertEqual(meta["rules"]["M"], 12)
        self.assertEqual(meta["rules"]["Rounds"], 3)
        self.assertEqual(meta["rules"]["BaseScore"], 2)
        self.assertTrue(meta["rules"]["YouCaiBiKao"])
        self.assertEqual(meta["rules"]["config"]["WindowSec"], 1.5)

    def test_shutdown_before_poll_returns_interrupted(self):
        api = self._make_api(
            statuses=[{"status": "running", "stage_id": "s1",
                       "my_games": []}])
        stop = __import__("threading").Event()
        stop.set()
        bot = _formal_bot(api)
        stats = bot.run(stop=stop)
        self.assertEqual(stats["termination_reason"], "INTERRUPTED")
        self.assertEqual(api.tournament_calls, 0)


class TestWorkerAndIsolation(unittest.TestCase):
    def setUp(self):
        FakeTournamentApi.instances = []
        FakeTournamentApi.plans = {}

    def test_preflight_identity_rules_and_bot_run_has_no_normal_cap(self):
        token = "registration-secret"
        FakeTournamentApi.plans[token] = {
            "tournament_id": "tid-main", "user_id": "u-main",
            "rules": {"M": 12, "Rounds": 2, "BaseScore": 2,
                      "YouCaiBiKao": True, "vendor_extra": "ignored"},
        }
        calls = {}

        class FakeBot:
            def __init__(self, api, name, decide, **kwargs):
                self.api = api
                self.context = None
                self.recorder = kwargs.get("recorder")

            def configure_tournament(self, context):
                self.context = context

            def run(self, max_games=None, stop=None):
                calls["max_games"] = max_games
                calls["rules"] = self.context.rules
                return {
                    "termination_reason": "FINISHED",
                    "final_status": "finished", "final_stage": "s1",
                    "qualified": True, "games": 2, "actions": 10,
                    "hu": 1, "response_409": 0, "post_uncertain": 0,
                }

        worker = TournamentWorker(
            "main", "https://server", token, strategy="random",
            recorder=False, api_factory=FakeTournamentApi,
            bot_factory=FakeBot)
        result = worker.run()
        self.assertEqual(result.termination_reason, "FINISHED")
        self.assertEqual(result.tournament_id, "tid-main")
        self.assertEqual(result.user_id, "u-main")
        self.assertIsNone(calls["max_games"])
        self.assertTrue(calls["rules"].you_cai_bi_kao)
        self.assertNotIn("vendor_extra", calls["rules"].config)

    def test_recorder_is_default_and_closed_after_worker_result(self):
        token = "recorder-secret"
        FakeTournamentApi.plans[token] = {"tournament_id": "tid-record"}
        seen = {}

        class FakeRecorder:
            def __init__(self, **kwargs):
                seen["recorder_kwargs"] = kwargs
                self.closed = False

            def close_all(self):
                self.closed = True
                seen["closed"] = True

        class FakeBot:
            def __init__(self, api, name, decide, recorder=None, mode=None,
                         **kwargs):
                seen["recorder"] = recorder

            def configure_tournament(self, context):
                pass

            def run(self, max_games=None, stop=None):
                return {"termination_reason": "FINISHED",
                        "final_status": "finished"}

        result = TournamentWorker(
            "main", "https://server", token, strategy="random",
            api_factory=FakeTournamentApi, bot_factory=FakeBot,
            recorder_factory=FakeRecorder).run()
        self.assertEqual(result.termination_reason, "FINISHED")
        self.assertIsNotNone(seen["recorder"])
        self.assertEqual(seen["recorder_kwargs"]["redact_secrets"],
                         (token,))
        self.assertTrue(seen["closed"])

    def test_two_tokens_keep_identity_rules_and_api_resources_isolated(self):
        FakeTournamentApi.plans = {
            "a-secret": {"tournament_id": "tid-a", "user_id": "ua",
                         "rules": {"YouCaiBiKao": False}},
            "b-secret": {"tournament_id": "tid-b", "user_id": "ub",
                         "rules": {"YouCaiBiKao": True}},
        }
        seen = {}

        class FakeBot:
            def __init__(self, api, name, decide, **kwargs):
                self.api = api
                self.name = name

            def configure_tournament(self, context):
                seen[self.name] = (context.tournament_id,
                                   context.rules.you_cai_bi_kao)

            def run(self, max_games=None, stop=None):
                return {"termination_reason": "ELIMINATED",
                        "final_status": "stage_open", "qualified": False}

        results = run_tournament(
            {"server": "https://server", "tokens":
             {"alpha": "a-secret", "beta": "b-secret"}},
            strategy="random", no_recorder=True,
            api_factory=FakeTournamentApi, bot_factory=FakeBot)
        self.assertEqual(results["alpha"]["tournament_id"], "tid-a")
        self.assertEqual(results["beta"]["tournament_id"], "tid-b")
        self.assertEqual(seen, {"alpha": ("tid-a", False),
                                "beta": ("tid-b", True)})
        self.assertEqual(len(FakeTournamentApi.instances), 2)
        self.assertIsNot(FakeTournamentApi.instances[0].state_throttle,
                         FakeTournamentApi.instances[1].state_throttle)

    def test_run_tournament_uses_dedicated_token_mapping(self):
        FakeTournamentApi.plans = {
            "formal-secret": {"tournament_id": "tid-formal",
                              "user_id": "u-formal"},
            "test-room-secret": {"tournament_id": "tid-room",
                                  "user_id": "u-room"},
        }
        seen = {}

        class FakeBot:
            def __init__(self, api, name, decide, **kwargs):
                seen[name] = api.token

            def configure_tournament(self, context):
                pass

            def run(self, max_games=None, stop=None):
                return {"termination_reason": "ELIMINATED"}

        results = run_tournament(
            {"server": "https://server",
             "tournament_token": "formal-secret",
             "tokens": {"room": "test-room-secret"}},
            strategy="random", no_recorder=True,
            api_factory=FakeTournamentApi, bot_factory=FakeBot)
        self.assertEqual(seen, {"tournament": "formal-secret"})
        self.assertEqual(list(results), ["tournament"])

    def test_unbound_and_auth_fail_without_starting_bot(self):
        class NeverBot:
            def __init__(self, *args, **kwargs):
                raise AssertionError("game bot must not start")

        for plan, reason in (
                ({"tournament_id": ""}, "TOKEN_NOT_BOUND"),
                ({"me_error": _api_error(401, "INVALID_TOKEN",
                                          "bad registration-secret")},
                 "AUTH_FAILED")):
            token = f"secret-{reason}"
            FakeTournamentApi.plans[token] = plan
            output = io.StringIO()
            worker = TournamentWorker(
                "main", "https://server", token, strategy="random",
                recorder=False, api_factory=FakeTournamentApi,
                bot_factory=NeverBot)
            with redirect_stdout(output):
                result = worker.run()
            self.assertEqual(result.termination_reason, reason)
            self.assertNotIn(token, output.getvalue())


class TestSecurityAndCli(unittest.TestCase):
    def test_redaction_covers_logs_recorder_trace_and_dump(self):
        token = "bearer-secret-123"
        self.assertNotIn(token, redact_text(
            f"Authorization: Bearer {token}", []))
        self.assertEqual(redact_value({"token": token, "body": token})[
            "token"], "[REDACTED]")

        with tempfile.TemporaryDirectory() as root:
            trace_root = os.path.join(root, "trace")
            recorder = Recorder(root=os.path.join(root, "games"),
                                default_name="main", replay_trace=True,
                                trace_root=trace_root,
                                redact_secrets=(token,))
            recorder.meta("g1", "main", "tid", rules={"error": token})
            recorder.action("g1", "draw", {"action": "discard",
                             "tile": "1w"}, ok=False,
                            message=f"Authorization: Bearer {token}")
            recorder.close_all()

            dump = DumpingApi("https://server", token, "main",
                              os.path.join(root, "dump"))
            dump._dump("error", {"body": token, "authorization": token})
            for directory, _, files in os.walk(root):
                for filename in files:
                    with open(os.path.join(directory, filename),
                              encoding="utf-8") as stream:
                        self.assertNotIn(token, stream.read())

    def test_cli_has_no_normal_games_argument_and_warns_on_debug_controls(self):
        parser = build_parser()
        self.assertNotIn("--games", parser.format_help())
        self.assertIn("--max-games-debug", parser.format_help())
        with self.assertRaises(SystemExit):
            parser.parse_args(["--games", "1"])

        cfg = {"server": "https://server", "tokens": {"main": "secret"}}
        with mock.patch("mj.platform.tournament_runner.load_tournament_config",
                        return_value=cfg), \
                mock.patch("mj.platform.tournament_runner.run_tournament",
                           return_value={"main": {
                               "termination_reason": "ELIMINATED"}}), \
                redirect_stdout(io.StringIO()) as output:
            code = main(["--no-recorder", "--max-games-debug", "1"])
        self.assertEqual(code, 0)
        self.assertIn("仅调试", output.getvalue())
        self.assertIn("关闭正式锦标赛 Recorder", output.getvalue())

    def test_cli_returns_nonzero_for_auth_or_protocol_failure(self):
        cfg = {"server": "https://server", "tokens": {"main": "secret"}}
        for reason, expected in (("AUTH_FAILED", 1), ("PROTOCOL_FATAL", 1),
                                 ("FINISHED", 0), ("ELIMINATED", 0)):
            with self.subTest(reason=reason), \
                    mock.patch(
                        "mj.platform.tournament_runner.load_tournament_config",
                        return_value=cfg), \
                    mock.patch("mj.platform.tournament_runner.run_tournament",
                               return_value={"main": {
                                   "termination_reason": reason}}), \
                    redirect_stdout(io.StringIO()):
                self.assertEqual(main([]), expected)


if __name__ == "__main__":
    unittest.main()
