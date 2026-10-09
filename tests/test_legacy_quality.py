"""Decision quality safety, scoring, survival, shadow and deadline contracts."""
import copy
import json
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from mj.bot import choose_action
from mj.decision.context import PublicDecisionContext, ContextError
from mj.decision.profile import fingerprint
from mj.decision.score_value import ScoreValue
from mj.game import Game, HU, PASS, PONG, CHOW_LOW
from mj.legacy_belief import LegacyDecisionFeatures, OpponentBelief, MODEL_VERSION
from mj.legacy_budget import SearchBudgetController, cap_ms, DecisionTimeout
from mj.legacy_calibration import fit, metrics, validate, VerifiedCalibration
from mj.legacy_completion import CompletionEstimator
from mj.legacy_danger import DangerEstimator, aggregate_loss
from mj.legacy_hand_plan import HandPlan
from mj.legacy_joint_reaction import enumerate_roots, choose_joint
from mj.legacy_quality_profile import FLAGS, LegacyQualityProfile
from mj.legacy_quality import _discard
from mj.legacy_value import ScoreEV, confident_winner, standing_value
from mj.scoring import settle
from mj.strategy_runtime import strategy_snapshot, decision_audit


def features(seed=11):
    game = Game(seed=seed)
    return LegacyDecisionFeatures.from_game(game, game.current_seat())


class CertainBelief:
    def estimate(self, f, seat, target, tile=None):
        from mj.legacy_belief import Estimate
        return Estimate(.1 if target == "next_win" else .25, .001, 10000, True, 1)


def test_all_gates_default_off_and_each_fingerprinted():
    p = LegacyQualityProfile()
    assert not p.active
    for flag in FLAGS:
        assert not getattr(p, flag)
        assert LegacyQualityProfile(**{flag: True}).fingerprint != p.fingerprint
    with pytest.raises(ValueError):
        LegacyQualityProfile(root_cap=4)
    with pytest.raises(ValueError):
        LegacyQualityProfile(discard_budget_ms=51)


def test_hidden_state_and_wall_invariant():
    first = Game(seed=44)
    second = copy.deepcopy(first)
    for seat in range(1, 4):
        second.hands[seat] = [0]*34
        second.hands[seat][seat] = 13
    second.wall.reverse()
    a, b = LegacyDecisionFeatures.from_game(first, 0), LegacyDecisionFeatures.from_game(second, 0)
    assert a.cache_key() == b.cache_key()
    assert OpponentBelief().evaluate(a, 0) == OpponentBelief().evaluate(b, 0)
    assert DangerEstimator().evaluate(a, a.context.legal_discards, CertainBelief()) == DangerEstimator().evaluate(b, b.context.legal_discards, CertainBelief())
    assert choose_action(first, 0, quality_profile=LegacyQualityProfile.phase("shadow")) == choose_action(second, 0, quality_profile=LegacyQualityProfile.phase("shadow"))


def test_public_only_type_and_missing_fields_rejected():
    with pytest.raises(TypeError):
        LegacyDecisionFeatures(Game(seed=4))
    with pytest.raises(ContextError):
        LegacyDecisionFeatures(features().context.replace(live_wall=None))
    with pytest.raises(TypeError):
        OpponentBelief().estimate(Game(seed=4), 1, "tenpai")


def test_game_mirror_feature_parity():
    game = Game(seed=51)
    c = PublicDecisionContext.from_game(game, 0)
    mirror = SimpleNamespace(me=0, my_hand=game.hands[0], melds=game.melds,
        discards=game.discards, chows=game.chows, dealer=game.dealer, base=game.base,
        you_cai_bi_kao=False, drawn=game.drawn[0], kong_draw=False,
        chain=0, chain_piao=0, pending=None, freeze=0, freezer=None, round_no=0,
        public_material_projection=lambda: (list(c.concealed_counts), "fixture", "verified"),
        live_wall_left=game.live_wall_left, build_game=lambda phase: game)
    a = LegacyDecisionFeatures.from_game(game, 0)
    b = LegacyDecisionFeatures.from_mirror(mirror)
    assert a.cache_key() == b.cache_key()
    assert DangerEstimator().evaluate(a, a.context.legal_discards, CertainBelief()) == DangerEstimator().evaluate(b, b.context.legal_discards, CertainBelief())


