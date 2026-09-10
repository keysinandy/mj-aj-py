"""窗口时序、409 恢复和多场隔离的独立回归测试。

这些测试使用单调时钟和会检查请求游标的假服务器。它们保留服务端
事件的真实先后关系，检查动作发送时刻和恢复请求，而不是只统计 409。
生产代码可以改变内部状态机，只要仍满足这里约束的协议语义即可。
"""

import copy
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

import mj.platform.bot_client as bot_client_module
from mj.game import CHOW_LOW
from mj.platform.api import ApiError
from mj.platform.bot_client import BotClient


class FakeClock:
    """可推进的 monotonic/epoch 时钟；sleep 不阻塞测试线程。"""

    def __init__(self, monotonic=100.0, epoch=1000.0):
        self._mono = float(monotonic)
        self._offset = float(epoch) - self._mono
        self._lock = threading.Lock()

    def monotonic(self):
        with self._lock:
            return self._mono

    def time(self):
        with self._lock:
            return self._mono + self._offset

    def sleep(self, seconds):
        with self._lock:
            self._mono += max(float(seconds), 0.0)

    def set_monotonic(self, value):
        with self._lock:
            self._mono = float(value)


HAND = [
    "1w", "2w", "3w", "4w", "5w", "6w", "7b", "8b",
    "1t", "2t", "3t", "4t", "9w",
]


def _ev(seq, event_type, seat, tile="", *, clock=None, data=None, ts=None):
    event = {"seq": seq, "type": event_type, "seat": seat,
             "tile": tile, "data": data}
    if ts is not None:
        event["ts"] = ts
    elif clock is not None:
        event["ts"] = clock.time()
    return event


def _response_timeout(window):
    return {"kind": "response", "window": window}


def _snapshot(hand=HAND, *, phase="draw", turn=1, round_no=1,
              drawn=None, discards=None, melds=None, last_discard=None,
              responding=None, window_deadline_ms=None):
    snapshot = {
        "game_id": "g1", "seat": 0, "phase": phase, "turn": turn,
        "responding_seats": list(responding or []), "round_no": round_no,
        "dealer": 0, "wall_remaining": 60, "drawn_tile": drawn,
        "my_hand": list(hand), "hand_counts": [len(hand), 13, 13, 13],
        "discards": copy.deepcopy(discards or [[], [], [], []]),
        "melds": copy.deepcopy(melds or [[], [], [], []]),
        "last_discard": last_discard, "scores": None,
    }
    if window_deadline_ms is not None:
        snapshot["window_deadline_ms"] = window_deadline_ms
    return snapshot


class ScriptedServer:
    """按请求顺序返回状态，并记录每次状态/动作的发生时刻。"""

    def __init__(self, responses, clock, *, expected_seqs=None,
                 action_errors=None, before_response=None):
        self.responses = list(responses)
        self.clock = clock
        self.expected_seqs = list(expected_seqs or [])
        self.action_errors = list(action_errors or [])
        self.before_response = dict(before_response or {})
        self.state_calls = []
        self.actions = []
        self._i = 0
        self._lock = threading.RLock()

    def game_state(self, gid, seq):
        with self._lock:
            self.state_calls.append((gid, seq, self.clock.monotonic()))
            if self.expected_seqs:
                expected = self.expected_seqs.pop(0)
                if seq != expected:
                    raise AssertionError(
                        f"{gid}: expected state seq={expected}, got {seq}")
            if self._i >= len(self.responses):
                raise AssertionError(f"{gid}: state response script exhausted")
            advance = self.before_response.get(self._i, 0.0)
            if advance:
                self.clock.sleep(advance)
            response = copy.deepcopy(self.responses[self._i])
            self._i += 1
            return response

    def game_action(self, gid, payload):
        with self._lock:
            self.actions.append((gid, copy.deepcopy(payload),
                                 self.clock.monotonic()))
            if self.action_errors:
                error = self.action_errors.pop(0)
                raise error
            return {}


def _finished():
    return {"finished": True, "snapshot": {"scores": [0, 0, 0, 0]}}


def _choose_chi_or_draw(game, seat):
    if game.phase == "discard":
        return game.drawn[seat]
    for action in (CHOW_LOW, CHOW_LOW - 1, CHOW_LOW - 2):
        if action in game.legal_actions():
            return action
    return -1


