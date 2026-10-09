import json
from pathlib import Path
from unittest.mock import patch

import pytest

import mj.bot as bot
from mj.game import Game, HU, KONG_CLOSED_BASE
from mj.legacy_eval import LegacyTwoPlyProfile
from mj.legacy_react import LegacyReactionProfile
from mj.tiles import counts, W
from scripts.legacy_v2_big_hand_grid_scan import _decide


def real_fixture():
    fixture = json.loads((Path(__file__).parent/"fixtures"/"legacy_hu_delay.json").read_text())
    game, profile = Game(seed=fixture["seed"], dealer=fixture["dealer"]), LegacyTwoPlyProfile.weighted_online()
    while not game.done:
        seat = game.current_seat()
        if game.phase == "discard" and seat == fixture["seat"] and game.hands[seat] == fixture["hand"]:
            return game, seat, fixture, profile
        action, _ = _decide(game, seat, profile)
        game.step(action)
    pytest.fail("frozen production replay did not reach fixture")


def test_small_reward_delay_accepts_existing_legal_hu():
    game, seat, fixture, profile = real_fixture()
    original, _ = _decide(game, seat, profile)
    action, info = _decide(game, seat, profile,
        LegacyReactionProfile.v2_online(hu_discard_delay_min_gain_ratio=1.1))
    assert original == fixture["baseline"] and HU in game.legal_actions()
    assert action == HU and info["hu_discard_delay_guard"]["override"]
    assert info["selected_type"] == "immediate_hu"
    assert info["hu_discard_delay_guard"]["frozen_selected"] == original
    game.step(action)
    assert game.done and game.result[0] == seat and sum(game.scores) == 0


def test_gain_above_threshold_remains_delayed():
    game, seat, fixture, profile = real_fixture()
    # The exact audited gain remains eligible with a lower configured hurdle.
    action, info = _decide(game, seat, profile,
        LegacyReactionProfile.v2_online(hu_discard_delay_min_gain_ratio=1.001))
    assert action == fixture["baseline"]
    assert not info["hu_discard_delay_guard"]["override"]


def test_incomplete_future_does_not_apply_experimental_hurdle():
    game, seat, fixture, profile = real_fixture()
    scorer = bot._hu_window_score
    def incomplete(*args, **kwargs):
        return dict(scorer(*args, **kwargs), complete=False, fallback_reason="test_partial")
    with patch.object(bot, "_hu_window_score", side_effect=incomplete):
        baseline, _ = _decide(game, seat, profile)
        action, info = _decide(game, seat, profile,
            LegacyReactionProfile.v2_online(hu_discard_delay_min_gain_ratio=1.1))
    assert action == baseline and "hu_discard_delay_guard" not in info


def test_default_fingerprint_and_invalid_threshold():
    original = LegacyReactionProfile.v2_online()
    assert original.fingerprint == "46534f7c4504d3ab"
    assert LegacyReactionProfile.v2_online(hu_discard_delay_min_gain_ratio=1.1).fingerprint != original.fingerprint
    for ratio in (.9, float("inf"), float("nan")):
        with pytest.raises(ValueError):
            LegacyReactionProfile.v2_online(hu_discard_delay_min_gain_ratio=ratio)


def test_explicit_doubling_piao_remains_eligible():
    game = Game(seed=0, dealer=0)
    game.hands[0] = counts("123m456m789m11pwww")
    game.drawn[0], game.turn, game.phase = W, 0, "discard"
    game.melds, game.discards = [[] for _ in range(4)], [[] for _ in range(4)]
    game.freeze, game.freezer = 0, None
    game.chain, game.chain_piao = [0]*4, [0]*4
    game._kong_draw = False
    profile = LegacyTwoPlyProfile.weighted_online()
    original, _ = _decide(game, 0, profile)
    action, info = _decide(game, 0, profile,
        LegacyReactionProfile.v2_online(hu_discard_delay_min_gain_ratio=1.25))
    assert action == original == W and HU in game.legal_actions()
    assert not info["hu_discard_delay_guard"]["override"]


def test_immediate_kong_replacement_does_not_use_discard_delay_hurdle():
    game = Game(seed=0, dealer=0)
    game.hands[0] = counts("1111m456m789m5pwww")
    game.drawn[0], game.turn, game.phase = W, 0, "discard"
    game.melds, game.discards = [[] for _ in range(4)], [[] for _ in range(4)]
    game.freeze, game.freezer, game._kong_draw = 0, None, False
    profile = LegacyTwoPlyProfile.weighted_online()
    # Compare complete roots: an online deadline under concurrent evaluation
    # can legitimately abstain from the guard, which is tested separately.
    baseline, _ = _decide(game, 0, profile, LegacyReactionProfile.v2_offline())
    action, info = _decide(game, 0, profile,
        LegacyReactionProfile.v2_offline(hu_discard_delay_min_gain_ratio=10))
    assert baseline == action == KONG_CLOSED_BASE
    assert not info["hu_discard_delay_guard"]["override"]
