"""Root-scope and reaction-cursor contracts for the v2 extension."""

import unittest

from mj.decision.context import ContextError, PublicDecisionContext
from mj.decision.profile import ProfileSpec
from mj.decision.root import evaluate_root_context
from mj.game import (CHOW_HIGH, CHOW_LOW, CHOW_MID, HU, KONG_CLOSED_BASE,
                     KONG_OPEN, PASS, PONG)
from mj.platform.mirror import Mirror
from mj.platform.proto import tidx
from mj.rollout.belief import BeliefSampler
from mj.rollout.evaluator import PairedTeacher
from scripts.bot_ev_root_teacher import (_context_from_case, load_fixture,
                                          run_case)


class RootScopeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = {case["id"]: case for case in load_fixture()["cases"]}

    def context(self, case_id):
        return _context_from_case(self.cases[case_id])

    def test_public_fixture_material_and_authoritative_legal_sets(self):
        for case_id, case in self.cases.items():
            context = self.context(case_id)
            context.validate_for("rollout")
            world = BeliefSampler(context, seed=7).sample(0)
            from mj.rollout.simulator import build_world_game
            game = build_world_game(context, world)
            self.assertEqual(set(game.legal_actions()),
                             set(context.legal_actions), case_id)
            self.assertTrue(case["context"].get("rollout_valid"))

    def test_hu_piao_contains_immediate_hu_and_piao_transition(self):
        context = self.context("hu-piao-immediate-vs-continue").replace(
            legal_actions=(HU, 33))
        profile = ProfileSpec.shape_v2_hu_piao(
            calibrated=True, horizon=1, node_budget=100000,
            time_budget_ms=5000)
        result = evaluate_root_context(context, profile, legacy_action=HU)
        self.assertEqual(result.level, "V2-ROOT")
        self.assertEqual({row["action"] for row in result.candidates},
                         {HU, 33})
        hu = next(row for row in result.candidates if row["action"] == HU)
        piao = next(row for row in result.candidates if row["action"] == 33)
        self.assertTrue(hu["instant_hu"]["legal"])
        self.assertTrue(piao["is_piao"])
        self.assertEqual((piao["post_chain"], piao["post_chain_piao"]),
                         (1, 1))
        self.assertEqual(piao["transition"]["chain"], 1)
        self.assertEqual(piao["transition"]["chain_piao"], 1)
        self.assertTrue(piao["transition"]["catch_play"])
        self.assertEqual(piao["transition"]["freeze_after_discard"], 3)
        self.assertTrue(hu["transition"]["immediate"])
        self.assertIsNotNone(piao["value"])

    def test_root_does_not_invent_discards_for_special_only_set(self):
        context = self.context("all-root-closed-kong").replace(
            legal_actions=(KONG_CLOSED_BASE,))
        result = evaluate_root_context(
            context, ProfileSpec.shape_v2_all_root(calibrated=True),
            legacy_action=KONG_CLOSED_BASE)
        self.assertEqual(result.level, "legacy")
        self.assertEqual(result.selected, KONG_CLOSED_BASE)
        self.assertEqual(result.candidates, ())

    def test_all_root_evaluates_closed_and_add_kong_with_same_transition_unit(self):
        profile = ProfileSpec.shape_v2_all_root(
            calibrated=True, horizon=1, node_budget=100000,
            time_budget_ms=5000)
        closed = self.context("all-root-closed-kong").replace(
            legal_actions=(0, KONG_CLOSED_BASE))
        add = self.context("all-root-add-kong").replace(
            legal_actions=(0, -41))
        closed_result = evaluate_root_context(closed, profile, legacy_action=0)
        add_result = evaluate_root_context(add, profile, legacy_action=0)
        self.assertEqual(closed_result.level, "V2-ROOT")
        self.assertEqual(add_result.level, "V2-ROOT")
        closed_row = next(row for row in closed_result.candidates
                          if row["action"] == KONG_CLOSED_BASE)
        add_row = next(row for row in add_result.candidates
                       if row["action"] == -41)
        self.assertEqual(closed_row["kong_evaluation"]["kind"], "closed")
        self.assertEqual(add_row["kong_evaluation"]["kind"], "add")
        self.assertEqual(closed_row["kong_evaluation"]["transition"]["locked"],
                         1)
        self.assertEqual(add_row["kong_evaluation"]["transition"]["locked"],
                         1)
        self.assertTrue(closed_row["kong_evaluation"]["transition"]
                        ["replacement_draw"])

    def test_wall_tail_kong_is_delegated_even_as_only_action(self):
        context = self.context("all-root-closed-kong").replace(
            legal_actions=(KONG_CLOSED_BASE,), live_wall=0,
            concealed_counts=(None,) * 4, rollout_valid=False)
        result = evaluate_root_context(
            context, ProfileSpec.shape_v2_all_root(calibrated=True),
            legacy_action=0)
        self.assertEqual(result.level, "legacy")
        self.assertEqual(result.reason, "root_legal_action_invalid")

    def test_root_teacher_uses_public_fixture_without_hidden_world_fields(self):
        artifact = run_case(
            self.cases["hu-piao-immediate-vs-continue"], seed=13,
            n0=1, batch=1, nmax=1)
        self.assertFalse(artifact["oracle"])
        self.assertFalse(artifact["online_decision"])
        self.assertEqual(artifact["metadata"]["scope"], "hu-piao")
        self.assertNotIn("hidden_hands", artifact["context"])
        self.assertNotIn("wall", artifact["context"])
        self.assertEqual(artifact["failed_samples"], 0)


