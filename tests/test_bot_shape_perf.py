"""Tests for the interleaved offline performance harness."""

import unittest
from unittest.mock import patch

from scripts import bot_shape_perf


class BotShapePerfTests(unittest.TestCase):
    def test_interleaved_schedule_reverses_order_and_compares_medians(self):
        calls = []

        def fake_run(games, seed_start, evaluator):
            calls.append((games, seed_start, evaluator))
            elapsed = {"shape-v1": 1.0, "shape-v2": 1.1}[evaluator]
            return {
                "games": games,
                "seed_start": seed_start,
                "evaluator": evaluator,
                "elapsed_s": elapsed * games,
                "elapsed_per_game_s": elapsed,
                "discard": {
                    "p50_ms": 1.0, "p95_ms": 2.0, "p99_ms": 3.0,
                    "max_ms": 4.0, "fallback_rate": 0.0,
                    "complete_level_rate": 1.0,
                },
                "react": {
                    "p50_ms": 0.1, "p95_ms": 0.2, "p99_ms": 0.3,
                    "max_ms": 0.4, "fallback_rate": 0.0,
                    "complete_level_rate": 1.0,
                },
            }

        with patch.object(bot_shape_perf, "run", side_effect=fake_run):
            result = bot_shape_perf.run_interleaved(
                games=2, seed_start=7,
                evaluators=("shape-v1", "shape-v2"), repetitions=3)

        self.assertEqual(calls, [
            (2, 7, "shape-v1"), (2, 7, "shape-v2"),
            (2, 7, "shape-v2"), (2, 7, "shape-v1"),
            (2, 7, "shape-v1"), (2, 7, "shape-v2"),
        ])
        self.assertEqual(result["schema"],
                         "bot-ev-discard/interleaved-performance-v1")
        self.assertEqual(result["aggregate"]["shape-v1"]["runs"], 3)
        self.assertAlmostEqual(
            result["comparison"]["shape-v2"]
            ["elapsed_per_game_median_increase_vs_shape_v1"], .1)
        self.assertTrue(result["comparison"]["shape-v2"]
                        ["performance_gate_passed"])
        self.assertTrue(result["offline_only"])
        self.assertTrue(result["legacy_default_unchanged"])


if __name__ == "__main__":
    unittest.main()
