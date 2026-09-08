"""bot_client 离线测试:FakeApi 按事件切片回放 synth 局,驱动完整对弈。

FakeApi 模拟 /state 长轮询:seq==cursor 时释放到下一决策点的事件切片;
seq=0/gap 返回当点快照(含实测富字段:discards/melds/wall_remaining/
last_discard)。bot 决策点 = 自家摸牌/吃碰后的弃牌 + 有非过选项的反应窗,
decide 用记录动作,逐点断言镜像构建的合法集包含该动作。
"""

import unittest

from mj.platform.bot_client import BotClient
from mj.platform.synth import synth_game, view_for

class FakeApi:
    """按 synth 局回放的假服务器(单座位视角)。"""

    def __init__(self, res, seat):
        self.res = res
        self.seat = seat
        self.final_scores = res["rounds"][0]["scores"]
        self.view = view_for(res, seat)
        self.decisions = res["decisions"]
        self.served = 0          # 已服务的视角事件 seq 水位(过滤事件不推进)
        self.finished = False
        self._initial_served = False
        self.submitted = []

    # ---- /api/tournaments 系列 ----
    def me(self):
        return {"user_id": f"u{self.seat}", "tournament_id": "room_t1",
                "active_games": [] if self.finished
                else [{"game_id": "g1"}]}

    def rules(self):
        return {"config": self.res["config"]}

    def register(self, tid):
        return {}

    def ready(self, tid):
        return {}

    def tournament(self, tid):
        return {"status": "finished" if self.finished else "running",
                "my_games": ["g1"]}

    # ---- /api/games ----
    def _snap(self):
        d = next((d for d in self.decisions
                  if d["events_before"] >= self.served), None)
        if d is None:
            return {"seat": self.seat, "phase": "finished", "turn": -1,
                    "responding_seats": [], "my_hand": [],
                    "god": {}, "round_no": 99,
                    "scores": self.final_scores}
        return d["all_prompts"][self.seat]

    def game_state(self, gid, seq):
        if self.finished:
            return {"finished": True,
                    "snapshot": {"scores": self.final_scores}}
        if seq == 0 and not self._initial_served:
            self._initial_served = True
            return {"snapshot": self._snap(), "seq": 0}
        if seq is not None and seq < self.served:
            return {"snapshot": self._snap(), "seq": self.served}
        assert seq == self.served, (seq, self.served)
        # 释放到下一决策边界;若视角内无事件(全被过滤)则继续推进边界,
        # 模拟长轮询等到下一个可见事件
        while True:
            nxt = next((d for d in self.decisions
                        if d["events_before"] > self.served), None)
            end = nxt["events_before"] if nxt else len(self.res["events"])
            slice_ = [e for e in self.view["events"]
                      if self.served < e["seq"] <= end]
            if slice_ or nxt is None:
                self.served = slice_[-1]["seq"] if slice_ else self.served
                if nxt is None:
                    self.finished = True
                return {"events": slice_, "seq": self.served}
            self.served = end

    def game_action(self, gid, payload):
        self.submitted.append(payload)
        return {}


def _drive(seat, seed=0, ycbk=False):
    res = synth_game(seed, you_cai_bi_kao=ycbk)
    fake = FakeApi(res, seat)
    # bot 会实际决策的点:弃牌决策 + 有非过选项的反应窗(过窗无选项时
    # bot 直接跳过,不调 decide)
    relevant = [d for d in res["decisions"]
                if d["seat"] == seat
                and (d["mode"] == "draw" or d["legal"] != [-1])]
    it = iter(relevant)
    calls = []

    def decide(g, s):
        d = next(it)
        act = d["action"]
        assert act in g.legal_actions(), \
            f"seat={seat} mode={d['mode']} act={act} legal={g.legal_actions()}"
        calls.append(act)
        return act

    bot = BotClient(fake, f"bot{seat}", decide, log=lambda m: None,
                    window_wait=0, idle_sleep=0)
    stats = bot.run(max_games=1)
    return fake, stats, relevant, calls


class TestBotClient(unittest.TestCase):
    def test_full_game_all_seats(self):
        for seat in range(4):
            fake, stats, relevant, calls = _drive(seat, seed=0)
            self.assertEqual(stats["games"], 1)
            self.assertEqual(calls, [d["action"] for d in relevant],
                             f"seat={seat} 决策序列应与记录一致")
            self.assertEqual(stats["err409"], 0)
            self.assertEqual(stats["mirror_resets"], 0)

    def test_full_game_multiple_seeds(self):
        for seed in (1, 2):
            ycbk = seed % 2 == 0
            for seat in range(4):
                fake, stats, relevant, calls = _drive(seat, seed=seed,
                                                       ycbk=ycbk)
                self.assertEqual(calls, [d["action"] for d in relevant],
                                 f"seed={seed} seat={seat}")

    def test_payloads_valid(self):
        """提交的 payload 结构合法(字段齐全、chi 恒附两张 tiles)。"""
        res = synth_game(3)
        fake = FakeApi(res, 0)
        relevant = [d for d in res["decisions"]
                    if d["seat"] == 0
                    and (d["mode"] == "draw" or d["legal"] != [-1])]
        it = iter(relevant)

        def decide(g, s):
            return next(it)["action"]

        bot = BotClient(fake, "bot0", decide, window_wait=0, idle_sleep=0)
        bot.run(max_games=1)
        # 提交数 = 弃牌 + 碰窗(含显式过)+ 非过的吃窗决策
        n_expected = sum(
            1 for d in relevant
            if d["mode"] == "draw" or d["mode"] == "claim"
            or d["action"] != -1)
        self.assertEqual(len(fake.submitted), n_expected)
        for payload in fake.submitted:
            self.assertIn(payload["action"],
                          ("discard", "chi", "peng", "gang", "hu", "pass"))
            if payload["action"] in ("discard", "chi", "peng", "gang"):
                self.assertTrue(payload.get("tile"))
            if payload["action"] == "chi":
                self.assertEqual(len(payload["tiles"]), 2)


if __name__ == "__main__":
    unittest.main()
