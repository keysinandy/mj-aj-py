"""形状护栏前沿契约:准入、截断、短路收紧、内核降级与预算语义。"""

import unittest
from unittest.mock import patch

from mj.bot import choose_discard
from mj.game import Game
from mj.legacy_eval import LegacyTwoPlyProfile
from mj.shanten import WEIGHTED_TWO_PLY_KERNEL_VERSION


# 唯一最大进张、但结构损失明显更大的局面(护栏目标样例):
# primary 打 3(进张 32/结构损失 12) vs 结构明显更优的候选。
GUARD_SEED = 290
PRIMARY_TILE = 3


@unittest.skipUnless(
    WEIGHTED_TWO_PLY_KERNEL_VERSION,
    "mj_kernels weighted_two_ply_frontier is not installed",
)
class TestShapeGuard(unittest.TestCase):
    def _profile(self, **overrides):
        values = {
            "kernel": "rust",
            "shape_guard_enabled": False,
            "shape_quality_enabled": False,
            "shape_quality_guard_enabled": False,
        }
        values.update(overrides)
        return LegacyTwoPlyProfile.weighted_online(**values)

    def _decide(self, seed=GUARD_SEED, **overrides):
        game = Game(seed=seed)
        seat = game.current_seat()
        action, info = choose_discard(
            game, seat, return_info=True, profile=self._profile(**overrides))
        rows = {row["tile"]: row for row in info["candidates"]}
        return action, info, rows

    def test_guard_explicit_off_keeps_singleton_short_circuit(self):
        action, info, rows = self._decide()
        self.assertEqual(action, PRIMARY_TILE)
        self.assertEqual(info["level"], "legacy-one-ply")
        self.assertEqual(info["short_circuit_reason"], "frontier_singleton")
        self.assertFalse(info["search_used"])
        guard = info["frontier_guard"]
        self.assertFalse(guard["enabled"])
        self.assertEqual(guard["admitted_tiles"], [])
        self.assertEqual(guard["primary_tiles"], [PRIMARY_TILE])
        self.assertTrue(all(row["admitted_by"] == "primary"
                            for row in rows.values()))

    def test_guard_defaults_on_in_online_and_offline_profiles(self):
        self.assertTrue(
            LegacyTwoPlyProfile.weighted_online(kernel="rust")
            .shape_guard_enabled)
        self.assertTrue(LegacyTwoPlyProfile.weighted_offline().shape_guard_enabled)
        # 精确/legacy V1 档案保持关闭,指纹不受影响
        exact = LegacyTwoPlyProfile.default()
        self.assertFalse(exact.shape_guard_enabled)
        self.assertNotIn("shape_guard_enabled", exact._payload())

    def test_default_profile_guards_the_singleton_state(self):
        game = Game(seed=GUARD_SEED)
        seat = game.current_seat()
        action, info = choose_discard(
            game, seat, return_info=True,
            profile=LegacyTwoPlyProfile.weighted_online(kernel="rust"))
        guard = info["frontier_guard"]
        self.assertTrue(guard["enabled"])
        self.assertTrue(guard["admitted_tiles"])
        self.assertIn(action, guard["primary_tiles"] + guard["admitted_tiles"])
        self.assertNotEqual(info["level"], "legacy-one-ply")

    def test_guard_admits_structurally_better_candidate(self):
        action, info, rows = self._decide(shape_guard_enabled=True)
        guard = info["frontier_guard"]
        self.assertTrue(guard["enabled"])
        self.assertIsNone(guard["skipped_reason"])
        self.assertEqual(guard["primary_tiles"], [PRIMARY_TILE])
        self.assertTrue(guard["admitted_tiles"])
        self.assertNotEqual(info["level"], "legacy-one-ply")
        self.assertIsNone(info["short_circuit_reason"])
        self.assertIn(action, guard["primary_tiles"] + guard["admitted_tiles"])
        admitted_rows = [row for row in rows.values()
                         if row["admitted_by"] == "shape_guard"]
        self.assertEqual(sorted(row["tile"] for row in admitted_rows),
                         sorted(guard["admitted_tiles"]))
        for row in rows.values():
            if row["admitted_by"] == "shape_guard":
                self.assertNotIn(row["tile"], guard["dropped_tiles"])

    def test_guard_gating_keeps_singleton(self):
        for overrides, reason in (
            ({"shape_guard_ukeire_slack": 0}, "slack_zero"),
            ({"shape_guard_shape_delta": 20}, "no_candidate_admitted"),
        ):
            action, info, _rows = self._decide(
                shape_guard_enabled=True, **overrides)
            self.assertEqual(info["frontier_guard"]["skipped_reason"], reason)
            self.assertEqual(info["level"], "legacy-one-ply")
            self.assertEqual(action, PRIMARY_TILE)

    def test_guard_skips_without_native_kernel(self):
        baseline_action, baseline_info, _ = self._decide()
        with patch("mj.legacy_eval.weighted_two_ply_frontier", None):
            action, info, _rows = self._decide(shape_guard_enabled=True)
        guard = info["frontier_guard"]
        self.assertEqual(guard["skipped_reason"], "kernel_unavailable")
        self.assertEqual(guard["admitted_tiles"], [])
        self.assertEqual(action, baseline_action)
        self.assertEqual(info["level"], baseline_info["level"])
        self.assertFalse(info["search_used"])

    def test_guard_keeps_partial_fallback_semantics(self):
        action, info, _rows = self._decide(
            shape_guard_enabled=True, soft_budget_ms=0.0, hard_budget_ms=0.0)
        self.assertFalse(info["complete"])
        self.assertFalse(info["partial_accepted"])
        self.assertEqual(info["level"], "legacy")
        self.assertTrue(info["fallback_reason"])
        self.assertFalse(info["search_used"])
        self.assertEqual(action, info["legacy_best"])

    def test_guard_off_keeps_legacy_short_circuit(self):
        # 默认开启后,显式关闭仍必须回到旧行为:唯一最大进张直接短路。
        for seed in (0, 7, GUARD_SEED):
            action, info, _rows = self._decide(seed=seed)
            primary = info["frontier_guard"]["primary_tiles"]
            if len(primary) == 1:
                self.assertEqual(info["level"], "legacy-one-ply")
                self.assertEqual(action, primary[0])

    def test_guard_knobs_enter_fingerprint_and_json(self):
        off = self._profile(shape_guard_enabled=False)
        on = self._profile(shape_guard_enabled=True)
        self.assertNotEqual(off.fingerprint, on.fingerprint)
        self.assertIs(off.as_json()["shape_guard_enabled"], False)
        self.assertIs(on.as_json()["shape_guard_enabled"], True)
        # 默认(非 weighted)profile 的指纹不因新增默认值而改变
        self.assertNotIn("shape_guard_enabled",
                         LegacyTwoPlyProfile.default()._payload())

    def test_runtime_kernel_diagnostic_reports_degradation(self):
        from mj import shanten as shanten_module

        healthy = shanten_module.kernel_runtime_diagnostic()
        self.assertFalse(healthy["degraded"])
        self.assertIsNone(healthy["reason"])
        self.assertEqual(healthy["weighted_kernel_version"],
                         WEIGHTED_TWO_PLY_KERNEL_VERSION)
        self.assertIn("正常", shanten_module.format_kernel_diagnostic())

        with patch.object(shanten_module, "_FORCE_PY", True):
            degraded = shanten_module.kernel_runtime_diagnostic()
            degraded_line = shanten_module.format_kernel_diagnostic()
        self.assertTrue(degraded["degraded"])
        self.assertEqual(degraded["reason"], "MJ_KERNELS=python")
        self.assertEqual(degraded["shanten_kernel"], "python")
        self.assertIn("降级", degraded_line)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