def test_cache_identity_public_river_model_rules_and_calibration():
    f = features()
    assert f.cache_key("a") != f.cache_key("b")
    assert f.cache_key() != LegacyDecisionFeatures(f.context.replace(base=2)).cache_key()
    assert f.cache_key() != LegacyDecisionFeatures(f.context, model_version="next").cache_key()


def test_loss_aggregation_single_and_multi_winner():
    assert aggregate_loss(((.5, 10), (.5, 20))) == 10
    assert aggregate_loss(((.5, 10), (.5, 20)), multi_winner=True) == 15
    risk = DangerEstimator().evaluate(features(), (0,), CertainBelief())[0]
    assert risk.immediate_ron_probability == 0
    assert risk.loss_ev <= 0 and risk.loss_ev+ risk.tempo_ev <= 0
    assert risk.calibrated


def test_next_draw_guarantee_is_conditional():
    result = CompletionEstimator().evaluate(features(), CertainBelief(), conditional_win_probability=1)
    assert result.next_draw_probability == pytest.approx(.9**3)
    assert result.completion_probability == pytest.approx(.9**3)
    assert result.loss_ev < 0
    result = CompletionEstimator().evaluate(features(), CertainBelief(), first_draw_delay=1, conditional_win_probability=1)
    assert result.next_draw_probability == 1
    c = features().context.replace(live_wall=2, concealed_counts=(None,)*4)
    assert CompletionEstimator().evaluate(LegacyDecisionFeatures(c), CertainBelief()).next_draw_probability == 0


def test_exact_score_settlement_units_and_conservation():
    standing = [0]*34
    for tile in (0, 1, 2, 9, 10, 11, 18, 19, 20, 27, 27, 28, 28):
        standing[tile] += 1
    # White wildcard completes the triplet with two 28s.
    final = list(standing)
    final[33] += 1
    for hero in range(4):
        for dealer in range(4):
            score = ScoreValue(dealer, 2, False, hero).hu(final, standing, 0, 33)
            assert score.legal
            assert score.reward == settle(hero, dealer, score.multiplier, 2)[hero]
            assert sum(score.settlement) == 0
    assert ScoreEV(4, -2, 1, -.5).total_ev == 2.5
    with pytest.raises(ValueError):
        ScoreEV(loss_ev=1)


def test_confidence_margin_partial_horizon_and_admission():
    p = LegacyQualityProfile()
    base = ScoreEV(win_ev=1, complete=True, calibrated=True, uncertainty=.2)
    tied = ScoreEV(win_ev=1.1, complete=True, calibrated=True, uncertainty=.2)
    assert confident_winner({0: base, 1: tied}, 0, p)[0] == 0
    clear = ScoreEV(win_ev=4, complete=True, calibrated=True, uncertainty=.1)
    assert confident_winner({0: base, 1: clear}, 0, p)[0] == 1
    assert confident_winner({1: clear}, 0, p)[1] == "baseline_not_admitted"
    assert confident_winner({0: base, 1: ScoreEV(win_ev=5)}, 0, p)[0] == 0
    assert confident_winner({0: base, 1: ScoreEV(win_ev=5, calibrated=True, complete=True, horizon="other")}, 0, p)[1] == "incompatible_horizon"


def test_stage_a_certificate_cannot_override():
    selected, detail = _discard(features(), 0, {"complete": False, "search_phase": "future_shanten"},
        CertainBelief(), LegacyQualityProfile.phase("B"), SearchBudgetController(50), {})
    assert selected == 0
    assert detail["certificate"] == "baseline_only"


def test_one_deadline_shared_and_context_reset():
    clock = [0.0]
    budget = SearchBudgetController(10, lambda: clock[0])
    with budget.activate():
        assert cap_ms(50) == 10
        clock[0] = .006
        assert cap_ms(50) == pytest.approx(4)
        clock[0] = .011
        with pytest.raises(DecisionTimeout):
            budget.check()
        assert cap_ms(50) == 0
    assert cap_ms(50) == 50
    assert SearchBudgetController.ambiguous(((1, 3), (2, 4)))
    assert not SearchBudgetController.ambiguous(((1, 2), (3, 4)))


