"""Contracts for reusable public standing-hand U2 evaluation."""

import unittest
from unittest.mock import patch

from mj.legacy_eval import (
    FutureEvaluation,
    LegacyTwoPlyProfile,
    StandingRoot,
    WEIGHTED_TWO_PLY_KERNEL_REQUIRED,
    evaluate_standing_frontier,
)
from mj.shanten import shanten
from tests.test_legacy_eval import _seq100_game


def _roots():
    game = _seq100_game()
    first = list(game.hands[0])
    first[17] -= 1
    second = list(game.hands[0])
    second[20] -= 1
    return game, (
        StandingRoot("discard-9p", tuple(first), shanten(first, 1)),
        StandingRoot("discard-3s", tuple(second), shanten(second, 1)),
    )


def _future(*, complete=True, stage_a=False, coverage=1.0):
    total = 100
    covered = int(total * coverage)
    return FutureEvaluation(
        complete=complete,
        root_shanten=1,
        future_improve_weight=30,
        future_improve_lower=30,
        future_improve_upper=30 if complete else 30 + total - covered,
        future_ukeire=None if stage_a else 200,
        future_ukeire_mean=None if stage_a else 2.0,
        future_ukeire_mean_denominator=None if stage_a else covered,
        future_ukeire_types=None if stage_a else 100,
        future_ukeire_types_mean=None if stage_a else 1.0,
        future_ukeire_skipped=stage_a,
        covered_weight=covered,
        total_weight=total,
        coverage=coverage,
        search_metrics={"search_phase": (
            "future_shanten" if stage_a else "two_ply")},
    )


