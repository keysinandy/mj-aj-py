"""Focused public-context, frontier, score and rollout contract tests."""

import unittest
from unittest.mock import patch

from mj.game import (Game, PASS, PONG, KONG_OPEN, CHOW_LOW, CHOW_MID,
                     CHOW_HIGH, HU, KONG_CLOSED_BASE)
from mj.bot import choose_action
from mj.decision.context import ContextError, PublicDecisionContext
from mj.decision.frontier import discard_frontier, discard_frontier_batch
from mj.decision.profile import ProfileSpec, ProfileFingerprintError, validate_profile_fingerprint
from mj.decision.score_value import ScoreValue
from mj.decision.root import evaluate_root_context
from mj.decision.fast_ev import (DecisionBudget, _apply_draw,
                                 evaluate_discard_context, future_values)
from mj.rollout.belief import BeliefSampler
from mj.rollout.evaluator import PairedTeacher
from mj.rollout.simulator import build_world_game, actor_view, RolloutOutcome
from mj.rollout.teacher_data import teacher_artifact


class BotEvDiscardTests(unittest.TestCase):
    def _complete_context(self, seed=11):
        game = Game(seed=seed, dealer=0, base=1)
        seat = game.current_seat()
        context = PublicDecisionContext.from_game(game, seat)
        chains = tuple(int(x) for x in game.chain)
        piao = tuple(int(x) for x in game.chain_piao)
        return context.replace(
            chain_counts=chains, chain_piao_counts=piao,
            rollout_valid=True, missing_fields=(), unsupported=())

    def test_platform_material_unknown_delegates_fast_ev(self):
        context = self._complete_context(seed=31).replace(
            concealed_counts=(None,) * 4,
            missing_fields=("public_hand_counts_unknown",),
            fast_valid=False,
            rollout_valid=False)
        result = evaluate_discard_context(
            context, ProfileSpec.shape_v2_discard(), legacy_best=0)
        self.assertEqual(result.level, "legacy")
        self.assertEqual(result.selected, 0)
        self.assertEqual(result.reason, "context_material_unknown")
        self.assertEqual(result.candidates, ())

    def test_platform_material_malformed_rejects_fast_ev(self):
        context = self._complete_context(seed=32).replace(
            concealed_counts=(None,) * 4,
            missing_fields=("public_hand_counts_malformed",),
            fast_valid=False,
            rollout_valid=False)
        with self.assertRaises(ContextError):
            evaluate_discard_context(
                context, ProfileSpec.shape_v2_discard(), legacy_best=1)

    def test_context_ignores_hidden_hands_and_wall_order(self):
        a = Game(seed=17)
        seat = a.current_seat()
        ca = PublicDecisionContext.from_game(a, seat)
        b = Game(seed=17)
        b.hands[1] = [0] * 34
        b.hands[1][0] = 13
        b.hands[2] = [0] * 34
        b.hands[2][1] = 13
        b.hands[3] = [0] * 34
        b.hands[3][2] = 13
        b.wall.reverse()
        cb = PublicDecisionContext.from_game(b, seat)
        self.assertEqual(ca.input_hash, cb.input_hash)
        self.assertEqual(ca.legal_discards, cb.legal_discards)

        # A projection for a non-current hero must not call legal_actions on
        # the current seat, whose concealed hand is outside the public view.
        c = Game(seed=17)
        c.hands[0] = [0] * 34
        c.hands[0][0] = 13
        c.wall.reverse()
        ca_other = PublicDecisionContext.from_game(a, 1)
        cb_other = PublicDecisionContext.from_game(c, 1)
        self.assertEqual(ca_other.input_hash, cb_other.input_hash)
        self.assertIn("legal_actions_for_hero", ca_other.missing_fields)

    def test_frontier_is_all_legal_distinct_discards(self):
        hand = [0] * 34
        for tile in (0, 1, 2, 9, 10, 11, 18, 19, 20, 27, 28, 29, 33, 8):
            hand[tile] += 1
        visible = list(hand)
        items = discard_frontier(hand, locked=0, visible=visible,
                                 use_rust=False)
        self.assertEqual(tuple(x.tile for x in items),
                         tuple(t for t, n in enumerate(hand) if n))
        self.assertTrue(all(x.legal and len(x.ukeire_tiles) ==
                            len(set(x.ukeire_tiles)) for x in items))

    def test_frontier_batch_matches_scalar_reference(self):
        context = self._complete_context(seed=27)
        root_hand = list(context.hand)
        root_hand[context.legal_discards[0]] -= 1
        states = []
        visibles = []
        for tile, left in enumerate(context.remaining):
            if left <= 0:
                continue
            hand = list(root_hand)
            hand[tile] += 1
            remaining = list(context.remaining)
            remaining[tile] -= 1
            states.append(tuple(hand))
            visibles.append(tuple(4 - value for value in remaining))
            if len(states) == 2:
                break
        batch = discard_frontier_batch(
            states, locked=context.locked, visibles=visibles, use_rust=False)
        scalar = tuple(discard_frontier(
            state, locked=context.locked, visible=visible, use_rust=False)
                       for state, visible in zip(states, visibles))
        self.assertEqual(
            [[item.as_json() for item in rows] for rows in batch],
            [[item.as_json() for item in rows] for rows in scalar])

    def test_profile_fingerprint_rejects_mutation(self):
        profile = ProfileSpec.shape_v2_discard()
        payload = profile.as_json()
        payload["q0_u1_weight"] = 99
        with self.assertRaises(ProfileFingerprintError):
            validate_profile_fingerprint(payload)

    def test_score_value_uses_existing_settlement_units(self):
        hand = [0] * 34
        for tile in (0, 1, 2, 0, 1, 2, 0, 1, 2, 3, 3, 3, 4, 4):
            hand[tile] += 1
        standing = list(hand)
        standing[4] -= 1
        dealer = ScoreValue(dealer=0, base=2, hero=0)
        non_dealer = ScoreValue(dealer=0, base=2, hero=1)
        a = dealer.hu(hand, standing, 0, drawn=4)
        b = non_dealer.hu(hand, standing, 0, drawn=4)
        self.assertTrue(a.legal and b.legal)
        self.assertEqual(a.reward, 24 * 2 * a.multiplier)
        self.assertEqual(b.reward, 10 * 2 * b.multiplier)
        self.assertEqual(sum(a.settlement), 0)

    def test_score_value_matches_game_hu_transition(self):
        game = Game(seed=99, dealer=0, base=2)
        hand = [0] * 34
        for tile in (0, 1, 2, 0, 1, 2, 0, 1, 2, 3, 3, 3, 4, 4):
            hand[tile] += 1
        game.hands[0] = hand[:]
        game.drawn[0] = 4
        game.turn = 0
        game.phase = "discard"
        game.chain[0] = 1
        game.chain_piao[0] = 0
        expected = ScoreValue(dealer=0, base=2, hero=0).hu_from_game(game)
        game.step(-75)
        self.assertEqual(tuple(game.scores), expected.settlement)
        self.assertEqual(game.result[1], expected.multiplier)

    def test_ev2_respects_wall_boundary_and_does_not_double_ev1(self):
        context = self._complete_context(seed=31)
        context = context.replace(
            legal_actions=context.legal_discards[:2], live_wall=4,
            concealed_counts=(None,) * 4, rollout_valid=False)
        from mj.decision.profile import ProfileSpec
        profile = ProfileSpec.shape_v2_discard(
            node_budget=100000, time_budget_ms=1000)
        result = evaluate_discard_context(context, profile, level="EV2")
        self.assertEqual(result.level, "V2-EV2")
        for candidate in result.candidates:
            self.assertEqual(candidate.ev1, candidate.ev2)
            self.assertAlmostEqual(candidate.value, candidate.ev2)

    def test_ev2_frontier_batch_matches_full_final_draw_reference(self):
        context = self._complete_context(seed=34).replace(live_wall=8)
        root_tile = context.legal_discards[0]
        scorer = ScoreValue(context.dealer, context.base,
                            bool(context.you_cai_bi_kao), context.hero_seat)
        root_hand, chain, piao, _ = scorer.discard(
            context.hand, root_tile, context.locked,
            context.chain_count, context.chain_piao)

        def reference_one_draw(hand, rem, locked, chain_count, chain_piao):
            n = sum(rem)
            total = 0.0
            for tile, left in enumerate(rem):
                if left <= 0:
                    continue
                hand2, _ = _apply_draw(hand, rem, tile)
                breakdown = scorer.hu(
                    hand2, scorer.standing_before_draw(hand2, tile), locked,
                    tile, False, chain_count, chain_piao)
                if breakdown.legal:
                    total += left / n * breakdown.reward
            return total

        def reference():
            rem = tuple(context.remaining)
            n = sum(rem)
            ev1 = ev2 = 0.0
            for tile, left in enumerate(rem):
                if left <= 0:
                    continue
                hand2, rem2 = _apply_draw(root_hand, rem, tile)
                breakdown = scorer.hu(
                    hand2, scorer.standing_before_draw(hand2, tile),
                    context.locked, tile, False, chain, piao)
                probability = left / n
                if breakdown.legal:
                    ev1 += probability * breakdown.reward
                    ev2 += probability * breakdown.reward
                    continue
                best = 0.0
                for discard, count in enumerate(hand2):
                    if count <= 0:
                        continue
                    next_hand, next_chain, next_piao, _ = scorer.discard(
                        hand2, discard, context.locked, chain, piao)
                    best = max(best, reference_one_draw(
                        next_hand, rem2, context.locked,
                        next_chain, next_piao))
                ev2 += probability * best
            return ev1, ev2

        expected = reference()
        actual = future_values(
            context, root_hand, context.locked, chain, piao,
            horizon=2, budget=DecisionBudget(10_000_000, 10_000))
        self.assertAlmostEqual(actual[0], expected[0])
        self.assertAlmostEqual(actual[1], expected[1])

    def test_uncalibrated_ev2_uses_q0_only_as_tie_break(self):
        from mj.decision.fast_ev import _candidate_q0

        context = self._complete_context(seed=33)
        item = discard_frontier(context, use_rust=False)[0]
        profile = ProfileSpec.shape_v2_discard(
            q0_shanten_weight=-100.0, q0_u1_weight=100.0,
            q0_ev2_weight=1.0)
        q0, value, contributions = _candidate_q0(
            item, context, profile, ev1=1.0, ev2=8.7)
        self.assertEqual(value, 8.7)
        self.assertEqual(contributions["EV2"], 8.7)
        self.assertNotEqual(q0, value)

    def test_high_layer_budget_falls_back_to_complete_q0(self):
        context = self._complete_context(seed=32)
        context = context.replace(legal_actions=context.legal_discards[:2],
                                  concealed_counts=(None,) * 4,
                                  rollout_valid=False)
        from mj.decision.profile import ProfileSpec
        profile = ProfileSpec.shape_v2_discard(node_budget=0,
                                                time_budget_ms=1000)
        result = evaluate_discard_context(context, profile, level="EV2",
                                          budget=DecisionBudget(0, 1000),
                                          legacy_best=context.legal_actions[0])
        self.assertEqual(result.level, "legacy")
        self.assertEqual(result.reason, "q0_node_budget")
        self.assertEqual(result.selected, context.legal_actions[0])

    def test_online_explanation_keeps_actual_legacy_choice_and_horizon(self):
        game = Game(seed=41)
        legacy = choose_action(game, game.current_seat(), evaluator="legacy")
        action, evaluation = choose_action(
            game, game.current_seat(), evaluator="shape-v2",
            return_evaluation=True)
        self.assertIn(action, game.legal_actions())
        self.assertEqual(evaluation["legacy_best"], legacy)
        self.assertEqual(evaluation["horizon"], 2)
        for candidate in evaluation.get("candidates", ()):
            self.assertIsNone(candidate.get("I"))
            self.assertIn("I", candidate.get("missing", ()))

    def test_calibrated_hu_piao_root_compares_hu_and_all_discards(self):
        game = Game(seed=44, dealer=0, base=1)
        hand = [0] * 34
        for tile in (0, 1, 2, 0, 1, 2, 0, 1, 2, 3, 3, 3, 4, 4):
            hand[tile] += 1
        game.hands[0] = hand
        game.drawn[0] = 4
        game.turn = 0
        game.phase = "discard"
        context = PublicDecisionContext.from_game(game, 0).replace(
            legal_actions=(HU, 0, 1),
            concealed_counts=(None,) * 4,
            rollout_valid=False)
        profile = ProfileSpec.shape_v2(
            scope="hu-piao", calibrated=True, node_budget=100000,
            time_budget_ms=10000)
        result = evaluate_root_context(context, profile, legacy_action=HU)
        self.assertEqual(result.level, "V2-ROOT")
        self.assertEqual(result.selected in (HU, 0, 1), True)
        self.assertEqual({row["action"] for row in result.candidates},
                         {HU, 0, 1})
        hu = next(row for row in result.candidates if row["action"] == HU)
        self.assertTrue(hu["instant_hu"]["legal"])

    def test_calibrated_all_root_evaluates_closed_kong_transition(self):
        game = Game(seed=45, dealer=0, base=1)
        hand = [0] * 34
        for tile in (0, 0, 0, 0, 1, 1, 1, 2, 2, 2, 3, 3, 4, 5):
            hand[tile] += 1
        game.hands[0] = hand
        game.drawn[0] = 5
        game.turn = 0
        game.phase = "discard"
        kong = KONG_CLOSED_BASE - 0
        context = PublicDecisionContext.from_game(game, 0).replace(
            legal_actions=(0, 1, kong), concealed_counts=(None,) * 4,
            rollout_valid=False)
        profile = ProfileSpec.shape_v2(
            scope="all-root", calibrated=True, node_budget=1000000,
            time_budget_ms=10000)
        result = evaluate_root_context(context, profile, legacy_action=0)
        self.assertEqual(result.level, "V2-ROOT")
        self.assertEqual({row["action"] for row in result.candidates},
                         {0, 1, kong})
        kong_row = next(row for row in result.candidates
                        if row["action"] == kong)
        self.assertEqual(kong_row["kong_evaluation"]["kind"], "closed")
        self.assertEqual(kong_row["kong_evaluation"]["transition"]["locked"],
                         1)

    def test_uncalibrated_all_root_still_delegates_kong(self):
        game = Game(seed=46, dealer=0)
        game.hands[0] = [4] + [0] * 33
        game.hands[0][1] = 4
        game.hands[0][2] = 3
        game.hands[0][3] = 2
        game.hands[0][4] = 1
        game.drawn[0] = 4
        game.turn = 0
        context = PublicDecisionContext.from_game(game, 0).replace(
            legal_actions=(0, 1, KONG_CLOSED_BASE),
            concealed_counts=(None,) * 4, rollout_valid=False)
        result = evaluate_root_context(
            context, ProfileSpec.shape_v2(scope="all-root"), legacy_action=0)
        self.assertEqual(result.level, "legacy")
        self.assertEqual(result.delegated_reason, "root_compare_uncalibrated")

    def test_calibrated_all_root_reaction_uses_complete_response_cursor(self):
        game = Game(seed=47, dealer=0, base=1)
        hand = [0] * 34
        hand[0] = 2
        for tile in range(1, 12):
            hand[tile] = 1
        game.hands[1] = hand
        game.hands[0] = [0] * 34
        game.discards = [[] for _ in range(4)]
        game.discards[0] = [0]
        game.pending = (0, 0)
        game.phase = "react"
        game.react_seq = [1, 2, 3, 1]
        game._n_claim = 3
        game.react_idx = 0
        game.turn = 1
        context = PublicDecisionContext.from_game(game, 1)
        profile = ProfileSpec.shape_v2(
            scope="all-root", calibrated=True, horizon=0,
            node_budget=2000, time_budget_ms=1000)
        result = evaluate_root_context(context, profile, legacy_action=PASS)
        self.assertEqual(result.level, "V2-ROOT")
        self.assertEqual({row["action"] for row in result.candidates},
                         {PASS, PONG})
        pong = next(row for row in result.candidates if row["action"] == PONG)
        self.assertEqual(pong["post_locked"], 1)
        self.assertEqual(pong["remaining_response_order"], [2, 3, 1])

    def test_calibrated_all_root_reaction_evaluates_open_kong_transition(self):
        game = Game(seed=48, dealer=0, base=1)
        hand = [0] * 34
        hand[0] = 3
        for tile in range(1, 11):
            hand[tile] = 1
        game.hands[1] = hand
        game.hands[0] = [0] * 34
        game.discards = [[] for _ in range(4)]
        game.discards[0] = [0]
        game.pending = (0, 0)
        game.phase = "react"
        game.react_seq = [1, 2, 3, 1]
        game._n_claim = 3
        game.react_idx = 0
        game.turn = 1
        context = PublicDecisionContext.from_game(game, 1)
        profile = ProfileSpec.shape_v2(
            scope="all-root", calibrated=True, horizon=0,
            node_budget=5000, time_budget_ms=1000)
        result = evaluate_root_context(context, profile, legacy_action=PASS)
        self.assertEqual(result.level, "V2-ROOT")
        self.assertEqual({row["action"] for row in result.candidates},
                         {PASS, PONG, KONG_OPEN})
        kong = next(row for row in result.candidates
                    if row["action"] == KONG_OPEN)
        self.assertEqual(kong["source"], "reaction_kong_transition")
        self.assertEqual(kong["post_locked"], 1)

    def test_calibrated_all_root_reaction_evaluates_chow_and_rejects_unknown(self):
        game = Game(seed=49, dealer=0, base=1)
        hand = [0] * 34
        hand[0] = 1
        hand[1] = 1
        for tile in range(3, 14):
            hand[tile] = 1
        game.hands[1] = hand
        game.hands[0] = [0] * 34
        game.discards = [[] for _ in range(4)]
        game.discards[0] = [2]
        game.pending = (0, 2)
        game.phase = "react"
        game.react_seq = [1, 2, 3, 1]
        game._n_claim = 3
        game.react_idx = 3
        game.turn = 1
        context = PublicDecisionContext.from_game(game, 1)
        profile = ProfileSpec.shape_v2(
            scope="all-root", calibrated=True, horizon=0,
            node_budget=2000, time_budget_ms=1000)
        result = evaluate_root_context(context, profile, legacy_action=PASS)
        self.assertEqual(result.level, "V2-ROOT")
        self.assertEqual({row["action"] for row in result.candidates},
                         {PASS, CHOW_LOW, CHOW_MID, CHOW_HIGH})
        unknown = context.replace(
            react_seq=(), react_index=None, react_claim_count=None)
        delegated = evaluate_root_context(unknown, profile, legacy_action=PASS)
        self.assertEqual(delegated.level, "legacy")
        self.assertEqual(delegated.delegated_reason,
                         "reaction_context_incomplete")

    def test_sampler_and_world_builder_preserve_material_and_actor_view(self):
        context = self._complete_context()
        world = BeliefSampler(context, seed=123).sample(4)
        self.assertEqual(len(world.wall), context.full_wall)
        game = build_world_game(context, world)
        view = actor_view(game, context.hero_seat)
        self.assertEqual(view.hands[context.hero_seat], game.hands[context.hero_seat])
        self.assertEqual(sum(sum(row) for row in view.hands),
                         sum(game.hands[context.hero_seat]))
        self.assertEqual(view.wall, [0] * len(game.wall))
        self.assertTrue(all(sum(row) == 0 for s, row in enumerate(view.hands)
                            if s != context.hero_seat))

    def test_teacher_uses_shared_sample_rows_and_resume_shape(self):
        context = self._complete_context(seed=22)
        context = context.replace(legal_actions=context.legal_discards[:2])
        result_rows = {}

        def fake_rollout(context, world, action, continuation, max_steps):
            out = RolloutOutcome("ok", float(action), world.sample_id,
                                 world.fingerprint, 1, None, None, True)
            result_rows.setdefault(world.sample_id, set()).add(world.fingerprint)
            return out

        with patch("mj.rollout.evaluator.run_rollout", side_effect=fake_rollout):
            result = PairedTeacher(context, seed=4, n0=1, batch=1,
                                   nmax=1).evaluate()
        self.assertEqual(result.attempted_samples, 1)
        self.assertEqual(len(result.rows), 1)
        self.assertEqual(len(result.rows[0]["outcomes"]), 2)
        self.assertFalse(result.unsupported)

    def test_teacher_small_exhaustive_reference_and_resume(self):
        """The bounded evaluator agrees with a four-world toy enumeration."""
        context = self._complete_context(seed=23)
        context = context.replace(legal_actions=context.legal_discards[:2])
        first, second = context.legal_actions

        def fake_rollout(context, world, action, continuation, max_steps):
            # An exhaustive four-world reference: the second legal action is
            # exactly ten points higher in every world.
            reward = float(world.sample_id + (10 if action == second else 0))
            return RolloutOutcome("ok", reward, world.sample_id,
                                 world.fingerprint, 1, None, None, False)

        with patch("mj.rollout.evaluator.run_rollout", side_effect=fake_rollout):
            teacher = PairedTeacher(
                context, seed=5, n0=1, batch=1, nmax=4,
                reward_bound=100.0)
            result = teacher.evaluate()
            resumed = teacher.evaluate(resume=result.as_json())

        values = {row["action"]: row["EV"] for row in result.candidates}
        self.assertEqual(values, {first: 1.5, second: 11.5})
        self.assertEqual(result.paired_rows[0]["delta"], 10.0)
        self.assertEqual(result.paired_deltas[0]["delta"]["mean"], 10.0)
        self.assertTrue(result.ambiguous)
        self.assertEqual(resumed.fingerprint, result.fingerprint)
        self.assertEqual(resumed.sample_count, 4)
        artifact = teacher_artifact(
            result, context=context, profile=ProfileSpec.shape_v2_discard())
        self.assertTrue(artifact["counterfactual_evaluation"])
        self.assertFalse(artifact["oracle"])
        self.assertEqual(artifact["contract"]["scope"], "discard")

    def test_teacher_uses_paired_ci_to_eliminate_actions(self):
        context = self._complete_context(seed=26)
        context = context.replace(legal_actions=context.legal_discards[:2])
        first, second = context.legal_actions

        def fake_rollout(context, world, action, continuation, max_steps):
            reward = 0.1 if action == first else 0.0
            return RolloutOutcome("ok", reward, world.sample_id,
                                 world.fingerprint, 1, None, None, True)

        with patch("mj.rollout.evaluator.run_rollout", side_effect=fake_rollout):
            result = PairedTeacher(
                context, seed=8, n0=8, batch=8, nmax=64,
                alpha=0.5, reward_bound=0.1).evaluate()

        self.assertFalse(result.ambiguous)
        self.assertEqual(result.stop_reason, "paired_elimination")
        self.assertTrue(result.elimination_history)
        self.assertEqual(result.elimination_history[0]["eliminated"], [second])
        self.assertLess(result.attempted_samples, 64)
        pair = next(item for item in result.pairwise_deltas
                    if item["left"] == first and item["right"] == second)
        self.assertEqual(pair["delta"]["method"],
                         "hoeffding-paired-bounded-simultaneous")

    def test_teacher_terminal_resume_deduplicates_and_restores_stop_state(self):
        context = self._complete_context(seed=24)
        context = context.replace(legal_actions=context.legal_discards[:2])
        first, second = context.legal_actions

        def fake_rollout(context, world, action, continuation, max_steps):
            return RolloutOutcome("ok", float(action == second),
                                 world.sample_id, world.fingerprint, 1,
                                 None, None, True)

        with patch("mj.rollout.evaluator.run_rollout", side_effect=fake_rollout):
            teacher = PairedTeacher(context, seed=6, n0=1, batch=1, nmax=1,
                                    reward_bound=1.0)
            result = teacher.evaluate()
            resume = result.as_json()
            state = dict(resume["resume_state"])
            state["rows"] = list(state["rows"]) + list(state["rows"])
            state["stop_reason"] = "simultaneous_ci_separated"
            state["ambiguous"] = False
            resume["resume_state"] = state
            resumed = teacher.evaluate(resume=resume)

        self.assertEqual(resumed.sample_count, 1)
        self.assertFalse(resumed.ambiguous)
        self.assertEqual(resumed.stop_reason, "simultaneous_ci_separated")

    def test_teacher_classifies_incomplete_context_as_unsupported(self):
        game = Game(seed=25)
        context = PublicDecisionContext.from_game(game, game.current_seat())
        result = PairedTeacher(context, seed=7, n0=1, batch=1,
                               nmax=1).evaluate()
        self.assertTrue(result.unsupported)
        self.assertEqual(result.status, "unsupported")
        self.assertEqual(result.stop_reason, "unsupported_context")
        self.assertEqual(result.sample_count, 0)


if __name__ == "__main__":
    unittest.main()
