import json
from pathlib import Path
from unittest.mock import patch

import pytest

from mj.bot import choose_discard, _expected_next_draw_reward
from mj.game import Game, DEAD_WALL
from mj.legacy_eval import LegacyTwoPlyProfile
from mj.legacy_ready_score import next_draw_score
from mj.shanten import shanten


def fixture_game():
    fixture = json.loads((Path(__file__).parent / "fixtures" / "legacy_ready_score.json").read_text())
    game = Game(seed=0, dealer=fixture["dealer"])
    seat = fixture["seat"]
    game.hands[seat] = fixture["hand"][:]
    game.melds = [[] for _ in range(4)]
    game.melds[seat] = fixture["melds"]
    game.wall = [0] * (DEAD_WALL + fixture["wall_left"])
    game.freeze = 0
    game.freezer = None
    game.chain = [0]*4
    game.chain_piao = [0]*4
    game.visible_counts = lambda hero: fixture["visible"][:]
    game.turn = seat
    game.drawn[seat] = None
    return game, seat, fixture


def test_score_tie_real_state_preserves_progress_and_increases_next_draw_reward():
    baseline_game, seat, fixture = fixture_game()
    candidate_game, _, _ = fixture_game()
    baseline, base_info = choose_discard(baseline_game, seat, True,
        LegacyTwoPlyProfile.weighted_online())
    selected, info = choose_discard(candidate_game, seat, True,
        LegacyTwoPlyProfile.weighted_online(baotou_score_tiebreak_enabled=True))
    assert baseline == fixture["baseline"] == 24
    assert selected == fixture["challenger"] == 13
    rows = {row["tile"]: row for row in info["candidates"]}
    assert rows[selected]["baotou_progress_score_x2"] == rows[baseline]["baotou_progress_score_x2"]
    assert rows[selected]["current_selfdraw_hu_ukeire"] >= rows[baseline]["current_selfdraw_hu_ukeire"]
    assert rows[selected]["shanten"] == rows[baseline]["shanten"] == 0
    tie = info["baotou_score_tiebreak"]
    assert tie["complete"] and tie["override"]
    assert tie["roots"] <= 3
    assert tie["values"][selected]["value"] > tie["values"][baseline]["value"]
    # The new score action must not be reported as an extra shape change.
    assert info["shape_changed_winner"] == base_info["shape_changed_winner"]
    remaining = [4-value for value in fixture["visible"]]
    for action in (baseline, selected):
        standing = fixture["hand"][:]
        standing[action] -= 1
        reference = _expected_next_draw_reward(candidate_game, seat, standing, 0, 0, 0,
                                              False, remaining, draw_delay=4)
        assert tie["values"][action]["value"] == pytest.approx(reference["value"])


def test_deadline_abstains_transactionally():
    game, seat, fixture = fixture_game()
    with patch("mj.legacy_ready_score.next_draw_score", side_effect=TimeoutError):
        action, info = choose_discard(game, seat, True,
            LegacyTwoPlyProfile.weighted_online(baotou_score_tiebreak_enabled=True))
    assert action == fixture["baseline"]
    assert not info["baotou_score_tiebreak"]["override"]
    assert not info["baotou_score_tiebreak"]["complete"]
    assert info["baotou_score_tiebreak"]["fallback_reason"] == "TimeoutError"


def test_weight_and_score_profiles_preserve_default_fingerprint():
    profile = LegacyTwoPlyProfile.weighted_online()
    assert profile.fingerprint == "9a2d4dba9c305668"
    assert LegacyTwoPlyProfile.weighted_online(baotou_progress_weight=2).fingerprint != profile.fingerprint
    assert LegacyTwoPlyProfile.weighted_online(baotou_score_tiebreak_enabled=True).fingerprint != profile.fingerprint
    for weight in (-1, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            LegacyTwoPlyProfile.weighted_online(baotou_progress_weight=weight)


def test_scoring_helper_rejects_expired_deadline_and_invalid_material():
    game, seat, fixture = fixture_game()
    standing = fixture["hand"][:]
    standing[24] -= 1
    with pytest.raises(TimeoutError):
        next_draw_score(standing, 0, [33], [1]*34, seat=seat, dealer=0, deadline=0)
    with pytest.raises(ValueError):
        next_draw_score(standing, 1, [33], [1]*34, seat=seat, dealer=0)
