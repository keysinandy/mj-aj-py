"""每令牌 /state 主动限速的确定性回归测试。"""

import io
import json
import threading
import time
import unittest
from unittest import mock

from mj.platform.api import Api
from mj.platform.throttle import StateThrottle


class SpyThrottle:
    def __init__(self):
        self.calls = []

    def acquire(self, gid, deadline):
        self.calls.append((gid, deadline))
        return None


class TestStateThrottle(unittest.TestCase):
    def test_spacing_after_cold_start(self):
        throttle = StateThrottle(rate=100.0, burst=1)
        throttle.acquire("g1")
        t0 = time.monotonic()
        throttle.acquire("g2")
        self.assertGreaterEqual(time.monotonic() - t0, 0.007)
        st = throttle.stats()
        self.assertEqual(st["grants"], 2)
        self.assertGreater(st["waited_ms"], 0)

    def test_urgent_deadline_precedes_normal_waiter(self):
        throttle = StateThrottle(rate=20.0, burst=1)
        throttle.acquire("seed")  # consume the immediate permit
        order = []
        normal_ready = threading.Event()

        def normal():
            normal_ready.set()
            throttle.acquire("normal")
            order.append("normal")

        t = threading.Thread(target=normal)
        t.start()
        normal_ready.wait(1)
        time.sleep(0.005)
        throttle.acquire("urgent", time.monotonic() + 1)
        order.append("urgent")
        t.join(1)
        self.assertEqual(order, ["urgent", "normal"])

    def test_deadline_ticket_marks_expired(self):
        throttle = StateThrottle(rate=100.0, burst=1)
        throttle.acquire("seed")
        ticket = throttle.acquire("late", time.monotonic() - 1)
        self.assertTrue(ticket.urgent)
        self.assertTrue(ticket.deadline_missed)
        self.assertLessEqual(ticket.deadline_left_ms, 0)

    def test_429_feedback_delays_only_the_next_permit(self):
        now = [10.0]
        throttle = StateThrottle(rate=10.0, burst=1, clock=lambda: now[0])
        throttle.acquire("seed")
        before = throttle._next
        throttle.note_429()
        self.assertEqual(throttle._next, max(before, 10.0 + 0.2))
        stats = throttle.stats()
        self.assertEqual(stats["feedback_429"], 1)
        self.assertGreaterEqual(stats["feedback_cooldown_ms"], 0.0)


class TestApiStateGating(unittest.TestCase):
    def test_only_game_state_uses_throttle_and_forwards_deadline(self):
        spy = SpyThrottle()
        api = Api("https://x", "tok", state_throttle=spy)
        responses = [
            io.BytesIO(json.dumps({"me": 1}).encode()),
            io.BytesIO(json.dumps({"events": [], "seq": 3}).encode()),
            io.BytesIO(json.dumps({}).encode()),
        ]

        def urlopen(*args, **kwargs):
            return responses.pop(0)

        deadline = time.monotonic() + 1
        with mock.patch("urllib.request.urlopen", urlopen):
            self.assertEqual(api.me(), {"me": 1})
            self.assertEqual(api.game_state("g1", 2, deadline),
                             {"events": [], "seq": 3})
            api.game_action("g1", {"action": "pass", "tile": ""})
        self.assertEqual(spy.calls, [("g1", deadline)])


if __name__ == "__main__":
    unittest.main()
