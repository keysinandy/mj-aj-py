"""BC 管线测试:数据生成不变量 / 花色置换增广语义 / 网络前向。"""

import unittest
from unittest.mock import patch

import numpy as np

from mj.bc_data import (
    TRAINING_BOT_EVALUATOR,
    generate_game,
    search_fallback_reason,
)
from mj.features import (
    N_ACTIONS, N_PLANES, SUIT_PERMS, augment_sample,
)
from mj.tiles import W


class TestGenerateGame(unittest.TestCase):
    def test_training_evaluator_uses_offline_profile(self):
        from mj.legacy_eval import (
            LegacyTwoPlyProfile,
            WEIGHTED_OFFLINE_PROFILE_VERSION,
        )

        self.assertEqual(TRAINING_BOT_EVALUATOR, WEIGHTED_OFFLINE_PROFILE_VERSION)
        offline = LegacyTwoPlyProfile.weighted_offline()
        online = LegacyTwoPlyProfile.weighted_online()
        self.assertEqual(offline.max_frontier_candidates,
                         online.max_frontier_candidates)
        self.assertEqual(offline.allow_partial, online.allow_partial)
        # 预算放大到不会因 deadline / work budget 回退
        self.assertGreaterEqual(offline.hard_budget_ms, 1000.0)
        self.assertGreaterEqual(offline.node_budget, 1_000_000)

    def test_scope_delegation_is_not_a_search_fallback(self):
        self.assertIsNone(search_fallback_reason({"level": "legacy-one-ply"}))
        self.assertIsNone(search_fallback_reason(
            {"level": "legacy", "fallback_reason": "reaction_scope"}))
        self.assertIsNone(search_fallback_reason(
            {"level": "legacy", "fallback_reason": "baotou_scope"}))
        self.assertIsNone(search_fallback_reason(
            {"level": "legacy", "fallback_reason": "hu_kong_scope"}))
        self.assertIsNone(search_fallback_reason(
            {"level": "legacy", "fallback_reason": "only_legal_action"}))
        self.assertEqual(
            search_fallback_reason(
                {"level": "legacy",
                 "fallback_reason": "partial_not_acceptable"}),
            "partial_not_acceptable")
        self.assertEqual(
            search_fallback_reason(
                {"level": "legacy", "fallback_reason": "hard_deadline"}),
            "hard_deadline")

    def test_search_fallback_is_rejected_for_training_labels(self):
        from mj.legacy_eval import LegacyTwoPlyProfile

        strict = LegacyTwoPlyProfile.weighted_online(
            soft_budget_ms=0.0, hard_budget_ms=0.0)
        with patch.object(LegacyTwoPlyProfile, "weighted_online",
                          return_value=strict):
            with self.assertRaises(RuntimeError) as ctx:
                generate_game(seed=0, evaluator="legacyV2")
        self.assertIn("搜索回退", str(ctx.exception))

    def test_training_labels_record_level_and_fallback(self):
        d = generate_game(seed=1)
        self.assertIn("label_level", d)
        self.assertIn("label_fallback_reason", d)
        levels = set(d["label_level"].tolist())
        self.assertTrue(levels <= {"", "legacy", "legacy-one-ply",
                                   "weighted-two-ply-v1",
                                   "weighted-two-ply-partial"})
        # 训练路径不允许出现预算类回退
        self.assertEqual(set(d["label_fallback_reason"].tolist()), {""})

    def test_invariants(self):
        d = generate_game(seed=123)
        n = len(d["action"])
        self.assertGreater(n, 50)
        self.assertEqual(d["planes"].shape, (n, N_PLANES, 34))
        self.assertEqual(d["planes"].dtype, np.float16)
        self.assertEqual(d["scalars"].shape, (n, 8))
        self.assertEqual(d["mask"].shape, (n, N_ACTIONS))
        self.assertEqual(d["seat"].shape, (n,))
        self.assertEqual(d["score"].shape, (n, 4))
        # 动作必须在掩码内(teacher 合法性)
        self.assertTrue(all(d["mask"][i][d["action"][i]] for i in range(n)))
        # 平面值域
        self.assertTrue(float(d["planes"].astype(np.float32).max()) <= 1.0)

    def test_value_target(self):
        from mj.bc_train import value_target

        d = generate_game(seed=5)
        z = value_target(d["score"], d["seat"])
        self.assertEqual(z.shape, (len(d["action"]),))
        for i, s in enumerate(d["seat"]):
            expect = min(max(d["score"][i][s] / 24.0, -4), 4) / 4.0
            self.assertAlmostEqual(z[i], expect, places=5)
        # 裁剪:|score|/24 大于 4 时截断到 ±1
        z2 = value_target(np.array([[1000, -1000, 0, 0]] * 3),
                          np.array([0, 1, 2]))
        self.assertEqual(list(z2), [1.0, -1.0, 0.0])

    def test_deterministic(self):
        a = generate_game(seed=77)
        b = generate_game(seed=77)
        for k in ("planes", "mask", "action", "seat", "score"):
            self.assertTrue((a[k] == b[k]).all(), k)


