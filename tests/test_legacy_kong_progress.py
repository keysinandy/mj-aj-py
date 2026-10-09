import copy
import json
from pathlib import Path

import pytest

from mj.bot import _choose_draw_action
from mj.game import Game, HU
from mj.legacy_eval import LegacyTwoPlyProfile
from mj.legacy_kong import redundant_self_kong_candidate
from mj.legacy_react import LegacyReactionProfile
from scripts.legacy_v2_big_hand_grid_scan import _decide


FIXTURES = json.loads((Path(__file__).parent / "fixtures" / "legacy_kong_progress.json").read_text())


@pytest.mark.parametrize("fixture", FIXTURES)
def test_real_production_rejection_can_take_redundant_replacement(fixture):
    game = Game(seed=fixture["seed"], dealer=fixture["dealer"])
    profile = LegacyTwoPlyProfile.weighted_online()
    while not game.done:
        seat = game.current_seat()
        if game.phase == "discard" and seat == fixture["seat"] and game.hands[seat] == fixture["hand"]:
            legal = tuple(game.legal_actions())
            assert HU not in legal
            original, base_info = _choose_draw_action(game, seat, legal, profile,
                                                     LegacyReactionProfile.v2_online())
            selected, info = _choose_draw_action(game, seat, legal, profile,
                LegacyReactionProfile.v2_online(self_kong_progress_enabled=True))
            assert original == fixture["baseline"]
            assert selected == fixture["candidate"]["action"] and selected in legal
            assert info["kong_progress_override"]
            assert info["kong_evaluation"]["structure_safe"]
            assert info["kong_evaluation"]["shape_preserved"]
            assert base_info["kong_candidates"][0]["rejection_reason"] == "post_kong_not_tenpai"
            material = sum(game.hands[seat])+sum(4 if kind.startswith("kong") else 3
                                               for kind, tile in game.melds[seat])
            wall = game.live_wall_left()
            game.step(selected)
            assert game.live_wall_left() == wall-1
            assert game._kong_draw
            assert sum(game.hands[seat])+sum(4 if kind.startswith("kong") else 3
                                           for kind, tile in game.melds[seat]) == material+1
            return
        action, _ = _decide(game, seat, profile)
        game.step(action)
    pytest.fail("fixture not reached by frozen production replay")


@pytest.mark.parametrize("field,value", [
    ("structure_safe", False), ("shape_preserved", False),
    ("rejection_reason", "shape_ukeire_lower"), ("kind", "open"),
    ("tile", 33),
])
def test_progress_exception_keeps_other_rejections(field, value):
    fixture = FIXTURES[0]
    row = copy.deepcopy(fixture["candidate"])
    row[field] = value
    assert redundant_self_kong_candidate([row], fixture["baseline"],
        live_wall=fixture["wall_left"], hand=fixture["hand"]) is None


def test_short_wall_and_lower_progress_abstain():
    fixture = FIXTURES[0]
    row = copy.deepcopy(fixture["candidate"])
    assert redundant_self_kong_candidate([row], fixture["baseline"],
        live_wall=15, hand=fixture["hand"]) is None
    row["post_kong_progress"]["ukeire_live"] = row["baseline_progress"]["ukeire_live"]-1
    assert redundant_self_kong_candidate([row], fixture["baseline"],
        live_wall=46, hand=fixture["hand"]) is None


def test_frozen_seat_keeps_original_action():
    fixture = next(row for row in FIXTURES if row["candidate"]["kind"] == "closed")
    game = Game(seed=fixture["seed"], dealer=fixture["dealer"])
    profile = LegacyTwoPlyProfile.weighted_online()
    while not game.done:
        seat = game.current_seat()
        if game.phase == "discard" and seat == fixture["seat"] and game.hands[seat] == fixture["hand"]:
            game.freeze, game.freezer = 1, (seat+1)%4
            game.drawn[seat] = fixture["baseline"]
            legal = tuple(game.legal_actions())
            baseline, _ = _choose_draw_action(game, seat, legal, profile, LegacyReactionProfile.v2_online())
            action, info = _choose_draw_action(game, seat, legal, profile,
                LegacyReactionProfile.v2_online(self_kong_progress_enabled=True))
            assert action == baseline and not info.get("kong_progress_override")
            return
        action, _ = _decide(game, seat, profile)
        game.step(action)
    pytest.fail("fixture not reached")


def test_default_profile_unchanged_and_experiment_identified():
    profile = LegacyReactionProfile.v2_online()
    assert not profile.self_kong_progress_enabled
    assert profile.fingerprint == "46534f7c4504d3ab"
    assert LegacyReactionProfile.v2_online(self_kong_progress_enabled=True).fingerprint != profile.fingerprint
