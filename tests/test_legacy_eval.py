"""legacy-two-ply-v1 public-information evaluator contract tests."""

import json
import unittest

from mj.bot import choose_action, choose_discard
from mj.game import Game
from mj.legacy_eval import (
    LegacyRootCandidate,
    LegacyTwoPlyProfile,
    evaluate_legacy_two_ply,
)
from mj.shanten import shanten
from mj.logview import render
from mj.platform.bot_client import _compact_evaluation
from mj.platform.runner import make_decide
from mj.tiles import counts, name


def _seq100_game():
    """Reconstruct the public seq=100 state from the 20260920 replay log.

    P0 has the 9t pong and has just drawn 8w.  Opponent concealed hands are
    intentionally absent; only the public rivers and exposed pongs are used.
    """
    game = Game.__new__(Game)
    game.hands = [counts("1388m34679p3s w"), [0] * 34, [0] * 34, [0] * 34]
    game.melds = [[("pong", 26)], [("pong", 31)], [], []]
    game.discards = [
        [28, 29, 32, 25],       # P0: 南、西、发、8t
        [28, 29, 9, 18],        # P1: 南、西、1b、1t (9t was called)
        [30, 31, 28, 17],       # P2: 北、中、南、9b
        [30, 25, 8, 9],         # P3: 北、8t、9w、1b
    ]
    game.drawn = [7, None, None, None]
    game.freeze = 0
    game.freezer = None
    game.phase = "discard"
    game.turn = 0
    game.dealer = 0
    game.wall = [0] * 60
    game.done = False
    game.pending = None
    game.chows = [0] * 4
    game.chain = [0] * 4
    game.chain_piao = [0] * 4
    game.scores = [0] * 4
    game.you_cai_bi_kao = False
    game._kong_draw = False
    return game