class WindowRecoveryTests(unittest.TestCase):
    def test_peng_timeout_same_batch_preserves_chi_window(self):
        """上家打 6b 后同批碰超时，7b8b 仍应进入吃窗并提交一次。"""
        clock = FakeClock()
        initial = _snapshot(turn=3)
        discard_batch = [
            _ev(1, "tile_drawn", 3, clock=clock),
            _ev(2, "tile_discarded", 3, "6b", clock=clock),
            _ev(3, "timeout", 0, data=_response_timeout("peng")),
            _ev(4, "timeout", 1, data=_response_timeout("peng")),
            _ev(5, "timeout", 2, data=_response_timeout("peng")),
        ]
        api = ScriptedServer(
            [{"snapshot": initial, "seq": 0},
             {"events": discard_batch, "seq": 5},
             {"events": [], "seq": 5},
             {"snapshot": _snapshot(
                 phase="response_chi", turn=3, responding=[0],
                 discards=[[], [], [], ["6b"]], last_discard="6b",
                 window_deadline_ms=1002000), "seq": 5},
             _finished()], clock)
        bot = BotClient(api, "bot0", _choose_chi_or_draw,
                        log=lambda _: None, window_wait=0, idle_sleep=0)

        with mock.patch.object(bot_client_module, "time", clock):
            bot.play_game("g1")

        self.assertEqual(
            [payload for _, payload, _ in api.actions],
            [{"action": "chi", "tile": "6b", "tiles": ["7b", "8b"]}],
        )
        self.assertEqual(bot.stats["err409"], 0)
        self.assertEqual(bot.stats["decide_errors"], 0)
        self.assertLess(api.actions[0][2], 102.0)

    def test_chi_sleep_crossing_deadline_does_not_post(self):
        """观察 T+1.1 后若保守等待会到 T+2.15，必须在发送前放弃。"""
        clock = FakeClock(monotonic=101.1, epoch=1001.1)
        api = ScriptedServer([], clock)
        bot = BotClient(api, "bot0", lambda g, s: CHOW_LOW,
                        log=lambda _: None, window_wait=1.0)
        mirror = bot._mirror_from_snapshot(
            _snapshot(phase="response_chi", turn=3,
                      discards=[[], [], [], ["6b"]],
                      last_discard="6b", responding=[0]))
        chi = {"t0": 100.0, "obs": 101.1, "anchored": True,
               "needed": {0, 1, 2}, "seen": {0}}

        with mock.patch.object(bot_client_module, "time", clock):
            bot._act_chi(mirror, chi, "g1")

        self.assertEqual(api.actions, [])
        self.assertEqual(bot.stats["err409"], 0)
        self.assertEqual(bot.stats["auto_played"], 0)
        self.assertEqual(bot.stats["client_deadline_abandons"], 1)

    def test_slow_draw_decision_does_not_post_after_deadline(self):
        """模型决策本身耗尽弃牌窗后，不能再把陈旧弃牌发给服务端。"""
        clock = FakeClock()
        api = ScriptedServer([], clock)

        def slow_decide(game, seat):
            clock.sleep(3.0)
            return game.drawn[seat]

        bot = BotClient(api, "bot0", slow_decide,
                        log=lambda _: None, window_wait=0)
        mirror = bot._mirror_from_snapshot(_snapshot(turn=1))
        drawn = _ev(1, "tile_drawn", 0, "9b", clock=clock)
        mirror.apply_event(drawn)

        with mock.patch.object(bot_client_module, "time", clock):
            bot._act_draw(mirror, "g1", drawn)

        self.assertEqual(api.actions, [])
        self.assertEqual(bot.stats["err409"], 0)
        self.assertEqual(bot.stats["auto_played"], 0)
        self.assertEqual(bot.stats["client_deadline_abandons"], 1)


