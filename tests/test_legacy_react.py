"""Contracts for the versioned legacy reaction policy."""

import json
import unittest
from unittest.mock import patch

from mj import bot as bot_mod
from mj.game import PASS, PONG
from mj.legacy_react import (
    LEGACY_REACTION_V1,
    LEGACY_REACTION_V2,
    LEGACY_REACTION_V2_OFFLINE,
    LegacyReactionProfile,
    IncompleteReactionFuture,
    ordinary_u2_gate,
    reaction_tempo,
)
from mj.legacy_eval import FutureEvaluation
from tests.test_bot import _react_game


class LegacyReactionProfileTests(unittest.TestCase):
    def test_v1_is_the_frozen_no_future_profile(self):
        profile = LegacyReactionProfile.v1()
        self.assertEqual(profile.version, LEGACY_REACTION_V1)
        self.assertTrue(profile.enabled)
        self.assertFalse(profile.future_enabled)
        self.assertFalse(profile.tempo_guard)

    def test_online_profile_is_enabled_and_budgeted(self):
        profile = LegacyReactionProfile.v2_online()
        self.assertEqual(profile.version, LEGACY_REACTION_V2)
        self.assertTrue(profile.future_enabled)
        self.assertEqual(profile.future_mode, "weighted")
        self.assertLess(profile.future_soft_budget_ms,
                        profile.future_hard_budget_ms)
        self.assertTrue(profile.allow_partial)
        self.assertEqual(profile.min_partial_coverage, 0.90)
        self.assertTrue(profile.tempo_guard)
        self.assertEqual(profile.continuation_node_budget, 512)
        self.assertEqual(profile.continuation_soft_budget_ms, 8.0)
        self.assertEqual(profile.continuation_hard_budget_ms, 15.0)
        self.assertTrue(profile.enabled)

    def test_offline_profile_requires_complete_future(self):
        profile = LegacyReactionProfile.v2_offline()
        self.assertEqual(profile.version, LEGACY_REACTION_V2_OFFLINE)
        self.assertTrue(profile.future_enabled)
        self.assertFalse(profile.allow_partial)
        self.assertEqual(profile.min_partial_coverage, 1.0)
        self.assertTrue(profile.require_complete)

    def test_fingerprint_covers_version_budget_coverage_and_tempo(self):
        baseline = LegacyReactionProfile.v2_online()
        variants = (
            LegacyReactionProfile.v2_online(version="legacy-react-v2.1"),
            LegacyReactionProfile.v2_online(future_hard_budget_ms=11.0),
            LegacyReactionProfile.v2_online(min_partial_coverage=0.95),
            LegacyReactionProfile.v2_online(tempo_guard=False),
        )
        self.assertTrue(all(
            profile.fingerprint != baseline.fingerprint
            for profile in variants
        ))
        payload = baseline.as_json()
        self.assertEqual(payload["fingerprint"], baseline.fingerprint)
        json.dumps(payload, ensure_ascii=False, allow_nan=False)

    def test_invalid_profile_contracts_fail_loud(self):
        with self.assertRaises(ValueError):
            LegacyReactionProfile.v2_online(
                future_soft_budget_ms=12.0,
                future_hard_budget_ms=10.0,
            )
        with self.assertRaises(ValueError):
            LegacyReactionProfile.v2_offline(allow_partial=True)


def _future(*, improve=10, ukeire=2.0, stage_a=False, usable=True):
    return FutureEvaluation(
        complete=usable,
        future_improve_weight=improve if usable else None,
        future_ukeire_mean=None if stage_a or not usable else ukeire,
        future_ukeire_skipped=stage_a,
    )


