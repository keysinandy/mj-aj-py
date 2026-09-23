"""Regression support tests for the legacy reaction replay audit."""

import json
from pathlib import Path
import unittest

from mj.game import Game, KONG_OPEN, PASS, PONG
from mj import bot
from mj.legacy_react import LegacyReactionProfile
from scripts.legacy_reaction_audit import (
    _public_reaction_context,
    _regression_categories,
)
from tests.test_bot import _react_game


class LegacyReactionAuditTests(unittest.TestCase):
    def test_high_tempo_category_reads_candidate_reason(self):
        row = {
            "legacy_v1_action": PONG,
            "legacy_v2_action": PASS,
            "tempo_cost": 3,
            "candidates": [{
                "v2_reason": "tempo_no_strict_future_gain",
            }],
        }
        self.assertEqual(
            _regression_categories(row),
            {"u1_good_u2_bad", "high_tempo_no_gain"},
        )

    def test_same_unit_action_flip_is_categorized(self):
        row = {
            "legacy_v1_action": PONG,
            "legacy_v2_action": KONG_OPEN,
            "tempo_cost": None,
            "candidates": [],
        }
        self.assertEqual(
            _regression_categories(row), {"pong_kong_flip"})

    def test_fixture_context_contains_public_state_only(self):
        game = _react_game(
            "33m456m789m12p45pE", 0, 2, mode="claim", seat=1)
        context = _public_reaction_context(game, 1)
        self.assertEqual(context["hand"], game.hands[1])
        self.assertEqual(context["visible"], game.visible_counts(1))
        self.assertEqual(context["pending_owner"], 0)
        self.assertEqual(context["pending_tile"], 2)
        self.assertEqual(context["legal_actions"], game.legal_actions())
        self.assertNotIn("hands", context)
        self.assertNotIn("wall", context)
        self.assertNotIn("wall_order", context)
        self.assertNotIn("opponent_hands", context)

    def test_replay_disagreement_fixture_is_stable(self):
        fixture_path = (Path(__file__).parent / "fixtures"
                        / "legacy_reaction_disagreements.json")
        payload = json.loads(fixture_path.read_text(encoding="utf-8"))
        self.assertEqual(
            payload["schema"], "legacy-reaction-public-regression-fixtures-v1")
        self.assertTrue(payload["cases"])
        for case in payload["cases"]:
            context = case["context"]
            seat = context["seat"]

            def build_game():
                game = Game.__new__(Game)
                game.hands = [[0] * 34 for _ in range(4)]
                game.hands[seat] = list(context["hand"])
                game.melds = [
                    [("pong", 27)] * count
                    for count in context["meld_counts"]
                ]
                game.discards = [[] for _ in range(4)]
                game.visible_counts = lambda _seat: list(context["visible"])
                game.live_wall_left = lambda: context["wall_left"]
                game.pending = (context["pending_owner"],
                                context["pending_tile"])
                game.turn = seat
                game.phase = "react"
                game.react_idx = context["react_idx"]
                game._n_claim = context["n_claim"]
                game.dealer = context["dealer"]
                game.base = context["base"]
                game.you_cai_bi_kao = context["you_cai_bi_kao"]
                game.chain = list(context["chain"])
                game.chain_piao = list(context["chain_piao"])
                game.freeze = context["freeze"]
                game.freezer = context["freezer"]
                game.chows = list(context["chows"])
                game.drawn = [None] * 4
                game.done = False
                return game

            expected = case["expected"]
            legal = list(context["legal_actions"])
            self.assertEqual(build_game().legal_actions(), legal)
            v1_action, v1 = bot._choose_react_evaluated(
                build_game(), seat, legal, return_evaluation=True)
            v2_action, v2 = bot._choose_react_evaluated(
                build_game(), seat, legal, return_evaluation=True,
                reaction_profile=LegacyReactionProfile.v2_online(
                    enabled=True))
            self.assertEqual(v1_action, expected["v1_action"], case["id"])
            self.assertEqual(v1["reason"], expected["v1_reason"], case["id"])
            self.assertEqual(v2_action, expected["v2_action"], case["id"])
            self.assertEqual(v2["tempo_cost"], expected["tempo_cost"])
            candidate_v1 = next(
                row for row in v1["candidates"]
                if row["action"] == expected["candidate_action"])
            candidate_v2 = next(
                row for row in v2["candidates"]
                if row["action"] == expected["candidate_action"])
            self.assertEqual(candidate_v1["reason"],
                             expected["candidate_v1_reason"])
            self.assertEqual(candidate_v2["v2_reason"],
                             expected["candidate_v2_reason"])
            self.assertTrue(candidate_v1["accepted"])
            self.assertFalse(candidate_v2["v2_accepted"])


if __name__ == "__main__":
    unittest.main()
