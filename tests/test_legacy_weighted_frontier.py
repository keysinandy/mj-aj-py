"""Weighted online two-ply frontier contract tests."""

import unittest
from unittest.mock import patch

from mj.bot import choose_action, choose_discard
from mj.game import Game
from mj.legacy_eval import (
    FutureEvaluation,
    LegacyRootCandidate,
    LegacyTwoPlyProfile,
    _limit_weighted_frontier,
    _native_legal_masks,
    _root_features,
)
from mj.legacy_eval import DEFAULT_BOT_EVALUATOR
from mj.shanten import WEIGHTED_TWO_PLY_KERNEL_VERSION, shanten
from mj.shanten import weighted_two_ply_frontier

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
        self.assertTrue(info["search_used"])
        self.assertEqual(info["search_phase"], "two_ply")

    def test_singleton_frontier_short_circuits_two_ply(self):
        game = _seq100_game()
        game.freeze = 2
        game.freezer = 2
        game.drawn[0] = 7
        profile = self._exact_weighted(
            node_budget=1,
            allow_partial=True,
            min_partial_coverage=0.0,
        )
        with patch("mj.legacy_eval._weighted_native_future_for_frontier",
                   side_effect=AssertionError("singleton must not search")):
            action, info = choose_discard(
                game, 0, return_info=True, profile=profile)
        self.assertEqual(action, 7)
        self.assertFalse(info["complete"])
        self.assertFalse(info["partial_accepted"])
        self.assertEqual(info["level"], "legacy-one-ply")
        self.assertEqual(info["short_circuit_reason"], "frontier_singleton")
        row = next(row for row in info["candidates"] if row["tile"] == 7)
        self.assertIsNone(row.get("future_improve_weight"))
        self.assertEqual(row["future_short_circuit_reason"],
                         "frontier_singleton")

    def test_partial_result_falls_back_when_bounds_overlap(self):
        game = _seq100_game()
        profile = self._exact_weighted(
            node_budget=100000,
            allow_partial=True,
            min_partial_coverage=0.9,
        )

        def fake_native(_game, _seat, frontier, *_args):
            self.assertGreaterEqual(len(frontier), 2)
            values = {}
            for index, root in enumerate(frontier):
                lower, upper = ((30, 40) if index == 0 else (20, 50))
                values[root.tile] = FutureEvaluation(
                    complete=False,
                    root_shanten=root.shanten,
                    future_improve_weight=lower,
                    future_improve_lower=lower,
                    future_improve_upper=upper,
                    future_ukeire=40,
                    future_ukeire_mean=2.0,
                    future_ukeire_mean_denominator=95,
                    future_ukeire_types=20,
                    future_ukeire_types_mean=1.0,
                    covered_weight=95,
                    total_weight=100,
                    coverage=0.95,
                    search_metrics={"shanten_cache_misses": 10},
                )
            return values, {"shanten_cache_misses": 10}, 1.0

        with patch("mj.legacy_eval._weighted_native_future_for_frontier",
                   side_effect=fake_native):
            action, info = choose_discard(
                game, 0, return_info=True, profile=profile)
        self.assertFalse(info["complete"])
        self.assertFalse(info["partial_accepted"])
        self.assertEqual(info["level"], "legacy")
        self.assertEqual(info["fallback_reason"], "partial_not_acceptable")

    def test_partial_result_accepts_strict_improvement_winner(self):
        game = _seq100_game()
        profile = self._exact_weighted(
            node_budget=100000,
            allow_partial=True,
            min_partial_coverage=0.9,
        )
        seen = {}

        def fake_native(_game, _seat, frontier, *_args):
            self.assertGreaterEqual(len(frontier), 2)
            seen["first"] = frontier[0].tile
            values = {}
            for index, root in enumerate(frontier):
                lower, upper = ((30, 35) if index == 0 else (10, 29))
                values[root.tile] = FutureEvaluation(
                    complete=False,
                    root_shanten=root.shanten,
                    future_improve_weight=lower,
                    future_improve_lower=lower,
                    future_improve_upper=upper,
                    future_ukeire=40,
                    future_ukeire_mean=2.0,
                    future_ukeire_mean_denominator=20,
                    future_ukeire_types=20,
                    future_ukeire_types_mean=1.0,
                    covered_weight=20,
                    total_weight=100,
                    coverage=0.2,
                    search_metrics={"shanten_cache_misses": 10},
                )
            return values, {"shanten_cache_misses": 10}, 1.0

        with patch("mj.legacy_eval._weighted_native_future_for_frontier",
                   side_effect=fake_native):
            action, info = choose_discard(
                game, 0, return_info=True, profile=profile)
        self.assertEqual(action, seen["first"])
        self.assertFalse(info["complete"])
        self.assertTrue(info["partial_accepted"])
        self.assertEqual(info["level"], "weighted-two-ply-partial")
        row = next(row for row in info["candidates"]
                   if row["tile"] == seen["first"])
        self.assertEqual(row["future_improve_lower"], 30)
        self.assertEqual(row["future_improve_upper"], 35)
        self.assertEqual(row["future_ukeire_mean"], 2.0)
        self.assertEqual(row["future_ukeire_mean_denominator"], 20)

    def test_stage_a_only_partial_keeps_ukeire_missing(self):
        game = _seq100_game()
        profile = self._exact_weighted(
            node_budget=100000,
            allow_partial=True,
            min_partial_coverage=0.9,
        )

        def fake_native(_game, _seat, frontier, *_args):
            self.assertGreaterEqual(len(frontier), 2)
            values = {}
            for index, root in enumerate(frontier):
                lower, upper = ((30, 35) if index == 0 else (10, 29))
                values[root.tile] = FutureEvaluation(
                    complete=False,
                    root_shanten=root.shanten,
                    future_improve_weight=lower,
                    future_improve_lower=lower,
                    future_improve_upper=upper,
                    future_ukeire_skipped=True,
                    covered_weight=20,
                    total_weight=100,
                    coverage=0.2,
                    search_metrics={"search_phase": "future_shanten"},
                    missing=("future_ukeire_not_evaluated",),
                    fallback_reason="future_ukeire_skipped",
                )
            return values, {"search_phase": "future_shanten"}, 1.0

        with patch("mj.legacy_eval._weighted_native_future_for_frontier",
                   side_effect=fake_native):
            _action, info = choose_discard(
                game, 0, return_info=True, profile=profile)
        self.assertTrue(info["partial_accepted"])
        self.assertTrue(info["search_used"])
        self.assertEqual(info["search_phase"], "future_shanten")
        row = next(row for row in info["candidates"]
                   if row["tile"] == info["selected"])
        self.assertIsNone(row["future_ukeire"])
        self.assertTrue(row["future_ukeire_skipped"])
        self.assertIn("future_ukeire_not_evaluated", row["missing"])

    def test_work_budget_counts_uncached_shanten_not_child_nodes(self):
        game = _seq100_game()
        profile = self._exact_weighted(
            node_budget=1,
            allow_partial=True,
            min_partial_coverage=0.0,
        )
        _action, info = choose_discard(
            game, 0, return_info=True, profile=profile)
        metrics = info["search_metrics"]
        self.assertEqual(metrics["child_nodes"], 0)
        self.assertLessEqual(metrics["shanten_cache_misses"], 3)
        self.assertEqual(info["fallback_reason"], "partial_not_acceptable")

    def test_native_work_budget_reports_work_budget_reason(self):
        game = _seq100_game()
        seat = 0
        locked = len(game.melds[seat])
        visible = tuple(game.visible_counts(seat))
        candidates = []
        best_s = None
        for tile, count in enumerate(game.hands[seat]):
            if count <= 0:
                continue
            hand = list(game.hands[seat])
            hand[tile] -= 1
            value = shanten(hand, locked)
            if best_s is None or value < best_s:
                best_s, candidates = value, []
            if value == best_s:
                candidates.append(LegacyRootCandidate(
                    tile=tile, hand=tuple(hand), shanten=value))
        _enriched, frontier, _diagnostics = _root_features(
            candidates, locked, visible)
        frontier, _diagnostics = _limit_weighted_frontier(
            frontier, _diagnostics, 0)
        rows = weighted_two_ply_frontier(
            [list(root.hand) for root in frontier],
            [root.shanten for root in frontier],
            list(visible),
            _native_legal_masks(frontier, visible, False),
            locked, False, 1, 10000.0, 10000.0, 8192, 0.0, True)
        self.assertTrue(rows)
        self.assertTrue(all(row[7] == "work_budget_exceeded" for row in rows))

    def test_native_hard_deadline_returns_rows_not_exception(self):
        game = _seq100_game()
        locked = len(game.melds[0])
        visible = tuple(game.visible_counts(0))
        candidates = []
        best_s = None
        for tile, count in enumerate(game.hands[0]):
            if count <= 0:
                continue
            hand = list(game.hands[0])
            hand[tile] -= 1
            value = shanten(hand, locked)
            if best_s is None or value < best_s:
                best_s, candidates = value, []
            if value == best_s:
                candidates.append(LegacyRootCandidate(
                    tile=tile, hand=tuple(hand), shanten=value))
        _enriched, frontier, _diagnostics = _root_features(
            candidates, locked, visible)
        frontier, _diagnostics = _limit_weighted_frontier(
            frontier, _diagnostics, 3)
        rows = weighted_two_ply_frontier(
            [list(root.hand) for root in frontier],
            [root.shanten for root in frontier],
            list(visible),
            _native_legal_masks(frontier, visible, False),
            locked, False, 100000, 0.0, 0.0, 8192, 0.0, True)
        self.assertTrue(rows)
        self.assertTrue(all(row[7] == "hard_deadline" for row in rows))

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