class TestLegacyTwoPlyProfile(unittest.TestCase):
    def test_fingerprint_is_stable_and_versioned(self):
        a = LegacyTwoPlyProfile.default()
        b = LegacyTwoPlyProfile.default()
        c = LegacyTwoPlyProfile(time_budget_ms=9.0)
        d = LegacyTwoPlyProfile(kernel="python")
        self.assertEqual(a.fingerprint, b.fingerprint)
        self.assertNotEqual(a.fingerprint, c.fingerprint)
        self.assertNotEqual(a.fingerprint, d.fingerprint)
        self.assertEqual(a.as_json()["fingerprint"], a.fingerprint)
        self.assertEqual(a.as_json()["kernel"], "auto")

    def test_result_is_immutable_and_missing_is_not_zero(self):
        game = _seq100_game()
        action, evaluation = choose_discard(
            game, 0, return_info=True,
            profile=LegacyTwoPlyProfile(node_budget=0, time_budget_ms=1000))
        self.assertEqual(action, 20)  # complete legacy fallback: 3t
        self.assertFalse(evaluation["complete"])
        self.assertEqual(evaluation["level"], "legacy")
        self.assertEqual(evaluation["fallback_reason"], "node_budget_exceeded")
        frontier = [c for c in evaluation["candidates"]
                    if not c.get("missing") or
                    "future_incomplete" in c.get("missing", [])]
        self.assertTrue(frontier)
        for candidate in frontier:
            self.assertIsNone(candidate.get("future_improve_weight"))
            self.assertIsNone(candidate.get("future_ukeire"))
            self.assertNotEqual(candidate.get("future_improve_weight"), 0)
        json.dumps(evaluation, ensure_ascii=False, allow_nan=False)

    def test_seq100_future_frontier_prefers_9b(self):
        game = _seq100_game()
        profile = LegacyTwoPlyProfile(node_budget=4096, time_budget_ms=1000)
        action, evaluation = choose_discard(
            game, 0, return_info=True, profile=profile)
        self.assertEqual(action, 17)  # 9b; 3t is 20
        self.assertTrue(evaluation["complete"])
        by_tile = {row["tile"]: row for row in evaluation["candidates"]}
        self.assertEqual(by_tile[17]["ukeire"], by_tile[20]["ukeire"])
        self.assertLess(by_tile[11]["ukeire"], by_tile[17]["ukeire"])
        self.assertGreater(by_tile[17]["future_ukeire"],
                           by_tile[20]["future_ukeire"])
        self.assertGreater(by_tile[17]["future_improve_weight"], 0)
        self.assertEqual(evaluation["selected"], 17)

    def test_v1_off_is_rollback_to_legacy(self):
        game = _seq100_game()
        baseline = choose_discard(game, 0)
        action, info = choose_discard(
            game, 0, return_info=True,
            profile=LegacyTwoPlyProfile(enabled=False, time_budget_ms=1000))
        self.assertEqual(baseline, 20)
        self.assertEqual(action, baseline)
        self.assertFalse(info["complete"])
        self.assertEqual(info["fallback_reason"], "profile_disabled")

    def test_freeze_never_expands_legal_root(self):
        game = _seq100_game()
        game.freeze = 2
        game.freezer = 2
        game.drawn[0] = 7
        action, info = choose_discard(
            game, 0, return_info=True,
            profile=LegacyTwoPlyProfile(node_budget=4096, time_budget_ms=1000))
        self.assertEqual(action, 7)
        self.assertIn(action, [row["tile"] for row in info["candidates"]])

    def test_choose_action_exposes_profile_without_changing_reaction_scope(self):
        game = _seq100_game()
        action, info = choose_action(
            game, 0, evaluator="legacy-two-ply-v1",
            return_evaluation=True)
        self.assertEqual(action, info["selected"])
        self.assertEqual(info["profile"], "legacy-two-ply-v1")

    def test_visible_state_isolated_and_invalid_material_falls_back(self):
        game = _seq100_game()
        profile = LegacyTwoPlyProfile(node_budget=4096, time_budget_ms=1000)
        baseline_action, baseline = choose_discard(
            game, 0, return_info=True, profile=profile)
        game.discards[2].pop()  # a public tile changes the future weights
        changed_action, changed = choose_discard(
            game, 0, return_info=True, profile=profile)
        self.assertEqual((baseline_action, changed_action), (17, 17))
        first = {c["tile"]: c for c in baseline["candidates"]}[17]
        second = {c["tile"]: c for c in changed["candidates"]}[17]
        self.assertNotEqual(first["future_ukeire"], second["future_ukeire"])

        root_hand = list(_seq100_game().hands[0])
        root_hand[17] -= 1
        root = LegacyRootCandidate(
            tile=17, hand=tuple(root_hand),
            shanten=shanten(root_hand, 1),
        )
        bad_visible = list(_seq100_game().visible_counts(0))
        bad_visible[0] = 5
        _, invalid = evaluate_legacy_two_ply(
            _seq100_game(), 0, [root], 1, tuple(bad_visible), profile)
        self.assertFalse(invalid.complete)
        self.assertIsNone(invalid.candidates[0].get("future_ukeire"))
        self.assertIn("visible_invalid", invalid.missing)

        illegal_root = LegacyRootCandidate(
            tile=31, hand=tuple(root_hand),
            shanten=shanten(root_hand, 1),
        )
        _, illegal = evaluate_legacy_two_ply(
            _seq100_game(), 0, [illegal_root], 1,
            tuple(_seq100_game().visible_counts(0)), profile)
        self.assertFalse(illegal.complete)
        self.assertIn("root_discard_not_in_hand", illegal.missing)

    def test_live_explanation_is_compact_without_re_evaluation(self):
        game = _seq100_game()
        profile = LegacyTwoPlyProfile(node_budget=4096, time_budget_ms=1000)
        _, full = choose_discard(game, 0, return_info=True, profile=profile)
        compact = _compact_evaluation(full, limit=1)
        self.assertEqual(compact["candidate_count"],
                         len(full["candidates"]))
        self.assertEqual(compact["selected"], full["selected"])
        selected = next(row for row in compact["candidates"]
                        if row.get("tile") == compact["selected"])
        self.assertIn("future_ukeire", selected)
        self.assertEqual(selected["future_ukeire"],
                         next(row for row in full["candidates"]
                              if row.get("tile") == full["selected"])
                         ["future_ukeire"])

    def test_runner_marks_actual_v1_evaluator_and_old_log_stays_unrecorded(self):
        decide = make_decide("bot", evaluator="legacy-two-ply-v1")
        result = decide(_seq100_game(), 0)
        self.assertIsInstance(result, tuple)
        self.assertEqual(getattr(decide, "bot_evaluator"),
                         "legacy-two-ply-v1")
        text = render({"type": "decision", "phase": "draw", "seq": 1,
                       "id": 1, "legal": [0], "action": 0,
                       "latency_ms": 0}, 0)
        self.assertIn("eval=legacy_unrecorded", text)


if __name__ == "__main__":
    unittest.main()