class ReactionU2GateTests(unittest.TestCase):
    def test_tempo_metadata_for_all_three_relative_seats(self):
        self.assertEqual(reaction_tempo(1, 0).as_json(), {
            "pass_draw_index": 1,
            "claim_draw_index": 4,
            "tempo_cost": 3,
        })
        self.assertEqual(reaction_tempo(1, 3).tempo_cost, 2)
        self.assertEqual(reaction_tempo(1, 2).tempo_cost, 1)

    def test_stage_b_claim_must_not_be_worse(self):
        self.assertEqual(
            ordinary_u2_gate(_future(), _future(improve=9), 1)[:2],
            (False, "u2_future_worse"),
        )
        self.assertEqual(
            ordinary_u2_gate(_future(), _future(ukeire=1.9), 1)[:2],
            (False, "u2_future_worse"),
        )

    def test_high_tempo_requires_strict_future_gain(self):
        self.assertEqual(
            ordinary_u2_gate(_future(), _future(), 3)[:2],
            (False, "tempo_no_strict_future_gain"),
        )
        self.assertEqual(
            ordinary_u2_gate(_future(), _future(improve=11), 3),
            (True, "u2_not_worse", True),
        )

    def test_low_tempo_allows_equal_future(self):
        self.assertEqual(
            ordinary_u2_gate(_future(), _future(), 1),
            (True, "u2_not_worse", False),
        )

    def test_stage_a_compares_only_future_improve(self):
        self.assertEqual(
            ordinary_u2_gate(
                _future(stage_a=True), _future(improve=11, stage_a=True), 3),
            (True, "u2_not_worse", True),
        )

    def test_stage_mismatch_and_incomplete_are_explicit(self):
        self.assertEqual(
            ordinary_u2_gate(
                _future(stage_a=True), _future(stage_a=False), 1)[1],
            "u2_stage_mismatch",
        )
        self.assertEqual(
            ordinary_u2_gate(_future(), _future(usable=False), 1)[1],
            "u2_incomplete",
        )


