"""Transactional opt-in quality layer around the frozen legacyV2 policy."""
from contextlib import nullcontext
import math
import time

from .decision.context import ContextError
from .decision.score_value import ScoreValue
from .game import HU
from .legacy_kong import kong_action_tile, kong_actions
from .legacy_belief import LegacyDecisionFeatures, OpponentBelief
from .legacy_budget import SearchBudgetController, DecisionTimeout
from .legacy_calibration import VerifiedCalibration
from .legacy_danger import DangerEstimator
from .legacy_completion import CompletionEstimator
from .legacy_hand_plan import public_route_values
from .legacy_joint_reaction import choose_joint, claim_discard_plan, QualityDecisionState
from .legacy_quality_profile import LegacyQualityProfile
from .legacy_value import ScoreEV, standing_value, confident_winner
from .legacy_tail import ContinuationValue, TAIL_HORIZON, TAIL_SCORE_VERSION


def _discard(features, baseline, evaluation, belief, profile, budget, danger, *,
             continuation=None, completion_estimator=None):
    # Only fully admitted baseline frontier rows may be reranked. Stage A's
    # speed certificate says nothing about the new comparator; abstain.
    if not evaluation.get("complete") or evaluation.get("search_phase") != "two_ply":
        return baseline, {"fallback_reason": "stage_a_or_partial_abstention", "certificate": "baseline_only"}
    rows = [r for r in evaluation.get("candidates", ()) if r.get("future_ukeire") is not None]
    actions = [r["tile"] for r in rows]
    if baseline not in actions or len(actions) > profile.root_cap:
        return baseline, {"fallback_reason": "frontier_admission_unknown"}
    if profile.score_ev_rerank_enabled and continuation is None and any(r.get("shanten", 1) > 0 for r in rows):
        return baseline, {"fallback_reason": "nonterminal_tail_missing"}
    c = features.context
    values = {}
    for action in actions:
        budget.check()
        standing, chain, piao, _ = ScoreValue(c.dealer, c.base, c.you_cai_bi_kao, c.hero_seat).discard(
            c.hand, action, c.locked, c.chain_count or 0, c.chain_piao or 0)
        if profile.score_ev_rerank_enabled:
            value, completion = standing_value(features, standing, c.locked, belief,
                chain=chain, chain_piao=piao, budget=budget, discard_tile=action,
                continuation=continuation, completion_estimator=completion_estimator)
            values[action] = value
        else:
            risk = danger[action]
            # A risk tie-break may compare only identical legacy progress
            # units; otherwise it could silently discard offensive value.
            keys = {(r.get("shanten"), r.get("current_ukeire"), r.get("future_improve_weight"), r.get("future_ukeire")) for r in rows}
            if len(keys) != 1:
                return baseline, {"fallback_reason": "risk_progress_not_tied"}
            values[action] = ScoreEV(loss_ev=risk.loss_ev, tempo_ev=risk.tempo_ev,
                                     uncertainty=risk.uncertainty, coverage=risk.coverage,
                                     complete=True, calibrated=risk.calibrated)
    selected, reason = confident_winner(values, baseline, profile)
    return selected, {"score_ev": {str(a): v.as_json() for a, v in values.items()},
                      "fallback_reason": reason if selected == baseline else None,
                      "override_reason": reason if selected != baseline else None,
                      "certificate": "common_horizon_confidence" if selected != baseline else "baseline_only"}


def _hu(features, baseline, evaluation, belief, profile, budget, *,
        continuation=None, completion_estimator=None):
    c = features.context
    rows = evaluation.get("hu_window_candidates", ())
    if not rows or len(rows) > profile.root_cap or any(not r.get("complete") or r.get("fallback_reason") for r in rows):
        return baseline, {"fallback_reason": "hu_frontier_incomplete_or_cap"}
    values, completions = {}, {}
    for row in rows:
        budget.check()
        action = row["action"]
        if action == HU:
            reward = ScoreValue(c.dealer, c.base, c.you_cai_bi_kao, c.hero_seat).hu(
                c.hand, locked=c.locked, drawn=c.drawn, kong_draw=c.kong_draw,
                chain_count=c.chain_count or 0, chain_piao=c.chain_piao or 0)
            if not reward.legal:
                return baseline, {"fallback_reason": "exact_hu_unavailable"}
            values[action] = ScoreEV(win_ev=reward.reward, complete=True, calibrated=True, coverage=1,
                horizon=TAIL_HORIZON if continuation is not None else ScoreEV.horizon,
                model_version=TAIL_SCORE_VERSION if continuation is not None else ScoreEV.model_version)
        elif row["type"] in ("piao_discard", "baotou_next_draw"):
            standing, chain, piao, _ = ScoreValue(c.dealer, c.base, c.you_cai_bi_kao, c.hero_seat).discard(
                c.hand, action, c.locked, c.chain_count or 0, c.chain_piao or 0)
            value, completion = standing_value(features, standing, c.locked, belief,
                chain=chain, chain_piao=piao, budget=budget, discard_tile=action,
                continuation=continuation, completion_estimator=completion_estimator)
            values[action], completions[action] = value, completion.as_json()
        else:
            if not row.get("gate_passed", row.get("accepted", False)):
                return baseline, {"fallback_reason": "hu_kong_gate_unavailable"}
            hand = list(c.hand)
            kind, tile = kong_action_tile(action)
            if kind == "closed":
                hand[tile] -= 4
                locked = c.locked + 1
            elif kind == "add":
                hand[tile] -= 1
                locked = c.locked
            else:
                return baseline, {"fallback_reason": "hu_kong_action_unsupported"}
            if min(hand) < 0:
                raise ValueError("hu_kong_material_invalid")
            value, completion = standing_value(features, tuple(hand), locked, belief,
                first_draw_delay=1, chain=(c.chain_count or 0)+1,
                chain_piao=c.chain_piao or 0, kong_draw=True, budget=budget,
                continuation=continuation, completion_estimator=completion_estimator)
            values[action], completions[action] = value, completion.as_json()
    selected, reason = confident_winner(values, baseline, profile)
    return selected, {"score_ev": {str(a): v.as_json() for a, v in values.items()},
                      "completion": completions, "fallback_reason": reason if selected == baseline else None,
                      "override_reason": reason if selected != baseline else None}


