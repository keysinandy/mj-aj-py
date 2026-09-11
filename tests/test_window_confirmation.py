"""Quantized timestamps are hints; snapshots authorize chi submissions."""

import io
import urllib.error
from unittest import mock

from mj.platform.api import Api, ApiError
from mj.platform.throttle import ThrottleTicket
from mj.platform.bot_client import BotClient
import mj.platform.bot_client as module
from test_window_recovery import (
    FakeClock, ScriptedServer, _snapshot, _finished, _choose_chi_or_draw,
    _ev,
)


def test_peng_snapshot_waits_for_authoritative_chi_and_does_not_replay():
    clock = FakeClock()
    peng = _snapshot(phase="response_peng", turn=3, responding=[0],
                     discards=[[], [], [], ["6b"]], last_discard="6b",
                     window_deadline_ms=1001000)
    chi = dict(peng, phase="response_chi", window_deadline_ms=1002000)
    api = ScriptedServer([
        {"snapshot": peng, "seq": 5},
        {"snapshot": chi, "seq": 8},
        {"snapshot": chi, "seq": 8}, _finished(),
    ], clock, expected_seqs=[0, 0, 8, 8])
    bot = BotClient(api, "b", _choose_chi_or_draw, log=lambda _: None,
                    idle_sleep=0)
    with mock.patch.object(module, "time", clock):
        bot.play_game("g")
    assert len(api.actions) == 1
    assert 101 <= api.actions[0][2] < 102


def test_timeout_uses_game_seat_and_local_abandon_is_not_server_timeout():
    bot = BotClient(mock.Mock(), "b", lambda *_: -1, log=lambda _: None)
    bot._current_me = 3  # Another worker must not affect this event.
    bot._deadline_abandon("g", "response_peng", "expired")
    bot._record_timeout({"type": "timeout", "kind": "response",
                         "seat": 0, "data": {"window": "peng"}}, me=0)
    assert bot.stats["my_timeout_peng"] == 1
    assert bot.stats["other_timeout_response"] == 0
    assert bot.stats["client_deadline_abandons"] == 1
    assert bot.stats["auto_played"] == 0


def test_every_physical_state_attempt_acquires_a_permit():
    api = Api("https://example.invalid", "token")
    throttle = mock.Mock()
    throttle.acquire.return_value = ThrottleTicket(2.0, False, False, None)
    api.state_throttle = throttle
    response = mock.MagicMock()
    response.__enter__.return_value.read.return_value = b'{}'
    errors = [urllib.error.HTTPError("https://example.invalid", 429, "busy",
                                     {}, io.BytesIO(b'{}')),
              urllib.error.URLError("connection lost"), response]
    with mock.patch("urllib.request.urlopen", side_effect=errors) as send, \
            mock.patch("mj.platform.api.time.sleep"):
        assert api.game_state("g", 3) == {}
    assert send.call_count == throttle.acquire.call_count == 3


def test_window_identity_survives_snapshot_but_not_new_meld():
    bot = BotClient(mock.Mock(), "b", lambda *_: -1, log=lambda _: None)
    snap = _snapshot(phase="response_chi", turn=3, responding=[0],
                     discards=[[], [], [], ["6b"]], last_discard="6b")
    mirror = bot._mirror_from_snapshot(snap)
    mirror._source_discard_seq = 10
    first = bot._window_key(mirror, "response_chi", ev={"seq": 10})
    assert first == bot._window_key(mirror, "response_chi", snap=snap)
    mirror.melds[1].append(object())
    # A re-anchor may rebuild meld/discard counts differently (a claimed
    # discard is popped from the river), but the source discard sequence is
    # the stable identity for this still-pending window.
    assert first == bot._window_key(mirror, "response_chi")
    mirror._source_discard_seq = 11
    assert first != bot._window_key(mirror, "response_chi")


def test_chi_409_recovers_in_same_loop_without_reposting():
    clock = FakeClock()
    chi = _snapshot(phase="response_chi", turn=3, responding=[0],
                    discards=[[], [], [], ["6b"]], last_discard="6b",
                    window_deadline_ms=1001000)
    api = ScriptedServer([
        {"snapshot": chi, "seq": 8}, {"snapshot": chi, "seq": 8},
        _finished(),
    ], clock, expected_seqs=[0, 0, 8],
        action_errors=[ApiError(409, '{}')])
    bot = BotClient(api, "b", _choose_chi_or_draw, log=lambda _: None)
    with mock.patch.object(module, "time", clock):
        bot.play_game("g")
    assert len(api.actions) == 1
    assert bot.stats["err409"] == 1
    assert bot.stats["decide_errors"] == 0
    assert bot.stats["games"] == 1


def test_quantized_peng_timestamp_confirms_instead_of_abandoning():
    clock = FakeClock(monotonic=101.2, epoch=1001.2)
    hand = ["5b", "5b", "1w", "2w", "3w", "4w", "5w", "6w",
            "1t", "2t", "3t", "7t", "8t"]
    peng = _snapshot(hand, phase="response_peng", turn=1, responding=[0],
                     discards=[[], ["5b"], [], []], last_discard="5b",
                     window_deadline_ms=1001800)
    api = ScriptedServer([
        {"snapshot": _snapshot(hand), "seq": 0},
        {"events": [_ev(1, "tile_discarded", 1, "5b", ts=1000)], "seq": 1},
        {"snapshot": peng, "seq": 1}, _finished(),
    ], clock, expected_seqs=[0, 0, 0, 1])
    bot = BotClient(api, "b", lambda g, s: next(a for a in g.legal_actions()
                                                if a != -1),
                    log=lambda _: None)
    with mock.patch.object(module, "time", clock):
        bot.play_game("g")
    assert len(api.actions) == 1
    assert api.actions[0][1]["action"] == "peng"
    assert bot.stats["auto_played"] == 0
    assert bot.stats["client_deadline_abandons"] == 0


def test_catch_play_peng_waits_for_authoritative_response_turn():
    """抓打圈弃牌先确认冻结/response turn，不提前 POST 碰。"""
    clock = FakeClock()
    hand = ["1w", "1w", "2w", "3w", "4w", "5w", "6w", "7b",
            "8b", "1t", "2t", "3t", "9w"]
    initial = _snapshot(hand, phase="draw", turn=1)
    discard = _ev(1, "tile_discarded", 1, "1w", clock=clock,
                  data={"catch_play": True})
    authoritative = _snapshot(
        hand, phase="response_peng", turn=1, responding=[2],
        discards=[[], ["1w"], [], []], last_discard="1w",
        window_deadline_ms=1001000)
    api = ScriptedServer([
        {"snapshot": initial, "seq": 0},
        {"events": [discard], "seq": 1},
        {"snapshot": authoritative, "seq": 1},
        _finished(),
    ], clock, expected_seqs=[0, 0, 0, 1])
    bot = BotClient(api, "b", lambda g, s: next(
        a for a in g.legal_actions() if a != -1), log=lambda _: None,
                    window_wait=0, idle_sleep=0)
    with mock.patch.object(module, "time", clock):
        bot.play_game("g")

    assert api.actions == []
    assert bot.stats["err409"] == 0
