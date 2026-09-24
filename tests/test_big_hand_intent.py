"""Public-only, bounded and deterministic BigHandIntent contract tests."""

import unittest
from dataclasses import FrozenInstanceError

from mj.big_hand_intent import (
    BigHandIntent,
    CHIITOI,
    INTENT_STRONG,
    LUXURY_CHIITOI,
    WHITE_RICH,
    evaluate_big_hand_discard_intents,
    evaluate_big_hand_intent,
)
from mj.shanten import _chiitoi, chiitoi_shanten
from mj.game import Game
from mj.tiles import W, counts


def _singles_and_pairs(wilds):
    hand = [0] * 34
    hand[0] = 2
    for tile in range(1, 12 - wilds):
        hand[tile] = 1
    hand[W] = wilds
    return hand


class TestChiitoiShanten(unittest.TestCase):
    def test_shared_helper_matches_frozen_zero_to_four_wild_edges(self):
        expected = (5, 4, 3, 2, 1)
        for wilds, distance in enumerate(expected):
            hand = _singles_and_pairs(wilds)
            with self.subTest(wilds=wilds):
                self.assertEqual(sum(hand), 13)
                self.assertEqual(chiitoi_shanten(hand), distance)
                self.assertEqual(_chiitoi(hand, 0), distance)

    def test_locked_and_odd_natural_or_quad_edges(self):
        self.assertEqual(chiitoi_shanten(counts("1111m22m33m44m55m6m")), 0)
        self.assertEqual(
            chiitoi_shanten(counts("111m22m33m44m55m6m w")), 0)
        self.assertEqual(chiitoi_shanten(counts("123m456m789m5pwwww"), 1),
                         9)

    def test_helper_rejects_malformed_vector(self):
        with self.assertRaises(ValueError):
            chiitoi_shanten([0] * 33)


class TestBigHandIntent(unittest.TestCase):
    def test_discard_batch_matches_per_root_reference(self):
        game = Game(seed=20260923)
        seat = game.current_seat()
        hand = tuple(game.hands[seat])
        visible = tuple(game.visible_counts(seat))
        discards = tuple(tile for tile, count in enumerate(hand) if count)
        batch = evaluate_big_hand_discard_intents(
            hand, discards, len(game.melds[seat]), visible,
            live_wall=game.live_wall_left(), max_opponent_melds=0)
        reference = []
        for tile in discards:
            standing = list(hand)
            standing[tile] -= 1
            reference.append(evaluate_big_hand_intent(
                standing, len(game.melds[seat]), visible,
                live_wall=game.live_wall_left(), max_opponent_melds=0))
        self.assertEqual(tuple(batch), tuple(reference))

    def test_live_and_dead_natural_luxury_are_distinguished(self):
        hand = counts("333m1122p4455s6m7p w")
        visible = list(hand)
        live = evaluate_big_hand_intent(hand, visible=visible)
        self.assertIn(LUXURY_CHIITOI, live.kinds)
        self.assertIn(2, live.luxury_upgrade_tiles)
        self.assertEqual(live.luxury_upgrade_live, 1)

        dead_visible = list(visible)
        dead_visible[2] = 4
        dead = evaluate_big_hand_intent(hand, visible=dead_visible)
        self.assertNotIn(2, dead.luxury_upgrade_tiles)
        self.assertEqual(dead.luxury_upgrade_live, 0)

    def test_three_natural_plus_wild_is_not_natural_luxury(self):
        hand = counts("333m1122p4455s6m7p w")
        intent = evaluate_big_hand_intent(hand, visible=hand)
        self.assertEqual(intent.luxury_groups, 0)

    def test_four_natural_tiles_count_as_luxury_group(self):
        hand = counts("1111m22m33m44m55m6m")
        intent = evaluate_big_hand_intent(hand, visible=hand)
        self.assertEqual(intent.luxury_groups, 1)

    def test_white_rich_is_resource_not_baotou_claim(self):
        hand = counts("111m22m33m44m5m6p w w")
        intent = evaluate_big_hand_intent(hand, visible=hand)
        self.assertIn(WHITE_RICH, intent.kinds)
        self.assertIn(CHIITOI, intent.kinds)
        self.assertGreaterEqual(intent.wild_live, 0)
        self.assertNotIn("baotou_ready", intent.as_json())

    def test_intent_is_deterministic_and_uses_only_explicit_public_inputs(self):
        hand = counts("111m22m33m44m5m6p w w")
        visible = tuple(hand)
        a = evaluate_big_hand_intent(
            hand, visible=visible, live_wall=32, max_opponent_melds=0)
        # No Game/opponent-hidden/wall-order input exists in this interface.
        b = evaluate_big_hand_intent(
            tuple(hand), visible=tuple(visible), live_wall=32,
            max_opponent_melds=0)
        self.assertEqual(a, b)
        self.assertEqual(a.as_json(), b.as_json())
        self.assertEqual(a.strength, INTENT_STRONG)
        with self.assertRaises(FrozenInstanceError):
            a.strength = "WEAK"

    def test_hidden_hand_and_wall_order_changes_do_not_change_intent(self):
        first = Game(seed=20260923)
        second = Game(seed=20260923)
        second.hands[1][0] = (second.hands[1][0] + 1) % 5
        second.wall.reverse()
        hand = tuple(first.hands[0])
        visible_first = tuple(first.visible_counts(0))
        visible_second = tuple(second.visible_counts(0))
        self.assertEqual(visible_first, visible_second)
        self.assertEqual(
            evaluate_big_hand_intent(hand, visible=visible_first),
            evaluate_big_hand_intent(hand, visible=visible_second),
        )

    def test_public_material_validation(self):
        hand = counts("123m456m789m123p5p")
        invalid_visible = list(hand)
        invalid_visible[0] = 0
        with self.assertRaises(ValueError):
            evaluate_big_hand_intent(hand, visible=invalid_visible)


if __name__ == "__main__":
    unittest.main()
