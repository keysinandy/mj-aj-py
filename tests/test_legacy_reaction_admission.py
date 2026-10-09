from dataclasses import replace
from unittest.mock import patch

import pytest

from mj import bot
from mj.game import PONG, PASS
from mj.legacy_react import LegacyReactionProfile, LegacyShapeProgress, significant_progress
from tests.test_bot import _react_game


def progress(live):
    return LegacyShapeProgress(1, 3, live, False, None, None, 0, 0)


def test_admission_is_opt_in_and_special_progress_is_unchanged():
    before, after = progress(10), progress(14)
    candidate = LegacyReactionProfile.v2_online(claim_min_gain_ratio=1.25)
    assert significant_progress(before, after, PONG) is None
    assert significant_progress(before, after, PONG, profile=candidate) == "ukeire_expansion"
    assert significant_progress(before, progress(13), PONG, profile=candidate) is None
    special_before = replace(before, baotou_ukeire_live=10)
    special_after = replace(before, baotou_ukeire_live=14)
    assert significant_progress(special_before, special_after, PONG, profile=candidate) is None


def test_fingerprint_preserves_default_and_identifies_all_tuning_fields():
    baseline = LegacyReactionProfile.v2_online()
    assert baseline.fingerprint == "46534f7c4504d3ab"
    for field, value in (("claim_min_gain_ratio", 1.25), ("pong_min_abs_gain", 2),
                         ("chow_min_abs_gain", 4)):
        profile = LegacyReactionProfile.v2_online(**{field: value})
        assert profile.fingerprint != baseline.fingerprint
        assert profile.as_json()[field] == value


@pytest.mark.parametrize("values", [{"claim_min_gain_ratio": float("nan")},
                                    {"claim_min_gain_ratio": .9},
                                    {"pong_min_abs_gain": 1.5}, {"chow_min_abs_gain": 0}])
def test_invalid_admission_contract(values):
    with pytest.raises(ValueError):
        LegacyReactionProfile.v2_online(**values)


@pytest.mark.parametrize("complete", [False, True])
def test_relaxed_claim_requires_future_proof(complete):
    game = _react_game("333m456m789m12p45p", 0, 2, mode="claim")
    from mj import legacy_react
    original = legacy_react.significant_progress

    def admission(before, after, action, *, profile=None):
        # Isolate the newly eligible branch without relying on a particular
        # public tile distribution. Existing production rejects this hand.
        if profile and profile.claim_min_gain_ratio < 1.5:
            return "ukeire_expansion"
        return original(before, after, action, profile=profile)

    from mj.legacy_eval import FutureEvaluation
    def future(roots, *args, **kwargs):
        return {root.stable_id: FutureEvaluation(
            complete=complete,
            future_improve_weight=10 if root.stable_id == "pass" else 11,
            future_ukeire_mean=2.0 if root.stable_id == "pass" else 3.0,
            future_ukeire_types_mean=1.0) for root in roots}

    with patch.object(legacy_react, "significant_progress", side_effect=admission), \
         patch.object(legacy_react, "evaluate_future_group", side_effect=future):
        action, evaluation = bot._choose_react_evaluated(
            game, 1, game.legal_actions(), reaction_profile=LegacyReactionProfile.v2_online(
                claim_min_gain_ratio=1.25))
    assert action == (PONG if complete else PASS)
    assert evaluation["v1_action"] == PASS
    assert evaluation["admission_action"] == PONG
    assert evaluation["u2_fallback_reason"] == (None if complete else "u2_incomplete")