class ActionRecoveryTests(unittest.TestCase):
    def test_409_forces_seq_zero_resync_without_retry_or_decide_error(self):
        """动作 409 后下一次 state 必须 seq=0，且不能重复发送旧动作。"""
        clock = FakeClock()
        initial = _snapshot(turn=1)
        after_recovery = _snapshot(turn=1)
        event = _ev(1, "tile_drawn", 0, "9b", clock=clock)
        api = ScriptedServer(
            [{"snapshot": initial, "seq": 0},
             {"events": [event], "seq": 1},
             {"snapshot": after_recovery, "seq": 1},
             _finished()], clock,
            expected_seqs=[0, 0, 0, 1],
            action_errors=[ApiError(
                409, '{"code":"STALE_ACTION","message":"expired"}')])
        bot = BotClient(api, "bot0", _choose_chi_or_draw,
                        log=lambda _: None, window_wait=0, idle_sleep=0)

        with mock.patch.object(bot_client_module, "time", clock):
            bot.play_game("g1")

        self.assertEqual([seq for _, seq, _ in api.state_calls], [0, 0, 0, 1])
        self.assertEqual(len(api.actions), 1)
        self.assertEqual(api.actions[0][1], {"action": "discard", "tile": "9b"})
        self.assertEqual(bot.stats["err409"], 1)
        self.assertEqual(bot.stats["decide_errors"], 0)

    def test_successful_action_echo_is_not_repeated(self):
        """正常提交后的自家弃牌回声只推进镜像，不再次提交。"""
        clock = FakeClock()
        initial = _snapshot(turn=1)
        drawn = _ev(1, "tile_drawn", 0, "9b", clock=clock)
        echo = _ev(2, "tile_discarded", 0, "9b", clock=clock)
        api = ScriptedServer(
            [{"snapshot": initial, "seq": 0},
             {"events": [drawn], "seq": 1},
             {"events": [echo], "seq": 2},
             _finished()], clock)
        bot = BotClient(api, "bot0", _choose_chi_or_draw,
                        log=lambda _: None, window_wait=0, idle_sleep=0)

        with mock.patch.object(bot_client_module, "time", clock):
            bot.play_game("g1")

        self.assertEqual(len(api.actions), 1)
        self.assertEqual(api.actions[0][1], {"action": "discard", "tile": "9b"})
        self.assertEqual(bot.stats["err409"], 0)
        self.assertEqual(bot.stats["decide_errors"], 0)


class SnapshotWindowRecoveryTests(unittest.TestCase):
    def test_peng_to_chi_snapshot_keeps_wait_and_posts_once_after_t_plus_one(self):
        """碰窗后 gap 快照显示吃窗时，保留原等待态并越过碰窗再提交一次。"""
        clock = FakeClock()
        initial = _snapshot(turn=3)
        discard = _ev(1, "tile_discarded", 3, "6b", clock=clock)
        response_chi = _snapshot(
            phase="response_chi", turn=3,
            discards=[[], [], [], ["6b"]], last_discard="6b",
            responding=[0], window_deadline_ms=1002000)
        api = ScriptedServer(
            [{"snapshot": initial, "seq": 0},
             {"events": [discard], "seq": 1},
             {"gap": True, "snapshot": response_chi, "seq": 1},
             {"events": [], "seq": 1},
             {"events": [], "seq": 1},
             _finished()], clock, before_response={2: 1.05})
        bot = BotClient(api, "bot0", _choose_chi_or_draw,
                        log=lambda _: None, window_wait=1.0, idle_sleep=0)

        with mock.patch.object(bot_client_module, "time", clock):
            bot.play_game("g1")

        self.assertEqual(len(api.actions), 1)
        self.assertEqual(api.actions[0][1],
                         {"action": "chi", "tile": "6b",
                          "tiles": ["7b", "8b"]})
        self.assertGreaterEqual(api.actions[0][2], 101.0)
        self.assertLess(api.actions[0][2], 102.0)

    def test_gap_new_round_clears_old_chi_pending(self):
        """跨局 gap 快照不应让旧局吃窗继续调用吃决策。"""
        clock = FakeClock()
        initial = _snapshot(turn=3)
        discard = _ev(1, "tile_discarded", 3, "6b", clock=clock)
        new_round = _snapshot(turn=1, round_no=2)
        api = ScriptedServer(
            [{"snapshot": initial, "seq": 0},
             {"events": [discard], "seq": 1},
             {"gap": True, "snapshot": new_round, "seq": 1},
             {"events": [], "seq": 1},
             {"events": [], "seq": 1},
             _finished()], clock)
        bot = BotClient(api, "bot0", _choose_chi_or_draw,
                        log=lambda _: None, window_wait=1.0, idle_sleep=0)
        chi_calls = []
        original_act_chi = bot._act_chi

        def record_old_chi(*args, **kwargs):
            chi_calls.append(1)
            return original_act_chi(*args, **kwargs)

        bot._act_chi = record_old_chi
        with mock.patch.object(bot_client_module, "time", clock):
            bot.play_game("g1")

        self.assertEqual(api.actions, [])
        self.assertEqual(chi_calls, [])
        self.assertEqual(bot.stats["decide_errors"], 0)


