"""hu_failed 异常流回归测试(match 实测 2026-09-08,5/1405 次认领)。

脚本化假服务器回放:我方碰牌被接受后,平台发 timeout kind=hu_failed
打在认领者身上且不开弃牌窗,turn 直达下家——手牌自此比引擎预期多
1 张。断言客户端:
1. 不提交认领后的废弃牌(必 409);
2. seq=0 快照重锚(stats.hu_failed);
3. 张数漂移期间的反应窗/弃牌回合交服务端代打(stats.auto_played),
   不炸线程、不烧决策自愈预算;
4. 决策路径异常(shanten 断言 ValueError)有限次快照自愈;
5. 自愈超限上抛时 _play_game_safe 落 end(error) 终态记录。
"""

import unittest

from mj.platform.bot_client import BotClient


def _ev(seq, t, seat, tile="", data=None):
    return {"seq": seq, "type": t, "seat": seat, "tile": tile,
            "data": data}


def _resp(win):
    return {"kind": "response", "window": win}


# 手牌:1w2w3w 5w6w7w 5b5b 1t2t3t 7t8t(两张 5b 供碰)
HAND0 = ["1w", "2w", "3w", "5w", "6w", "7w",
         "5b", "5b", "1t", "2t", "3t", "7t", "8t"]
# 碰 5b 后(服务端未收弃牌,张数 =11 = 引擎预期 +1)
HAND1 = ["1w", "2w", "3w", "5w", "6w", "7w",
         "1t", "2t", "3t", "7t", "8t"]


def _snap(hand, turn, melds=None, drawn=None, phase="draw"):
    return {"game_id": "g1", "seat": 0, "phase": phase, "turn": turn,
            "responding_seats": [], "round_no": 1, "dealer": 0,
            "wall_remaining": 60, "drawn_tile": drawn,
            "my_hand": hand, "hand_counts": [len(hand), 13, 13, 13],
            "discards": [["4t"], [], [], []],
            "melds": melds or [[], [], [], []],
            "last_discard": None, "scores": None}


class ScriptedApi:
    """按预切批次回放的假服务器(单座位视角,seat 0 = 我方)。

    batches: 每次 /state 轮询释放一个事件批;snaps: seq=0 时按已服务
    水位选择快照(索引 = 已服务的批数 - 1,首查为初始快照)。
    """

    def __init__(self, batches, snaps, final_scores=None):
        self.batches = list(batches)
        self.snaps = list(snaps)
        self.i = 0                 # 已服务批数
        self.final_scores = final_scores or [1, 0, 0, -1]
        self.submitted = []
        self._initial_served = False

    # ---- /api/games ----
    def game_state(self, gid, seq):
        if not self._initial_served:
            self._initial_served = True
            return {"snapshot": self.snaps[0], "seq": 0}
        last = self.batches[self.i - 1][-1]["seq"] if self.i else 0
        if seq is not None and seq < last:
            # 重锚(hu_failed / 决策自愈):快照取当前水位(优先于终局)
            k = min(self.i, len(self.snaps) - 1)
            return {"snapshot": self.snaps[k], "seq": last}
        if self.i >= len(self.batches):
            return {"finished": True,
                    "snapshot": {"scores": self.final_scores}}
        batch = self.batches[self.i]
        self.i += 1
        return {"events": batch, "seq": batch[-1]["seq"]}

    def game_action(self, gid, payload):
        self.submitted.append(payload)
        return {}


def _hu_failed_batches():
    """正常开局 → 我方碰 → hu_failed(跳弃牌)→ 漂移期他家/我方摸打。"""
    return [
        # 我方摸 4t → 决策弃 4t
        [_ev(1, "tile_drawn", 0, "4t")],
        [_ev(2, "tile_discarded", 0, "4t"),
         _ev(3, "pass", 1), _ev(4, "pass", 2), _ev(5, "pass", 3)],
        # seat1 打 5b → 我方碰窗决策(PONG)
        [_ev(6, "tile_drawn", 1),
         _ev(7, "tile_discarded", 1, "5b"),
         _ev(8, "timeout", 2, data=_resp("peng")),
         _ev(9, "timeout", 3, data=_resp("peng"))],
        # 碰被接受 + hu_failed:无弃牌窗,turn 直达下家
        [_ev(10, "peng", 0, "5b"),
         _ev(11, "timeout", 0, data={"kind": "hu_failed"})],
        # seat2 摸打(无我方事件)
        [_ev(12, "tile_drawn", 2),
         _ev(13, "tile_discarded", 2, "9t"),
         _ev(14, "timeout", 3, data=_resp("peng")),
         _ev(15, "timeout", 0, data=_resp("peng")),
         _ev(16, "timeout", 3, data=_resp("chi"))],
        # seat3 摸打 → 我方反应窗(张数漂移,交服务端代打)
        [_ev(17, "tile_drawn", 3),
         _ev(18, "tile_discarded", 3, "2b"),
         _ev(19, "timeout", 0, data=_resp("peng")),
         _ev(20, "timeout", 1, data=_resp("peng")),
         _ev(21, "timeout", 0, data=_resp("chi"))],
        # 我方摸 6w(手牌 12 = 预期 11 + 漂移 1)→ 代打弃 6w
        [_ev(22, "tile_drawn", 0, "6w")],
        [_ev(23, "tile_discarded", 0, "6w"),
         _ev(24, "timeout", 0, data={"kind": "discard"})],
    ]


