"""平台映射层测试:牌名往返 + 引擎动作码 ↔ 平台 JSON 往返(离线)。"""

import unittest

from mj.game import (
    PASS, HU, PONG, KONG_OPEN, KONG_CLOSED_BASE, KONG_ADD_BASE, CHOW_LOW,
)
from mj.platform.proto import API_NAME, tname, tidx, ProtocolError, parse_event
from mj.platform.actions import action_to_payload, payload_to_engine_action


class TestTileMapping(unittest.TestCase):
    def test_roundtrip_all_34(self):
        for t in range(34):
            self.assertEqual(tidx(tname(t)), t)

    def test_known_names(self):
        self.assertEqual(tname(0), "1w")
        self.assertEqual(tname(8), "9w")
        self.assertEqual(tname(9), "1b")
        self.assertEqual(tname(18), "1t")
        self.assertEqual(tname(26), "9t")
        self.assertEqual(tname(27), "东")
        self.assertEqual(tname(33), "白")  # 财神

    def test_rejects(self):
        for bad in ("0w", "白板", "m", "10w", "", "w1"):
            with self.assertRaises((ProtocolError, KeyError)):
                tidx(bad)
        self.assertEqual(len(API_NAME), 34)


class TestActionMapping(unittest.TestCase):
    def test_discard(self):
        for t in (0, 13, 33):
            p = action_to_payload(t, None)
            self.assertEqual(p, {"action": "discard", "tile": tname(t)})
            self.assertEqual(payload_to_engine_action(p, None, None), t)

    def test_specials(self):
        self.assertEqual(action_to_payload(HU, None),
                         {"action": "hu", "tile": ""})
        self.assertEqual(action_to_payload(PASS, 5),
                         {"action": "pass", "tile": ""})
        self.assertEqual(payload_to_engine_action(
            {"action": "hu", "tile": ""}, None, None), HU)
        self.assertEqual(payload_to_engine_action(
            {"action": "pass", "tile": ""}, None, None), PASS)
        self.assertEqual(action_to_payload(PONG, 7),
                         {"action": "peng", "tile": tname(7)})
        self.assertEqual(payload_to_engine_action(
            {"action": "peng", "tile": tname(7)}, None, 7), PONG)

    def test_chow_three_positions(self):
        pending = 10  # 2b
        for action, expected_low in (
            (CHOW_LOW, 10), (CHOW_LOW - 1, 9), (CHOW_LOW - 2, 8),
        ):
            p = action_to_payload(action, pending)
            self.assertEqual(p["action"], "chi")
            self.assertEqual(p["tile"], tname(pending))
            tiles = sorted(tidx(n) for n in p["tiles"])
            self.assertEqual(
                tiles,
                sorted(x for x in range(expected_low, expected_low + 3)
                       if x != pending))
            back = payload_to_engine_action(p, None, pending)
            self.assertEqual(back, action)

    def test_gang_kinds(self):
        # 明杠:react 阶段,pending 即杠牌
        p = action_to_payload(KONG_OPEN, 25)
        self.assertEqual(p, {"action": "gang", "tile": tname(25)})
        self.assertEqual(
            payload_to_engine_action(p, None, 25), KONG_OPEN)
        # 暗杠:手牌恰 4 张
        t = 3
        hand = [0] * 34
        hand[t] = 4
        p = action_to_payload(KONG_CLOSED_BASE - t, None)
        self.assertEqual(p, {"action": "gang", "tile": tname(t)})
        self.assertEqual(
            payload_to_engine_action(p, hand, None), KONG_CLOSED_BASE - t)
        # 加杠:手牌 1 张 + 既有明碰
        hand[t] = 1
        p = action_to_payload(KONG_ADD_BASE - t, None)
        self.assertEqual(
            payload_to_engine_action(p, hand, None,
                                     has_open_pong=lambda x: x == t),
            KONG_ADD_BASE - t)


class TestParseEvent(unittest.TestCase):
    def test_chi_parse(self):
        raw = {"type": "chi", "seat": 2, "tile": "3w", "seq": 9,
               "data": {"tiles": ["1w", "2w", "3w"]}}
        ev = parse_event(raw)
        self.assertEqual(ev["tiles"], [0, 1])  # 去被吃牌 3w(2w)
        self.assertEqual(ev["seat"], 2)
        self.assertEqual(ev["tile"], 2)

    def test_unknown_type_rejected(self):
        with self.assertRaises(ProtocolError):
            parse_event({"type": "nonsense", "seat": 0})


if __name__ == "__main__":
    unittest.main()
