"""Explicit root-action scope dispatch for shape-v2.

Ordinary discard is the only fully implemented online optimisation scope in
the initial profile.  The wider scopes are represented here so that HU,
KONG, and reaction actions are either compared under an explicitly calibrated
same-unit model or delegated with a truthful reason.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import math

from ..game import HU, KONG_OPEN, KONG_CLOSED_BASE, KONG_ADD_BASE
from .context import ContextError, PublicDecisionContext
from .fast_ev import evaluate_discard_context
from .profile import ProfileSpec
from .score_value import ScoreValue


def _is_kong(action):
    return (action == KONG_OPEN or
            KONG_CLOSED_BASE - 33 <= action <= KONG_CLOSED_BASE or
            KONG_ADD_BASE - 33 <= action <= KONG_ADD_BASE)


@dataclass(frozen=True)
class RootEvaluation:
    profile: str
    profile_fingerprint: str
    scope: str
    context_hash: str
    level: str
    selected: int | None
    candidates: tuple
    reason: str
    delegated_reason: str | None = None
    missing_fields: tuple = ()
    complete: bool = True
    counterfactual: bool = False

    def as_json(self):
        return {
            "version": self.profile, "profile": self.profile,
            "profile_fingerprint": self.profile_fingerprint,
            "scope": self.scope, "context_hash": self.context_hash,
            "level": self.level, "selected": self.selected,
            "candidate_count": len(self.candidates),
            "candidates": list(self.candidates), "reason": self.reason,
            "delegated_reason": self.delegated_reason,
            "missing_fields": list(self.missing_fields),
            "complete": self.complete, "counterfactual": self.counterfactual,
        }


def _delegated(context, profile, reason, selected=None):
    return RootEvaluation(
        profile=profile.name, profile_fingerprint=profile.fingerprint,
        scope=profile.scope, context_hash=context.context_hash,
        level="legacy", selected=selected, candidates=(), reason=reason,
        delegated_reason=reason, missing_fields=tuple(context.missing_fields))


def _discard_profile(profile):
    if profile.scope == "discard":
        return profile
    return replace(profile, scope="discard")


def evaluate_root_context(context: PublicDecisionContext,
                          profile: ProfileSpec | None = None,
                          *, legacy_action=None):
    """Evaluate a supported root or return an explicit scope delegation."""
    profile = profile or ProfileSpec.shape_v2(scope="all-root")
    if profile.scope == "discard":
        raise ContextError("root evaluator requires hu-piao or all-root scope")
    context.validate_for("fast")
    actions = tuple(context.legal_actions)
    if len(actions) == 1:
        return _delegated(context, profile, "only_legal_action", actions[0])
    if context.phase not in ("discard", "draw"):
        return _delegated(context, profile, "scope_delegated_reaction",
                          legacy_action)
    if any(_is_kong(action) for action in actions):
        return _delegated(context, profile,
                          "scope_delegated_kong_transition", legacy_action)
    if profile.scope == "all-root" and any(action < 0 and action != HU
                                           for action in actions):
        return _delegated(context, profile, "scope_delegated_root_action",
                          legacy_action)
    if HU in actions and profile.scope not in ("hu-piao", "all-root"):
        return _delegated(context, profile, "scope_delegated_hu", legacy_action)

    # The discard-only evaluator is reused for the discard subset, but the
    # root comparison is allowed only for a calibrated same-unit profile.
    if not profile.calibrated:
        return _delegated(context, profile, "root_compare_uncalibrated",
                          legacy_action)
    discard_result = evaluate_discard_context(
        context, _discard_profile(profile), level="EV2",
        legacy_best=(legacy_action if legacy_action is not None and
                     0 <= int(legacy_action) < 34 else None))
    if discard_result.level not in ("V2-EV1", "V2-EV2") or any(
            c.value is None for c in discard_result.candidates):
        return _delegated(context, profile, "root_discard_layer_incomplete",
                          legacy_action)

    candidates = [{
        "action": c.tile, "legal": True, "value": c.value,
        "Q": c.value, "EV1": c.ev1, "EV2": c.ev2,
        "source": "discard_scope",
    } for c in discard_result.candidates]
    if HU in actions:
        scorer = ScoreValue(context.dealer, context.base,
                            bool(context.you_cai_bi_kao), context.hero_seat)
        breakdown = scorer.hu(
            context.hand, scorer.standing_before_draw(context.hand,
                                                      context.drawn),
            context.locked, context.drawn, context.kong_draw,
            context.chain_count, context.chain_piao)
        if not breakdown.legal:
            return _delegated(context, profile, "hu_context_not_legal",
                              legacy_action)
        candidates.append({
            "action": HU, "legal": True, "value": breakdown.reward,
            "Q": breakdown.reward, "instant_hu": breakdown.as_json(),
            "source": "score_value",
        })
    selected = max(candidates, key=lambda row: (
        float(row["value"]), -int(row["action"]))).get("action")
    return RootEvaluation(
        profile=profile.name, profile_fingerprint=profile.fingerprint,
        scope=profile.scope, context_hash=context.context_hash,
        level="V2-ROOT", selected=selected, candidates=tuple(candidates),
        reason="shape_v2_root_same_unit", missing_fields=(), complete=True)


def choose_root_game_action(game, seat, profile=None):
    """Project a game, obtain a legacy fallback, then evaluate the root."""
    profile = profile or ProfileSpec.shape_v2(scope="all-root")
    context = PublicDecisionContext.from_game(game, seat)
    from ..bot import choose_action
    legacy = choose_action(game, seat, evaluator="legacy")
    result = evaluate_root_context(context, profile, legacy_action=legacy)
    return result.selected, result.as_json()


evaluate_root = evaluate_root_context
