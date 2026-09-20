"""scripts/tournament.py(锦标赛启动器)的护栏/摘要/探活判定单测。

不触网:RoomProbe 注入 fake Api;main 的子进程与守护循环不做集成测试
(正式赛不可再生,线上行为由 docs/锦标赛README.md 的实弹记录背书)。
"""

import os
import sys

import pytest

_SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import tournament as t  # noqa: E402


# ---- evaluate_guards:安全护栏 ----

def test_guard_no_recorder_requires_accept():
    errs = t.evaluate_guards(strategy="bot", evaluator="legacy",
                             no_recorder=True,
                             accept_no_recorder=False,
                             accept_shape_v2=False)
    assert len(errs) == 1 and "--accept-no-recorder" in errs[0]


def test_guard_no_recorder_accepted():
    assert t.evaluate_guards(strategy="bot", evaluator="legacy",
                             no_recorder=True, accept_no_recorder=True,
                             accept_shape_v2=False) == []


def test_guard_bot_shape_v2_requires_accept():
    errs = t.evaluate_guards(strategy="bot", evaluator="shape-v2",
                             no_recorder=False, accept_no_recorder=False,
                             accept_shape_v2=False)
    assert len(errs) == 1 and "--accept-shape-v2" in errs[0]


def test_guard_bot_shape_v2_accepted():
    assert t.evaluate_guards(strategy="bot", evaluator="shape-v2",
                             no_recorder=False, accept_no_recorder=False,
                             accept_shape_v2=True) == []


def test_guard_defaults_pass():
    assert t.evaluate_guards(strategy="bot", evaluator="legacy",
                             no_recorder=False, accept_no_recorder=False,
                             accept_shape_v2=False) == []


def test_guard_policy_shape_v2_not_blocked():
    # 护栏只针对 strategy=bot 的 bot-evaluator;policy 策略不走该路径。
    assert t.evaluate_guards(strategy="policy", evaluator="shape-v2",
                             no_recorder=False, accept_no_recorder=False,
                             accept_shape_v2=False) == []


# ---- summarize_rules:配置摘要(用 2026-09-17 实弹 config 原文) ----

SESSION_CONFIG = {
    "M": 10, "Rounds": 16, "BaseScore": 1, "Name": "1024杭麻竞技二测",
    "Description": "决赛第一名奖金300,第二名奖金150,第三名奖金50",
    "YouCaiBiKao": False, "StartAt": 1789646400,
    "RegisterDeadlineAt": 1789617600, "DiscardTimeoutSec": 3,
    "PengTimeoutSec": 1, "ChiTimeoutSec": 1, "TimeoutMin": 0,
    "OnlineConfirm": True,
}


def test_summarize_rules_real_config():
    lines = t.summarize_rules(SESSION_CONFIG)
    text = "\n".join(lines)
    assert "1024杭麻竞技二测" in text
    assert "每场 16 局 × M=10" in text
    assert "(YCBK):关" in text
    assert "开赛时刻:" in text and "报名截止:" in text
    assert "DiscardTimeoutSec=3s" in text


def test_summarize_rules_degenerate():
    assert len(t.summarize_rules("bad")) == 1
    assert t.summarize_rules({}) == ["锦标赛:(未命名)"]


# ---- room_should_restart:终态判定 ----

@pytest.mark.parametrize("status,expected", [
    ("registering", True), ("stage_open", True),
    ("running", True), ("stage_done", True),
    ("finished", False), ("closed", False), ("void", False),
])
def test_room_should_restart(status, expected):
    assert t.room_should_restart(status) is expected


# ---- RoomProbe:注入 fake Api ----

class FakeApi:
    def __init__(self, me_resp=None, tournament_resp=None,
                 me_exc=None, tournament_exc=None):
        self.me_calls = 0
        self.tournament_calls = 0
        self._me_resp = me_resp
        self._tournament_resp = tournament_resp
        self._me_exc = me_exc
        self._tournament_exc = tournament_exc

    def me(self):
        self.me_calls += 1
        if self._me_exc is not None:
            raise self._me_exc
        return self._me_resp

    def tournament(self, tid):
        self.tournament_calls += 1
        if self._tournament_exc is not None:
            raise self._tournament_exc
        return self._tournament_resp