class ClaimCancellationTests(unittest.TestCase):
    def test_other_player_peng_cancels_pending_chi(self):
        """等待吃牌期间观察到他家碰走同一张牌，不能再发吃。"""
        clock = FakeClock()
        initial = _snapshot(turn=3)
        discard = _ev(1, "tile_discarded", 3, "6b", clock=clock)
        other_peng = _ev(2, "peng", 2, "6b", clock=clock)
        api = ScriptedServer(
            [{"snapshot": initial, "seq": 0},
             {"events": [discard], "seq": 1},
             {"events": [other_peng], "seq": 2},
             _finished()], clock)
        bot = BotClient(api, "bot0", _choose_chi_or_draw,
                        log=lambda _: None, window_wait=1.0, idle_sleep=0)

        with mock.patch.object(bot_client_module, "time", clock):
            bot.play_game("g1")

        self.assertEqual(api.actions, [])
        self.assertEqual(bot.stats["err409"], 0)

    def test_chi_timeout_in_same_batch_cancels_chi(self):
        """同批已收到我方吃窗超时，不应在批处理结束后重新创建吃窗。"""
        clock = FakeClock()
        initial = _snapshot(turn=3)
        batch = [
            _ev(1, "tile_discarded", 3, "6b", clock=clock),
            _ev(2, "timeout", 0, data=_response_timeout("peng")),
            _ev(3, "timeout", 1, data=_response_timeout("peng")),
            _ev(4, "timeout", 2, data=_response_timeout("peng")),
            _ev(5, "timeout", 0, data=_response_timeout("chi")),
        ]
        api = ScriptedServer(
            [{"snapshot": initial, "seq": 0},
             {"events": batch, "seq": 5},
             {"events": [], "seq": 5}, _finished()], clock)
        bot = BotClient(api, "bot0", _choose_chi_or_draw,
                        log=lambda _: None, window_wait=0, idle_sleep=0)

        with mock.patch.object(bot_client_module, "time", clock):
            bot.play_game("g1")

        self.assertEqual(api.actions, [])
        self.assertEqual(bot.stats["err409"], 0)


class ConcurrentGameIsolationTests(unittest.TestCase):
    def test_ten_games_have_independent_sequences_and_actions(self):
        """十个并发场次的游标、摸牌和动作不能互相串线。"""
        clock = FakeClock()
        tiles = ["9b", "5t", "6t", "7t", "8t",
                 "9t", "1b", "2b", "3b", "4b"]
        gids = [f"g{i}" for i in range(10)]

        class TenGameServer:
            def __init__(self):
                self.stage = {gid: 0 for gid in gids}
                self.calls = []
                self.actions = []
                self.lock = threading.RLock()

            def game_state(self, gid, seq):
                with self.lock:
                    self.calls.append((gid, seq))
                    stage = self.stage[gid]
                    self.stage[gid] += 1
                    if stage == 0:
                        return {"snapshot": _snapshot(turn=1), "seq": 0}
                    if stage == 1:
                        tile = tiles[gids.index(gid)]
                        return {"events": [_ev(1, "tile_drawn", 0, tile,
                                                clock=clock)], "seq": 1}
                    if stage == 2:
                        return _finished()
                    raise AssertionError(f"extra state call for {gid}")

            def game_action(self, gid, payload):
                with self.lock:
                    self.actions.append((gid, copy.deepcopy(payload)))
                    return {}

        api = TenGameServer()
        bot = BotClient(api, "bot0", _choose_chi_or_draw,
                        log=lambda _: None, window_wait=0, idle_sleep=0)

        with mock.patch.object(bot_client_module, "time", clock):
            with ThreadPoolExecutor(max_workers=10) as pool:
                list(pool.map(bot.play_game, gids))

        self.assertEqual(bot.stats["games"], 10)
        self.assertEqual(bot.stats["err409"], 0)
        self.assertEqual(bot.stats["decide_errors"], 0)
        got = {gid: payload for gid, payload in api.actions}
        self.assertEqual(set(got), set(gids))
        self.assertEqual(
            {gid: payload["tile"] for gid, payload in got.items()},
            dict(zip(gids, tiles)),
        )
        for gid in gids:
            self.assertEqual(
                [seq for call_gid, seq in api.calls if call_gid == gid],
                [0, 0, 1],
            )


if __name__ == "__main__":
    unittest.main()
