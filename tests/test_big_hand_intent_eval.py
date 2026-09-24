import unittest

from scripts.legacy_big_hand_intent_eval import (
    _bucket_summary,
    _challenger_event_counts,
)


class TestBigHandIntentScoreAudit(unittest.TestCase):
    def test_non_override_challengers_are_bucketed_separately(self):
        rows = [{
            "score_delta": -3.0,
            "candidate": {
                "intent_kinds_seen": ["CHIITOI"],
                "override_events": [{
                    "override": False,
                    "live_wall": 20,
                    "opponent_melds": 2,
                    "intent_kinds": ["CHIITOI"],
                }],
            },
        }]
        summary = _bucket_summary(rows, rounds=100, seed=20260923)
        event_counts = _challenger_event_counts(
            rows[0]["candidate"]["override_events"])

        self.assertEqual(summary["game_with_challenger"]["n"], 1)
        self.assertEqual(
            summary["challenger_live_wall:12-23"]["mean"], -3.0)
        self.assertEqual(
            summary["challenger_opponent_melds:2+"]["mean"], -3.0)
        self.assertNotIn("game_with_override", summary)
        self.assertEqual(event_counts["total"], 1)
        self.assertEqual(event_counts["overrides"], 0)
        self.assertEqual(event_counts["by_live_wall"], {"12-23": 1})
        self.assertEqual(event_counts["by_opponent_melds"], {"2+": 1})
        self.assertEqual(event_counts["overrides_by_live_wall"], {})

    def test_actual_override_gets_its_own_wall_and_meld_strata(self):
        rows = [{
            "score_delta": 2.0,
            "candidate": {
                "intent_kinds_seen": ["LUXURY_CHIITOI"],
                "override_events": [{
                    "override": True,
                    "live_wall": 8,
                    "opponent_melds": 1,
                    "intent_kinds": ["LUXURY_CHIITOI"],
                }],
            },
        }]
        summary = _bucket_summary(rows, rounds=100, seed=20260923)
        event_counts = _challenger_event_counts(
            rows[0]["candidate"]["override_events"])

        self.assertEqual(summary["game_with_override"]["mean"], 2.0)
        self.assertEqual(
            summary["override_live_wall:0-11"]["mean"], 2.0)
        self.assertEqual(
            summary["override_opponent_melds:1"]["mean"], 2.0)
        self.assertEqual(event_counts["overrides"], 1)
        self.assertEqual(event_counts["overrides_by_live_wall"],
                         {"0-11": 1})
        self.assertEqual(event_counts["overrides_by_opponent_melds"],
                         {"1": 1})


if __name__ == "__main__":
    unittest.main()
