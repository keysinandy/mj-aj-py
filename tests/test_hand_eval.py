"""Properties and small regression fixtures for the shape-v1 evaluator."""

import json
import os
import random
import unittest

from mj.bot import choose_action, choose_discard
from mj.game import Game, PASS, PONG
from mj.hand_eval import (
    EvalContext, EvalProfile, InvalidEvaluationInput,
    enumerate_decompositions, evaluate_discard_candidates,
    evaluate_reaction, evaluate_standing,
)
from mj.platform.bot_client import _compact_evaluation
from mj.shanten import shanten
from mj.tiles import counts


FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures",
                       "bot_shape_cases.json")


def _visible_for(hand, locked=0, melds=(), extras=()):
    vis = list(hand)
    for meld in melds:
        kind, start = meld
        if kind == "chow":
            for t in (start, start + 1, start + 2):
                vis[t] += 1
        elif kind.startswith("kong"):
            vis[start] += 4
        else:
            vis[start] += 3
    for t, n in extras:
        vis[t] += n
    return tuple(vis)


class TestHandEvaluation(unittest.TestCase):
    def test_context_rejects_invalid_visible_and_hidden_state_is_irrelevant(self):
        hand = counts("123m456m789m123p5p")
        with self.assertRaises(InvalidEvaluationInput):
            EvalContext(hand=hand, visible=tuple(hand[:33]) + (5,))
        vis = _visible_for(hand)
        a = EvalContext(hand=hand, visible=vis, live_wall=20, seat=0)
        b = a.replace(live_wall=1, freezer=3)  # allowed public context differs
        # The evaluator has no hidden-wall/opponent input at all; profile
        # fingerprints remain stable and both outputs are serialisable.
        self.assertEqual(a.cache_key(EvalProfile.shape_v1())[0],
                         b.cache_key(EvalProfile.shape_v1())[0])
        self.assertIn("model_assumption",
                      evaluate_standing(a, "shape-v1").as_json())

    def test_decomposition_material_conservation_and_1234_889_alternatives(self):
        hand = counts("1234m889p123s556s")
        self.assertEqual(sum(hand), 13)
        ds = enumerate_decompositions(hand, limit=512)
        self.assertEqual(min(d.score for d in ds), shanten(hand))
        saw_seq = saw_pair = False
        for d in ds:
            used = [0] * 34
            for _kind, tiles, _wild in d.melds:
                for t in tiles:
                    used[t] += 1
            if d.pair:
                for t in d.pair[0]:
                    used[t] += 1
                if 16 in d.pair[0]:
                    saw_pair = True
            for tiles, _wild in d.extra_pairs:
                for t in tiles:
                    used[t] += 1
                if 16 in tiles:
                    saw_pair = True
            for _kind, tiles, _wild in d.taatsu:
                for t in tiles:
                    used[t] += 1
            self.assertTrue(all(x <= y for x, y in zip(used, hand)))
            self.assertLessEqual(d.wild_used + d.wild_left, hand[33])
            saw_seq |= any(kind == "sequence" and 0 in tiles
                           for kind, tiles, _wild in d.melds)
        self.assertTrue(saw_seq)
        self.assertTrue(saw_pair)

    def test_n_unknown_one_makes_improvement_zero(self):
        hand = counts("123m456m789m123p5p")
        vis = [4] * 34
        # Keep the hand visible and leave exactly one 6m unseen.
        vis[5] = 3
        ev = evaluate_standing(EvalContext(hand=hand, visible=tuple(vis)),
                               "shape-v1", level="Q")
        self.assertEqual(ev.improvement, 0.0)

    def test_with_draw_updates_hand_and_visible_once(self):
        hand = counts("123m456m789m123p5p")
        visible = list(hand)
        visible[5] = 3
        ctx = EvalContext(hand=hand, visible=tuple(visible))
        nxt = ctx.with_draw(5)
        self.assertEqual(nxt.hand[5], hand[5] + 1)
        self.assertEqual(nxt.visible[5], visible[5] + 1)
        self.assertEqual(nxt.remaining[5], 0)
        with self.assertRaises(InvalidEvaluationInput):
            nxt.with_draw(5)

    def test_a_shape_paths_distinguish_68_and_89_when_budget_allows(self):
        # The fixture's 5m visibility leaves only one natural 5m completion;
        # the two same-shanten candidates therefore have different future
        # paths even though their direct U1 is equal.
        drawn = counts("689m13p12389sB")
        visible = list(drawn)
        for t in (0, 1, 2):
            visible[t] += 1       # exposed 123m meld
        visible[4] += 3           # three 5m already in the rivers
        profile = EvalProfile.shape_v1(discard_time_budget_ms=1000)
        values = []
        for tile in (5, 8):
            standing = list(drawn)
            standing[tile] -= 1
            ev = evaluate_standing(EvalContext(
                hand=tuple(standing), locked=1, visible=tuple(visible),
                live_wall=47), profile, level="Q")
            self.assertTrue(ev.complete)
            self.assertGreaterEqual(ev.improvement, 0.0)
            values.append(ev.improvement)
        self.assertNotEqual(values[0], values[1])

    def test_profile_is_explicit_and_shape_action_is_legal(self):
        g = Game(seed=7)
        seat = g.current_seat()
        act, ev = choose_action(g, seat, evaluator="shape-v1",
                                return_evaluation=True)
        self.assertIn(act, g.legal_actions())
        self.assertEqual(ev.profile, "shape-v1")
        self.assertIn(ev.level, ("Q", "Q0", "legacy"))
        self.assertEqual(ev.as_json()["profile_fingerprint"],
                         EvalProfile.shape_v1().fingerprint)

    def test_live_explanation_is_bounded_without_reordering(self):
        payload = {"best_discard": 7, "legacy_best": 8,
                   "candidates": [{"tile": i} for i in range(10)]}
        compact = _compact_evaluation(payload)
        self.assertEqual(compact["candidate_count"], 10)
        self.assertLessEqual(len(compact["candidates"]), 5)
        self.assertTrue({7, 8}.issubset(
            {x["tile"] for x in compact["candidates"]}))

    def test_explanation_flag_does_not_change_q0_result(self):
        hand = counts("123m456m789m123p5p")
        ctx = EvalContext(hand=hand, visible=tuple(hand))
        on = evaluate_standing(ctx, EvalProfile.shape_v1(explanation=True),
                               level="Q0")
        off = evaluate_standing(ctx, EvalProfile.shape_v1(explanation=False),
                                level="Q0")
        self.assertEqual((on.shanten, on.u1, on.q0),
                         (off.shanten, off.u1, off.q0))

    def test_fixture_cases_are_parseable_and_do_not_hard_code_actions(self):
        data = json.load(open(FIXTURE, encoding="utf-8"))
        self.assertEqual(len(data["cases"]), 5)
        for case in data["cases"]:
            hand = counts(case["hand"])
            self.assertLessEqual(max(hand), 4)
            self.assertEqual(len(case["visible"]), 34)
            self.assertTrue(all(h <= v <= 4
                                for h, v in zip(hand, case["visible"])))
            if sum(hand) == 13 and case["phase"] == "draw":
                vis = tuple(case["visible"])
                ev = evaluate_standing(EvalContext(
                    hand=hand, visible=vis, locked=case.get("locked", 0)),
                    "shape-v1", level="Q0")
                self.assertGreaterEqual(ev.p1, 0.0)
        self.assertNotEqual(data["cases"][0]["actual_action"],
                            data["cases"][0].get("counterfactual_action"))

    def test_reaction_fixture_keeps_real_pong_legal(self):
        g = Game(seed=0)
        g.hands[2] = counts("49m3688p11568sEB")
        g.melds = [[], [], [], []]
        g.discards = [[], [16], [], []]
        g.pending = (1, 16)
        g.phase = "react"
        g.turn = 2
        g._n_claim = 3
        g.react_idx = 0
        g.react_seq = [2, 3, 0]
        g.chows = [0] * 4
        g.freeze = 0
        g.freezer = None
        act, _ev = choose_action(g, 2, evaluator="shape-v1",
                                 return_evaluation=True)
        self.assertEqual(act, PONG)
        self.assertIn(act, g.legal_actions())

    def test_reaction_q0_table_survives_full_q_timeout(self):
        """A short reaction budget falls back to complete Q0, not legacy."""
        g = Game(seed=0)
        g.hands[2] = counts("49m3688p11568sEB")
        g.melds = [[], [], [], []]
        g.discards = [[], [16], [], []]
        g.pending = (1, 16)
        g.phase = "react"
        g.turn = 2
        g._n_claim = 3
        g.react_idx = 0
        g.react_seq = [2, 3, 0]
        g.chows = [0] * 4
        g.freeze = 0
        g.freezer = None
        action, evaluation = evaluate_reaction(
            g, 2, EvalProfile.shape_v1(react_time_budget_ms=7.0))
        self.assertIn(action, g.legal_actions())
        self.assertEqual(evaluation["level"], "Q0")
        self.assertTrue(evaluation["q0_complete"])
        self.assertFalse(evaluation["q_complete"])
        self.assertEqual({item["action"] for item in evaluation["candidates"]},
                         {PASS, PONG})

    def test_reaction_q0_timeout_uses_legacy_only_when_q0_incomplete(self):
        g = Game(seed=0)
        g.hands[2] = counts("49m3688p11568sEB")
        g.melds = [[], [], [], []]
        g.discards = [[], [16], [], []]
        g.pending = (1, 16)
        g.phase = "react"
        g.turn = 2
        g._n_claim = 3
        g.react_idx = 0
        g.react_seq = [2, 3, 0]
        g.chows = [0] * 4
        g.freeze = 0
        g.freezer = None
        action, evaluation = evaluate_reaction(
            g, 2, EvalProfile.shape_v1(react_node_budget=0,
                                       react_time_budget_ms=1.0))
        self.assertIn(action, g.legal_actions())
        self.assertEqual(evaluation["level"], "legacy")
        self.assertEqual(evaluation["fallback_stage"], "Q0")
        self.assertFalse(evaluation["q0_complete"])

    def test_d_counterfactual_is_legacy_four_wan_not_server_timeout(self):
        g = Game(seed=0)
        g.hands[2] = counts("4m346688p115688sB")
        g.melds = [[], [], [], []]
        g.discards = [[], [], [], []]
        g.drawn = [None, None, 12, None]
        g.turn = 2
        g.phase = "discard"
        g.freeze = 0
        g.freezer = None
        self.assertEqual(choose_discard(g, 2), 3)
        # The fixture's portal timeout action is evidence about the server,
        # not a strategy decision; this test intentionally never emits one.

    def test_b_and_e_are_evaluated_from_features_not_action_exceptions(self):
        data = json.load(open(FIXTURE, encoding="utf-8"))
        for ident, tile in (("B", 18), ("E", 26)):
            case = next(c for c in data["cases"] if c["id"] == ident)
            hand = list(counts(case["hand"]))
            hand[tile] -= 1
            ev = evaluate_standing(EvalContext(
                hand=tuple(hand), locked=case["locked"],
                visible=tuple(case["visible"])), "shape-v1", level="Q0")
            self.assertEqual(ev.shanten, shanten(hand, case["locked"]))
            self.assertGreaterEqual(ev.q0, ev.p1)

    def test_random_decomposition_scores_match_reference(self):
        for seed in range(40):
            rng = random.Random(seed)
            deck = [t for t in range(34) for _ in range(4)]
            rng.shuffle(deck)
            hand = [0] * 34
            for t in deck[:13]:
                hand[t] += 1
            ds = enumerate_decompositions(hand, limit=128)
            self.assertEqual(min(d.score for d in ds), shanten(hand))

        # This overlap-heavy hand used to expose the bounded traversal bug:
        # the first 128 allocations were 2-shanten even though a later
        # 1-shanten allocation existed.  The public cap must not change the
        # minimum score.
        late_optimal = counts("45688m556p1223sB")
        self.assertEqual(min(d.score for d in
                             enumerate_decompositions(late_optimal, limit=4)),
                         shanten(late_optimal))

    def test_ycbk_filters_non_baotou_waits_and_locked_context(self):
        # This is a real 0-shanten hand containing two财神 but not爆头.
        hand = [1, 1, 2, 0, 1, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0,
                0, 0, 0, 0, 1, 1, 0, 1, 1, 1, 0, 0, 0, 0, 0, 0, 0, 2]
        normal = evaluate_standing(EvalContext(hand=hand,
                                               visible=tuple(hand)),
                                   "shape-v1", level="Q0")
        gated = evaluate_standing(EvalContext(hand=hand,
                                              visible=tuple(hand),
                                              you_cai_bi_kao=True),
                                  "shape-v1", level="Q0")
        self.assertEqual(normal.shanten, 0)
        self.assertGreater(normal.u1, 0)
        self.assertEqual(gated.u1, 0)

        locked_hand = counts("123m456m789m5p")
        locked_eval = evaluate_standing(EvalContext(
            hand=locked_hand, visible=tuple(locked_hand), locked=1),
            "shape-v1", level="Q0")
        self.assertEqual(sum(locked_hand), 10)
        self.assertGreaterEqual(locked_eval.shanten, 0)


if __name__ == "__main__":
    unittest.main()
