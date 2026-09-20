"""clientd 线上匹配会话的参数、凭据隔离与 runner wiring。"""

import time

import pytest

from mj.clientd.arena import make_arena_session_manager
from mj.clientd.errors import ValidationError
from mj.clientd.match import make_match_runner, normalize_match_config
from mj.clientd.settings import PlatformSettings


def test_normalize_match_config_defaults_and_bounds():
    cfg = normalize_match_config({})
    assert cfg["max_games"] == 10
    assert cfg["strategy"] == "bot"
    assert cfg["evaluator"] == "legacy"
    assert cfg["state_rate"] == 16.0

    with pytest.raises(ValidationError):
        normalize_match_config({"max_games": 0})
    with pytest.raises(ValidationError):
        normalize_match_config({"strategy": "unknown"})
    with pytest.raises(ValidationError):
        normalize_match_config({"state_rate": 0})


def test_match_runner_reads_local_settings_and_reports_progress(tmp_path,
                                                                 monkeypatch):
    settings_path = tmp_path / "platform.json"
    PlatformSettings(settings_path).update({
        "server": "https://platform.example",
        "tokens": {"match": "local-secret"},
    })
    seen = {}

    class FakeApi:
        def __init__(self, server, token, **kwargs):
            seen["api"] = (server, token, kwargs)

        def me(self):
            return {"user_id": "player-7"}

    def fake_decide(*args, **kwargs):
        fn = lambda *_: 0
        seen["decide"] = (args, kwargs)
        return fn

    class FakeRecorder:
        def __init__(self, **kwargs):
            seen["recorder"] = kwargs

    class FakeBot:
        def __init__(self, api, name, decide, **kwargs):
            seen["bot"] = (api, name, decide, kwargs)
            self._stats_lock = __import__("threading").Lock()
            self.stats = {"games": 0, "rooms": 0, "scores": []}

        def run_match(self, **kwargs):
            seen["run"] = kwargs
            assert callable(kwargs["stop"].is_set)
            assert kwargs["stop"].is_set() is False
            with self._stats_lock:
                self.stats.update(games=2, rooms=1, scores=[[1, 2, 3, 4]])
            return dict(self.stats)

    monkeypatch.setattr("mj.platform.api.Api", FakeApi)
    monkeypatch.setattr("mj.platform.runner.make_decide", fake_decide)
    monkeypatch.setattr("mj.platform.bot_client.BotClient", FakeBot)
    monkeypatch.setattr("mj.platform.recorder.Recorder", FakeRecorder)

    runner = make_match_runner(
        {"max_games": 2, "strategy": "bot", "evaluator": "legacy"},
        settings_path=settings_path,
    )
    session = type("Session", (), {"id": "s1", "progress": None})()
    result = runner(lambda: False, session)

    assert seen["api"] == ("https://platform.example", "local-secret", {"state_rate": 16.0})
    assert seen["bot"][1] == "player-7"
    assert seen["run"]["max_games"] == 2
    assert seen["run"]["room_close_wait"] == 65.0
    assert result["games"] == 2 and result["rooms"] == 1
    assert result["strategy"] == "bot"
    assert session.progress["done"] == 2
    assert session.progress["total"] == 2


def test_arena_manager_accepts_match_session_with_injected_runner(tmp_path,
                                                                  monkeypatch):
    def fake_runner_factory(config, settings_path=None):
        def runner(stop, session):
            session.progress = {"done": 1, "total": 1, "rate": 1.0,
                                "last_index": 0, "rooms": 1}
            return {"games": 1, "rooms": 1}
        return runner

    monkeypatch.setattr("mj.clientd.match.make_match_runner",
                        fake_runner_factory)
    mgr = make_arena_session_manager(settings_path=tmp_path / "unused.json")
    session = mgr.create("match", {"max_games": 1})
    deadline = time.time() + 2
    while time.time() < deadline and mgr.get(session.id).status == "running":
        time.sleep(0.01)
    assert mgr.get(session.id).status == "finished"
    assert mgr.get(session.id).result["games"] == 1
