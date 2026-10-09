import json
from pathlib import Path

import pytest

import mj.bot as bot
from mj.game import Game, HU
from mj.legacy_eval import LegacyTwoPlyProfile
from mj.legacy_react import LegacyReactionProfile
from mj.platform.runner import make_decide
from mj.strategy_runtime import decision_audit, snapshot_for_config
from scripts.legacy_v2_big_hand_grid_scan import _decide


def fixture_game():
    root = Path(__file__).resolve().parents[1]
    fixture = json.loads((root/"tests/fixtures/legacy_hu_delay.json").read_text())
    game = Game(seed=fixture["seed"], dealer=fixture["dealer"])
    while not game.done:
        seat = game.current_seat()
        if game.phase == "discard" and seat == fixture["seat"] and game.hands[seat] == fixture["hand"]:
            return game, seat, fixture
        action, _ = _decide(game, seat, LegacyTwoPlyProfile.weighted_online())
        game.step(action)
    pytest.fail("fixture not reached")


def test_workspace_integration_is_loaded():
    assert Path(bot.__file__).resolve() == Path(__file__).resolve().parents[1] / 'mj/bot.py'


@pytest.mark.parametrize("evaluator", ["legacyV2", "legacy-v2", "legacy", None])
def test_public_override_and_explicit_rollback(evaluator):
    game, seat, fixture = fixture_game()
    baseline = bot.choose_action(game, seat, evaluator=evaluator)
    action, info = bot.choose_action(game, seat, evaluator=evaluator, return_evaluation=True,
        hu_discard_delay_min_gain_ratio=1.10)
    rollback = bot.choose_action(game, seat, evaluator=evaluator,
                                 hu_discard_delay_min_gain_ratio=1.0)
    assert baseline == action == HU and rollback == fixture["baseline"]
    assert info["hu_discard_delay_guard"]["override"]
    assert info["reaction_profile"]["fingerprint"] == "0ca5ac2cb6570b82"
    assert bot.choose_action(game, seat, evaluator=evaluator,
        hu_discard_delay_min_gain_ratio=1.0) == rollback


def test_platform_snapshot_and_decision_audit_match_effective_policy():
    game, seat, fixture = fixture_game()
    baseline = make_decide("bot")
    candidate = make_decide("bot", hu_discard_delay_min_gain_ratio=1.10)
    rollback = make_decide("bot", hu_discard_delay_min_gain_ratio=1.0)
    assert candidate.strategy_snapshot.config_hash == baseline.strategy_snapshot.config_hash
    assert rollback.strategy_snapshot.config_hash != baseline.strategy_snapshot.config_hash
    assert candidate.strategy_snapshot.profile_config["reaction"]["fingerprint"] == "0ca5ac2cb6570b82"
    assert candidate.strategy_snapshot.features["hu_discard_delay_guard"]["min_gain_ratio"] == 1.10
    action, info = candidate(game, seat)
    assert action == HU and baseline(game, seat)[0] == HU
    assert rollback(game, seat)[0] == fixture["baseline"]
    audit = decision_audit(info, action, 1.0, phase="discard", snapshot=candidate.strategy_snapshot)
    assert audit["features"]["hu_discard_delay_guard"]["override"]
    assert audit["features"]["hu_discard_delay_guard"]["min_gain_ratio"] == 1.10
    assert audit["strategy_config_hash"] == candidate.strategy_snapshot.config_hash


@pytest.mark.parametrize("evaluator", ["legacy-v1", "shape-v1", "shape-v2", "policy-v3"])
def test_unsupported_evaluator_rejects_override(evaluator):
    game, seat, fixture = fixture_game()
    with pytest.raises(ValueError, match="require online legacyV2"):
        bot.choose_action(game, seat, evaluator=evaluator, hu_discard_delay_min_gain_ratio=1.10)
    with pytest.raises(ValueError, match="require online legacyV2"):
        make_decide("bot", evaluator=evaluator, hu_discard_delay_min_gain_ratio=1.10)