class StandingFrontierTests(unittest.TestCase):
    def setUp(self):
        self.game, self.roots = _roots()
        self.visible = tuple(self.game.visible_counts(0))
        self.profile = LegacyTwoPlyProfile.weighted_online(
            kernel="rust",
            max_frontier_candidates=0,
            shape_guard_enabled=False,
            shape_quality_enabled=False,
            shape_quality_guard_enabled=False,
        )

    def _evaluate_with(self, values, profile=None, roots=None):
        roots = tuple(self.roots if roots is None else roots)
        with patch("mj.legacy_eval.weighted_two_ply_frontier", object()), \
             patch("mj.legacy_eval.WEIGHTED_TWO_PLY_KERNEL_VERSION",
                   WEIGHTED_TWO_PLY_KERNEL_REQUIRED), \
             patch("mj.legacy_eval._weighted_native_future_for_frontier",
                   return_value=(values, {}, 0.1)) as native:
            result = evaluate_standing_frontier(
                roots, 1, self.visible, profile or self.profile)
        return result, native

    def test_batch_preserves_stable_ids_and_uses_one_native_frontier(self):
        result, native = self._evaluate_with({
            0: _future(),
            1: _future(),
        })
        self.assertEqual(tuple(result), ("discard-9p", "discard-3s"))
        self.assertTrue(all(value.complete for value in result.values()))
        native.assert_called_once()
        frontier = native.call_args.args[2]
        self.assertEqual([root.tile for root in frontier], [0, 1])
        self.assertEqual([root.hand for root in frontier],
                         [root.hand for root in self.roots])

    def test_offline_adds_private_tie_guards_to_force_stage_b(self):
        values = {index: _future() for index in range(4)}
        offline = LegacyTwoPlyProfile.weighted_offline(
            require_complete=True)
        result, native = self._evaluate_with(values, profile=offline)
        self.assertEqual(tuple(result), ("discard-9p", "discard-3s"))
        self.assertTrue(all(value.complete for value in result.values()))
        frontier = native.call_args.args[2]
        self.assertEqual(len(frontier), 4)
        self.assertEqual(frontier[0].hand, frontier[2].hand)
        self.assertEqual(frontier[1].hand, frontier[3].hand)

    def test_online_singleton_requests_stage_a_partial_without_guard(self):
        root = self.roots[0]
        result, native = self._evaluate_with(
            {0: _future(complete=False, stage_a=True, coverage=0.95)},
            roots=(root,))

        self.assertEqual(tuple(result), (root.stable_id,))
        self.assertTrue(result[root.stable_id].partial_accepted)
        self.assertTrue(result[root.stable_id].future_ukeire_skipped)
        self.assertTrue(native.call_args.kwargs["stage_a_only"])
        frontier = native.call_args.args[2]
        self.assertEqual(len(frontier), 1)
        self.assertEqual(frontier[0].hand, root.hand)

    def test_stage_a_only_safe_partial_stays_stage_a(self):
        result, _native = self._evaluate_with({
            0: _future(complete=False, stage_a=True, coverage=0.95),
            1: _future(complete=False, stage_a=True, coverage=0.95),
        })
        self.assertTrue(all(value.partial_accepted
                            for value in result.values()))
        self.assertTrue(all(value.future_ukeire_skipped
                            for value in result.values()))
        self.assertTrue(all(value.future_ukeire is None
                            for value in result.values()))

    def test_stage_b_safe_partial_is_accepted_at_common_coverage(self):
        result, _native = self._evaluate_with({
            0: _future(complete=False, coverage=0.95),
            1: _future(complete=False, coverage=0.95),
        })
        self.assertTrue(all(value.partial_accepted
                            for value in result.values()))
        self.assertTrue(all(value.future_ukeire_mean == 2.0
                            for value in result.values()))

    def test_low_coverage_discards_the_entire_layer(self):
        result, _native = self._evaluate_with({
            0: FutureEvaluation(**{
                **_future(complete=False, coverage=0.95).__dict__,
                "elapsed_ms": 7.5,
                "nodes": 123,
            }),
            1: _future(complete=False, coverage=0.80),
        })
        self.assertTrue(all(not value.complete for value in result.values()))
        self.assertEqual(
            {value.fallback_reason for value in result.values()},
            {"standing_coverage_insufficient"},
        )
        self.assertTrue(all(value.future_improve_weight is None
                            for value in result.values()))
        self.assertEqual(result["discard-9p"].elapsed_ms, 7.5)
        self.assertEqual(result["discard-9p"].nodes, 123)
        self.assertEqual(result["discard-9p"].coverage, 0.95)

    def test_stage_mismatch_discards_the_entire_layer(self):
        result, _native = self._evaluate_with({
            0: _future(complete=False, stage_a=True, coverage=0.95),
            1: _future(complete=False, stage_a=False, coverage=0.95),
        })
        self.assertEqual(
            {value.fallback_reason for value in result.values()},
            {"standing_stage_mismatch"},
        )

    def test_rust_unavailable_is_explicit_and_does_not_run_python_dfs(self):
        with patch("mj.legacy_eval.weighted_two_ply_frontier", None), \
             patch("mj.legacy_eval.WEIGHTED_TWO_PLY_KERNEL_VERSION", None):
            result = evaluate_standing_frontier(
                self.roots, 1, self.visible, self.profile)
        self.assertEqual(
            {value.fallback_reason for value in result.values()},
            {"native_weighted_kernel_unavailable"},
        )

    def test_old_native_kernel_version_falls_back_before_new_api_call(self):
        with patch("mj.legacy_eval.WEIGHTED_TWO_PLY_KERNEL_VERSION",
                   "rust-weighted-two-ply-v2"), \
             patch("mj.legacy_eval._weighted_native_future_for_frontier",
                   side_effect=AssertionError("stale kernel must not run")):
            result = evaluate_standing_frontier(
                self.roots, 1, self.visible, self.profile)
        self.assertEqual(
            {value.fallback_reason for value in result.values()},
            {"native_weighted_kernel_version_mismatch"},
        )

    def test_public_result_is_independent_of_hidden_hands_and_wall_order(self):
        mirrored = _seq100_game()
        mirrored.hands[1] = [0] * 34
        mirrored.hands[1][0] = 4
        mirrored.wall = list(reversed(mirrored.wall))
        self.assertEqual(mirrored.visible_counts(0), list(self.visible))

        values = {0: _future(), 1: _future()}
        first, _native = self._evaluate_with(values)
        with patch("mj.legacy_eval.weighted_two_ply_frontier", object()), \
             patch("mj.legacy_eval.WEIGHTED_TWO_PLY_KERNEL_VERSION",
                   WEIGHTED_TWO_PLY_KERNEL_REQUIRED), \
             patch("mj.legacy_eval._weighted_native_future_for_frontier",
                   return_value=(values, {}, 0.1)):
            second = evaluate_standing_frontier(
                self.roots, 1, mirrored.visible_counts(0), self.profile)
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
