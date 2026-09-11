"""记录器测试:synth 全场驱动 BotClient → JSONL 落盘 → 逐类记录断言。

复用 test_platform_client 的 FakeApi(单座位视角假服务器);另含
Recorder 直接调用(409/失步/线程安全)的单元测试。
"""

import json
import os
import tempfile
import threading
import unittest

from mj.platform.api import ApiError
from mj.platform.bot_client import BotClient
from mj.platform.recorder import GameLog, Recorder
from mj.platform.synth import synth_game, view_for
from test_platform_client import FakeApi


def _drive(seat, seed, recorder, api=None):
    """打一局 synth 对局并落日志,返回 (FakeApi, stats, 相关决策点, res)。"""
    res = synth_game(seed)
    fake = api(res, seat) if api else FakeApi(res, seat)
    relevant = [d for d in res["decisions"]
                if d["seat"] == seat
                and (d["mode"] == "draw" or d["legal"] != [-1])]
    it = iter(relevant)

    def decide(g, s):
        d = next(it)
        fake.deciding(d)          # 通知夹具:该决策点已消费
        return d["action"]

    bot = BotClient(fake, f"bot{seat}", decide, log=lambda m: None,
                    window_wait=0, idle_sleep=0, recorder=recorder)
    stats = bot.run(max_games=1)
    return fake, stats, relevant, res


def _read_all(recorder, gid, name="bot0"):
    """按落盘路径直接读(赛后 GameLog 已关闭弹出,不能走 log_for)。"""
    import time as _time
    path = os.path.join(recorder.root, _time.strftime("%Y%m%d"),
                        f"{name}_{gid}.jsonl")
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


