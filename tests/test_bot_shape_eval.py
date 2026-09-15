"""Contract tests for the local score-based BOT comparison report."""

import unittest
from unittest.mock import patch

from scripts import bot_shape_eval


class BotShapeEvalTests(unittest.TestCase):
    def test_round_score_is_primary_metric(self):
        def fake_play(seed, seat, dealer, ycbk, evaluator):
            score = 10.0 if evaluator == "shape-v1" else 2.0
            return {"score": score, "win": False, "mult": None,
                    "draw": False}

        with patch.object(bot_shape_eval, "_play", side_effect=fake_play):
            result = bot_shape_eval.run(games=16, seed_start=1,
                                        evaluator="shape-v1")

        self.assertEqual(result["primary_metric"],
                         "hero_round_score_points")
        self.assertTrue(result["higher_is_better"])
        self.assertEqual(result["legacy_score"]["total"], 32.0)
        self.assertEqual(result["legacy_score"]["mean"], 2.0)
        self.assertEqual(result["shape_score"]["total"], 160.0)
        self.assertEqual(result["shape_score"]["mean"], 10.0)
        self.assertEqual(result["score_delta"]["mean"], 8.0)


if __name__ == "__main__":
    unittest.main()
