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
        return next(it)["action"]

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