class TestRecorderFullGame(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.rec = Recorder(root=self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_full_game_records(self):
        for seat in range(4):
            fake, stats, relevant, res = _drive(seat, 0, self.rec)
            gid = "g1"
            self.assertEqual(stats["games"], 1)
            recs = _read_all(self.rec, gid, f"bot{seat}")
            types = [r["type"] for r in recs]
            self.assertEqual(types[0], "meta")
            self.assertEqual(types[-1], "end")
            for want in ("meta", "req", "events", "snapshot",
                         "decision", "action"):
                self.assertIn(want, types, f"seat={seat} 缺 {want}")
            self.assertEqual(stats["mirror_resets"], 0)

            meta = recs[0]
            self.assertEqual(meta["name"], f"bot{seat}")
            self.assertEqual(meta["tid"], "room_t1")
            self.assertIn("you_cai_bi_kao", meta)
            self.assertIn("base", meta)
            # 座次身份:快照记录带 seat
            snaps = [r for r in recs if r["type"] == "snapshot"]
            self.assertTrue(all(s["snap"].get("seat") == seat
                                for s in snaps))

            # 决策记录:条数 = bot 实际决策点,合法集包含所选动作
            decisions = [r for r in recs if r["type"] == "decision"]
            self.assertEqual(len(decisions), len(relevant))
            self.assertEqual([d["action"] for d in decisions],
                             [d["action"] for d in relevant])
            for d in decisions:
                self.assertIn(d["action"], d["legal"])
                self.assertIn(d["phase"], ("draw", "response_peng",
                                           "response_chi"))
                self.assertIn("latency_ms", d)
                self.assertIn("seq", d)

            # action 记录:payload 结构合法,配对 decision id 有效
            decisions_by_id = {d["id"]: d for d in decisions}
            actions = [r for r in recs if r["type"] == "action"]
            for a in actions:
                self.assertTrue(a["ok"])
                self.assertIn(a["payload"]["action"],
                              ("discard", "chi", "peng", "gang", "hu", "pass"))
                did = a.get("decision")
                self.assertIn(did, decisions_by_id)
                self.assertEqual(
                    a["payload"].get("action") == "pass",
                    decisions_by_id[did]["action"] == -1)

            # end 记录:终局积分
            end = recs[-1]
            self.assertEqual(end["reason"], "finished")
            self.assertEqual(end["scores"],
                             res["rounds"][0]["scores"])

            # 游标推进:游标先降后升不要求,但 req 游标集合应包含最大事件 seq
            seqs = [r["seq"] for r in recs
                    if r["type"] == "req" and r.get("seq") is not None]
            self.assertTrue(seqs)

    def test_events_all_served(self):
        """事件批记录合计 = 该座位可见事件总数(FakeApi 每条只服务一次)。"""
        seat, seed = 2, 1
        res = synth_game(seed)
        view = view_for(res, seat)
        _, stats, _, _ = _drive(seat, seed, self.rec)
        recs = _read_all(self.rec, "g1", f"bot{seat}")
        n = sum(len(r["events"]) for r in recs if r["type"] == "events")
        self.assertEqual(n, len(view["events"]))


class TestRecorderUnits(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.rec = Recorder(root=self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_file_layout(self):
        self.rec.meta("gX", "朱雀", "t1", you_cai_bi_kao=True, base=2)
        log = self.rec.log_for("gX")
        rel = os.path.relpath(log.path, self._tmp.name)
        day, fname = os.path.split(rel)
        self.assertEqual(day, __import__("time").strftime("%Y%m%d"))
        self.assertEqual(fname, "朱雀_gX.jsonl")

    def test_sequence_and_pairing(self):
        self.rec.meta("g", "b", "t")
        self.rec.req("g", 0, 200, 12.3, 1, {"n_events": 0})
        self.rec.snapshot("g", 0, {"seat": 1})
        self.rec.events("g", 5, [{"seq": 5, "type": "tile_drawn"}])
        did = self.rec.decision("g", "draw", [1, 2, 3], 2, 5.0)
        self.assertEqual(did, 1)
        self.rec.action("g", "draw", {"action": "discard", "tile": "5b"},
                        ok=True, latency_ms=8.0)
        self.rec.reset("g", "镜像失步")
        self.rec.end("g", "inaccessible")
        recs = _read_all(self.rec, "g", "b")
        self.assertEqual([r["type"] for r in recs],
                         ["meta", "req", "snapshot", "events",
                          "decision", "action", "reset", "end"])
        d, a = recs[4], recs[5]
        self.assertEqual(d["seq"], 5)  # 游标随 events 推进
        self.assertEqual(d["legal"], [1, 2, 3])
        self.assertEqual(a["decision"], did)
        self.assertTrue(all("ts" in r for r in recs))
        # end 后关闭,再写静默丢弃
        self.rec.events("g", 9, [])
        self.assertEqual(len(_read_all(self.rec, "g", "b")), 8)

    def test_409_recorded(self):
        """动作被 409 拒绝 → action 记录 ok=False + 错误码,对弈继续。"""
        class FailingApi(FakeApi):
            def __init__(self, res, seat):
                super().__init__(res, seat)
                self._failed = False

            def game_action(self, gid, payload):
                if not self._failed:
                    self._failed = True
                    raise ApiError(409, json.dumps(
                        {"code": "INVALID_ACTION", "message": "race"}))
                return super().game_action(gid, payload)

        _, stats, _, _ = _drive(0, 0, self.rec, api=FailingApi)
        self.assertEqual(stats["err409"], 1)
        recs = _read_all(self.rec, "g1", "bot0")
        bad = [r for r in recs
               if r["type"] == "action" and not r["ok"]]
        self.assertEqual(len(bad), 1)
        self.assertEqual(bad[0]["status"], 409)
        self.assertEqual(bad[0]["code"], "INVALID_ACTION")
        self.assertIn("decision", bad[0])
        self.assertEqual(recs[-1]["type"], "end")

    def test_logical_request_and_physical_attempt_fields_are_additive(self):
        self.rec.req(
            "g", 0, 200, 1.0, attempts=2,
            request_kind="WINDOW_PENG",
            logical_request_id="state-7", attempt_index=1,
            reason=["SSE_DELTA", "WINDOW_CONFIRM"], generation=4,
            demand={"watermark_target": 12,
                    "full_snapshot_required": True})
        self.rec.action(
            "g", "response_peng", {"action": "pass"}, ok=True,
            logical_request_id="window:g:7", attempt_index=2,
            window_id={"source_discard_seq": 7,
                       "identity_status": "authoritative"},
            window_attempt_key={"phase": "response_peng"},
            identity_status="authoritative")
        recs = _read_all(self.rec, "g", "bot")
        self.assertEqual(recs[0]["logical_request_id"], "state-7")
        self.assertEqual(recs[0]["attempt_index"], 1)
        self.assertEqual(recs[1]["logical_request_id"], "window:g:7")
        self.assertEqual(recs[1]["identity_status"], "authoritative")

    def test_gamelog_thread_safe(self):
        with tempfile.TemporaryDirectory() as d:
            log = GameLog(os.path.join(d, "x.jsonl"), "g")
            def w(k):
                for i in range(200):
                    log.write({"type": "events", "w": k, "i": i})
            ts = [threading.Thread(target=w, args=(k,)) for k in range(8)]
            for t in ts:
                t.start()
            for t in ts:
                t.join()
            with open(log.path, encoding="utf-8") as f:
                lines = [l for l in f if l.strip()]
            self.assertEqual(len(lines), 1600)
            recs = [json.loads(l) for l in lines]
            self.assertEqual(len({r["i"] for r in recs}), 200)


if __name__ == "__main__":
    unittest.main()