def _probe(fake, attempts=1):
    factory = lambda server, token, **kw: fake  # noqa: E731
    return t.RoomProbe("srv", "tok", api_factory=factory,
                       attempts=attempts).probe()


def test_probe_running_room_restart():
    verdict, detail = _probe(FakeApi(
        me_resp={"tournament_id": "t_1"},
        tournament_resp={"status": "running"}))
    assert verdict == "restart" and "running" in detail


def test_probe_finished_room_stop():
    verdict, detail = _probe(FakeApi(
        me_resp={"tournament_id": "t_1"},
        tournament_resp={"status": "finished"}))
    assert verdict == "stop" and "finished" in detail


def test_probe_unbound_token_stop():
    verdict, detail = _probe(FakeApi(me_resp={}))
    assert verdict == "stop" and "TOKEN_NOT_BOUND" in detail


def test_probe_auth_error_stop_without_retry():
    from mj.platform.api import ApiError
    fake = FakeApi(me_exc=ApiError(401, "denied"))
    verdict, detail = _probe(fake, attempts=3)
    assert verdict == "stop" and "401" in detail
    assert fake.me_calls == 1  # 鉴权失败不烧重试次数


def test_probe_transient_404_then_ok(monkeypatch):
    from mj.platform.api import ApiError
    # 2026-09-17 实弹:瞬时 404 TOURNAMENT_GONE 房间仍在,须重试。
    monkeypatch.setattr(t.time, "sleep", lambda s: None)
    gone = ApiError(404, '{"code":"TOURNAMENT_GONE","message":"x"}')
    responses = iter([gone, gone])
    fake = FakeApi(me_resp={"tournament_id": "t_1"},
                   tournament_resp={"status": "stage_open"})

    class Flaky(FakeApi):
        def me(self):
            exc = next(responses, None)
            if exc is not None:
                raise exc
            return super().me()

    flaky = Flaky(me_resp={"tournament_id": "t_1"},
                  tournament_resp={"status": "stage_open"})
    verdict, _ = _probe(flaky, attempts=3)
    assert verdict == "restart"


def test_probe_not_found_404_stops(monkeypatch):
    from mj.platform.api import ApiError
    # 房间真注销(TOURNAMENT_NOT_FOUND)不重试,直接 stop(2026-09-20
    # 真实平台 dry-run 验证过此路径)。
    monkeypatch.setattr(t.time, "sleep", lambda s: None)
    fake = FakeApi(
        me_resp={"tournament_id": "t_1"},
        tournament_exc=ApiError(
            404, '{"code":"TOURNAMENT_NOT_FOUND","message":"no such"}'))
    verdict, detail = _probe(fake, attempts=3)
    assert verdict == "stop" and "不可达" in detail
    assert fake.tournament_calls == 1


def test_probe_persistent_failure_unknown(monkeypatch):
    from mj.platform.api import ApiError
    monkeypatch.setattr(t.time, "sleep", lambda s: None)
    verdict, detail = _probe(
        FakeApi(me_exc=ApiError(0, "timeout")), attempts=2)
    assert verdict == "unknown" and "me()" in detail


# ---- main 的配置错误路径(不触网) ----

def test_main_rejects_missing_config(tmp_path, capsys):
    rc = t.main(["--config", str(tmp_path / "nope.json")])
    assert rc == 2
    assert "配置错误" in capsys.readouterr().err


def test_main_guards_reject_no_recorder(tmp_path):
    cfg = tmp_path / "platform.json"
    cfg.write_text('{"server": "https://x", "tournament_token": "abc"}',
                   encoding="utf-8")
    rc = t.main(["--config", str(cfg), "--no-recorder"])
    assert rc == 2
