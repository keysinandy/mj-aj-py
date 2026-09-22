import json
import unittest
from unittest import mock

from mj import bot as bot_mod
from mj.game import HU, KONG_ADD_BASE, KONG_OPEN, PASS, PONG
from mj.legacy_eval import LegacyTwoPlyProfile
from mj.tiles import W, counts
from tests.test_bot import _draw_game, _react_game


def _progress(**values):
    base = {
        "shanten": 1,
        "ukeire_types": 2,
        "ukeire_live": 8,
        "baotou_ready": False,
        "baotou_ukeire_types": None,
        "baotou_ukeire_live": None,
        "piao_draw_types": 0,
        "piao_draw_live": 0,
    }
    base.update(values)
    return bot_mod.LegacyShapeProgress(**base)


class LegacyShapeProgressTests(unittest.TestCase):
    def test_baotou_ready_upgrade_is_acceptable(self):
        before = _progress(baotou_ready=False)
        after = _progress(baotou_ready=True)
        self.assertEqual(
            bot_mod._significant_progress(before, after, PONG),
            "baotou_progress")

    def test_wait_type_expansion_requires_two_types_and_no_live_loss(self):
        before = _progress(shanten=0, ukeire_types=2, ukeire_live=8)
        after = _progress(shanten=0, ukeire_types=4, ukeire_live=8)
        weak = _progress(shanten=0, ukeire_types=3, ukeire_live=9)
        self.assertEqual(
            bot_mod._significant_progress(before, after, PONG),
            "wait_expansion")
        self.assertIsNone(
            bot_mod._significant_progress(before, weak, PONG))

    def test_positive_shanten_uses_absolute_and_ratio_thresholds(self):
        before = _progress(shanten=1, ukeire_live=10)
        absolute_only = _progress(shanten=1, ukeire_live=14)
        significant = _progress(shanten=1, ukeire_live=15)
        self.assertIsNone(bot_mod._significant_progress(
            before, absolute_only, PONG))
        self.assertEqual(
            bot_mod._significant_progress(before, significant, PONG),
            "ukeire_expansion")

    def test_piao_draw_progress_is_structural(self):
        no_wild = counts("123m456m789m123p5p")
        piao = counts("123m456m789m123pw")
        visible = piao
        before = bot_mod._legacy_shape_progress(
            no_wild, 0, visible, piao_allowed=True)
        after = bot_mod._legacy_shape_progress(
            piao, 0, visible, piao_allowed=True)
        self.assertEqual(before.piao_draw_live, 0)
        self.assertGreaterEqual(after.piao_draw_live, 2)
        self.assertTrue(after.baotou_ready)
        synthetic_before = _progress(piao_draw_live=0)
        synthetic_after = _progress(piao_draw_live=4)
        self.assertEqual(
            bot_mod._significant_progress(
                synthetic_before, synthetic_after, PONG),
            "piao_progress")

    def test_no_rust_baotou_ukeire_cannot_authorize_claim(self):
        bot_mod._cached_legacy_shape_progress.cache_clear()
        game = _react_game("33m456m789m123p45p", 0, 2, mode="claim")
        with mock.patch.object(bot_mod, "BAOTOU_UKEIRE_RUST", False):
            self.assertEqual(bot_mod.choose_action(game, 1), PASS)

    def test_react_evaluation_is_json_serialisable(self):
        game = _react_game("33m456m789m123p45p", 0, 2, mode="claim")
        action, evaluation = bot_mod.choose_action(
            game, 1, evaluator="legacy", return_evaluation=True)
        self.assertEqual(action, PONG)
        self.assertEqual(evaluation["reason"], "baotou_progress")
        self.assertEqual(evaluation["version"],
                         bot_mod.LEGACY_SHAPE_PROGRESS_VERSION)
        self.assertIn("before_progress", evaluation)
        self.assertIn("after_progress", evaluation)
        self.assertIn("thresholds", evaluation)
        self.assertIn("progress_diagnostics", evaluation)
        json.dumps(evaluation, ensure_ascii=False, allow_nan=False)

    def test_normal_discard_does_not_call_progress_or_decomposition(self):
        game = _draw_game("123m456m789m11p555s", 24)
        before = bot_mod._legacy_progress_diagnostics()
        bot_mod.choose_discard(game, 0)
        after = bot_mod._legacy_progress_diagnostics()
        self.assertEqual(after["progress_calls"], before["progress_calls"])
        self.assertEqual(
            after["decomposition_calls"], before["decomposition_calls"])


