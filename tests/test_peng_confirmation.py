"""Peng confirmation must preserve opportunity without granting stale actions."""

import json
from unittest import mock

import pytest

import mj.platform.bot_client as module
from mj.platform.api import ApiError, _TLS
from mj.platform.bot_client import BotClient
from mj.platform.recorder import Recorder
from mj.platform.throttle import StateThrottle
from test_window_recovery import FakeClock, ScriptedServer, _snapshot, _ev, _finished


HAND = ["5b", "5b", "1w", "2w", "3w", "4w", "5w", "6w",
        "1t", "2t", "3t", "7t", "8t"]


def peng_snapshot(**overrides):
    return _snapshot(HAND, **dict(
        dict(phase="response_peng", turn=1, responding=[0],
             discards=[[], ["5b"], [], []], last_discard="5b",
             window_deadline_ms=1001800), **overrides))


def play(tmp_path, snap, *, decide=None, epoch=1001.2, errors=None, repeat=False):
    clock = FakeClock(monotonic=epoch - 900, epoch=epoch)
    responses = [
        {"snapshot": _snapshot(HAND), "seq": 10},
        {"events": [_ev(11, "tile_discarded", 1, "5b", ts=1000)], "seq": 11},
        {"snapshot": snap, "seq": 12},
    ]
    expected = [0, 10, 0]
    if repeat:
        responses.append({"snapshot": snap, "seq": 12})
        expected.append(0 if errors else 12)
    responses.append(_finished())
    expected.append(12)
    api = ScriptedServer(responses, clock, expected_seqs=expected,
                         action_errors=errors)
    rec = Recorder(root=str(tmp_path))
    choose = decide or (lambda game, _: next(a for a in game.legal_actions() if a != -1))
    bot = BotClient(api, "b", choose, recorder=rec, idle_sleep=0,
                    log=lambda _: None, window_wait=0)
    requests = []
    original_state = bot._state

    def state(gid, seq, deadline, request_kind=None):
        requests.append((seq, deadline, request_kind))
        return original_state(gid, seq, deadline, request_kind)

    with mock.patch.object(module, "time", clock), mock.patch.object(bot, "_state", state):
        bot.play_game("g")
    rows = [json.loads(line) for p in tmp_path.glob("**/*.jsonl")
            for line in p.read_text().splitlines()]
    return bot, api, rows, requests


def test_open_confirmation_beats_backlog_and_does_not_log_miss(tmp_path):
    bot, api, rows, requests = play(tmp_path, peng_snapshot(), repeat=True)
    assert len(api.actions) == 1
    assert api.actions[0][1]["action"] == "peng"
    assert bot.stats["window_confirm_requests"] == bot.stats["window_confirm_open"] == 1
    assert not [r for r in rows if r["type"] in ("claim_miss", "reset")]
    assert requests[2][0] == 0 and requests[2][2] == "WINDOW_PENG"
    assert requests[2][1] == pytest.approx(102.0)
    assert requests[3][2] == "SSE_DELTA" and requests[3][1] is None
    throttle = StateThrottle(clock=lambda: 101.2)
    throttle._waiters = [dict(gid=i, deadline=None, arrived=100, serial=i)
                         for i in range(9)]
    confirm = dict(gid="peng", deadline=requests[2][1], arrived=101.2, serial=9)
    throttle._waiters.append(confirm)
    assert throttle._head(101.2) is confirm
    assert throttle._head(102.1) is not confirm


@pytest.mark.parametrize("snap,outcome,miss", [
    (peng_snapshot(phase="response_chi"), "closed", True),
    (peng_snapshot(responding=[2, 3]), "not_responding", True),
    (peng_snapshot(window_deadline_ms=1001100), "expired", True),
    (peng_snapshot(window_deadline_ms=None), "unconfirmed", False),
    (peng_snapshot(window_deadline_ms=1005000), "unconfirmed", False),
    (_snapshot(HAND, phase="draw", turn=2, round_no=2), "stale", True),
    (peng_snapshot(last_discard="9t", discards=[[], ["5b", "9t"], [], []]),
     "stale", True),
])
def test_closed_or_changed_window_never_reuses_old_candidate(tmp_path, snap, outcome, miss):
    bot, api, rows, _ = play(tmp_path, snap)
    assert not api.actions
    confirmations = [r for r in rows if r["type"] == "window_confirm"]
    assert outcome in [r["outcome"] for r in confirmations]
    misses = [r for r in rows if r["type"] == "claim_miss"]
    assert len(misses) == int(miss) == bot.stats["window_confirm_miss"]
    if miss:
        assert misses[0]["chosen"] is None
        assert misses[0]["pending"] == [1, 13]
        assert misses[0]["seq"] == 11
        assert "decision" not in misses[0]


def test_confirmed_policy_pass_is_not_a_miss(tmp_path):
    bot, api, rows, _ = play(tmp_path, peng_snapshot(), decide=lambda *_: -1)
    assert [a[1]["action"] for a in api.actions] == ["pass"]
    assert bot.stats["window_confirm_miss"] == 0
    assert not [r for r in rows if r["type"] == "claim_miss"]


def test_decision_crossing_boundary_is_recomputed_after_confirmation(tmp_path):
    calls = []

    def choose(game, seat):
        calls.append(game.phase)
        if len(calls) == 1:
            module.time.sleep(.07)
        return next(a for a in game.legal_actions() if a != -1)

    bot, api, rows, _ = play(tmp_path, peng_snapshot(), epoch=1000.93, decide=choose)
    assert len(calls) == 2 and len(api.actions) == 1
    assert not [r for r in rows if r["type"] == "claim_miss"]
    assert bot.stats["window_confirm_requests"] == 1


@pytest.mark.parametrize("error", [ApiError(409, "{}"), ApiError(0, "lost", uncertain=True)])
def test_failed_confirmed_post_is_never_retried(tmp_path, error):
    bot, api, rows, requests = play(tmp_path, peng_snapshot(), errors=[error], repeat=True)
    assert len(api.actions) == 1
    assert requests[3][2] == "RESYNC"
    assert bot.stats["response_409"] + bot.stats["post_uncertain"] == 1


def test_reanchor_discard_count_change_does_not_lose_same_window(tmp_path):
    snap = peng_snapshot(discards=[["9t"], ["5b"], [], []])
    _, api, rows, _ = play(tmp_path, snap)
    assert len(api.actions) == 1
    assert not [r for r in rows if r["type"] == "claim_miss"]


def test_state_diagnostics_are_captured_before_actions_overwrite_tls(tmp_path):
    bot = BotClient(None, "b", None)
    state_meta = {"state_physical_attempts": 2, "state_429": 1}
    bot._record_state_transport(2, state_meta)
    _TLS.request_meta = {"attempts": 1, "retry_429": 0}
    assert bot.stats["state_attempts"] == 2
    assert bot.stats["state_retry_429"] == 1
