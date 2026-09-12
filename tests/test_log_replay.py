"""离线重放/转换测试:记录器产出 JSONL → replay_game 对账 → npz 分片。

闭环断言:重放合法集与线上一致、样本动作 = 实际提交成功的动作、
npz 与 bc_data 同构且 mask/action/score 自洽。
"""

import glob
import json
import os
import tempfile
import unittest

import numpy as np

from mj.features import action_to_flat, N_PLANES, N_SCALARS
from mj.log2data import collect, write_shards
from mj.log_replay import replay_game
from mj.platform.bot_client import BotClient
from mj.platform.recorder import Recorder
from mj.platform.synth import synth_game
from test_platform_client import FakeApi


def _record_game(root, seat=2, seed=5):
    """打一局 synth 对弈落日志,返回 (日志路径, FakeApi, 相关决策点)。"""
    res = synth_game(seed)
    fake = FakeApi(res, seat)
    relevant = [d for d in res["decisions"]
                if d["seat"] == seat
                and (d["mode"] == "draw" or d["legal"] != [-1])]
    it = iter(relevant)

    def decide(g, s):
        d = next(it)
        fake.deciding(d)          # 通知夹具:该决策点已消费
        return d["action"]

    rec = Recorder(root=root)
    bot = BotClient(fake, f"bot{seat}", decide, log=lambda m: None,
                    window_wait=0, idle_sleep=0, recorder=rec)
    bot.run(max_games=1)
    return sorted(glob.glob(os.path.join(root, "**", "*.jsonl"),
                            recursive=True))[0], fake, relevant