def test_default_deadline_uses_high_resolution_clock():
    with patch("time.monotonic", side_effect=AssertionError("coarse clock used")):
        budget = SearchBudgetController(10)
        assert budget.clock is time.perf_counter
        budget.check()


def test_route_hysteresis_reset_and_no_legal_lock():
    plan = HandPlan()
    assert plan.update(1, {"ordinary": 0, "chiitoi": .3})["route"] == "chiitoi"
    assert plan.update(1, {"ordinary": .4, "chiitoi": .3})["route"] == "chiitoi"
    assert plan.update(2, {"ordinary": 0, "chiitoi": .1})["route"] == "ordinary"


def test_calibration_split_identity_metrics_and_freezing():
    rows = [{"seed": 1, "target": "tenpai", "bucket": "early:0:none", "label": y, "prediction": .5} for y in (0, 1)]
    rows.append({"seed": 2, "target": "tenpai", "bucket": "early:0:none", "label": 1, "prediction": .5})
    artifact = fit(rows, {1}, {2})
    assert validate(artifact) == artifact
    assert artifact["validation"]["tenpai|early:0:none"]["brier"] == .25
    model = VerifiedCalibration(artifact)
    with pytest.raises(TypeError):
        model.buckets["tenpai|early:0:none"]["probability"] = 1
    with pytest.raises(ValueError):
        fit(rows, {1}, {1})
    artifact["calibration_id"] = "bad"
    with pytest.raises(ValueError):
        validate(artifact)


@pytest.mark.parametrize("phase", ("off", "shadow", "A", "B", "C", "route"))
def test_flags_off_shadow_or_absent_calibration_retain_baseline(phase):
    game = Game(seed=20)
    baseline = choose_action(copy.deepcopy(game), 0)
    selected, info = choose_action(game, 0, return_evaluation=True, quality_profile=LegacyQualityProfile.phase(phase))
    assert selected == baseline
    assert info["quality"]["old_selected"] == baseline
    assert info["quality"]["override_reason"] is None


def test_snapshot_and_audit_quality_configuration():
    a = strategy_snapshot("bot", "legacyV2", quality_profile=LegacyQualityProfile())
    b = strategy_snapshot("bot", "legacyV2", quality_profile=LegacyQualityProfile.phase("shadow"))
    assert a.config_hash != b.config_hash
    assert b.features["belief_shadow_enabled"]["status"] == "enabled"
    game = Game(seed=11)
    action, info = choose_action(game, 0, quality_profile=LegacyQualityProfile.phase("shadow"), return_evaluation=True)
    audit = decision_audit(info, action, 1, snapshot=b)
    assert audit["decision_quality"]["old_selected"] == action


def test_frozen_hand_keeps_only_drawn_discard():
    game = Game(seed=21)
    game.freeze, game.freezer = 2, 2
    action = choose_action(game, 0, quality_profile=LegacyQualityProfile.phase("B"))
    assert action in game.legal_actions()
    if action >= 0:
        assert action == game.drawn[0]


def test_joint_roots_include_v1_rejected_claim_and_all_child_discards():
    context = PublicDecisionContext(hero_seat=1, hand=tuple([2,1,1]+[0]*24+[3,3,3,0,0,0,0]),
        visible=tuple([3,1,1]+[0]*24+[3,3,3,0,0,0,0]),
        dealer=0, base=1, live_wall=50, phase="react", pending_owner=0, pending_tile=0,
        discards=((0,),(),(),()), legal_actions=(PASS,PONG), turn=1)
    f = LegacyDecisionFeatures(context)
    roots = enumerate_roots(f, {"candidates": [{"action": PONG, "accepted": False}]})
    assert set(roots) == {PASS,PONG}
    assert all(sum(row[1]) == 10 and min(row[1]) >= 0 for row in roots[PONG])
    assert len(roots[PONG]) == 5
    with pytest.raises(DecisionTimeout):
        choose_joint(f, PASS, {"candidates": []}, CertainBelief(), LegacyQualityProfile.phase("C"), SearchBudgetController(0))


