"""Public-history and hidden-information boundary regression tests."""

import copy
import unittest
from types import SimpleNamespace

from mj.belief import (
    InformationHistory,
    PublicEvent,
    history_from_game,
    history_from_records,
    replay_against_context,
    replay_public_history,
)
from mj.decision.context import PublicDecisionContext
from mj.game import Game
from mj.search.tree import HeroInfoNodeKey


class TestPublicEventHistory(unittest.TestCase):
    def test_order_is_semantic_but_transport_identity_is_not(self):
        first = PublicEvent("DISCARD", actor=0, phase="discard",
                            public_payload={"tile": 3}, gid="a", seq=1,
                            log_format="old")
        second = PublicEvent("PASS", actor=1, phase="response_peng",
                             gid="a", seq=2)
        same_semantics = InformationHistory((PublicEvent(
            "DISCARD", actor=0, phase="discard", public_payload={"tile": 3},
            gid="different", seq=90, log_format="new"), PublicEvent(
                "PASS", actor=1, phase="response_peng", gid="different", seq=91)))
        history = InformationHistory((first, second))
        self.assertEqual(history.history_hash, same_semantics.history_hash)
        self.assertNotEqual(history.history_hash,
                             InformationHistory((second, first)).history_hash)

    def test_game_history_records_action_then_internal_draw(self):
        game = Game(seed=20260916)
        action = game.legal_actions()[0]
        game.step(action)
        events = game.public_history.events
        self.assertEqual(events[-1].event_type, "DISCARD")
        self.assertEqual(events[-1].public_payload["tile"], action)
        self.assertEqual(events[0].event_type, "ROUND_START")

    def test_hidden_world_changes_do_not_change_public_context_or_key(self):
        game = Game(seed=17)
        other = copy.deepcopy(game)
        # Swap two hidden cards in the same opponent hand.  Counts and all
        # public material remain unchanged, while the hidden assignment does.
        row = other.hands[1]
        left, right = next(i for i, value in enumerate(row) if value), next(
            i for i in range(33) if row[i] == 0)
        row[left] -= 1
        row[right] += 1
        context_a = PublicDecisionContext.from_game_complete(
            game, game.current_seat())
        context_b = PublicDecisionContext.from_game_complete(
            other, other.current_seat())
        self.assertEqual(context_a.context_hash, context_b.context_hash)
        self.assertEqual(history_from_game(game).history_hash,
                         history_from_game(other).history_hash)
        self.assertEqual(
            HeroInfoNodeKey.from_context(context_a, history_from_game(game)).value,
            HeroInfoNodeKey.from_context(context_b, history_from_game(other)).value)

    def test_platform_gap_is_degraded_without_guessing_response_order(self):
        history = history_from_records([
            {"type": "snapshot", "snap": {"phase": "response_peng"}},
            {"type": "events", "events": [
                {"type": "tile_discarded", "seat": 0, "tile": "1w", "seq": 4},
                {"type": "pass", "seat": 1, "seq": 5},
            ]},
        ])
        self.assertTrue(history.history_incomplete)
        self.assertIn("snapshot_without_event_history", history.incomplete_reasons)
        self.assertTrue(replay_public_history(history).incomplete)

    def test_unknown_schema_fields_are_not_silently_dropped(self):
        with self.assertRaises(ValueError):
            PublicEvent.from_json({"event_type": "PASS", "ordering_policy": "x"})
        with self.assertRaises(ValueError):
            InformationHistory.from_json({"events": [], "strategy": "x"})

    def test_replay_tracks_response_cursor_and_chow_start(self):
        history = InformationHistory((
            PublicEvent("ROUND_START", actor=0, phase="discard"),
            PublicEvent("DISCARD", actor=0, phase="discard",
                        public_payload={"tile": 5}),
            PublicEvent("PASS", actor=1, phase="react"),
            PublicEvent("PASS", actor=2, phase="react"),
            PublicEvent("PASS", actor=3, phase="react"),
            PublicEvent("CHOW", actor=1, phase="react",
                        public_payload={"tile": 5, "position": 1}),
        ))
        context = SimpleNamespace(
            discards=((), (), (), ()),
            melds=((), (("chow", 4),), (), ()),
            chows=(0, 1, 0, 0), pending_owner=None, pending_tile=None,
            phase="discard", turn=1, freeze=0, freezer=None,
            chain_counts=(0, 0, 0, 0), react_seq=(), react_index=None,
            react_claim_count=None,
        )
        report = replay_against_context(history, context)
        self.assertTrue(report["matched"], report)


if __name__ == "__main__":
    unittest.main()