class ReactionV2IntegrationTests(unittest.TestCase):
    def _profile(self):
        return LegacyReactionProfile.v2_online(enabled=True)

    def _future_group(self, pass_improve=10, claim_improve=10,
                      pass_ukeire=2.0, claim_ukeire=2.0):
        seen = []

        def evaluate(roots, _locked, _visible, _profile, **_kwargs):
            seen.append(tuple(root.stable_id for root in roots))
            result = {}
            for root in roots:
                is_pass = root.stable_id == "pass"
                result[root.stable_id] = FutureEvaluation(
                    complete=True,
                    root_shanten=root.shanten,
                    future_improve_weight=(pass_improve if is_pass
                                           else claim_improve),
                    future_ukeire_mean=(pass_ukeire if is_pass
                                        else claim_ukeire),
                    future_ukeire_types_mean=1.0,
                    coverage=1.0,
                    elapsed_ms=2.0 if is_pass else 3.0,
                )
            return result
        return seen, evaluate

    def _choose(self, owner, side_effect):
        game = _react_game(
            "33m456m789m12p45pE", owner, 2, mode="claim")
        with patch("mj.legacy_react.evaluate_future_group",
                   side_effect=side_effect):
            return bot_mod._choose_react_evaluated(
                game, 1, game.legal_actions(),
                reaction_profile=self._profile())

    def test_v1_gate_is_still_required(self):
        game = _react_game(
            "333m456m789m12p45p", 0, 2, mode="claim")
        with patch("mj.legacy_react.evaluate_future_group",
                   side_effect=AssertionError("v1 PASS must not run U2")):
            action, evaluation = bot_mod._choose_react_evaluated(
                game, 1, game.legal_actions(),
                reaction_profile=self._profile())
        self.assertEqual(action, PASS)
        self.assertEqual(evaluation["v1_action"], PASS)

    def test_future_worse_vetoes_v1_ukeire_claim(self):
        _seen, evaluate = self._future_group(
            pass_improve=10, claim_improve=9)
        action, evaluation = self._choose(2, evaluate)
        self.assertEqual(action, PASS)
        row = evaluation["candidates"][0]
        self.assertEqual(row["v2_reason"], "u2_future_worse")
        self.assertFalse(row["v2_accepted"])

    def test_complete_post_claim_frontier_is_not_trimmed_at_u1(self):
        seen, evaluate = self._future_group(claim_improve=11)
        action, evaluation = self._choose(2, evaluate)
        self.assertEqual(action, PONG)
        claim_batches = [batch for batch in seen if batch != ("pass",)]
        self.assertEqual(len(claim_batches), 1)
        self.assertGreater(len(claim_batches[0]), 1)
        self.assertTrue(evaluation["u2_eligible"])
        self.assertTrue(evaluation["u2_complete_or_safe_partial"])
        self.assertEqual(evaluation["u2_extra_elapsed_ms"], 5.0)
        self.assertEqual(evaluation["u2_coverage"], 1.0)

    def test_same_hand_locks_tempo_cost_three_two_one(self):
        for owner, expected_cost, expected_action in (
                (0, 3, PASS), (3, 2, PASS), (2, 1, PONG)):
            _seen, evaluate = self._future_group()
            action, evaluation = self._choose(owner, evaluate)
            self.assertEqual(action, expected_action, owner)
            self.assertEqual(evaluation["tempo_cost"], expected_cost)
            row = evaluation["candidates"][0]
            if expected_cost >= 2:
                self.assertEqual(
                    row["v2_reason"], "tempo_no_strict_future_gain")
            else:
                self.assertEqual(row["v2_reason"], "u2_not_worse")

    def test_shanten_drop_and_special_baotou_progress_skip_generic_u2(self):
        shanten_game = _react_game(
            "55m678m123p456p9sE", 0, 4, mode="claim")
        baotou_game = _react_game(
            "33m456m789m123p45p", 0, 2, mode="claim")
        with patch("mj.legacy_react.evaluate_future_group",
                   side_effect=AssertionError("exempt claim must not run U2")):
            shanten_action, shanten_eval = bot_mod._choose_react_evaluated(
                shanten_game, 1, shanten_game.legal_actions(),
                reaction_profile=self._profile())
            baotou_action, baotou_eval = bot_mod._choose_react_evaluated(
                baotou_game, 1, baotou_game.legal_actions(),
                reaction_profile=self._profile())
        self.assertEqual(shanten_action, PONG)
        self.assertEqual(shanten_eval["candidates"][0]["u2_exempt"],
                         "shanten_drop")
        self.assertEqual(baotou_action, PONG)
        self.assertEqual(baotou_eval["candidates"][0]["v2_reason"],
                         "baotou_ukeire_gain")
        self.assertTrue(baotou_eval["candidates"][0]
                        ["u2_special_metric_missing"])

    def test_default_legacy_and_legacy_v2_aliases_route_to_online_v2(self):
        game = _react_game(
            "33m456m789m12p45pE", 2, 2, mode="claim")
        v1_action, v1_eval = bot_mod.choose_action(
            game, 1, evaluator="legacy-v1", return_evaluation=True)
        self.assertEqual(v1_eval["version"], LEGACY_REACTION_V1)

        routes = (
            bot_mod.choose_action(game, 1, return_evaluation=True),
            bot_mod.choose_action(
                game, 1, evaluator="legacy", return_evaluation=True),
            bot_mod.choose_action(
                game, 1, evaluator="legacyV2", return_evaluation=True),
            bot_mod.choose_action(
                game, 1, evaluator="legacy-v2", return_evaluation=True),
        )
        for action, evaluation in routes:
            self.assertIn(action, game.legal_actions())
            self.assertEqual(
                evaluation["reaction_profile"]["version"],
                LEGACY_REACTION_V2)
            self.assertTrue(evaluation["reaction_profile"]["enabled"])
            self.assertNotEqual(
                evaluation.get("u2_fallback_reason"), "profile_disabled")
        self.assertEqual(len({action for action, _ in routes}), 1)
        self.assertIn(v1_action, game.legal_actions())

    def test_online_legacy_alias_incomplete_u2_falls_back_to_v1(self):
        game = _react_game(
            "33m456m789m12p45pE", 2, 2, mode="claim")
        v1_action = bot_mod.choose_action(game, 1, evaluator="legacy-v1")

        def incomplete(roots, *_args, **_kwargs):
            return {root.stable_id: FutureEvaluation(
                complete=False,
                root_shanten=root.shanten,
                fallback_reason="test_incomplete",
            ) for root in roots}

        with patch("mj.legacy_react.evaluate_standing_frontier",
                   side_effect=incomplete):
            action, evaluation = bot_mod.choose_action(
                game, 1, evaluator="legacy", return_evaluation=True)
        self.assertEqual(action, v1_action)
        self.assertEqual(evaluation["v1_action"], v1_action)
        self.assertEqual(evaluation["u2_fallback_reason"], "u2_incomplete")
        self.assertTrue(evaluation["reaction_profile"]["enabled"])

    def test_offline_route_fails_loud_on_incomplete_u2(self):
        game = _react_game(
            "33m456m789m12p45pE", 2, 2, mode="claim")

        def incomplete(roots, *_args, **_kwargs):
            return {root.stable_id: FutureEvaluation(
                complete=False,
                root_shanten=root.shanten,
                fallback_reason="test_incomplete",
            ) for root in roots}

        with patch("mj.legacy_react.evaluate_standing_frontier",
                   side_effect=incomplete):
            with self.assertRaises(IncompleteReactionFuture):
                bot_mod.choose_action(
                    game, 1, evaluator="legacyV2-offline",
                    return_evaluation=True)


if __name__ == "__main__":
    unittest.main()