def test_acceptance_two_matches_and_zero_sum(tmp_path):
    from scripts.legacy_v2_quality_acceptance import play_pair, summarize, interval
    artifact = fit([], {1}, {2})
    row = play_pair((0, 100, "shadow", artifact))
    assert len(row["games"]) == 2
    assert {tuple(r["candidate_seats"]) for r in row["games"]} == {(0,2),(1,3)}
    assert all(sum(m["scores"]) == 0 for m in row["games"])
    assert row["counts"]["candidate"]["override"] == 0
    report = summarize([row], 2, "shadow", artifact, {})
    assert report["games_completed"] == 2
    assert not report["gate"]["default_enabled"]


def test_golden_fixed_public_replay():
    data = json.loads(Path("tests/fixtures/legacy_quality_golden.json").read_text(encoding="utf-8"))
    for row in data["states"]:
        c = PublicDecisionContext(**row["context"])
        f = LegacyDecisionFeatures(c)
        result = DangerEstimator().evaluate(f, c.legal_discards, OpponentBelief())
        assert f.cache_key() == row["cache_key"]
        assert json.loads(json.dumps({str(t): r.as_json() for t,r in result.items()})) == row["danger"]
        game = Game(seed=row["seed"])
        assert choose_action(game, 0, quality_profile=LegacyQualityProfile.phase("shadow")) == row["baseline_selected"]


def test_exact_guaranteed_baotou_value_is_survival_weighted():
    from mj.tiles import counts
    standing = tuple(counts("123m456m789m123pw"))
    # Use a value-only context; opponents' concealed counts are unavailable.
    c = PublicDecisionContext(hand=standing, dealer=0, base=1, live_wall=60,
                              legal_actions=(0,), chain_count=0, chain_piao=0)
    f = LegacyDecisionFeatures(c)
    value, completion = standing_value(f, standing, 0, CertainBelief())
    assert completion.completion_probability == pytest.approx(.9**3)
    assert value.win_ev > 0
    assert completion.next_draw_probability < 1


def test_known_illegal_response_probabilities_are_exact_zero():
    f = features()
    for target in ("chi", "pong", "kong"):
        e = OpponentBelief().estimate(f, 1, target, 33)
        assert e.probability == 0 and e.calibrated


def test_v1_reject_rescue_uses_common_score_and_all_children():
    context = PublicDecisionContext(hero_seat=1, hand=tuple([2,1,1]+[0]*24+[3,3,3,0,0,0,0]),
        visible=tuple([3,1,1]+[0]*24+[3,3,3,0,0,0,0]),
        dealer=0, base=1, live_wall=50, phase="react", pending_owner=0, pending_tile=0,
        discards=((0,),(),(),()), legal_actions=(PASS,PONG), turn=1)
    def value(f, hand, locked, belief, **kwargs):
        return (ScoreEV(win_ev=10 if locked else 0, calibrated=True, complete=True, coverage=1),
                CompletionEstimator().evaluate(f, CertainBelief()))
    with patch("mj.legacy_joint_reaction.standing_value", side_effect=value):
        selected, detail = choose_joint(LegacyDecisionFeatures(context), PASS,
            {"candidates": [{"action": PONG, "accepted": False}]}, CertainBelief(),
            LegacyQualityProfile.phase("C"), SearchBudgetController(50))
    assert selected == PONG
    assert detail["children"][PONG]["rescue"]
    assert detail["children"][PONG]["children_evaluated"] > 0


def test_late_deadline_rolls_back_an_override_atomically():
    artifact = fit([], {1}, {2})
    game = Game(seed=11)
    baseline = choose_action(copy.deepcopy(game), 0)
    challenger = next(a for a in game.legal_actions() if a >= 0 and a != baseline)
    p = LegacyQualityProfile.phase("B", calibration_id=artifact["calibration_id"])
    with patch("mj.legacy_quality._discard", return_value=(challenger, {"override_reason": "test", "certificate": "common_horizon_confidence"})), \
         patch("mj.legacy_budget.SearchBudgetController.check", side_effect=[None, DecisionTimeout("decision_deadline")]):
        selected, result = choose_action(game, 0, return_evaluation=True,
            quality_profile=p, quality_calibration=VerifiedCalibration(artifact))
    assert selected == baseline
    assert result["quality"]["override_reason"] is None
    assert result["quality"]["certificate"] == "baseline_only"
