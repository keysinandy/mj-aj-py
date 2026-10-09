"""Conditional value, committed claims, and hot-path regression checks."""
import copy
from dataclasses import replace
from unittest.mock import patch

import pytest

from mj.bot import choose_action
from mj.game import Game, PASS, PONG, CHOW_LOW, CHOW_MID, CHOW_HIGH
from mj.legacy_belief import Estimate, LegacyDecisionFeatures, OpponentBelief
from mj.legacy_budget import DecisionTimeout, SearchBudgetController
from mj.legacy_calibration import fit, VerifiedCalibration, validate
from mj.legacy_completion import CompletionEstimator
from mj.legacy_joint_reaction import (QualityDecisionState, claim_discard_plan,
                                      enumerate_roots, choose_joint)
from mj.legacy_quality import choose_quality
from mj.legacy_quality_profile import LegacyQualityProfile
from mj.legacy_tail import (ContinuationValue, TailEstimate, continuation_bucket,
                            TAIL_HORIZON)
from mj.legacy_value import standing_value
from mj.shanten import shanten
from mj.tiles import counts


class NoClaimsBelief:
    def estimate(self, features, seat, target, tile=None):
        return Estimate(.1 if target == "next_win" else 0, 0, 10000, True)


def reaction_game(kind=None):
    for seed in range(10, 18):
        game = Game(seed=seed)
        game.gid, game.round_no = "optimization-test", 0
        while not game.done:
            claims = [a for a in game.legal_actions() if a in (PONG, CHOW_LOW, CHOW_MID, CHOW_HIGH)]
            if claims and (kind is None or any((a == PONG) == (kind == "pong") for a in claims)):
                action = next(a for a in claims if kind is None or (a == PONG) == (kind == "pong"))
                return game, action
            game.step(choose_action(game, game.current_seat()))
    raise AssertionError("no claim fixture")


def claim_fixture(kind=None):
    game, action = reaction_game(kind)
    seat = game.current_seat()
    f = LegacyDecisionFeatures.from_game(game, seat)
    children = enumerate_roots(f, {})[action]
    discard = min(children, key=lambda r: (shanten(r[1], r[2]), r[0]))[0]
    artifact = fit([], {1}, {2})
    profile = LegacyQualityProfile(joint_reaction_enabled=True, calibration_id=artifact["calibration_id"])
    return game, action, seat, f, discard, artifact, profile


@pytest.mark.parametrize("kind", ("pong", "chow"))
def test_claim_projection_matches_actual_game(kind):
    game, action, seat, f, discard, _, profile = claim_fixture(kind)
    plan = claim_discard_plan(f, action, discard, profile)
    game.step(action)
    actual = LegacyDecisionFeatures.from_game(game, seat)
    assert plan.public_input_hash == actual.context.input_hash
    assert plan.round_key == (actual.context.gid, actual.context.round_no)
    assert discard in game.legal_actions()


def test_joint_child_is_executed_once_instead_of_reranking_again():
    game, action, seat, f, discard, artifact, profile = claim_fixture()
    state = QualityDecisionState()
    child = {"children": {action: {"discard": discard}}, "override_reason": "test"}
    with patch("mj.legacy_quality.choose_joint", return_value=(action, child)):
        selected, _ = choose_quality(game, seat, lambda: (PASS, {}), profile=profile,
                                    calibration=VerifiedCalibration(artifact), state=state)
    assert selected == action and state.pending_discard is not None
    game.step(action)
    other = next(t for t in game.legal_actions() if t != discard)
    with patch("mj.legacy_quality._discard", side_effect=AssertionError("committed child reranked")):
        selected, info = choose_quality(game, seat, lambda: (other, {}), profile=profile,
                                       calibration=VerifiedCalibration(artifact), state=state)
    assert selected == discard
    assert info["quality"]["committed_discard"]
    assert info["quality"]["certificate"] == "joint_reaction_child"
    assert state.pending_discard is None


@pytest.mark.parametrize("change", ("round", "wall", "profile", "disabled"))
def test_changed_public_state_or_configuration_discards_pending_plan(change):
    game, action, seat, f, discard, artifact, profile = claim_fixture()
    state = QualityDecisionState(claim_discard_plan(f, action, discard, profile))
    game.step(action)
    other = next(t for t in game.legal_actions() if t != discard)
    if change == "round":
        game.round_no += 1
    elif change == "wall":
        game.wall.pop()
    elif change == "profile":
        profile = replace(profile, min_margin=.2)
    else:
        profile = LegacyQualityProfile()
    selected, info = choose_quality(game, seat, lambda: (other, {}), profile=profile,
                                   calibration=VerifiedCalibration(artifact), state=state)
    assert selected == other and not info["quality"].get("committed_discard")
    assert state.pending_discard is None


