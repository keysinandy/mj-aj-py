"""Focused contracts for reward envelopes and paired teacher racing."""

import json
import unittest
from unittest.mock import patch

from mj.game import Game
from mj.decision.context import PublicDecisionContext
from mj.decision.fast_ev import choose_game_action, evaluate_discard_context
from mj.decision.profile import ProfileSpec
from mj.decision.score_value import (
    REWARD_ENVELOPE_VERSION, RewardEnvelope, reward_envelope,
)
from mj.rollout.evaluator import PairedTeacher, pair_interval
from mj.rollout.simulator import RolloutOutcome
from mj.rollout.teacher_data import teacher_artifact


class TightBoundRacingTests(unittest.TestCase):
    def _context(self, seed=101, count=3):
        game = Game(seed=seed, dealer=0, base=1)
        context = PublicDecisionContext.from_game(game, game.current_seat())
        return context.replace(
            legal_actions=context.legal_discards[:count],
            chain_counts=tuple(int(value) for value in game.chain),
            chain_piao_counts=tuple(int(value) for value in game.chain_piao),
            rollout_valid=True, missing_fields=(), unsupported=())

    def test_envelope_is_versioned_candidate_specific_and_signed(self):
        context = self._context(count=2)
        envelope = reward_envelope(context, context.legal_actions[0])
        self.assertEqual(envelope.version, REWARD_ENVELOPE_VERSION)
        self.assertEqual(envelope.candidate, context.legal_actions[0])
        self.assertGreaterEqual(envelope.fast_upper, 0)
        self.assertLess(envelope.rollout_lower, 0)
        self.assertGreaterEqual(
            envelope.rollout_abs,
            max(abs(envelope.rollout_lower), abs(envelope.rollout_upper)))
        self.assertEqual(envelope["certificate_fingerprint"],
                         envelope.certificate.fingerprint)
        self.assertEqual(len(envelope.components["rollout_winner_bounds"]), 4)
        self.assertIn("fast_multiplier_max", envelope.components)

    def test_root_transition_distinguishes_plain_discard_and_piao(self):
        from mj.tiles import W

        hand = [0] * 34
        for tile, count in ((0, 4), (1, 3), (2, 3), (3, 3), (W, 1)):
            hand[tile] = count
        context = PublicDecisionContext(
            dealer=0, base=1, you_cai_bi_kao=False, hand=hand,
            visible=hand, legal_actions=(0, W), live_wall=8,
            chain_count=2, chain_piao=1)
        # The fixture isolates the root transition; structural reachability is
        # covered by the rule implementation and does not read a sampled world.
        with patch("mj.decision.score_value.is_baotou", return_value=True):
            plain = reward_envelope(context, 0)
            piao = reward_envelope(context, W)
        self.assertEqual(plain.components["root"]["post_chain"], 0)
        self.assertEqual(plain.components["root"]["post_chain_piao"], 0)
        self.assertEqual(piao.components["root"]["post_chain"], 3)
        self.assertEqual(piao.components["root"]["post_chain_piao"], 2)
        self.assertGreater(piao.components["chain_max"],
                           plain.components["chain_max"])

    def test_existing_pong_adds_upgrade_capacity_without_extra_slot(self):
        hand = [0] * 34
        hand[1] = 1
        hand[2] = 3
        hand[3] = 3
        hand[4] = 3
        hand[5] = 1
        visible = list(hand)
        visible[0] += 3
        context = PublicDecisionContext(
            dealer=0, base=1, you_cai_bi_kao=False, hand=hand,
            visible=visible, locked=1, melds=((("pong", 0),), (), (), ()),
            legal_actions=(1,), live_wall=8, chain_count=0, chain_piao=0)
        envelope = reward_envelope(context, 1)
        self.assertEqual(envelope.components["existing_pong_count"], 1)
        self.assertEqual(envelope.components["kong_slots_max"], 4)

    def test_unknown_opponent_chain_keeps_fast_separate_from_rollout(self):
        context = self._context(count=2).replace(
            chain_counts=(0, None, None, None),
            chain_piao_counts=(0, None, None, None))
        envelope = reward_envelope(context, context.legal_actions[0])
        self.assertIsNotNone(envelope.fast_upper)
        self.assertIsNone(envelope.rollout_abs)
        self.assertEqual(envelope.as_json()["fast_status"], "known")
        self.assertEqual(envelope.as_json()["rollout_status"], "unknown")
        self.assertTrue(envelope.as_json()["rollout_missing"])

    def test_invalid_special_only_candidate_never_gets_legacy_bound(self):
        context = self._context(count=2).replace(legal_actions=(-75,))
        envelope = reward_envelope(
            context, context.legal_discards[0], allow_legacy_fallback=True)
        self.assertEqual(envelope.mode, "unknown")
        self.assertIsNone(envelope.fast_upper)

    def test_explicit_legacy_fallback_is_not_a_new_pruning_certificate(self):
        context = self._context(count=1).replace(dealer=None)
        envelope = reward_envelope(
            context, context.legal_actions[0], allow_legacy_fallback=True)
        self.assertEqual(envelope.mode, "legacy_conservative_fallback")
        self.assertFalse(envelope.safe_for_fast_pruning)
        self.assertEqual(envelope.certificate.proof,
                         "theoretical_reward_bound")

    def test_small_enumerated_future_draw_rewards_fit_fast_upper(self):
        hand = [0] * 34
        for tile in (0, 1, 2, 0, 1, 2, 0, 1, 2, 3, 3, 3, 4, 4):
            hand[tile] += 1
        context = PublicDecisionContext(
            dealer=0, base=1, you_cai_bi_kao=False, hand=hand,
            visible=hand, legal_actions=(4,), live_wall=8,
            chain_count=0, chain_piao=0)
        envelope = reward_envelope(context, 4)
        from mj.decision.fast_ev import _apply_draw
        from mj.decision.score_value import ScoreValue

        scorer = ScoreValue(0, 1, False, 0)
        root_hand, chain, piao, _ = scorer.discard(
            context.hand, 4, context.locked, context.chain_count,
            context.chain_piao)
        for tile, left in enumerate(context.remaining):
            if left <= 0:
                continue
            drawn, _remaining = _apply_draw(root_hand, context.remaining, tile)
            breakdown = scorer.hu(
                drawn, scorer.standing_before_draw(drawn, tile),
                context.locked, tile, False, chain, piao)
            if breakdown.legal:
                self.assertGreaterEqual(breakdown.reward, 0)
                self.assertLessEqual(breakdown.reward, envelope.fast_upper)

    def test_fast_explanation_contains_each_envelope_and_profile_binding(self):
        context = self._context(count=2)
        profile = ProfileSpec.shape_v2_discard(
            node_budget=100000, time_budget_ms=1000)
        result = evaluate_discard_context(context, profile, level="EV2")
        self.assertEqual(result.bound_version, REWARD_ENVELOPE_VERSION)
        self.assertTrue(all(candidate.reward_envelope for candidate in
                            result.candidates))
        data = result.as_json()
        self.assertEqual(set(data["reward_bounds"]),
                         {str(action) for action in context.legal_actions})
        self.assertTrue(all(candidate["certificate_fingerprint"]
                            for candidate in data["candidates"]))
        self.assertNotEqual(
            profile.fingerprint,
            ProfileSpec.shape_v2_discard(bound_mode="unknown").fingerprint)
        override_profile = ProfileSpec.shape_v2_discard(bound_override=7.0)
        self.assertEqual(override_profile.bound_mode, "override")
        override = evaluate_discard_context(
            context, override_profile, level="EV2")
        self.assertTrue(all(item.reward_envelope["mode"] == "override"
                            for item in override.candidates))
        self.assertTrue(all(item.reward_envelope["fast_upper"] == 7.0
                            for item in override.candidates))

    def test_shape_v2_two_draw_path_initializes_each_draw_probability(self):
        game = Game(seed=190000, dealer=0)
        action, explanation = choose_game_action(game, game.current_seat())
        self.assertIn(action, game.legal_actions())
        self.assertIn(explanation["level"], ("V2-EV2", "V2-Q0", "legacy"))

    def test_unknown_fast_bound_disables_pruning_without_zero_fill(self):
        context = self._context(count=2).replace(
            chain_count=None, chain_piao=None,
            chain_counts=(None,) * 4, chain_piao_counts=(None,) * 4,
            rollout_valid=False)
        result = evaluate_discard_context(
            context, ProfileSpec.shape_v2_discard(
                node_budget=100000, time_budget_ms=1000), level="EV2")
        self.assertEqual(result.level, "V2-Q0")
        self.assertTrue(all(candidate.reward_envelope["mode"] == "unknown"
                            for candidate in result.candidates))
        self.assertTrue(all(not candidate.pruned for candidate in result.candidates))
        self.assertTrue(all("reward_bound" in candidate.missing
                            for candidate in result.candidates))

    def test_pair_interval_uses_unequal_candidate_bound_sum_without_clipping(self):
        interval = pair_interval([100.0, -100.0], 2.0, 5.0, alpha=0.1)
        self.assertEqual(interval["left_bound"], 2.0)
        self.assertEqual(interval["right_bound"], 5.0)
        self.assertEqual(interval["pair_bound"], 7.0)
        self.assertEqual(interval["difference_bound"], 7.0)
        self.assertEqual(interval["mean"], 0.0)
        self.assertEqual(interval["bound"], 7.0)

    def test_zero_high_boundary_is_not_strictly_below_zero(self):
        interval = pair_interval([0.0], 0.0, 0.0, alpha=0.05)
        self.assertEqual(interval["high"], 0.0)
        self.assertFalse(interval["high"] < 0)

    def test_teacher_eliminates_on_paired_ucb_and_preserves_sparse_rows(self):
        context = self._context(seed=102, count=3)
        actions = tuple(context.legal_actions)
        leader, middle, worst = actions
        rewards = {leader: 0.0, middle: 0.96, worst: 1.0}
        calls = []

        def fake_rollout(context, world, action, continuation, max_steps):
            calls.append((world.sample_id, action, world.fingerprint))
            return RolloutOutcome("ok", rewards[action], world.sample_id,
                                  world.fingerprint, 1, None, None, True)

        with patch("mj.rollout.evaluator.run_rollout", side_effect=fake_rollout):
            result = PairedTeacher(
                context, seed=9, n0=1, batch=1, nmax=4, alpha=0.5,
                reward_bound=0.01).evaluate(actions=actions)

        self.assertFalse(result.ambiguous)
        self.assertEqual(result.best_action, worst)
        self.assertEqual(result.stop_reason, "paired_elimination")
        self.assertEqual(len(result.rows), 2)
        self.assertEqual(result.rows[0]["active_actions"], list(actions))
        self.assertEqual(result.rows[1]["active_actions"], [middle, worst])
        self.assertNotIn(str(leader), result.rows[1]["outcomes"])
        self.assertEqual(len(calls), 5)
        certificate = result.elimination_history[0]["certificates"][0]
        self.assertIn("leader", certificate)
        self.assertIn("pair_bound", certificate)
        self.assertIn("delta_interval", certificate)
        self.assertIn("effective_n", certificate["delta_interval"])
        self.assertAlmostEqual(certificate["alpha"], 0.5 / (4 * 3))
        self.assertFalse(next(item for item in result.candidates
                              if item["action"] == leader)["active"])

    def test_marginal_ci_is_diagnostic_and_nmax_stays_ambiguous(self):
        context = self._context(seed=103, count=2)
        first, second = context.legal_actions

        def fake_rollout(context, world, action, continuation, max_steps):
            reward = 1.0 if action == second else 0.0
            return RolloutOutcome("ok", reward, world.sample_id,
                                  world.fingerprint, 1, None, None, True)

        with patch("mj.rollout.evaluator.run_rollout", side_effect=fake_rollout):
            result = PairedTeacher(
                context, seed=10, n0=1, batch=1, nmax=1, alpha=0.5,
                reward_bound=1000.0).evaluate()
        self.assertTrue(result.ambiguous)
        self.assertEqual(result.stop_reason, "nmax_ambiguous")
        candidate = next(item for item in result.candidates
                         if item["action"] == first)
        self.assertEqual(candidate["statistical_role"],
                         "marginal_diagnostic_only")
        self.assertFalse(candidate["racing_stop_authority"])

    def test_active_failure_invalidates_shared_row_without_zero_reward(self):
        context = self._context(seed=104, count=2)
        first, second = context.legal_actions

        def fake_rollout(context, world, action, continuation, max_steps):
            if action == first:
                return RolloutOutcome("failed", None, world.sample_id,
                                      world.fingerprint, error="incomplete_rollout")
            return RolloutOutcome("ok", 99.0, world.sample_id,
                                  world.fingerprint, 1, None, None, True)

        with patch("mj.rollout.evaluator.run_rollout", side_effect=fake_rollout):
            result = PairedTeacher(
                context, seed=11, n0=1, batch=1, nmax=1,
                reward_bound=1.0).evaluate()
        self.assertEqual(result.sample_count, 0)
        self.assertEqual(result.failed_samples, 1)
        self.assertEqual(result.paired_deltas[0]["delta"]["n"], 0)
        self.assertTrue(result.failures)
        self.assertTrue(all(candidate["EV"] is None for candidate in
                            result.candidates))
        self.assertIsNone(result.rows[0]["outcomes"][str(first)]["reward"])

    def test_resume_requires_new_schema_and_deduplicates_rows(self):
        context = self._context(seed=105, count=2)

        def fake_rollout(context, world, action, continuation, max_steps):
            return RolloutOutcome("ok", float(action), world.sample_id,
                                  world.fingerprint, 1, None, None, True)

        with patch("mj.rollout.evaluator.run_rollout", side_effect=fake_rollout):
            teacher = PairedTeacher(context, seed=12, n0=1, batch=1,
                                    nmax=1, reward_bound=10.0)
            result = teacher.evaluate()
            self.assertEqual(result.as_json()["bound_certificates"].keys(),
                             {str(action) for action in context.legal_actions})
            state = dict(result.resume_state)
            state["rows"] = list(state["rows"]) + list(state["rows"])
            resumed = teacher.evaluate(resume=state)
            self.assertEqual(resumed.attempted_samples, 1)
            old = dict(state)
            old.pop("schema")
            with self.assertRaises(ValueError):
                teacher.evaluate(resume=old)

            reordered = teacher.evaluate(actions=tuple(reversed(
                context.legal_actions)))
        self.assertEqual(
            [row["world_fingerprint"] for row in result.rows],
            [row["world_fingerprint"] for row in reordered.rows])
        self.assertEqual(result.stop_reason, reordered.stop_reason)

    def test_teacher_artifact_contains_public_bound_evidence_only(self):
        context = self._context(seed=106, count=2)

        def fake_rollout(context, world, action, continuation, max_steps):
            return RolloutOutcome("ok", 0.0, world.sample_id,
                                  world.fingerprint, 1, None, None, True)

        with patch("mj.rollout.evaluator.run_rollout", side_effect=fake_rollout):
            result = PairedTeacher(context, seed=13, n0=1, batch=1,
                                   nmax=1, reward_bound=1.0).evaluate()
        artifact = teacher_artifact(
            result, context=context, profile=ProfileSpec.shape_v2_discard())
        encoded = json.dumps(artifact, ensure_ascii=False)
        self.assertNotIn("hidden_hands", encoded)
        self.assertNotIn("wall_order", encoded)
        self.assertEqual(artifact["resume_schema"], "rollout-teacher-resume-v2")
        self.assertTrue(artifact["candidate_envelopes"])


if __name__ == "__main__":
    unittest.main()