class ReactionCursorTests(unittest.TestCase):
    def _mirror(self):
        mirror = Mirror(my_seat=1, dealer=0)
        mirror.apply_snapshot({
            "seat": 1, "phase": "response_peng", "turn": 0,
            "drawn_tile": "", "my_hand": [
                "1w", "1w", "2w", "3w", "4w", "5w", "6w",
                "7w", "8w", "9w", "1b", "2b", "3b",
            ],
            "discards": [["1w"], [], [], []],
            "melds": [[], [], [], []], "last_discard": "1w",
            "wall_remaining": 83,
            "god": {"catch_play": False, "chain_count": 0},
        })
        return mirror

    def test_mirror_does_not_turn_responding_seats_into_fake_order(self):
        mirror = self._mirror()
        context = PublicDecisionContext.from_mirror(mirror, "response_peng")
        self.assertEqual(context.react_seq, ())
        self.assertIn("response_order", context.unsupported)
        result = PairedTeacher(context, n0=1, batch=1, nmax=1).evaluate(
            (PASS, PONG))
        self.assertTrue(result.unsupported)

    def test_explicit_cursor_restores_full_priority_order_and_legal_mode(self):
        mirror = self._mirror()
        context = PublicDecisionContext.from_mirror(
            mirror, "response_peng",
            response_order=(1, 2, 3, 1), response_index=0,
            response_claim_count=3)
        self.assertEqual(context.react_seq, (1, 2, 3, 1))
        self.assertEqual(context.react_index, 0)
        self.assertEqual(context.react_claim_count, 3)
        self.assertEqual(context.legal_actions, (PASS, PONG))
        self.assertEqual(mirror.build_game("response_peng").react_seq,
                         [1])  # explicit adapter argument does not mutate mirror

    def test_snapshot_cursor_is_used_when_protocol_supplies_it(self):
        mirror = self._mirror()
        mirror.apply_snapshot({
            "phase": "response_peng", "turn": 0,
            "last_discard": "1w", "response_order": [1, 2, 3, 1],
            "response_index": 0, "response_claim_count": 3,
        })
        self.assertEqual(mirror.response_order, (1, 2, 3, 1))
        self.assertEqual(mirror.build_game("response_peng").react_seq,
                         [1, 2, 3, 1])

    def test_generic_react_phase_uses_explicit_cursor_without_guessing_mode(self):
        mirror = self._mirror()
        mirror.apply_snapshot({
            "phase": "react", "turn": 0, "last_discard": "1w",
            "response": {"order": [1, 2, 3, 1], "index": 0,
                          "claim_count": 3},
        })
        context = PublicDecisionContext.from_mirror(mirror, "react")
        self.assertEqual(context.react_seq, (1, 2, 3, 1))
        self.assertEqual(context.legal_actions, (PASS, PONG))

    def test_unknown_window_delegates_root_and_rejects_teacher(self):
        context = _context_from_case(load_fixture()["cases"][3]).replace(
            phase="response_unknown")
        with self.assertRaises(ContextError):
            context.validate_for("rollout")
        result = PairedTeacher(context, n0=1, batch=1, nmax=1).evaluate(
            (PASS, PONG))
        self.assertTrue(result.unsupported)
        root = evaluate_root_context(
            context, ProfileSpec.shape_v2_all_root(calibrated=True),
            legacy_action=PASS)
        self.assertEqual(root.level, "legacy")


