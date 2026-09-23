"""Tests for the offline paired score evidence runner."""

import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch

from scripts import bot_ev_score_eval


class BotEvScoreEvalTests(unittest.TestCase):
    def test_aggregate_timing_uses_raw_decision_samples(self):
        result = bot_ev_score_eval._merge_eval_stats([
            {"evaluation": {
                "calls": 5, "ordinary_discard_calls": 5,
                "ev2_complete": 5, "q0_fallback": 0, "levels": {},
                "fallbacks": {}, "timing_ms": {"n": 2, "mean_ms": 50},
                "ordinary_timing_ms": {"n": 5, "mean_ms": 40.2},
                "_timing_samples_ms": [1.0, 1.0, 1.0, 99.0, 99.0],
                "_ordinary_timing_samples_ms": [1.0, 1.0, 1.0, 99.0, 99.0],
            }},
        ])
        self.assertEqual(result["timing_ms"]["p50_ms"], 1.0)
        self.assertEqual(result["timing_ms"]["p95_ms"], 99.0)
        self.assertEqual(result["ordinary_timing_ms"]["p99_ms"], 99.0)

    def test_runner_records_balanced_schedule_and_keeps_gate_closed(self):
        def fake_play(seed, seat, dealer, ycbk, evaluator, *, profile=None):
            score = 2.0 if evaluator == "legacy-v1" else 3.0
            return {"score": score, "win": False, "mult": None,
                    "draw": False, "evaluation": {
                        "calls": 1, "ordinary_discard_calls": 1,
                        "ev2_complete": 1 if evaluator == "shape-v2" else 0,
                        "q0_fallback": 0, "levels": {"V2-EV2": 1}
                        if evaluator == "shape-v2" else {},
                        "fallbacks": {},
                        "timing_ms": {"n": 1, "mean_ms": 1.0},
                    }}

        with patch.object(bot_ev_score_eval, "_play", side_effect=fake_play):
            result = bot_ev_score_eval.run(
                games=16, seed_start=1, evaluator="shape-v2",
                required_pairs=4096, bootstrap_rounds=20)
        self.assertEqual(result["games_completed"], 16)
        self.assertTrue(result["release_gate"]["balance"]["passed"])
        self.assertFalse(result["release_gate"]["passed"])
        self.assertEqual(result["score_delta"]["mean"], 1.0)
        self.assertEqual(result["candidate_evaluation"]["ev2_coverage"], 1.0)
        self.assertTrue(result["offline_only"])
        self.assertTrue(result["default_strategy_unchanged"])
        self.assertEqual(result["contract"]["scope"], "discard")

    def test_full_ev2_cli_selects_explicit_offline_budget(self):
        captured = {}

        def fake_run(*args, **kwargs):
            captured.update(kwargs)
            return {"schema": "test", "input_fingerprint": "x",
                    "source_groups": 0, "fingerprint": "y"}

        with patch.object(bot_ev_score_eval, "run", side_effect=fake_run):
            with redirect_stdout(StringIO()):
                bot_ev_score_eval.main(["--games", "1", "--full-ev2"])
        self.assertEqual(captured["profile"].node_budget, 10_000_000)
        self.assertEqual(captured["profile"].time_budget_ms, 30_000.0)


if __name__ == "__main__":
    unittest.main()