class KongGuardTests(unittest.TestCase):
    def test_closed_kong_rejects_123333(self):
        hand = counts("123333m456m789m11p")
        guard = bot_mod._kong_structure_guard(hand, 0, "closed", 2)
        self.assertFalse(guard["structure_safe"])
        self.assertEqual(guard["structure_reason"], "tile_used_by_sequence")

        game = _draw_game("123333m456m789m11p", 2)
        action, detail = bot_mod._choose_draw_action(
            game, 0, game.legal_actions())
        self.assertNotEqual(action, -7)
        self.assertEqual(detail["kong_candidates"][0]["structure_reason"],
                         "tile_used_by_sequence")

    def test_closed_kong_accepts_triplet_plus_redundant_single(self):
        hand = counts("1111m456m789m11pw5s")
        guard = bot_mod._kong_structure_guard(hand, 0, "closed", 0)
        self.assertTrue(guard["structure_safe"])
        self.assertEqual(guard["structure_reason"],
                         "safe_triplet_plus_single")

        game = _draw_game("1111m456m789m11pw5s", 27)
        action, detail = bot_mod._choose_draw_action(
            game, 0, game.legal_actions())
        self.assertEqual(action, -7)
        self.assertEqual(detail["reason"], "kong_expected_value")
        self.assertTrue(detail["kong_evaluation"]["gate_passed"])

    def test_add_kong_rejects_tile_used_by_sequence(self):
        game = _draw_game(
            "123m456m789m1pw", 2, melds=[("pong", 2)])
        self.assertIn(KONG_ADD_BASE - 2, game.legal_actions())
        guard = bot_mod._kong_structure_guard(
            game.hands[0], len(game.melds[0]), "add", 2)
        self.assertFalse(guard["structure_safe"])
        self.assertEqual(guard["structure_reason"], "tile_used_by_sequence")

        action, detail = bot_mod._choose_draw_action(
            game, 0, game.legal_actions())
        self.assertEqual(action, HU)
        self.assertEqual(detail["kong_candidates"][0]["rejection_reason"],
                         "tile_used_by_sequence")

    def test_open_kong_rejects_three_tiles_not_used_as_triplet(self):
        game = _react_game(
            "123m234m345m789p5s", 0, 2, mode="claim")
        self.assertIn(KONG_OPEN, game.legal_actions())
        action, evaluation = bot_mod.choose_action(
            game, 1, evaluator="legacy", return_evaluation=True)
        self.assertEqual(action, PASS)
        self.assertEqual(evaluation["kong"]["structure_reason"],
                         "tile_used_by_sequence")
        self.assertEqual(evaluation["kong"]["rejection_reason"],
                         "tile_used_by_sequence")

    def test_post_kong_live_wait_decrease_is_rejected(self):
        baseline = _progress(shanten=0, ukeire_live=8)
        post = _progress(shanten=0, ukeire_live=2)
        self.assertEqual(
            bot_mod._kong_shape_gate(baseline, post),
            "shape_ukeire_lower")

    def test_zero_winning_mass_rejects_kong_kai(self):
        standing = counts("456m789m11pw5s")
        remaining = [0] * 34
        wait_tiles, mass = bot_mod._kong_winning_tiles(
            standing, 1, remaining)
        self.assertEqual(wait_tiles, ())
        self.assertEqual(mass, 0)

    def test_rejected_kong_never_reaches_ev(self):
        game = _draw_game("123333m456m789m11p", 2)
        _action, detail = bot_mod._choose_draw_action(
            game, 0, game.legal_actions())
        candidate = detail["kong_candidates"][0]
        self.assertFalse(candidate["gate_passed"])
        self.assertEqual(candidate["kong_expected_value"], 0.0)
        self.assertEqual(candidate["kong_win_probability"], 0.0)

    def test_self_kong_baseline_uses_requested_profile(self):
        game = _draw_game("1111m456m789m11pw5s", 27)
        profile = LegacyTwoPlyProfile.weighted_online()
        calls = []

        def fake_choose_discard(_game, _seat, return_info=False, profile=None):
            calls.append(profile)
            return (22, {}) if return_info else 22

        with mock.patch.object(
                bot_mod, "choose_discard", side_effect=fake_choose_discard):
            bot_mod._choose_draw_action(
                game, 0, game.legal_actions(), discard_profile=profile)
        self.assertEqual(calls, [profile])


if __name__ == "__main__":
    unittest.main()