def choose_quality(game, seat, baseline_choose, *, profile=None, calibration=None, hand_plan=None,
                   state=None):
    profile = profile or LegacyQualityProfile()
    started = time.perf_counter()
    actions = tuple(game.legal_actions())
    scope = "reaction" if game.phase != "discard" else "hu" if HU in actions else "kong" if kong_actions(actions) else "discard"
    budget_ms = profile.reaction_budget_ms if scope == "reaction" else profile.hu_kong_budget_ms if scope in ("hu", "kong") else profile.discard_budget_ms
    budget = SearchBudgetController(budget_ms)
    if state is None:
        states = getattr(game, "_legacy_quality_states", None)
        if states is None and profile.joint_reaction_enabled:
            states = {}
            game._legacy_quality_states = states
        if states is not None:
            state = states.setdefault(seat, QualityDecisionState())
    pending = state.pending_discard if state is not None else None
    if state is not None:
        state.pending_discard = None
    with budget.activate() if profile.adaptive_budget_enabled else nullcontext():
        baseline, evaluation = baseline_choose()
        evaluation = dict(evaluation or {})
        audit = {"decision_scope": scope, "old_selected": baseline, "new_selected": baseline,
                 "override_reason": None, "fallback_reason": None,
                 "risk_ev": {}, "score_ev": {}, "calibration_id": profile.calibration_id,
                 "coverage": 0.0, "confidence": 0.0, "profile": profile.as_json(),
                 "certificate": "baseline_only"}
        if profile.adaptive_budget_enabled:
            intervals = [(r.get("future_improve_lower"), r.get("future_improve_upper")) for r in evaluation.get("candidates", ())]
            intervals = [(lo, hi) for lo, hi in intervals if lo is not None and hi is not None]
            audit["adaptive_budget"] = {"shared_deadline": True,
                "escalation_policy": "native_stage_a_overlapping_bounds_only",
                "candidate_overlap": SearchBudgetController.ambiguous(intervals),
                "stage_b_entered": bool(evaluation.get("stage_b_entered")),
                "partial_certificate_scope": "frozen_comparator_only"}
        selected = baseline
        danger = {}
        next_plan = None
        window_enabled = ((scope == "discard" and (profile.danger_rerank_enabled or profile.score_ev_rerank_enabled))
                          or (scope == "reaction" and profile.joint_reaction_enabled)
                          or (scope == "hu" and profile.survival_hu_enabled))
        route_enabled = profile.hand_plan_enabled and hand_plan is not None
        try:
            if not profile.active:
                audit["fallback_reason"] = "quality_disabled"
            elif len(actions) == 1:
                audit["fallback_reason"] = "only_legal_action"
            elif not (window_enabled or profile.belief_shadow_enabled or route_enabled or pending):
                audit["fallback_reason"] = "quality_window_inactive"
            elif (scope == "discard" and window_enabled and not pending and not route_enabled
                    and not profile.belief_shadow_enabled
                    and (not evaluation.get("complete") or evaluation.get("search_phase") != "two_ply")):
                audit["fallback_reason"] = "stage_a_or_partial_abstention"
            elif (scope == "hu" and window_enabled and not route_enabled and not profile.belief_shadow_enabled
                    and (not evaluation.get("hu_window_candidates")
                         or len(evaluation["hu_window_candidates"]) > profile.root_cap
                         or any(not r.get("complete") or r.get("fallback_reason") for r in evaluation["hu_window_candidates"]))):
                audit["fallback_reason"] = "hu_frontier_incomplete_or_cap"
            else:
                features = LegacyDecisionFeatures.from_game(game, seat)
                audit["public_input_hash"] = features.cache_key(profile.calibration_id)
                if route_enabled:
                    if features.context.gid is None and features.context.round_no is None:
                        audit["hand_plan"] = {"fallback_reason": "route_round_identity_absent"}
                    else:
                        audit["hand_plan"] = hand_plan.update((features.context.gid, features.context.round_no),
                            public_route_values(features.context), profile.route_hysteresis)
                budget.check()
                c = features.context
                if (pending is not None and profile.joint_reaction_enabled and scope == "discard"
                        and pending.profile_fingerprint == profile.fingerprint
                        and pending.round_key == (c.gid, c.round_no)
                        and pending.public_input_hash == c.input_hash and pending.discard in actions):
                    selected = pending.discard
                    audit.update(committed_discard=True, certificate="joint_reaction_child",
                                 override_reason="joint_reaction_child" if selected != baseline else None)
                elif calibration is None and window_enabled:
                    audit["fallback_reason"] = "calibration_absent"
                else:
                    if calibration is not None:
                        if not isinstance(calibration, VerifiedCalibration):
                            calibration = VerifiedCalibration(calibration)
                        if calibration["calibration_id"] != profile.calibration_id:
                            raise ValueError("configured calibration identity mismatch")
                    belief = OpponentBelief(calibration, profile.min_samples, profile.confidence_z)
                    estimator = CompletionEstimator(budget)
                    continuation = (ContinuationValue(calibration, profile.min_tail_samples, profile.confidence_z)
                                    if profile.continuation_ev_enabled else None)
                    if profile.completion_enabled and window_enabled:
                        audit["completion"] = estimator.evaluate(features, belief, next_draw_only=True).as_json()
                    infer_risk = (profile.belief_shadow_enabled or
                                  (scope == "discard" and profile.danger_rerank_enabled and not profile.score_ev_rerank_enabled))
                    if infer_risk:
                        tiles = (c.legal_discards if scope != "reaction" else (c.pending_tile,))
                        if not profile.belief_shadow_enabled and scope == "discard":
                            tiles = tuple(r["tile"] for r in evaluation.get("candidates", ()) if r.get("future_ukeire") is not None)
                        danger = DangerEstimator().evaluate(features, tiles, belief, completion_estimator=estimator)
                        audit["risk_ev"] = {str(t): r.as_json() for t, r in danger.items()}
                        audit["coverage"] = min((r.coverage for r in danger.values()), default=0)
                    if profile.belief_shadow_enabled:
                        audit["shadow"] = True
                    kwargs = {"continuation": continuation, "completion_estimator": estimator}
                    if scope == "discard" and window_enabled:
                        selected, detail = _discard(features, baseline, evaluation, belief, profile, budget, danger, **kwargs)
                        audit.update(detail)
                    elif scope == "reaction" and window_enabled:
                        selected, detail = choose_joint(features, baseline, evaluation, belief, profile, budget, **kwargs)
                        audit.update(detail)
                        if selected != baseline:
                            next_plan = claim_discard_plan(features, selected, detail["children"][selected]["discard"], profile)
                    elif scope == "hu" and window_enabled:
                        selected, detail = _hu(features, baseline, evaluation, belief, profile, budget, **kwargs)
                        audit.update(detail)
                budget.check()
        except (ContextError, ValueError, TypeError, DecisionTimeout) as exc:
            selected = baseline
            audit["override_reason"] = None
            audit["certificate"] = "baseline_only"
            audit["fallback_reason"] = str(exc)
            audit.pop("committed_discard", None)
            next_plan = None
        if next_plan is not None and state is not None:
            state.pending_discard = next_plan
        audit["new_selected"] = selected
        score_rows = list(audit.get("score_ev", {}).values())
        if score_rows:
            audit["coverage"] = min(r["coverage"] for r in score_rows)
            audit["confidence"] = math.erf(profile.confidence_z/math.sqrt(2)) if all(r["calibrated"] for r in score_rows) else 0.0
        elif danger:
            audit["confidence"] = math.erf(profile.confidence_z/math.sqrt(2)) if all(r.calibrated for r in danger.values()) else 0.0
        audit["elapsed_ms"] = (time.perf_counter() - started) * 1000
        audit["baseline_fallback_reason"] = evaluation.get("fallback_reason") or evaluation.get("u2_fallback_reason")
        audit["deadline_ms"] = budget_ms
        audit["deadline_exceeded"] = budget.remaining_ms <= 0
        if selected not in actions:
            raise AssertionError("quality selection violated legal action set")
        evaluation["quality"] = audit
        if selected != baseline:
            evaluation["selected"] = selected
            evaluation["quality_override"] = True
            evaluation["short_circuit_reason"] = None
        return selected, evaluation