def _hu_failed_snaps():
    meld0 = [{"kind": "peng", "tiles": ["5b", "5b", "5b"]}]
    return [
        _snap(HAND0, turn=1),                       # 初始(未到我方回合)
        _snap(HAND0, turn=0, drawn="4t"),           # 决策自愈重拉用
        _snap(HAND1, turn=2, melds=[meld0, [], [], []]),  # hu_failed 重锚
    ]


def _decide(g, seat):
    if g.phase == "discard":
        assert g.drawn[seat] is not None
        return g.drawn[seat]  # 弃摸到的牌
    legal = g.legal_actions()
    return -5 if -5 in legal else -1  # 有碰必碰


class TestHuFailed(unittest.TestCase):
    def test_hu_failed_skip_discard_and_survive(self):
        api = ScriptedApi(_hu_failed_batches(), _hu_failed_snaps())
        bot = BotClient(api, "bot0", _decide, log=lambda m: None,
                        window_wait=0, idle_sleep=0)
        bot.play_game("g1")
        # 不提交认领后的废弃牌:全程只有 弃4t + 碰5b 两个动作
        self.assertEqual(
            api.submitted,
            [{"action": "discard", "tile": "4t"},
             {"action": "peng", "tile": "5b"}])
        self.assertEqual(bot.stats["games"], 1)      # 正常 finished
        self.assertEqual(bot.stats["hu_failed"], 1)  # 快照重锚一次
        # 漂移期:反应窗 + 弃牌回合各代打一次,不烧决策预算
        self.assertGreaterEqual(bot.stats["auto_played"], 2)
        self.assertEqual(bot.stats["decide_errors"], 0)
        self.assertEqual(bot.stats["mirror_resets"], 0)

    def test_decide_crash_self_heal(self):
        """决策抛 ValueError(实测:暗牌张数与副露不符)→ 快照重锚自愈。"""
        api = ScriptedApi([[_ev(1, "tile_drawn", 0, "4t")]],
                          [_snap(HAND0, turn=1),
                           _snap(HAND0, turn=0, drawn="4t")])
        calls = []

        def decide(g, s):
            calls.append(1)
            if len(calls) == 1:
                raise ValueError("暗牌张数 12 与副露不符")
            return _decide(g, s)

        bot = BotClient(api, "bot0", decide, log=lambda m: None,
                        window_wait=0, idle_sleep=0)
        bot.play_game("g1")
        self.assertGreaterEqual(len(calls), 2)  # 重锚后重试
        self.assertEqual(bot.stats["decide_errors"], 1)
        self.assertEqual(bot.stats["games"], 1)
        self.assertEqual(api.submitted,
                         [{"action": "discard", "tile": "4t"}])

    def test_decide_crash_budget_ends_with_error_record(self):
        """自愈超限:上抛终止并落 end(error),不再静默丢局。"""

        class RecStub:
            def __init__(self):
                self.records = []

            def meta(self, *a, **k):
                pass

            def req(self, *a, **k):
                pass

            def snapshot(self, *a, **k):
                pass

            def events(self, *a, **k):
                pass

            def decision(self, *a, **k):
                pass

            def action(self, *a, **k):
                pass

            def reset(self, gid, reason):
                self.records.append(("reset", reason))

            def end(self, gid, reason, scores=None, error=None):
                self.records.append(("end", reason, error))

        api = ScriptedApi([[_ev(1, "tile_drawn", 0, "4t")]],
                          [_snap(HAND0, turn=1),
                           _snap(HAND0, turn=0, drawn="4t")])

        def boom(g, s):
            raise ValueError("暗牌张数 12 与副露不符")

        rec = RecStub()
        bot = BotClient(api, "bot0", boom, log=lambda m: None,
                        window_wait=0, idle_sleep=0, recorder=rec)
        bot._play_game_safe("g1")  # 不应向外抛
        ends = [r for r in rec.records if r[0] == "end"]
        self.assertEqual(len(ends), 1)
        self.assertEqual(ends[0][1], "error")
        self.assertIn("暗牌张数", ends[0][2])
        # 3 次重锚后第 4 次上抛
        resets = [r for r in rec.records if r[0] == "reset"]
        self.assertEqual(len(resets), 3)
        self.assertEqual(bot.stats["decide_errors"], 3)


if __name__ == "__main__":
    unittest.main()
