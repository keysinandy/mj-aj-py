"""Optional Rust batch-kernel parity and fallback tests."""

import unittest

from mj.bot import choose_discard
from mj.game import Game
from mj.legacy_eval import LegacyTwoPlyProfile
from mj.shanten import LEGACY_TWO_PLY_KERNEL_VERSION, shanten

try:
    from tests.test_legacy_eval import _seq100_game
except ImportError:  # pragma: no cover - direct unittest invocation
    from test_legacy_eval import _seq100_game


@unittest.skipUnless(
    LEGACY_TWO_PLY_KERNEL_VERSION,
    "mj_kernels legacy_two_ply_frontier is not installed",
)
class TestLegacyRustKernel(unittest.TestCase):
    def _profile(self, **overrides):
        values = {
            "kernel": "rust",
            "node_budget": 4096,
            "time_budget_ms": 10000,
        }
        values.update(overrides)
        return LegacyTwoPlyProfile(**values)

    def test_seq100_matches_python_reference_and_records_kernel(self):
        rust_game = _seq100_game()
        py_game = _seq100_game()
        rust_action, rust_info = choose_discard(
            rust_game, 0, return_info=True, profile=self._profile())
        py_action, py_info = choose_discard(
            py_game, 0, return_info=True,
            profile=self._profile(kernel="python"))
        self.assertEqual(rust_action, py_action)
        self.assertEqual(rust_action, 17)
        self.assertTrue(rust_info["complete"])
        self.assertEqual(rust_info["actual_kernel"], "rust")
        self.assertEqual(rust_info["kernel_version"],
                         LEGACY_TWO_PLY_KERNEL_VERSION)
        rust_rows = {row["tile"]: row for row in rust_info["candidates"]}
        py_rows = {row["tile"]: row for row in py_info["candidates"]}
        for tile in rust_rows:
            for field in ("future_improve_weight", "future_ukeire",
                          "future_best_discards"):
                self.assertEqual(rust_rows[tile].get(field),
                                 py_rows[tile].get(field),
                                 (tile, field))

    def test_native_budget_fallback_is_transactional(self):
        game = _seq100_game()
        action, info = choose_discard(
            game, 0, return_info=True,
            profile=self._profile(node_budget=0))
        self.assertEqual(action, 20)
        self.assertFalse(info["complete"])
        self.assertEqual(info["level"], "legacy")
        self.assertEqual(info["fallback_reason"], "node_budget_exceeded")
        self.assertEqual(info["actual_kernel"], "legacy")
        self.assertEqual(info["kernel_fallback_reason"],
                         "native_node_budget_exceeded")
        for row in info["candidates"]:
            if row["tile"] in (17, 20):
                self.assertIsNone(row.get("future_ukeire"))

    def test_native_freeze_does_not_expand_child_actions(self):
        game = _seq100_game()
        game.freeze = 2
        game.freezer = 2
        game.drawn[0] = 7
        action, info = choose_discard(
            game, 0, return_info=True, profile=self._profile())
        self.assertEqual(action, 7)
        self.assertTrue(info["complete"])
        self.assertEqual([row["tile"] for row in info["candidates"]], [7])

    def test_native_rejects_invalid_visible_material(self):
        import mj_kernels

        game = _seq100_game()
        hand = list(game.hands[0])
        hand[17] -= 1
        visible = list(game.visible_counts(0))
        masks = []
        for draw, count in enumerate(visible):
            next_hand = list(hand)
            next_hand[draw] += 1
            masks.append(sum(1 << tile for tile, value in enumerate(next_hand)
                             if value > 0) if count < 4 else 0)
        visible[0] = 5
        with self.assertRaises(ValueError):
            mj_kernels.legacy_two_ply_frontier(
                [hand], [shanten(hand, 1)], visible, [masks],
                1, False, 4096, 1000.0, True)

    def test_random_public_states_match_python_reference(self):
        for seed in range(2):
            rust_game = Game(seed=seed)
            py_game = Game(seed=seed)
            seat = rust_game.current_seat()
            rust_action, rust_info = choose_discard(
                rust_game, seat, return_info=True, profile=self._profile())
            py_action, py_info = choose_discard(
                py_game, seat, return_info=True,
                profile=self._profile(kernel="python"))
            self.assertEqual(rust_action, py_action, seed)
            self.assertEqual(rust_info["complete"], py_info["complete"], seed)
            if rust_info["complete"] and py_info["complete"]:
                rust_rows = {row["tile"]: row
                             for row in rust_info["candidates"]}
                py_rows = {row["tile"]: row
                           for row in py_info["candidates"]}
                self.assertEqual(rust_rows.keys(), py_rows.keys())
                for tile in rust_rows:
                    self.assertEqual(
                        rust_rows[tile].get("future_ukeire"),
                        py_rows[tile].get("future_ukeire"),
                        (seed, tile),
                    )


if __name__ == "__main__":
    unittest.main()
