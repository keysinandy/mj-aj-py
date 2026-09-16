"""Paired-game harness tests (P8)."""

import unittest

from mj.game import HU
from mj.training.distillation_profile import OpponentPopulationProfile
from mj.training.paired_eval import (
    PairedSchedule,
    opponent_callables,
    paired_score_report,
    play_pair,
    policy_callable,
)


def _first_legal(game, seat):
    legal = game.legal_actions()
    hu = [action for action in legal if action == HU]
    return hu[0] if hu else legal[0]


def _row(seed=240000, seat=0, dealer=0, ycbk=False, delta=1.0,
         winner=None):
    return {"index": 0, "seed": seed, "seat": seat, "dealer": dealer,
            "you_cai_bi_kao": ycbk, "cluster": f"game:{seed}",
            "hero_seat": seat, "score_candidate": delta,
            "score_baseline": 0.0, "delta": delta,
            "winner_candidate": winner, "winner_baseline": None,
            "multiplier_candidate": None, "multiplier_baseline": None,
            "draw_candidate": False, "draw_baseline": False}


class TestSchedule(unittest.TestCase):
    def test_balanced_seat_dealer_and_ycbk(self):
        schedule = PairedSchedule(seed_start=100, games=16,
                                  ycbk_variants=(False, True))
        rows = schedule.rows()
        self.assertEqual(len(rows), 32)
        combos = {(row["seat"], row["dealer"]) for row in rows}
        self.assertEqual(len(combos), 16)
        self.assertEqual({row["you_cai_bi_kao"] for row in rows},
                         {False, True})
        self.assertEqual(rows[0]["cluster"], "game:100")
        self.assertEqual(rows[-1]["cluster"], "game:115")
        self.assertEqual(schedule.pairs, 32)


class TestPairedReport(unittest.TestCase):
    def test_superiority_requires_positive_ci_lower(self):
        rows = [_row(seed=240000 + index, delta=2.0) for index in range(32)]
        report = paired_score_report(rows, required_pairs=16, rounds=200)
        self.assertEqual(report["verdict"], "superior")
        self.assertGreater(report["ci95_lower"], 0)
        self.assertTrue(report["meets_required_pairs"])
        self.assertEqual(report["cluster"], "source_game_seed")
        self.assertTrue(report["fingerprint"])

    def test_ambiguous_and_regression_labels(self):
        mixed = [_row(seed=240000 + index, delta=2.0 if index % 2 else -2.0)
                 for index in range(32)]
        report = paired_score_report(mixed, rounds=200)
        self.assertEqual(report["verdict"], "non_regression_ambiguous")
        negative = [_row(seed=240000 + index, delta=-1.0)
                    for index in range(32)]
        report = paired_score_report(negative, rounds=200)
        self.assertEqual(report["verdict"], "regression")

    def test_secondary_diagnostics(self):
        rows = [_row(seed=240000 + index, delta=1.0, winner=0)
                for index in range(4)]
        report = paired_score_report(rows, rounds=50)
        diagnostics = report["secondary_diagnostics"]
        self.assertEqual(diagnostics["candidate_win_rate"], 1.0)
        self.assertEqual(diagnostics["draw_rate"], 0.0)


class TestPlayPair(unittest.TestCase):
    def test_pair_uses_identical_schedule_and_reports_delta(self):
        row = {"index": 0, "seed": 42, "seat": 1, "dealer": 2,
               "you_cai_bi_kao": True, "cluster": "game:42"}
        played = play_pair(row, candidate=_first_legal,
                           baseline=_first_legal,
                           opponents=[_first_legal] * 4)
        self.assertEqual(played["hero_seat"], 1)
        self.assertEqual(played["delta"],
                         played["score_candidate"] - played["score_baseline"])
        self.assertIsInstance(played["draw_candidate"], bool)

    def test_opponent_splits_build_per_seat_callables(self):
        population = OpponentPopulationProfile(members=(("legacy", 1.0),))
        self_play = opponent_callables("self_play", candidate=_first_legal,
                                       population=population)
        self.assertEqual(len(self_play), 4)
        frozen = opponent_callables("frozen_population",
                                    candidate=_first_legal,
                                    population=population,
                                    source_group="game:1")
        self.assertEqual(len(frozen), 4)
        shape_v1 = opponent_callables("legacy_shape_v1",
                                      candidate=_first_legal,
                                      population=population)
        self.assertEqual(len(shape_v1), 4)
        with self.assertRaises(ValueError):
            opponent_callables("nope", candidate=_first_legal,
                               population=population)

    def test_policy_callable_sources(self):
        self.assertTrue(callable(policy_callable("heuristic:legacy")))
        self.assertTrue(callable(policy_callable("shape-v2")))
        with self.assertRaises(ValueError):
            policy_callable("unknown-source")


if __name__ == "__main__":
    unittest.main()