class TestSuitPerms(unittest.TestCase):
    def test_structure(self):
        self.assertEqual(len(SUIT_PERMS), 6)
        qs = [tuple(q) for q, _ in SUIT_PERMS]
        aas = [tuple(a) for _, a in SUIT_PERMS]
        self.assertEqual(len(set(qs)), 6)   # 互异
        self.assertIn(tuple(range(34)), qs)  # 含恒等
        self.assertIn(tuple(range(N_ACTIONS)), aas)
        # 瓦片子集上 Q 与 A 互逆(位置回拉 = 动作前推的逆),逐对验证
        for q, a in SUIT_PERMS:
            self.assertEqual(list(a[q]), list(range(34)))
            self.assertEqual(list(q[a[:34]]), list(range(34)))
        # 封闭性:Q 集对复合封闭(构成 S3)
        for q1, _ in SUIT_PERMS:
            for q2, _ in SUIT_PERMS:
                self.assertIn(tuple(q1[q2]), qs)

    def test_semantic_direction_on_samples(self):
        """弃牌动作与手牌平面必须同向置换(方向 bug 回归)。"""
        d = generate_game(seed=11)
        planes = d["planes"].astype(np.float32)
        idx = next(i for i, a in enumerate(d["action"]) if a < 34)
        t = int(d["action"][idx])
        for perm in SUIT_PERMS:
            p2, m2, a2 = augment_sample(planes[idx], d["mask"][idx], t, perm)
            # 新动作列上的手牌多重性 = 旧动作列(同一张牌搬到了新位置)
            for k in range(4):
                self.assertEqual(float(p2[k][a2]), float(planes[idx][k][t]))
            # 增广后动作仍合法
            self.assertTrue(m2[a2])

    def test_augment_all_samples_legal(self):
        d = generate_game(seed=3)
        planes = d["planes"].astype(np.float32)
        for i in range(len(d["action"])):
            for perm in SUIT_PERMS:
                _p, m, a = augment_sample(planes[i], d["mask"][i],
                                          int(d["action"][i]), perm)
                self.assertTrue(m[a])
                # 字牌位(含财神)若为弃牌目标则不变
                if d["action"][i] >= 27:
                    self.assertEqual(a, int(d["action"][i]))


class TestModel(unittest.TestCase):
    def setUp(self):
        try:
            import torch  # noqa: F401
        except ImportError:
            self.skipTest("torch 不可用")

    def test_forward(self):
        import torch

        from mj.model import Net, masked_ce, masked_policy

        torch.manual_seed(0)
        net = Net(blocks=2, width=32)
        planes = torch.rand(4, N_PLANES, 34)
        scalars = torch.rand(4, 8)
        logits, v = net(planes, scalars)
        self.assertEqual(logits.shape, (4, N_ACTIONS))
        self.assertEqual(v.shape, (4,))
        self.assertTrue(bool((v > -1).all() and (v < 1).all()))
        mask = torch.zeros(4, N_ACTIONS, dtype=torch.bool)
        mask[:, 0] = True
        mask[:, 108] = True
        loss = masked_ce(logits, mask, torch.zeros(4, dtype=torch.long)) \
            + torch.mean((v - 0.1) ** 2)
        self.assertTrue(torch.isfinite(loss))
        p = masked_policy(logits, mask)
        self.assertEqual(p.shape, (4, N_ACTIONS))
        self.assertTrue(torch.allclose(p.sum(-1), torch.ones(4)))
        self.assertTrue(bool((p[:, 1:108] == 0).all()))
        # 双头均可训练:有限梯度
        loss.backward()
        self.assertTrue(all(
            p.grad is not None and torch.isfinite(p.grad).all()
            for p in net.parameters() if p.requires_grad))


class TestInitFromCkpt(unittest.TestCase):
    """bc_train --init:BC 老 75 平面补零迁移 + PPO action_net 头切片。"""

    def test_bc_75_plane_zero_pad_preserves_function(self):
        import torch

        from mj.bc_train import load_init_weights
        from mj.features import N_PLANES, N_PLANES_ORACLE
        from mj.model import Net

        torch.manual_seed(0)
        old = Net(blocks=2, width=32, n_planes=N_PLANES)
        new = Net(blocks=2, width=32, n_planes=N_PLANES_ORACLE)
        load_init_weights(new, {"state_dict": old.state_dict(),
                                "blocks": 2, "width": 32})
        planes = torch.rand(3, N_PLANES, 34)
        padded = torch.zeros(3, N_PLANES_ORACLE, 34)
        padded[:, :N_PLANES] = planes
        scalars = torch.rand(3, 8)
        with torch.no_grad():
            lo, vo = old(planes, scalars)
            ln, vn = new(padded, scalars)
        self.assertTrue(torch.allclose(lo, ln, atol=1e-5))
        self.assertTrue(torch.allclose(vo, vn, atol=1e-5))

    def test_ppo_head_slice(self):
        import torch

        from mj.bc_train import load_init_weights
        from mj.features import N_PLANES_ORACLE
        from mj.model import Net

        torch.manual_seed(0)
        ref = Net(blocks=2, width=32, n_planes=N_PLANES_ORACLE)
        action_net = torch.nn.Linear(2 * 34 + 34, 109)
        new = Net(blocks=2, width=32, n_planes=N_PLANES_ORACLE)
        approx = load_init_weights(new, {"net": ref.state_dict(),
                                         "action_net": action_net.state_dict(),
                                         "blocks": 2, "width": 32})
        self.assertTrue(approx)
        self.assertTrue(torch.equal(new.p_fc.weight,
                                    action_net.weight[:, : 2 * 34]))
        self.assertTrue(torch.equal(new.p_fc.bias, action_net.bias))
        # 主干/value 头与源一致
        self.assertTrue(torch.equal(new.stem[0].weight, ref.stem[0].weight))
        self.assertTrue(torch.equal(new.v_fc[0].weight, ref.v_fc[0].weight))


if __name__ == "__main__":
    unittest.main()