class ReactionDeltaRegressionTests(unittest.TestCase):
    def test_shape_v1_and_legacy_keep_reaction_legal_set_and_visible_snapshot(self):
        from mj.bot import choose_action
        from mj.hand_eval import EvalProfile
        from mj.tiles import counts
        from mj.game import Game

        game = Game(seed=0)
        game.hands[2] = counts("49m3688p11568sEB")
        game.melds = [[], [], [], []]
        game.discards = [[], [16], [], []]
        game.pending = (1, 16)
        game.phase = "react"
        game.turn = 2
        game._n_claim = 3
        game.react_idx = 0
        game.react_seq = [2, 3, 0]
        game.chows = [0] * 4
        game.freeze = 0
        game.freezer = None
        before = tuple(game.visible_counts(2))
        legacy = choose_action(game, 2, evaluator="legacy-v1")
        shape, evaluation = choose_action(
            game, 2, evaluator="shape-v1", return_evaluation=True)
        self.assertIn(legacy, game.legal_actions())
        self.assertIn(shape, game.legal_actions())
        self.assertEqual(before, tuple(game.visible_counts(2)))
        self.assertEqual(evaluation["profile"], "shape-v1")
        self.assertIn(evaluation["level"], ("legacy", "Q0", "Q"))
        self.assertEqual(EvalProfile.shape_v1().name, "shape-v1")

    def test_shape_v1_logs_recomputable_threshold_for_claim(self):
        from mj.hand_eval import EvalProfile, evaluate_reaction
        from mj.tiles import counts
        from mj.game import Game

        game = Game(seed=0)
        game.hands[2] = counts("49m3688p11568sEB")
        game.melds = [[], [], [], []]
        game.discards = [[], [16], [], []]
        game.pending = (1, 16)
        game.phase = "react"
        game.turn = 2
        game._n_claim = 3
        game.react_idx = 0
        game.react_seq = [2, 3, 0]
        game.chows = [0] * 4
        game.freeze = 0
        game.freezer = None
        action, evaluation = evaluate_reaction(
            game, 2, EvalProfile.shape_v1(
                react_node_budget=100000, react_time_budget_ms=50.0))
        pong = next(item for item in evaluation["candidates"]
                    if item["action"] == PONG)
        self.assertEqual(evaluation["threshold"]["unit"], "normalized_Q")
        self.assertEqual(pong["threshold_unit"], "normalized_Q")
        self.assertIn("Q_claim-Q_pass", pong["threshold_formula"])
        self.assertAlmostEqual(
            pong["delta_vs_pass"], pong["Q"] - pong["pass_value"])
        self.assertEqual(pong["accepted"],
                         pong["delta_vs_pass"] >= pong["tau"])
        self.assertIn(action, game.legal_actions())


class ReactionPerformanceReportTests(unittest.TestCase):
    def test_performance_summary_exposes_v2_completion_and_gate(self):
        from scripts import bot_react_perf

        summary = bot_react_perf._summary([
            {"elapsed_ms": 4.0, "level": "V2-ROOT",
             "reason": "shape_v2_reaction_same_unit", "complete": True,
             "nodes": 8, "kernel_calls": 1},
            {"elapsed_ms": 6.0, "level": "V2-ROOT",
             "reason": "shape_v2_reaction_same_unit", "complete": True,
             "nodes": 9, "kernel_calls": 1},
        ], "all-root")
        self.assertEqual(summary["complete_rate"], 1.0)
        self.assertEqual(summary["fallback_rate"], 0.0)
        self.assertTrue(summary["reaction_p95_gate"])
        self.assertEqual(summary["nodes"]["p95"], 8)

    def test_profile_scope_fingerprint_separates_hu_piao_and_all_root(self):
        hu = ProfileSpec.shape_v2_hu_piao()
        root = ProfileSpec.shape_v2_all_root()
        self.assertNotEqual(hu.fingerprint, root.fingerprint)
        self.assertEqual(hu.scope, "hu-piao")
        self.assertEqual(root.scope, "all-root")


if __name__ == "__main__":
    unittest.main()
