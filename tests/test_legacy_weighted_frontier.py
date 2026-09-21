"""Weighted online two-ply frontier contract tests."""

import unittest

from mj.bot import choose_action, choose_discard
from mj.game import Game
from mj.legacy_eval import LegacyTwoPlyProfile
from mj.legacy_eval import DEFAULT_BOT_EVALUATOR
from mj.shanten import WEIGHTED_TWO_PLY_KERNEL_VERSION

try:
    from tests.test_legacy_eval import _seq100_game
except ImportError:  # pragma: no cover
    from test_legacy_eval import _seq100_game


@unittest.skipUnless(
    WEIGHTED_TWO_PLY_KERNEL_VERSION,
    "mj_kernels weighted_two_ply_frontier is not installed",
)
class TestWeightedTwoPlyFrontier(unittest.TestCase):
    def _exact_weighted(self, **overrides):
        values = {
            "kernel": "rust",
            "time_budget_ms": 10000.0,
            "soft_budget_ms": 10000.0,
            "hard_budget_ms": 10000.0,
            "node_budget": 100000,
            "max_frontier_candidates": 0,
            "allow_partial": False,
        }
        values.update(overrides)
        return LegacyTwoPlyProfile.weighted_online(**values)

    def test_seq100_future_metrics_distinguish_9b_and_3t(self):
        action, info = choose_discard(
            _seq100_game(), 0, return_info=True, profile=self._exact_weighted())
        self.assertEqual(action, 17)
        self.assertTrue(info["complete"])
        self.assertEqual(info["kernel_version"],
                         WEIGHTED_TWO_PLY_KERNEL_VERSION)
        rows = {row["tile"]: row for row in info["candidates"]}
        self.assertEqual(rows[17]["ukeire"], rows[20]["ukeire"])
        self.assertGreater(rows[17]["future_ukeire"],
                           rows[20]["future_ukeire"])
        self.assertGreater(rows[17]["future_ukeire_types"],
                           rows[20]["future_ukeire_types"])
        self.assertEqual(rows[17]["coverage"], 1.0)
        self.assertEqual(rows[17]["covered_weight"],
                         rows[17]["total_weight"])
        self.assertGreater(info["search_metrics"]["shanten_cache_hits"], 0)
        self.assertLess(info["search_metrics"]["ukeire_calls"],
                        info["search_metrics"]["child_nodes"])

    def test_partial_result_can_be_accepted_after_root_commit(self):
        game = _seq100_game()
        game.freeze = 2
        game.freezer = 2
        game.drawn[0] = 7
        profile = self._exact_weighted(
            node_budget=1,
            allow_partial=True,
            min_partial_coverage=0.0,
        )
        action, info = choose_discard(
            game, 0, return_info=True, profile=profile)
        self.assertEqual(action, 7)
        self.assertFalse(info["complete"])
        self.assertTrue(info["partial_accepted"])
        self.assertGreater(info["coverage"], 0.0)
        self.assertLess(info["coverage"], 1.0)
        self.assertEqual(info["level"], "weighted-two-ply-partial")

    def test_partial_result_falls_back_when_coverage_is_insufficient(self):
        game = _seq100_game()
        game.freeze = 2
        game.freezer = 2
        game.drawn[0] = 7
        profile = self._exact_weighted(
            node_budget=1,
            allow_partial=True,
            min_partial_coverage=1.0,
        )
        action, info = choose_discard(
            game, 0, return_info=True, profile=profile)
        self.assertEqual(action, 7)
        self.assertFalse(info["complete"])
        self.assertFalse(info["partial_accepted"])
        self.assertEqual(info["level"], "legacy")
        self.assertEqual(info["fallback_reason"], "partial_not_acceptable")

    def test_profile_routing_accepts_legacy_v2_alias(self):
        game = _seq100_game()
        action, info = choose_action(
            game, 0, evaluator="weighted-two-ply-frontier-v1",
            return_evaluation=True)
        self.assertEqual(action, info["selected"])
        self.assertEqual(info["profile"], "legacyV2")
        self.assertEqual(info["mode"], "weighted")

    def test_default_route_uses_legacy_v2(self):
        game = _seq100_game()
        action, info = choose_action(game, 0, return_evaluation=True)
        self.assertIn(action, game.legal_actions())
        self.assertEqual(DEFAULT_BOT_EVALUATOR, "legacyV2")
        self.assertEqual(info["profile"], "legacyV2")
        self.assertEqual(info["mode"], "weighted")

    def test_public_visible_state_isolated_from_hidden_hands(self):
        first = _seq100_game()
        second = _seq100_game()
        second.hands[1][0] = 1  # concealed opponent change, not public
        profile = self._exact_weighted()
        _, first_info = choose_discard(
            first, 0, return_info=True, profile=profile)
        _, second_info = choose_discard(
            second, 0, return_info=True, profile=profile)
        first_rows = {row["tile"]: row for row in first_info["candidates"]}
        second_rows = {row["tile"]: row for row in second_info["candidates"]}
        self.assertEqual(first_info["selected"], second_info["selected"])
        for tile in first_rows:
            self.assertEqual(
                first_rows[tile].get("future_ukeire"),
                second_rows[tile].get("future_ukeire"),
                tile,
            )

    def test_random_complete_native_matches_python_reference(self):
        for seed in range(2):
            rust_game = Game(seed=seed)
            py_game = Game(seed=seed)
            rust_action, rust_info = choose_discard(
                rust_game, rust_game.current_seat(), return_info=True,
                profile=self._exact_weighted())
            py_action, py_info = choose_discard(
                py_game, py_game.current_seat(), return_info=True,
                profile=self._exact_weighted(kernel="python"))
            self.assertEqual(rust_action, py_action, seed)
            self.assertTrue(rust_info["complete"], seed)
            self.assertTrue(py_info["complete"], seed)
            rust_rows = {row["tile"]: row for row in rust_info["candidates"]}
            py_rows = {row["tile"]: row for row in py_info["candidates"]}
            self.assertEqual(rust_rows.keys(), py_rows.keys())
            for tile in rust_rows:
                for field in ("future_improve_weight", "future_ukeire",
                              "future_ukeire_types"):
                    self.assertEqual(rust_rows[tile].get(field),
                                     py_rows[tile].get(field), (seed, tile,
                                                                field))


if __name__ == "__main__":
    unittest.main()
