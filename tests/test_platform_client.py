"""bot_client 离线测试:FakeApi 按事件切片回放 synth 局,驱动完整对弈。

FakeApi 模拟 /state 长轮询:seq==cursor 时释放到下一决策点的事件切片;
seq=0/gap 返回当点快照(含实测富字段:discards/melds/wall_remaining/
last_discard)。bot 决策点 = 自家摸牌/吃碰后的弃牌 + 有非过选项的反应窗,
decide 用记录动作,逐点断言镜像构建的合法集包含该动作。
"""

import time
import unittest
from unittest.mock import patch

import mj.shanten as shanten
from mj.platform.bot_client import BotClient, _meta_evaluator_kernel
from mj.platform.synth import synth_game, view_for

# 夹具放行上限:客户端在某决策点连续 N 次未决策(例如其镜像判定无合法选项)
# 就放行该点,避免夹具与客户端互相等待造成死循环。放行会记入 stalls,
# 测试可用它断言"没有被兜底放行过"。
STALL_LIMIT = 4


class FakeApi:
    """按 synth 局回放的假服务器(单座位视角)。

    窗口保真(2026-09-10):客户端改为「快照权威」的吃窗决策后,夹具必须
    ①不把事件水位推过本方尚未决策的决策点(否则会把客户端自己未来的
    动作回声提前发下去,镜像越过窗口);②在该决策点下发对应快照并带
    `window_deadline_ms`。测试的 decide 回调需调用 `deciding(d)` 通知
    夹具该点已消费。
    """

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
        # 本方决策点调度(与测试的 relevant 同口径)
        self.schedule = [d for d in self.decisions
                         if d["seat"] == seat
                         and (d["mode"] == "draw" or d["legal"] != [-1])]
        self.k = 0               # 下一个待消费的决策点
        self.stalls = 0          # 兜底放行次数(应恒为 0)
        self._pending_offers = 0

    # ---- 决策点调度 ----
    def deciding(self, d):
        """测试回调:客户端已完成决策点 d 的决策。"""
        if self.k < len(self.schedule) and self.schedule[self.k] is d:
            self.k += 1
            self._pending_offers = 0
            self.served = max(self.served, d["events_before"])

    def _pending(self):
        return self.schedule[self.k] if self.k < len(self.schedule) else None

    def _pending_snap(self, d):
        """本方决策点的快照;反应窗补服务端截止字段(客户端据此提交)。"""
        snap = dict(d["all_prompts"][self.seat])
        if str(snap.get("phase", "")).startswith("response"):
            snap["window_deadline_ms"] = (time.time() + 1.0) * 1000.0
        return snap

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
        d = self._pending()
        if d is not None and self.served >= d["events_before"]:
            return self._pending_snap(d)   # 已到本方窗口:给窗口状态
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
        while True:
            pending = self._pending()
            # 有未消费的本方决策点:事件水位不得推过它(否则提前下发
            # 客户端自己未来的动作回声,镜像越过窗口 → 跳过该窗口)
            if pending is not None:
                end = pending["events_before"]
            else:
                nxt = next((d for d in self.decisions
                            if d["events_before"] > self.served), None)
                end = nxt["events_before"] if nxt else len(self.res["events"])
            slice_ = [e for e in self.view["events"]
                      if self.served < e["seq"] <= end]
            if slice_:
                self.served = slice_[-1]["seq"]
                self._pending_offers = 0
                return {"events": slice_, "seq": self.served}
            if pending is not None:
                # 已推进到本方决策点:下发窗口快照(“轮到你”)
                if self._pending_offers >= STALL_LIMIT:
                    # 客户端始终不决策(镜像判定无选项):放行,防死循环
                    self.stalls += 1
                    self.k += 1
                    self._pending_offers = 0
                    self.served = max(self.served, pending["events_before"])
                    continue
                self._pending_offers += 1
                return {"snapshot": self._pending_snap(pending),
                        "seq": self.served}
            if end >= len(self.res["events"]) and not slice_:
                self.finished = True
                return {"events": [], "seq": self.served}
            self.served = end

    def game_action(self, gid, payload):
        self.submitted.append(payload)
        return {}


def _drive(seat, seed=0, ycbk=False, recorder=None, bot_evaluator=None):
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
        fake.deciding(d)          # 通知夹具:该决策点已消费
        act = d["action"]
        assert act in g.legal_actions(), \
            f"seat={seat} mode={d['mode']} act={act} legal={g.legal_actions()}"
        calls.append(act)
        return act

    if bot_evaluator is not None:
        decide.bot_evaluator = bot_evaluator

    bot = BotClient(fake, f"bot{seat}", decide, log=lambda m: None,
                    window_wait=0, idle_sleep=0, recorder=recorder)
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
            d = next(it)
            fake.deciding(d)
            return d["action"]

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


class TestMetaEvaluatorKernel(unittest.TestCase):
    """meta.evaluator_kernel 必须上报实际生效的内核版本,不能写死标签。"""

    def test_weighted_reports_installed_kernel_version(self):
        self.assertEqual(
            _meta_evaluator_kernel("legacyV2"),
            shanten.WEIGHTED_TWO_PLY_KERNEL_VERSION or "python-fallback")

    def test_legacy_two_ply_reports_installed_kernel_version(self):
        self.assertEqual(
            _meta_evaluator_kernel("legacy-two-ply-v1"),
            shanten.LEGACY_TWO_PLY_KERNEL_VERSION or "python-frontier-v1")

    def test_degraded_runs_report_explicit_fallbacks(self):
        with patch.object(shanten, "WEIGHTED_TWO_PLY_KERNEL_VERSION", None), \
                patch.object(shanten, "LEGACY_TWO_PLY_KERNEL_VERSION", None):
            self.assertEqual(_meta_evaluator_kernel("legacyV2"),
                             "python-fallback")
            self.assertEqual(_meta_evaluator_kernel("legacy_v1"),
                             "python-frontier-v1")

    def test_other_evaluators_have_no_kernel_label(self):
        self.assertIsNone(_meta_evaluator_kernel("shape-v1"))
        self.assertEqual(
            _meta_evaluator_kernel("legacy"),
            shanten.WEIGHTED_TWO_PLY_KERNEL_VERSION or "python-fallback")


if __name__ == "__main__":
    unittest.main()