def test_invalid_threshold_rejected_before_play():
    with pytest.raises(ValueError, match="finite"):
        make_decide("bot", hu_discard_delay_min_gain_ratio=float("nan"))


def test_raw_config_snapshot_preserves_none_and_rejects_wrong_scope():
    baseline = snapshot_for_config({"strategy": "bot", "evaluator": "legacyV2"})
    optional = snapshot_for_config({"strategy": "bot", "evaluator": "legacyV2",
                                   "hu_discard_delay_min_gain_ratio": None})
    assert optional.config_hash == baseline.config_hash
    with pytest.raises(ValueError, match="require online legacyV2"):
        snapshot_for_config({"strategy": "bot", "evaluator": "legacy-v1",
                             "hu_discard_delay_min_gain_ratio": 1.10})


def test_clientd_local_player_uses_the_configured_margin():
    from mj.clientd.strategies import make_player
    game, seat, fixture = fixture_game()
    candidate = make_player({"strategy": "bot", "evaluator": "legacy-v2",
                             "hu_discard_delay_min_gain_ratio": "1.10"})
    action, info = candidate.decide_with_evaluation(game, seat)
    assert action == HU
    assert info["reaction_profile"]["fingerprint"] == "0ca5ac2cb6570b82"
    assert info["hu_discard_delay_guard"]["override"]
    assert make_player({"strategy": "bot", "hu_discard_delay_min_gain_ratio": 1.0})(
        game, seat) == fixture["baseline"]


@pytest.mark.parametrize("session_kind", ["match", "tournament"])
def test_clientd_online_config_and_actual_decision_agree(session_kind):
    from mj.clientd.match import _build_decide, normalize_match_config
    from mj.clientd.tournament import normalize_tournament_config
    normalize = normalize_match_config if session_kind == "match" else normalize_tournament_config
    config = normalize({"strategy": "bot", "evaluator": "legacy-v2",
                        "hu_discard_delay_min_gain_ratio": "1.10"})
    assert config["hu_discard_delay_min_gain_ratio"] == 1.10
    decide = _build_decide(config)
    game, seat, fixture = fixture_game()
    action, info = decide(game, seat)
    assert action == HU and info["hu_discard_delay_guard"]["override"]
    assert decide.strategy_snapshot.config_hash == snapshot_for_config(config).config_hash
    assert decide.strategy_snapshot.profile_config["reaction"]["fingerprint"] == "0ca5ac2cb6570b82"
    rollback = normalize({"strategy": "bot", "hu_discard_delay_min_gain_ratio": 1.0})
    assert _build_decide(rollback)(game, seat)[0] == fixture["baseline"]


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), 0.9, "invalid"])
def test_clientd_invalid_margin_rejected_before_a_session(invalid):
    from mj.clientd.errors import ValidationError
    from mj.clientd.strategies import make_player
    from mj.clientd.match import normalize_match_config
    from mj.clientd.tournament import normalize_tournament_config
    config = {"strategy": "bot", "hu_discard_delay_min_gain_ratio": invalid}
    for factory in (make_player, normalize_match_config, normalize_tournament_config):
        with pytest.raises(ValidationError):
            factory(config)


def test_clientd_unsupported_policy_rejects_margin_instead_of_ignoring_it():
    from mj.clientd.errors import ValidationError
    from mj.clientd.strategies import make_player
    from mj.clientd.match import normalize_match_config
    for config in ({"strategy": "policy-v3"}, {"strategy": "bot", "evaluator": "legacy-v1"}):
        config["hu_discard_delay_min_gain_ratio"] = 1.10
        for factory in (make_player, normalize_match_config):
            with pytest.raises(ValidationError, match="require online legacyV2"):
                factory(config)
        with pytest.raises(ValueError, match="require online legacyV2"):
            snapshot_for_config(config)
