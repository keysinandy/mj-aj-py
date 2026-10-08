"""Frozen legacyV2 contract before adding BigHandIntent."""

import json
import os
import unittest
from dataclasses import replace
from unittest.mock import patch

from mj.bot import choose_action, choose_discard
from mj.game import Game
from mj.legacy_eval import (
    CHIITOI,
    LUXURY_CHIITOI,
    WHITE_RICH,
    FutureEvaluation,
    LegacyRootCandidate,
    LegacyTwoPlyProfile,
    _BudgetExceeded,
    _NativeKernelUnavailable,
    _apply_shape_guard,
    _apply_big_hand_guard,
    _big_hand_route_reason,
    _can_big_hand_override,
    _legacy_speed_best,
    evaluate_legacy_two_ply,
)
from mj.shanten import shanten, ukeire
from mj.shape_quality import standing_shape_quality
from mj.tiles import W, counts
from scripts.legacy_v2_big_hand_grid_scan import pair_assignment

try:
    from tests.test_legacy_eval import _seq100_game
except ImportError:  # pragma: no cover
    from test_legacy_eval import _seq100_game


FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures",
                       "legacy_v2_big_hand_baseline.json")


class TestLegacyV2BigHandBaseline(unittest.TestCase):
    def test_score_pair_assignment_balances_dealer_independently_of_hero_seats(self):
        assignments = [pair_assignment(index) for index in range(8)]
        self.assertEqual(sum(dealer in seats for dealer, seats in assignments), 4)
        for dealer in range(4):
            seats = [tuple(sorted(heroes)) for d, heroes in assignments if d == dealer]
            self.assertEqual(set(seats), {(0, 2), (1, 3)})

    def test_seeded_discard_outputs_match_frozen_legacy_v2(self):
        with open(FIXTURE, encoding="utf-8") as handle:
            baseline = json.load(handle)["baseline"]
        for row in baseline["fixtures"]:
            with self.subTest(case=row["id"]):
                game = Game(seed=row["seed"], dealer=row["dealer"])
                hand = game.hands[game.current_seat()]
                best_shanten = min(
                    shanten([count - (index == tile)
                             for index, count in enumerate(hand)],
                            len(game.melds[game.current_seat()]))
                    for tile, count in enumerate(hand) if count
                )
                action, evaluation = choose_action(
                    game, game.current_seat(),
                    evaluator="legacy-v2-baseline",
                    return_evaluation=True)
                self.assertEqual(best_shanten, row["best_shanten"])
                self.assertEqual(action, row["action"])
                self.assertEqual(evaluation["level"], row["level"])
                self.assertEqual(evaluation["complete"], row["complete"])
                self.assertEqual(len(evaluation["candidates"]),
                                 row["root_candidates"])
                self.assertEqual(
                    evaluation.get("search_metrics", {}).get(
                        "root_candidates", 1),
                    row["frontier_roots"],
                )
                self.assertEqual(evaluation["big_hand_phase"], "disabled")
                self.assertFalse(
                    evaluation["big_hand_guard"]["enabled"])
        rich = next(row for row in baseline["fixtures"]
                    if row["id"] == "white_rich_best_shanten_1")
        game = Game(seed=rich["seed"], dealer=rich["dealer"])
        self.assertGreaterEqual(game.hands[0][W], 2)
        self.assertNotEqual(rich["action"], W)

    def test_profile_fields_are_fingerprinted_and_phase_b_is_opt_in(self):
        default = LegacyTwoPlyProfile.weighted_online()
        phase_a = LegacyTwoPlyProfile.weighted_online(big_hand_enabled=True)
        phase_b = LegacyTwoPlyProfile.weighted_online(
            big_hand_enabled=True, big_hand_plus_one_enabled=True)
        self.assertFalse(default.big_hand_enabled)
        self.assertFalse(default.big_hand_plus_one_enabled)
        self.assertNotEqual(default.fingerprint, phase_a.fingerprint)
        self.assertNotEqual(phase_a.fingerprint, phase_b.fingerprint)
        self.assertEqual(default.as_json()["big_hand"]["min_live"], 24)
        for field, value in (
                ("big_hand_plus_one_parallel", True),
                ("big_hand_plus_one_luxury_noninferior", True),
                ("big_hand_plus_one_min_strength", "MEDIUM")):
            with self.subTest(field=field):
                changed = replace(default, **{field: value})
                self.assertNotEqual(default.fingerprint, changed.fingerprint)

    def test_parallel_same_and_plus_one_respect_cap_and_preserve_speed_slots(self):
        primary = LegacyRootCandidate(
            tile=3, hand=(0,) * 34, shanten=1, current_ukeire=12,
            shanten_verified=True, speed_eligible=True)
        other = replace(primary, tile=6, current_ukeire=11)
        same = replace(primary, tile=4, current_ukeire=8,
                       intent_kinds=(CHIITOI, LUXURY_CHIITOI),
                       intent_strength="STRONG", chiitoi_shanten=1,
                       luxury_upgrade_live=1)
        plus = replace(same, tile=5, shanten=2, speed_eligible=False,
                       live_wall=40, max_opponent_melds=0)
        profile = LegacyTwoPlyProfile.weighted_online(
            big_hand_enabled=True, big_hand_plus_one_enabled=True,
            big_hand_plus_one_parallel=True)
        for speed_roots in ((primary,), (primary, other)):
            with self.subTest(speed_slots=len(speed_roots)), \
                    patch("mj.legacy_eval._weighted_native_ready", return_value=True):
                enriched = (*speed_roots, same, plus)
                diagnostics = tuple((root, root in speed_roots, ()) for root in enriched)
                result = _apply_big_hand_guard(
                    enriched, speed_roots, diagnostics, profile, 0)
                frontier, _diag, _admitted, guard, challenger = result[:5]
                self.assertEqual(len(frontier), 3)
                self.assertTrue(set(root.tile for root in speed_roots).issubset(
                    root.tile for root in frontier))
                if len(speed_roots) == 1:
                    self.assertEqual(challenger, plus.tile)
                    self.assertIn(plus.tile, guard["admitted_tiles"])
                else:
                    self.assertIsNone(challenger)
                    self.assertEqual(guard["candidate_gate_reasons"][str(plus.tile)],
                                     "frontier_cap_no_challenger_slot")

    def test_same_shanten_guard_admits_one_route_and_ignores_dead_luxury(self):
        primary = LegacyRootCandidate(
            tile=3, hand=(0,) * 34, shanten=1, shape_loss=20,
            current_ukeire=12, shanten_verified=True)
        live = LegacyRootCandidate(
            tile=4, hand=(0,) * 34, shanten=1, shape_loss=0,
            current_ukeire=8, shanten_verified=True,
            intent_kinds=(CHIITOI, LUXURY_CHIITOI),
            intent_strength="STRONG", chiitoi_shanten=1,
            luxury_upgrade_live=1, luxury_upgrade_tiles=(4,),
            speed_eligible=True)
        dead = LegacyRootCandidate(
            tile=5, hand=(0,) * 34, shanten=1, shape_loss=0,
            current_ukeire=8, shanten_verified=True,
            intent_kinds=(CHIITOI, LUXURY_CHIITOI),
            intent_strength="STRONG", chiitoi_shanten=1,
            speed_eligible=True)
        profile = LegacyTwoPlyProfile.weighted_online(
            big_hand_enabled=True, big_hand_plus_one_enabled=True)
        with patch("mj.legacy_eval._weighted_native_ready", return_value=True):
            (frontier, diagnostics, admitted, guard, challenger, phase,
             _dropped, speed_winner) = _apply_big_hand_guard(
                (primary, live, dead),
                (primary,),
                ((primary, True, ()), (live, False, ("current_ukeire_frontier",)),
                 (dead, False, ("current_ukeire_frontier",))),
                profile, 0)
        self.assertEqual([root.tile for root in frontier], [3, 4])
        self.assertEqual(admitted[4], "big_hand_guard")
        self.assertEqual(challenger, None)
        self.assertEqual(phase, "same-shanten")
        self.assertEqual(speed_winner, 3)
        self.assertEqual(guard["selected_tile"], 4)
        reasons = {root.tile: root.big_hand_gate_reason
                   for root, _eligible, _missing in diagnostics}
        self.assertEqual(reasons[5], "no_same_shanten_strong_route")

    def test_phase_b_admits_only_best_shanten_plus_one_under_public_guards(self):
        speed = LegacyRootCandidate(
            tile=3, hand=(0,) * 34, shanten=0, current_ukeire=8,
            shanten_verified=True, speed_eligible=True)
        plus_one = LegacyRootCandidate(
            tile=4, hand=(0,) * 34, shanten=1, current_ukeire=5,
            shanten_verified=True, speed_eligible=False,
            intent_kinds=(CHIITOI, LUXURY_CHIITOI),
            intent_strength="STRONG", chiitoi_shanten=1,
            luxury_upgrade_live=2, live_wall=40, max_opponent_melds=0)
        too_slow = LegacyRootCandidate(
            tile=5, hand=(0,) * 34, shanten=2, current_ukeire=20,
            shanten_verified=True, speed_eligible=False,
            intent_kinds=(CHIITOI, LUXURY_CHIITOI),
            intent_strength="STRONG", chiitoi_shanten=1,
            luxury_upgrade_live=3, live_wall=40, max_opponent_melds=0)
        profile = LegacyTwoPlyProfile.weighted_online(
            big_hand_enabled=True, big_hand_plus_one_enabled=True)
        with patch("mj.legacy_eval._weighted_native_ready", return_value=True):
            (frontier, _diagnostics, admitted, guard, challenger, phase,
             _dropped, speed_winner) = _apply_big_hand_guard(
                (speed, plus_one, too_slow), (speed,),
                ((speed, True, ()), (plus_one, False, ("shanten_regression",)),
                 (too_slow, False, ("shanten_regression",))),
                profile, 0)
        self.assertEqual([root.tile for root in frontier], [3, 4])
        self.assertEqual(admitted[4], "big_hand_guard")
        self.assertEqual(challenger, 4)
        self.assertEqual(phase, "plus-one")
        self.assertEqual(speed_winner, 3)
        self.assertEqual(guard["candidate_gate_reasons"]["5"],
                         "shanten_regression_too_large")

    def test_phase_b_cannot_evict_a_speed_root_when_frontier_is_full(self):
        speed_roots = (
            LegacyRootCandidate(
                tile=3, hand=(0,) * 34, shanten=0, current_ukeire=20,
                shanten_verified=True, speed_eligible=True),
            LegacyRootCandidate(
                tile=4, hand=(0,) * 34, shanten=0, current_ukeire=12,
                shanten_verified=True, speed_eligible=True),
            LegacyRootCandidate(
                tile=5, hand=(0,) * 34, shanten=0, current_ukeire=8,
                shanten_verified=True, speed_eligible=True),
        )
        plus_one = LegacyRootCandidate(
            tile=6, hand=(0,) * 34, shanten=1, current_ukeire=7,
            shanten_verified=True, speed_eligible=False,
            intent_kinds=(CHIITOI, WHITE_RICH),
            intent_strength="STRONG", chiitoi_shanten=1,
            pair_units=4, wild_count=2, live_wall=40,
            max_opponent_melds=0)
        enriched = (*speed_roots, plus_one)
        diagnostics = tuple(
            (root, root.shanten == 0,
             () if root.shanten == 0 else ("shanten_regression",))
            for root in enriched
        )
        profile = LegacyTwoPlyProfile.weighted_online(
            big_hand_enabled=True, big_hand_plus_one_enabled=True,
            max_frontier_candidates=3)
        with patch("mj.legacy_eval._weighted_native_ready", return_value=True):
            (frontier, updated, admitted_by, guard, challenger, phase,
             dropped, speed_winner) = _apply_big_hand_guard(
                 enriched, speed_roots, diagnostics, profile, 0)

        self.assertEqual(frontier, speed_roots)
        self.assertIsNone(challenger)
        self.assertEqual(phase, "plus-one")
        self.assertEqual(speed_winner, 3)
        self.assertEqual(admitted_by, {3: "primary", 4: "primary",
                                       5: "primary"})
        self.assertEqual(dropped, ())
        self.assertEqual(guard["skipped_reason"],
                         "frontier_cap_no_challenger_slot")
        rejected = next(root for root, _eligible, _missing in updated
                        if root.tile == plus_one.tile)
        self.assertEqual(rejected.big_hand_gate_reason,
                         "frontier_cap_no_challenger_slot")

    def test_shape_and_big_hand_guards_share_the_three_root_cap(self):
        primary_shape = standing_shape_quality(counts("12s"))
        admitted_shape = standing_shape_quality(counts("23s"))
        primary = LegacyRootCandidate(
            tile=3, hand=(0,) * 34, shanten=1, shape_loss=20,
            current_ukeire=12, shanten_verified=True,
            standing_shape_quality=primary_shape.encoded,
            standing_shape_signature=primary_shape.signature,
            shape_quality_version=primary_shape.version)
        shape_root = LegacyRootCandidate(
            tile=4, hand=(0,) * 34, shanten=1, shape_loss=0,
            current_ukeire=11, shanten_verified=True,
            standing_shape_quality=admitted_shape.encoded,
            standing_shape_signature=admitted_shape.signature,
            shape_quality_version=admitted_shape.version)
        intent_root = LegacyRootCandidate(
            tile=5, hand=(0,) * 34, shanten=1, shape_loss=1,
            current_ukeire=9, shanten_verified=True,
            intent_kinds=(CHIITOI, LUXURY_CHIITOI),
            intent_strength="STRONG", chiitoi_shanten=1,
            luxury_upgrade_live=2, luxury_upgrade_tiles=(5,))
        profile = LegacyTwoPlyProfile.weighted_online(
            big_hand_enabled=True, max_frontier_candidates=3,
            shape_quality_enabled=True, shape_quality_stage="root",
            shape_quality_guard_enabled=True)
        shape_diagnostics = (
            (primary, True, ()),
            (shape_root, False, ("current_ukeire_frontier",)),
            (intent_root, False, ("current_ukeire_frontier",)),
        )
        with patch("mj.legacy_eval._weighted_native_ready", return_value=True):
            shaped, diagnostics, shape_guard, admitted_by = _apply_shape_guard(
                (primary,), shape_diagnostics, profile)
            self.assertEqual([root.tile for root in shaped], [3, 4])
            (frontier, updated, admitted_by, big_guard, _challenger, _phase,
             _dropped, _winner) = _apply_big_hand_guard(
                (primary, shape_root, intent_root), shaped, diagnostics,
                profile, 0, admitted_by)
        self.assertTrue(shape_guard["admitted_tiles"])
        self.assertEqual([root.tile for root in frontier], [3, 4, 5])
        self.assertLessEqual(len(frontier), profile.max_frontier_candidates)
        self.assertEqual(admitted_by[4], "shape_guard")
        self.assertEqual(admitted_by[5], "big_hand_guard")
        self.assertEqual(big_guard["admitted_tiles"], [5])

    def test_white_rich_plus_chiitoi_is_a_strong_plus_one_route(self):
        speed = LegacyRootCandidate(
            tile=3, hand=(0,) * 34, shanten=0,
            luxury_groups=0, luxury_upgrade_live=0,
            wild_count=0, pair_units=2)
        candidate = LegacyRootCandidate(
            tile=4, hand=(0,) * 34, shanten=1,
            intent_kinds=(CHIITOI, WHITE_RICH),
            intent_strength="STRONG", chiitoi_shanten=1,
            pair_units=4, wild_count=2)
        profile = LegacyTwoPlyProfile.weighted_online(
            shape_quality_enabled=False,
            shape_quality_guard_enabled=False)
        self.assertIsNone(_big_hand_route_reason(
            candidate, profile, speed_winner=speed, locked=0,
            plus_one=True))
        self.assertEqual(_big_hand_route_reason(
            candidate, profile, speed_winner=speed, locked=1,
            plus_one=True), "locked_hand")

    def test_override_gate_is_conservative_and_requires_complete_future(self):
        speed = LegacyRootCandidate(
            tile=3, hand=(0,) * 34, shanten=0, current_ukeire=10,
            luxury_upgrade_live=0)
        challenger = LegacyRootCandidate(
            tile=4, hand=(0,) * 34, shanten=1, current_ukeire=8,
            intent_kinds=(CHIITOI, LUXURY_CHIITOI, WHITE_RICH),
            intent_strength="STRONG", chiitoi_shanten=1,
            pair_units=5, luxury_upgrade_live=2, wild_count=2,
            live_wall=40, max_opponent_melds=0)
        profile = LegacyTwoPlyProfile.weighted_online()
        full = FutureEvaluation(
            complete=True, future_improve_weight=12, coverage=1.0)
        accepted, reason = _can_big_hand_override(
            challenger, speed, full, profile, 0)
        self.assertTrue(accepted)
        self.assertEqual(reason, "strong_intent_override")
        incomplete = FutureEvaluation(complete=False, future_improve_weight=12)
        accepted, reason = _can_big_hand_override(
            challenger, speed, incomplete, profile, 0)
        self.assertFalse(accepted)
        self.assertEqual(reason, "challenger_future_incomplete")
        weak_ukeire = LegacyRootCandidate(
            **{**challenger.__dict__, "current_ukeire": 0})
        accepted, reason = _can_big_hand_override(
            weak_ukeire, speed, full, profile, 0)
        self.assertFalse(accepted)
        self.assertEqual(reason, "ukeire_absolute_floor")
        late = LegacyRootCandidate(
            **{**challenger.__dict__, "current_ukeire": 8,
               "live_wall": 20})
        accepted, reason = _can_big_hand_override(
            late, speed, full, profile, 0)
        self.assertFalse(accepted)
        self.assertEqual(reason, "live_wall_guard")
        unknown = LegacyRootCandidate(
            **{**challenger.__dict__, "live_wall": None})
        accepted, reason = _can_big_hand_override(
            unknown, speed, full, profile, 0)
        self.assertFalse(accepted)
        self.assertEqual(reason, "live_wall_guard")
        accepted, reason = _can_big_hand_override(
            challenger, speed, full, profile, 1)
        self.assertFalse(accepted)
        self.assertEqual(reason, "locked_hand")

    def test_native_failure_with_plus_one_root_returns_frozen_speed_best(self):
        game = Game(seed=1)
        seat = game.current_seat()
        locked = len(game.melds[seat])
        visible = game.visible_counts(seat)
        hand = game.hands[seat]
        candidates = []
        for tile, count in enumerate(hand):
            if not count:
                continue
            post = list(hand)
            post[tile] -= 1
            value = shanten(post, locked)
            candidates.append((tile, post, value))
        best_s = min(value for _tile, _post, value in candidates)
        roots = []
        challenger_tile = None
        for tile, post, value in candidates:
            metadata = {}
            if value == best_s:
                metadata["speed_eligible"] = True
            elif value == best_s + 1 and challenger_tile is None:
                challenger_tile = tile
                metadata.update({
                    "speed_eligible": False,
                    "intent_kinds": (CHIITOI, LUXURY_CHIITOI),
                    "intent_strength": "STRONG",
                    "chiitoi_shanten": 1,
                    "luxury_upgrade_live": 2,
                    "luxury_upgrade_tiles": (tile,),
                    "live_wall": game.live_wall_left(),
                    "max_opponent_melds": 0,
                })
            else:
                metadata["speed_eligible"] = value == best_s
            roots.append(LegacyRootCandidate(
                tile=tile, hand=tuple(post), shanten=value,
                shanten_verified=True,
                current_ukeire=ukeire(post, locked, visible)[2],
                **metadata))
        self.assertIsNotNone(challenger_tile)
        speed_roots = [root for root in roots if root.shanten == best_s]
        challenger = next(root for root in roots
                          if root.tile == challenger_tile)
        roots = (*speed_roots[:2], challenger)
        frozen_legacy = _legacy_speed_best(roots)
        profile = LegacyTwoPlyProfile.weighted_online(
            big_hand_enabled=True, big_hand_plus_one_enabled=True)
        with patch("mj.legacy_eval._weighted_native_future_for_frontier",
                   side_effect=_NativeKernelUnavailable("test_kernel_failure")):
            selected, explanation = evaluate_legacy_two_ply(
                game, seat, roots, locked, visible, profile)
        evaluation = explanation.as_json()
        self.assertEqual(evaluation["big_hand_challenger"], challenger_tile)
        self.assertEqual(selected, frozen_legacy)
        self.assertEqual(selected, evaluation["legacy_best"])
        self.assertFalse(evaluation["big_hand_override"])
        self.assertEqual(evaluation["big_hand_override_reason"],
                         "test_kernel_failure")
        self.assertLessEqual(
            evaluation["search_metrics"].get("root_candidates", 3), 3)

    def test_complete_plus_one_override_is_independent_of_speed_comparator(self):
        game = Game(seed=1)
        seat = game.current_seat()
        locked = len(game.melds[seat])
        visible = game.visible_counts(seat)
        hand = game.hands[seat]
        candidates = []
        for tile, count in enumerate(hand):
            if not count:
                continue
            post = list(hand)
            post[tile] -= 1
            candidates.append((tile, post, shanten(post, locked)))
        best_s = min(value for _tile, _post, value in candidates)
        roots = []
        challenger_tile = None
        for tile, post, value in candidates:
            metadata = {"speed_eligible": value == best_s}
            if value == best_s + 1 and challenger_tile is None:
                challenger_tile = tile
                metadata.update({
                    "speed_eligible": False,
                    "intent_kinds": (CHIITOI, LUXURY_CHIITOI),
                    "intent_strength": "STRONG",
                    "chiitoi_shanten": 1,
                    "luxury_upgrade_live": 2,
                    "luxury_upgrade_tiles": (tile,),
                    "live_wall": game.live_wall_left(),
                    "max_opponent_melds": 0,
                })
            roots.append(LegacyRootCandidate(
                tile=tile, hand=tuple(post), shanten=value,
                shanten_verified=True,
                current_ukeire=ukeire(post, locked, visible)[2],
                **metadata))
        self.assertIsNotNone(challenger_tile)
        speed_roots = [root for root in roots if root.shanten == best_s]
        challenger = next(root for root in roots
                          if root.tile == challenger_tile)
        roots = (*speed_roots[:2], challenger)
        profile = LegacyTwoPlyProfile.weighted_online(
            big_hand_enabled=True, big_hand_plus_one_enabled=True,
            big_hand_min_ukeire=0, big_hand_max_ukeire_loss=100)

        def complete_frontier(*args, **_kwargs):
            frontier = args[2]
            values = {
                root.tile: FutureEvaluation(
                    complete=True, root_shanten=root.shanten,
                    future_improve_weight=(1000 if root.tile == challenger_tile
                                           else 1),
                    future_improve_lower=(1000 if root.tile == challenger_tile
                                          else 1),
                    future_improve_upper=(1000 if root.tile == challenger_tile
                                          else 1),
                    future_ukeire=1, future_ukeire_mean=1.0,
                    future_ukeire_types=1, future_ukeire_types_mean=1.0,
                    covered_weight=100, total_weight=100, coverage=1.0)
                for root in frontier
            }
            return values, {"root_candidates": len(frontier),
                            "search_phase": "two_ply",
                            "child_nodes": len(frontier)}, 0.05

        with patch("mj.legacy_eval._weighted_native_ready", return_value=True), \
                patch("mj.legacy_eval._weighted_native_future_for_frontier",
                      side_effect=complete_frontier):
            selected, explanation = evaluate_legacy_two_ply(
                game, seat, roots, locked, visible, profile)
        info = explanation.as_json()
        self.assertEqual(info["big_hand_challenger"], challenger_tile)
        self.assertTrue(info["big_hand_override"])
        self.assertEqual(info["big_hand_override_reason"],
                         "strong_intent_override")
        self.assertEqual(info["speed_winner"], info["legacy_best"])
        self.assertEqual(selected, challenger_tile)
        self.assertLessEqual(info["search_metrics"]["root_candidates"], 3)

        # Native cross-shanten rows need the opt-in Python completion path.
        # A failed or disabled completion must keep the frozen speed fallback.
        scenarios = (
            ("disabled", False, "incomplete", None, False),
            ("complete", True, "incomplete", None, True),
            ("speed_partial", True, "partial_speed", None, True),
            ("uncommitted_speed", True, "uncommitted_speed", None, False),
            ("budget", True, "incomplete", _BudgetExceeded("hard_deadline"), False),
            ("native_failure", True, "failure", None, False),
            ("already_complete", True, "complete", None, True),
        )
        for name, parallel, native_state, topup_error, expected_override in scenarios:
            with self.subTest(topup=name):
                def native_frontier(*args, **kwargs):
                    if native_state == "failure":
                        raise _NativeKernelUnavailable("test_kernel_failure")
                    values, metrics, elapsed = complete_frontier(*args, **kwargs)
                    if native_state == "incomplete" and challenger_tile in values:
                        values[challenger_tile] = replace(
                            values[challenger_tile], complete=False)
                    if native_state == "uncommitted_speed":
                        values.pop(next(iter(values)))
                    if native_state == "partial_speed":
                        for index, tile in enumerate(values):
                            values[tile] = replace(values[tile], complete=False,
                                future_improve_lower=2 if index == 0 else 0,
                                future_improve_upper=2 if index == 0 else 1)
                    return values, metrics, elapsed

                full_future = complete_frontier(None, None, roots)[0][challenger_tile]
                with patch("mj.legacy_eval._weighted_native_ready", return_value=True), \
                        patch("mj.legacy_eval._weighted_native_future_for_frontier",
                              side_effect=native_frontier), \
                        patch("mj.legacy_eval._weighted_future_for_root",
                              return_value=full_future,
                              side_effect=topup_error) as topup:
                    chosen, result = evaluate_legacy_two_ply(
                        game, seat, roots, locked, visible,
                        replace(profile, big_hand_plus_one_parallel=parallel))
                info = result.as_json()
                expected_call = parallel and native_state != "failure"
                self.assertEqual(topup.call_count, int(expected_call))
                self.assertEqual(info["big_hand_override"], expected_override)
                self.assertEqual(chosen, challenger_tile if expected_override
                                 else info["legacy_best"])
                if expected_call:
                    self.assertEqual(topup.call_args.args[2].tile, challenger_tile)
                    self.assertLess(topup.call_args.args[5].hard_budget_ms,
                                    profile.hard_budget_ms)
                    metrics = info["search_metrics"]["big_hand_challenger_topup"]
                    self.assertEqual(metrics["complete"], topup_error is None)
                    self.assertGreaterEqual(info["search_metrics"]["elapsed_ms"],
                                            metrics["elapsed_ms"])

    def test_freeze_and_singleton_frontier_baseline(self):
        game = _seq100_game()
        game.freeze = 2
        game.freezer = 2
        game.drawn[0] = 7
        action, info = choose_discard(
            game, 0, return_info=True,
            profile=LegacyTwoPlyProfile.weighted_online())
        self.assertEqual(action, 7)
        self.assertEqual(info["short_circuit_reason"], "frontier_singleton")
        self.assertEqual(info["search_metrics"]["root_candidates"], 1)
        self.assertEqual([row["tile"] for row in info["candidates"]
                          if row.get("admitted_by") == "primary"], [7])

    def test_shape_guard_expansion_baseline_contract(self):
        primary = LegacyRootCandidate(
            tile=3, hand=(0,) * 34, shanten=1, shape_loss=20,
            current_ukeire=12, current_ukeire_tiles=(1, 2, 3),
            shanten_verified=True)
        guarded = LegacyRootCandidate(
            tile=4, hand=(0,) * 34, shanten=1, shape_loss=0,
            current_ukeire=11, current_ukeire_tiles=(4, 5),
            shanten_verified=True)
        diagnostics = ((primary, True, ()),
                       (guarded, False, ("current_ukeire_frontier",)))
        profile = LegacyTwoPlyProfile.weighted_online(
            shape_quality_enabled=False,
            shape_quality_guard_enabled=False)
        with patch("mj.legacy_eval._weighted_native_ready", return_value=True):
            frontier, _diagnostics, guard, admitted_by = _apply_shape_guard(
                (primary,), diagnostics, profile)
        self.assertEqual([root.tile for root in frontier], [3, 4])
        self.assertEqual(guard["admitted_tiles"], [4])
        self.assertEqual(admitted_by[4], "shape_guard")

    def test_kernel_unavailable_and_budget_failure_keep_legacy_fallback(self):
        profile = LegacyTwoPlyProfile.weighted_online()
        with patch("mj.legacy_eval.weighted_two_ply_frontier", None):
            unavailable_action, unavailable = choose_discard(
                _seq100_game(), 0, return_info=True, profile=profile)
        self.assertFalse(unavailable["complete"])
        self.assertEqual(unavailable["fallback_reason"],
                         "native_weighted_kernel_unavailable")
        self.assertEqual(unavailable_action, unavailable["legacy_best"])

        bounded = LegacyTwoPlyProfile.weighted_online(
            node_budget=0, time_budget_ms=1000.0,
            soft_budget_ms=1000.0, hard_budget_ms=1000.0)
        budget_action, budget = choose_discard(
            _seq100_game(), 0, return_info=True, profile=bounded)
        self.assertFalse(budget["complete"])
        self.assertIsNotNone(budget["fallback_reason"])
        self.assertEqual(budget_action, budget["legacy_best"])


if __name__ == "__main__":
    unittest.main()