def _load(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


class TestLogReplay(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def test_roundtrip_clean(self):
        path, fake, relevant = _record_game(self._tmp.name)
        recs = _load(path)
        rep = replay_game(recs)
        self.assertEqual(rep["illegal"], [])
        self.assertTrue(rep["clean"])
        self.assertEqual(rep["end_scores"], fake.final_scores)
        self.assertEqual(rep["my_seat"], 2)
        # 样本动作 = 实际提交成功的决策(draw/claim 恒提交;chow 仅非过)
        expected = [action_to_flat(d["action"]) for d in relevant
                    if d["mode"] in ("draw", "claim") or d["action"] != -1]
        self.assertEqual([s["action_flat"] for s in rep["samples"]],
                         expected)
        # 每个样本:所选动作在掩码内,平面形状正确
        for s in rep["samples"]:
            self.assertEqual(s["planes"].shape, (N_PLANES, 34))
            self.assertEqual(s["scalars"].shape, (N_SCALARS,))
            self.assertTrue(s["mask"][s["action_flat"]])

    def test_dirty_reset_not_clean(self):
        path, _, _ = _record_game(self._tmp.name)
        recs = _load(path)
        # 注入一条 reset(镜像失步)→ clean=False
        for i, r in enumerate(recs):
            if r["type"] == "decision":
                recs.insert(i, {"type": "reset", "reason": "注入"})
                break
        rep = replay_game(recs)
        self.assertFalse(rep["clean"])

    def test_tampered_legal_flagged(self):
        """线上合法集与重放不符 → illegal(对账核心)。"""
        path, _, _ = _record_game(self._tmp.name)
        recs = _load(path)
        for r in recs:
            if r["type"] == "decision":
                r["legal"] = r["legal"][:1]  # 篡改
                break
        rep = replay_game(recs)
        self.assertEqual(len(rep["illegal"]), 1)
        self.assertIn("合法集不符", rep["illegal"][0]["msg"])

    def test_409_sample_dropped(self):
        """被 409 拒绝的动作不进样本(strict 口径)。"""
        path, _, relevant = _record_game(self._tmp.name)
        recs = _load(path)
        acts = [r for r in recs if r["type"] == "action"]
        acts[0]["ok"] = False
        acts[0]["status"] = 409
        acts[0]["code"] = "INVALID_ACTION"
        rep = replay_game(recs)
        expected = [action_to_flat(d["action"]) for d in relevant
                    if d["mode"] in ("draw", "claim") or d["action"] != -1]
        self.assertEqual(
            [s["action_flat"] for s in rep["samples"]], expected[1:])


class TestRoundBoundarySkip(unittest.TestCase):
    """轮边界快照跳过(协议固有):req 记录推断跳过段,累计对账降级。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def test_skipped_ranges_merge(self):
        from mj.log_replay import _skipped_ranges
        recs = [
            {"type": "req", "seq": 10, "res": {"snapshot": True, "seq": 14}},
            {"type": "req", "seq": 14, "res": {"snapshot": True, "seq": 14}},
            {"type": "req", "seq": 14, "res": {"events": True, "seq": 20}},
            {"type": "req", "seq": 20, "res": {"snapshot": True, "seq": 23}},
            {"type": "req", "seq": 40, "res": {"finished": True, "seq": 42}},
        ]
        self.assertEqual(_skipped_ranges(recs), [(11, 14), (21, 23), (41, 42)])

    def _drop_round_ended(self, recs, with_skip_marker):
        """删掉唯一 round_ended(模拟轮翻转快照跳过),可选拦 req 标记。"""
        for r in recs:
            if r["type"] != "events":
                continue
            for i, ev in enumerate(r["events"]):
                if ev.get("type") == "round_ended":
                    del r["events"][i]
                    if with_skip_marker:
                        recs.append({
                            "type": "req", "seq": ev["seq"] - 1,
                            "res": {"snapshot": True, "gap": True,
                                    "seq": ev["seq"], "n_events": 0}})
                    return True
        return False

    def test_skip_marker_downgrades_to_clean(self):
        """round_ended 被跳过且有 req 标记 → 不非法(协议固有)。"""
        path, _, _ = _record_game(self._tmp.name)
        recs = _load(path)
        self.assertTrue(self._drop_round_ended(recs, with_skip_marker=True))
        rep = replay_game(recs)
        self.assertEqual(rep["n_rounds"], 0)
        self.assertNotIn("终局积分不符",
                         [i["msg"] for i in rep["illegal"]])
        self.assertTrue(rep["clean"])

    def test_missing_round_without_marker_flagged(self):
        """round_ended 缺失且无快照跳过段解释 → 非法。"""
        path, _, _ = _record_game(self._tmp.name)
        recs = _load(path)
        self.assertTrue(self._drop_round_ended(recs, with_skip_marker=False))
        # The current client may legitimately issue a seq=0 window-confirm
        # re-anchor while recording this synthetic game.  Remove that
        # response marker so this fixture tests the actual no-explanation
        # branch rather than treating the client-generated re-anchor as
        # evidence for the missing round boundary.
        for row in recs:
            if row["type"] != "req":
                continue
            res = row.get("res") or {}
            row_seq = row.get("seq")
            res_seq = res.get("seq")
            if (res.get("snapshot") and row_seq is not None
                    and res_seq is not None and res_seq > row_seq):
                res["snapshot"] = False
        rep = replay_game(recs)
        self.assertFalse(rep["clean"])
        self.assertTrue(any("无快照跳过段解释" in i["msg"]
                            for i in rep["illegal"]))

    def test_no_skip_still_strict(self):
        """无跳过时累计对账保持严格:end 篡改 → 非法。"""
        path, _, _ = _record_game(self._tmp.name)
        recs = _load(path)
        for r in recs:
            if r["type"] == "end":
                r["scores"] = [s + 1 for s in r["scores"]]
        rep = replay_game(recs)
        self.assertFalse(rep["clean"])
        self.assertTrue(any("终局积分不符" in i["msg"]
                            for i in rep["illegal"]))


class TestLog2Data(unittest.TestCase):
    def test_shard_written(self):
        with tempfile.TemporaryDirectory() as root:
            path, fake, relevant = _record_game(root)
            collected, skipped = collect([path])
            self.assertEqual(skipped, [])
            self.assertEqual(len(collected), 1)
            outs = list(write_shards(collected, os.path.join(root, "npz"),
                                     per_shard=1000))
            self.assertEqual(len(outs), 1)
            shard, n = outs[0]
            d = np.load(shard)
            self.assertEqual(sorted(d.files), sorted(
                ["planes", "scalars", "mask", "action", "seat", "score"]))
            self.assertEqual(d["planes"].shape, (n, N_PLANES, 34))
            self.assertEqual(d["scalars"].shape, (n, N_SCALARS))
            self.assertEqual(d["action"].shape, (n,))
            self.assertTrue((d["seat"] == 2).all())
            self.assertTrue(
                (d["score"] == np.array(fake.final_scores)).all())
            # mask 与 action 自洽
            self.assertTrue(d["mask"][np.arange(n), d["action"]].all())
            # 期望样本数:draw/claim 恒提交 + chow 非过
            expected = sum(1 for x in relevant
                           if x["mode"] in ("draw", "claim")
                           or x["action"] != -1)
            self.assertEqual(n, expected)


if __name__ == "__main__":
    unittest.main()