def test_timeout_does_not_arm_claim_child():
    game, action, seat, _, discard, artifact, profile = claim_fixture()
    state = QualityDecisionState()
    with patch("mj.legacy_quality.choose_joint", return_value=(action, {"children": {action: {"discard": discard}}})), \
         patch("mj.legacy_budget.SearchBudgetController.check", side_effect=[None, DecisionTimeout("decision_deadline")]):
        selected, info = choose_quality(game, seat, lambda: (PASS, {}), profile=profile,
                                       calibration=VerifiedCalibration(artifact), state=state)
    assert selected == PASS and state.pending_discard is None
    assert info["quality"]["certificate"] == "baseline_only"


@pytest.mark.parametrize("phase", ("shadow", "B", "C", "combined"))
def test_forced_pass_skips_public_projection_and_inference(phase):
    game = Game(seed=11)
    while not game.done and (game.phase != "react" or game.legal_actions() != [PASS]):
        game.step(choose_action(game, game.current_seat()))
    assert not game.done
    with patch("mj.legacy_quality.LegacyDecisionFeatures.from_game", side_effect=AssertionError("forced window projected")):
        selected, info = choose_action(game, game.current_seat(), quality_profile=LegacyQualityProfile.phase(phase),
                                       return_evaluation=True)
    assert selected == PASS and info["quality"]["fallback_reason"] == "only_legal_action"


def test_discard_only_phase_skips_nontrivial_reaction_window():
    game, _ = reaction_game()
    with patch("mj.legacy_quality.LegacyDecisionFeatures.from_game", side_effect=AssertionError("inactive window projected")):
        selected, info = choose_action(game, game.current_seat(), quality_profile=LegacyQualityProfile.phase("B"),
                                       return_evaluation=True)
    assert selected in game.legal_actions()
    assert info["quality"]["fallback_reason"] == "quality_window_inactive"


def test_cached_profile_audit_payloads_cannot_mutate_configuration():
    profile = LegacyQualityProfile.phase("C")
    original = profile.fingerprint
    payload = profile.as_json()
    payload["continuation_ev_enabled"] = False
    payload["fingerprint"] = "changed"
    assert profile.as_json()["continuation_ev_enabled"]
    assert profile.as_json()["fingerprint"] == original


def test_online_profile_cache_preserves_values_fingerprints_and_audit_isolation():
    from dataclasses import fields
    from mj.legacy_eval import LegacyTwoPlyProfile
    from mj.legacy_react import LegacyReactionProfile
    from mj.decision.profile import fingerprint
    for factory in (LegacyTwoPlyProfile.weighted_online, LegacyReactionProfile.v2_online):
        profile = factory()
        assert factory() is profile
        fresh = type(profile)(**{f.name: getattr(profile, f.name) for f in fields(profile)})
        assert fresh == profile and fresh.fingerprint == profile.fingerprint
        payload = profile.as_json()
        original = fingerprint(payload)
        payload["fingerprint"] = "changed"
        if "big_hand" in payload:
            payload["big_hand"]["enabled"] = True
            payload["marginal_structure_slack_by_shanten"][0] = 999
        assert fingerprint(factory().as_json()) == original
    flexible = LegacyTwoPlyProfile.weighted_online(marginal_structure_slack_by_shanten=[0, 2, 4, 6])
    assert flexible.fingerprint == LegacyTwoPlyProfile.weighted_online().fingerprint


def test_tail_has_value_before_tenpai_and_excludes_terminal_events():
    game = Game(seed=11)
    f = LegacyDecisionFeatures.from_game(game, 0)
    standing = list(f.context.hand)
    standing[next(t for t, n in enumerate(standing) if n)] -= 1
    assert shanten(standing, 0) > 0
    class Tail:
        def estimate(self, *args, **kwargs):
            return TailEstimate(6, .5, True, 64, "test")
    value, completion = standing_value(f, standing, 0, NoClaimsBelief(), continuation=Tail())
    assert value.win_ev == 0
    assert value.continuation_ev == pytest.approx(6*.9**3)
    assert value.loss_ev == completion.loss_ev
    assert value.total_ev == pytest.approx(value.continuation_ev+value.loss_ev)
    assert value.horizon == TAIL_HORIZON and value.calibrated
    assert value.continuation_samples == 64


def test_guaranteed_next_draw_does_not_query_or_add_tail():
    from mj.decision.context import PublicDecisionContext
    standing = tuple(counts("123m456m789m123pw"))
    f = LegacyDecisionFeatures(PublicDecisionContext(hand=standing, dealer=0, base=1,
        live_wall=60, legal_actions=(0,), chain_count=0, chain_piao=0))
    class ForbiddenTail:
        def estimate(self, *args, **kwargs):
            raise AssertionError("terminal win queried tail")
    value, completion = standing_value(f, standing, 0, NoClaimsBelief(), continuation=ForbiddenTail())
    assert value.continuation_ev == 0 and value.win_ev > 0
    assert completion.next_draw_probability == pytest.approx(.9**3)


def test_tail_calibration_uses_independent_seeds_and_scales_net_points():
    game = Game(seed=11, base=2)
    f = LegacyDecisionFeatures.from_game(game, 0)
    standing = list(f.context.hand)
    standing[next(t for t, n in enumerate(standing) if n)] -= 1
    bucket = continuation_bucket(f, standing, 0)
    train, validation = set(range(32)), set(range(100, 108))
    rows = [{"seed": seed, "target": "continuation", "bucket": bucket, "label": 2}
            for seed in train | validation for _ in range(20)]
    artifact = fit(rows, train, validation)
    model = VerifiedCalibration(artifact)
    estimate = ContinuationValue(model).estimate(f, standing, 0)
    assert estimate.calibrated and estimate.mean == 4 and estimate.samples == 32
    assert not ContinuationValue(model, min_samples=33).estimate(f, standing, 0).calibrated
    assert not ContinuationValue(VerifiedCalibration(fit([], {1}, {2}))).estimate(f, standing, 0).calibrated
    with pytest.raises(TypeError):
        model.tail_buckets[bucket]["mean"] = 5
    corrupt = copy.deepcopy(artifact)
    corrupt["tail_buckets"][bucket]["standard_error"] = -1
    from mj.decision.profile import fingerprint
    corrupt["calibration_id"] = fingerprint({k: v for k, v in corrupt.items() if k != "calibration_id"})
    with pytest.raises(ValueError, match="invalid continuation cell"):
        validate(corrupt)


def test_next_draw_horizon_matches_full_estimate_and_reuses_estimates():
    game = Game(seed=11)
    f = LegacyDecisionFeatures.from_game(game, 0)
    belief = NoClaimsBelief()
    estimator = CompletionEstimator()
    short = estimator.evaluate(f, belief, next_draw_only=True)
    full = estimator.evaluate(f, belief)
    assert len(short.horizon) == 1 < len(full.horizon)
    assert short.next_draw_probability == full.next_draw_probability
    assert short.loss_ev == full.loss_ev
    assert short.uncertainty == full.uncertainty
    assert estimator.evaluate(f, belief, next_draw_only=True) is short
    public_belief = OpponentBelief()
    with patch.object(public_belief, "_estimate", wraps=public_belief._estimate) as underlying:
        for _ in range(10):
            public_belief.estimate(f, 1, "next_win")
    assert underlying.call_count == 1


def test_zero_win_probability_has_no_multiplier_uncertainty_penalty():
    class NoWins:
        def estimate(self, *args, **kwargs):
            return Estimate(0, 0, 10000, True, 1, 100)
    game = Game(seed=11)
    result = CompletionEstimator().evaluate(LegacyDecisionFeatures.from_game(game, 0), NoWins(), next_draw_only=True)
    assert result.loss_ev == result.uncertainty == 0
    assert result.next_draw_probability == 1


def test_joint_does_not_score_worse_shanten_discards():
    game, action, seat, f, _, _, profile = claim_fixture()
    scored = []
    original = standing_value
    class Tail:
        def estimate(self, *args, **kwargs):
            return TailEstimate(0, 0, True, 64, "test")
    def observe(features, standing, locked, belief, **kwargs):
        if locked:
            scored.append(shanten(standing, locked))
        return original(features, standing, locked, belief, **kwargs)
    with patch("mj.legacy_joint_reaction.standing_value", side_effect=observe):
        choose_joint(f, PASS, {"candidates": [{"action": action, "accepted": True}]},
                     NoClaimsBelief(), profile, SearchBudgetController(50), continuation=Tail())
    assert scored and len(set(scored)) == 1


def test_nonterminal_joint_comparison_requires_offensive_tail():
    game, action, seat, f, _, _, profile = claim_fixture()
    assert shanten(f.context.hand, f.context.locked) > 0
    with patch("mj.legacy_joint_reaction.standing_value", side_effect=AssertionError("loss-only joint scored")):
        selected, info = choose_joint(f, PASS, {}, NoClaimsBelief(), profile, SearchBudgetController(50))
    assert selected == PASS and info["fallback_reason"] == "joint_nonterminal_tail_missing"


def test_offline_tail_collection_contains_only_terminal_labels():
    from scripts.legacy_v2_quality_acceptance import collect_game
    rows = collect_game(720003)
    tails = [r for r in rows if r["target"] == "continuation"]
    assert tails
    game = Game(seed=720003, dealer=720003 % 4)
    while not game.done:
        game.step(choose_action(game, game.current_seat()))
    assert all(r["label"] == game.scores[r["seat"]]/game.base for r in tails)
