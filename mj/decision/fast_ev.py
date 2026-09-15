"""Opt-in shape-v2 Fast EV for ordinary discard decisions.

The first implementation keeps the complete-candidate transaction explicit:
all legal discards get Q0, and EV2 can replace the table only when every
candidate finishes.  A partial EV2 pass therefore cannot influence the
selected action.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import time
from typing import Optional

from .context import ContextError, PublicDecisionContext
from .frontier import discard_frontier, DiscardFrontierItem
from .profile import ProfileSpec
from .score_value import ScoreValue, theoretical_reward_bound


MODEL_ASSUMPTION = "uniform_unseen_no_opponent_actions_score_v1"


class BudgetExceeded(RuntimeError):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


class DecisionBudget:
    """One budget shared by frontier, EV and explanation construction."""

    def __init__(self, nodes, time_ms):
        self.limit = max(0, int(nodes))
        self.time_ms = max(0.0, float(time_ms))
        self.started = time.monotonic()
        self.nodes = 0
        self.kernel_calls = 0
        self.exceeded = None

    def consume(self, count=1):
        if self.exceeded:
            raise BudgetExceeded(self.exceeded)
        if self.nodes + count > self.limit:
            self.exceeded = "node_budget"
            raise BudgetExceeded(self.exceeded)
        if (time.monotonic() - self.started) * 1000.0 >= self.time_ms:
            self.exceeded = "time_budget"
            raise BudgetExceeded(self.exceeded)
        self.nodes += count

    def kernel(self):
        self.kernel_calls += 1

    @property
    def elapsed_ms(self):
        return (time.monotonic() - self.started) * 1000.0


@dataclass(frozen=True)
class V2Candidate:
    tile: int
    shanten: int
    u1: int
    p1: float
    ukeire_tiles: tuple
    ukeire_bitset: int
    structure_ukeire: tuple
    ev1: Optional[float]
    ev2: Optional[float]
    q0: float
    value: Optional[float]
    weighted_contributions: dict
    missing: tuple = ()
    upper_bound: Optional[float] = None
    pruned: bool = False
    cache_key: Optional[tuple] = None
    level: str = "V2-Q0"
    waits: tuple = ()
    visible_unknown: Optional[int] = None
    risk: Optional[float] = None

    def as_json(self):
        return {
            "tile": self.tile, "legal": True, "shanten": self.shanten,
            "U1": self.u1, "p1": self.p1,
            "ukeire_tiles": list(self.ukeire_tiles),
            "ukeire_bitset": self.ukeire_bitset,
            "structure_ukeire": list(self.structure_ukeire),
            "waits": list(self.waits),
            "unknown_pool": self.visible_unknown, "risk": self.risk,
            # ``I`` is shape-v1's improvement feature, not the score-valued
            # EV1 layer.  Fast v2 does not compute it yet, so keep it missing
            # instead of silently aliasing two different quantities.
            "I": None, "EV1": self.ev1, "EV2": self.ev2,
            "q0": self.q0, "Q0": self.q0, "fast_ev": self.value,
            "Q": self.value, "weighted_contributions": self.weighted_contributions,
            "missing": list(self.missing),
            "q_upper_bound": self.upper_bound,
            "q_pruned": self.pruned, "cache_key": list(self.cache_key)
            if self.cache_key is not None else None, "level": self.level,
        }


@dataclass(frozen=True)
class FastEvaluation:
    profile: str
    profile_fingerprint: str
    scope: str
    context_hash: str
    level: str
    selected: Optional[int]
    candidates: tuple
    candidate_count: int
    reason: str
    legacy_best: Optional[int] = None
    model_assumption: str = MODEL_ASSUMPTION
    complete: bool = True
    ev2_complete: bool = False
    missing_fields: tuple = ()
    fallback_reason: Optional[str] = None
    delegated_reason: Optional[str] = None
    nodes: int = 0
    kernel_calls: int = 0
    elapsed_ms: float = 0.0
    budget: Optional[dict] = None
    rule_version: str = "hangzhou-platform-guide-v34"
    reward_units: str = "base-score points"
    kernel_version: str = "python-frontier-v1"
    belief_version: str = "uniform_unseen-v1"
    continuation_version: str = "not_used_fast_ev"
    tail_version: str = "zero-v1"
    horizon: int = 2
    calibrated: bool = False
    runtime_kernel: str = "python-frontier-v1"

    def as_json(self):
        selected = next((c for c in self.candidates if c.tile == self.selected), None)
        return {
            "version": self.profile, "profile": self.profile,
            "profile_fingerprint": self.profile_fingerprint,
            "scope": self.scope, "context_hash": self.context_hash,
            "level": self.level, "selected": self.selected,
            "legacy_best": self.legacy_best,
            "rules_version": self.rule_version,
            "reward_units": self.reward_units,
            "kernel_version": self.kernel_version,
            "runtime_kernel": self.runtime_kernel,
            "belief_version": self.belief_version,
            "continuation_version": self.continuation_version,
            "tail_version": self.tail_version,
            "horizon": self.horizon,
            "calibrated": self.calibrated,
            "candidate_count": self.candidate_count,
            "candidates": [c.as_json() for c in self.candidates],
            "selected_candidate": selected.as_json() if selected else None,
            "reason": self.reason, "model_assumption": self.model_assumption,
            "complete": self.complete, "ev2_complete": self.ev2_complete,
            "missing_fields": list(self.missing_fields),
            "fallback_reason": self.fallback_reason,
            "delegated_reason": self.delegated_reason,
            "nodes": self.nodes, "kernel_calls": self.kernel_calls,
            "elapsed_ms": self.elapsed_ms, "budget": self.budget,
        }

    to_json = as_json


def _candidate_q0(item: DiscardFrontierItem, context, profile,
                  ev1=None, ev2=None):
    unknown = item.visible_unknown
    contributions = {
        "shanten": profile.q0_shanten_weight * float(item.shanten),
        "U1": profile.q0_u1_weight * float(item.u1),
    }
    if context.chain_count is not None:
        contributions["chain"] = profile.q0_fan_weight * float(context.chain_count)
    contributions["risk"] = profile.q0_risk_weight * float(
        max(0, unknown - item.u1))
    base_q0 = float(profile.calibration_intercept) + sum(
        contributions.values())
    if profile.calibrated and profile.calibration_intercept:
        contributions["intercept"] = float(profile.calibration_intercept)
    # EV2 already contains the first-draw HU contribution.  When the two-draw
    # layer is present, do not add EV1 a second time even if an old/custom
    # profile happens to carry a non-zero EV1 coefficient.
    if ev2 is None and ev1 is not None:
        contributions["EV1"] = profile.q0_ev1_weight * float(ev1)
    if ev2 is not None:
        contributions["EV2"] = profile.q0_ev2_weight * float(ev2)
    value = base_q0 + sum(v for key, v in contributions.items()
                          if key in ("EV1", "EV2"))
    # Q0 is a structurally defined fallback.  It is not called an EV when no
    # calibrated score layer is available, even though it remains sortable.
    return base_q0, value, contributions


def _apply_draw(hand, rem, tile):
    h = list(hand)
    r = list(rem)
    h[tile] += 1
    r[tile] -= 1
    return tuple(h), tuple(r)


def _one_draw_value(hand, rem, live_wall, locked, chain_count,
                    chain_piao, scorer, budget):
    """One future hero draw; non-HU tail is explicitly zero."""
    if live_wall < 4:
        return 0.0
    n = sum(max(0, x) for x in rem)
    if n <= 0:
        return 0.0
    total = 0.0
    for tile, left in enumerate(rem):
        if left <= 0:
            continue
        budget.consume()
        hand2, _rem2 = _apply_draw(hand, rem, tile)
        breakdown = scorer.hu(
            hand2, scorer.standing_before_draw(hand2, tile), locked,
            tile, False, chain_count, chain_piao)
        if breakdown.legal:
            total += (left / n) * float(breakdown.reward)
    return total


def future_values(context: PublicDecisionContext, root_hand, locked,
                  chain_count, chain_piao, *, horizon=2, budget=None):
    """Return ``(EV1, EV2)`` for one root discard.

    The first-draw traversal is shared.  EV2 is the two-draw value itself,
    including EV1 outcomes; callers must not add EV1 to EV2.
    """
    if horizon <= 0:
        return 0.0, 0.0
    if (context.live_wall is None or context.base is None or
            context.dealer is None or chain_count is None or
            chain_piao is None):
        return None, None
    scorer = ScoreValue(context.dealer, context.base,
                        bool(context.you_cai_bi_kao), context.hero_seat)
    rem = tuple(int(x) for x in context.remaining)
    if context.live_wall < 4 or sum(rem) <= 0:
        return 0.0, 0.0
    budget = budget or DecisionBudget(10**9, 10**9)
    n = sum(rem)
    ev1 = 0.0
    ev2 = 0.0
    live_after = max(0, int(context.live_wall) - 4)
    for tile, left in enumerate(rem):
        if left <= 0:
            continue
        budget.consume()
        hand2, rem2 = _apply_draw(root_hand, rem, tile)
        breakdown = scorer.hu(
            hand2, scorer.standing_before_draw(hand2, tile), locked,
            tile, False, chain_count, chain_piao)
        probability = left / n
        if breakdown.legal:
            # The first draw is a terminal value for both horizons.
            ev1 += probability * float(breakdown.reward)
            ev2 += probability * float(breakdown.reward)
            continue
        if horizon < 2 or live_after < 4:
            continue
        # All legal post-draw discards participate in the continuation.  This
        # intentionally does not call shape-v1's minimum-shanten helper.
        best_second = 0.0
        best_seen = False
        for discard, count in enumerate(hand2):
            if count <= 0:
                continue
            budget.consume()
            next_hand, next_chain, next_piao, _piao = scorer.discard(
                hand2, discard, locked, chain_count, chain_piao)
            value = _one_draw_value(
                next_hand, rem2, live_after, locked, next_chain, next_piao,
                scorer, budget)
            if not best_seen or value > best_second:
                best_second, best_seen = value, True
        if best_seen:
            ev2 += probability * best_second
    return ev1, ev2


def _legacy_fallback_result(context, profile, reason, selected=None,
                            candidates=(), budget=None, delegated=None,
                            legacy_best=None):
    missing = list(context.missing_fields)
    if context.chain_count is None:
        missing.append("chain_count")
    if context.chain_piao is None:
        missing.append("chain_piao")
    return FastEvaluation(
        profile=profile.name, profile_fingerprint=profile.fingerprint,
        scope=profile.scope, context_hash=context.context_hash,
        level="legacy", selected=selected, candidates=tuple(candidates),
        candidate_count=len(candidates), reason=reason,
        legacy_best=selected if legacy_best is None else legacy_best,
        complete=True,
        ev2_complete=False, missing_fields=tuple(sorted(set(missing))),
        fallback_reason=reason, delegated_reason=delegated,
        nodes=budget.nodes if budget else 0,
        kernel_calls=budget.kernel_calls if budget else 0,
        elapsed_ms=round(budget.elapsed_ms, 3) if budget else 0.0,
        budget={"node_limit": profile.node_budget,
                "time_limit_ms": profile.time_budget_ms} if budget else None,
        rule_version=profile.rules_version,
        reward_units=profile.reward_units,
        kernel_version=profile.kernel_version,
        belief_version=profile.belief_version,
        continuation_version=profile.continuation_version,
        tail_version=profile.tail_version,
        horizon=profile.horizon,
        calibrated=profile.calibrated, runtime_kernel=profile.kernel_version)


def evaluate_discard_context(context: PublicDecisionContext,
                             profile: ProfileSpec | None = None,
                             *, level="EV2", budget=None, legacy_best=None):
    """Evaluate every legal ordinary discard from a public context."""
    profile = profile or ProfileSpec.shape_v2_discard()
    if profile.scope != "discard":
        raise ContextError("discard evaluator requires discard scope")
    if context.phase not in ("discard", "draw"):
        raise ContextError("discard evaluator requires a discard context")
    context.validate_for("fast")
    started = budget or DecisionBudget(profile.node_budget, profile.time_budget_ms)
    runtime_kernel = profile.kernel_version
    try:
        frontier = discard_frontier(context, use_rust=True)
        kernels = {item.kernel for item in frontier}
        runtime_kernel = ",".join(sorted(kernels)) or runtime_kernel
        if any(not kernel.startswith("python-") for kernel in kernels):
            started.kernel()
        candidates = []
        for item in frontier:
            started.consume()
            q0, value, contributions = _candidate_q0(item, context, profile)
            candidates.append(V2Candidate(
                item.tile, item.shanten, item.u1, item.p1,
                item.ukeire_tiles, item.ukeire_bitset,
                item.structure_ukeire, None, None, q0, value,
                contributions, missing=("I", "EV1", "EV2"),
                level="V2-Q0", waits=item.waits,
                visible_unknown=item.visible_unknown,
                risk=max(0, item.visible_unknown - item.u1)))
    except BudgetExceeded as exc:
        return _legacy_fallback_result(context, profile, "q0_" + exc.reason,
                                       budget=started,
                                       legacy_best=legacy_best)
    if not candidates:
        return _legacy_fallback_result(context, profile, "no_legal_discard",
                                       budget=started,
                                       legacy_best=legacy_best)

    requested_high = level in ("EV1", "EV2", "Q")
    high_complete = (requested_high and profile.horizon in (1, 2) and
                     context.chain_count is not None and
                     context.chain_piao is not None)
    evaluated = []
    if high_complete:
        high_complete = True
        frontier_by_tile = {item.tile: item for item in frontier}
        want_ev2 = level in ("EV2", "Q") and profile.horizon >= 2
        completed_values = []
        cache = {}
        for candidate in candidates:
            try:
                layer = "EV2" if want_ev2 else "EV1"
                horizon = 2 if want_ev2 else 1
                cache_key = (context.context_hash, profile.fingerprint,
                             candidate.tile, layer, horizon)
                # The upper bound is used only as a certificate.  With no
                # completed same-layer value it never removes the first
                # candidate; equality is deliberately retained.
                reward_bound = (profile.reward_upper_bound
                                if profile.reward_upper_bound is not None
                                else theoretical_reward_bound(context.base or 1))
                layer_weight = (profile.q0_ev2_weight if want_ev2
                                else profile.q0_ev1_weight)
                effective_weight = abs(layer_weight)
                upper = candidate.q0 + effective_weight * reward_bound
                if completed_values and upper < max(completed_values):
                    evaluated.append(V2Candidate(
                        candidate.tile, candidate.shanten, candidate.u1,
                        candidate.p1, candidate.ukeire_tiles,
                        candidate.ukeire_bitset, candidate.structure_ukeire,
                        None, None, candidate.q0, None,
                        {"upper_bound": upper,
                         "certificate": "rules_reward_bound_v1"},
                        ("I", "EV1", "EV2", "pruned_by_upper_bound"),
                        upper, True, cache_key,
                        "V2-EV2" if want_ev2 else "V2-EV1",
                        waits=frontier_by_tile[candidate.tile].waits,
                        visible_unknown=candidate.visible_unknown,
                        risk=max(0, candidate.visible_unknown - candidate.u1)))
                    continue
                if cache_key in cache:
                    ev1, ev2 = cache[cache_key]
                else:
                    root_hand, chain, piao, _ = ScoreValue(
                        context.dealer or 0, context.base or 1,
                        bool(context.you_cai_bi_kao), context.hero_seat).discard(
                            context.hand, candidate.tile, context.locked,
                            context.chain_count, context.chain_piao)
                    ev1, ev2 = future_values(
                        context, root_hand, context.locked, chain, piao,
                        horizon=horizon,
                        budget=started)
                    cache[cache_key] = (ev1, ev2)
                if ev1 is None or (want_ev2 and ev2 is None):
                    high_complete = False
                    break
                # Recompute contribution without creating a partial mixed
                # layer; q is available only after every candidate finishes.
                q0, _value, contributions = _candidate_q0(
                    frontier_by_tile[candidate.tile], context, profile,
                    ev1=ev1, ev2=ev2 if want_ev2 else None)
                evaluated.append(V2Candidate(
                    candidate.tile, candidate.shanten, candidate.u1,
                    candidate.p1, candidate.ukeire_tiles,
                    candidate.ukeire_bitset, candidate.structure_ukeire,
                    ev1, ev2, q0, _value, contributions, upper_bound=upper,
                    cache_key=cache_key,
                    missing=("I",) if ev2 is not None else ("I", "EV2"),
                    level="V2-EV2" if want_ev2 else "V2-EV1",
                    waits=frontier_by_tile[candidate.tile].waits,
                    visible_unknown=candidate.visible_unknown,
                    risk=max(0, candidate.visible_unknown - candidate.u1)))
                completed_values.append(_value)
            except BudgetExceeded:
                high_complete = False
                break
            except (ValueError, ContextError):
                high_complete = False
                break
    if high_complete and evaluated:
        candidates = evaluated
        selected = max(candidates, key=lambda c: (
            c.value if c.value is not None else -math.inf,
            c.q0, -c.tile)).tile
        return FastEvaluation(
            profile=profile.name, profile_fingerprint=profile.fingerprint,
            scope=profile.scope, context_hash=context.context_hash,
            level="V2-EV2" if want_ev2 else "V2-EV1",
            selected=selected, legacy_best=legacy_best,
            candidates=tuple(candidates),
            candidate_count=len(candidates),
            reason="shape_v2_ev2" if want_ev2 else "shape_v2_ev1",
            complete=True, ev2_complete=want_ev2, nodes=started.nodes,
            kernel_calls=started.kernel_calls,
            elapsed_ms=round(started.elapsed_ms, 3), budget={
                "node_limit": profile.node_budget,
                "time_limit_ms": profile.time_budget_ms,
            }, rule_version=profile.rules_version,
            reward_units=profile.reward_units,
            kernel_version=profile.kernel_version,
            belief_version=profile.belief_version,
            continuation_version=profile.continuation_version,
            tail_version=profile.tail_version,
            horizon=profile.horizon,
            calibrated=profile.calibrated, runtime_kernel=runtime_kernel)
    # High-level computation is transactional: retain the complete Q0 table.
    selected = max(candidates, key=lambda c: (c.q0, -c.tile)).tile
    reason = ("shape_v2_q0_missing_chain" if requested_high and
              (context.chain_count is None or context.chain_piao is None)
              else "shape_v2_q0_fallback")
    return FastEvaluation(
        profile=profile.name, profile_fingerprint=profile.fingerprint,
        scope=profile.scope, context_hash=context.context_hash,
        level="V2-Q0", selected=selected,
        legacy_best=(legacy_best if legacy_best is not None else None),
        candidates=tuple(candidates),
        candidate_count=len(candidates), reason=reason, complete=True,
        ev2_complete=False, fallback_reason=(
            "missing_chain" if requested_high and
            (context.chain_count is None or context.chain_piao is None)
            else started.exceeded or "ev2_incomplete"),
        nodes=started.nodes, kernel_calls=started.kernel_calls,
        elapsed_ms=round(started.elapsed_ms, 3), budget={
            "node_limit": profile.node_budget,
            "time_limit_ms": profile.time_budget_ms,
        }, rule_version=profile.rules_version,
        reward_units=profile.reward_units,
        kernel_version=profile.kernel_version,
        belief_version=profile.belief_version,
        continuation_version=profile.continuation_version,
        tail_version=profile.tail_version,
        horizon=profile.horizon,
        calibrated=profile.calibrated, runtime_kernel=runtime_kernel)


def choose_game_action(game, seat, profile=None):
    """Return ``(action, FastEvaluation.as_json())`` for opt-in shape-v2."""
    profile = profile or ProfileSpec.shape_v2_discard()
    context = PublicDecisionContext.from_game(game, seat)
    acts = tuple(game.legal_actions())
    if len(acts) == 1:
        return acts[0], _legacy_fallback_result(
            context, profile, "only_legal_action", selected=acts[0]).as_json()
    if game.phase != "discard":
        from ..bot import choose_action
        action = choose_action(game, seat, evaluator="legacy")
        return action, _legacy_fallback_result(
            context, profile, "scope_delegated_reaction", selected=action,
            delegated="reaction_scope").as_json()
    if any(a < 0 for a in acts):
        from ..bot import choose_action
        action = choose_action(game, seat, evaluator="legacy")
        if profile.scope != "discard":
            from .root import evaluate_root_context
            root = evaluate_root_context(context, profile, legacy_action=action)
            return root.selected, root.as_json()
        return action, _legacy_fallback_result(
            context, profile, "scope_delegated_root_action", selected=action,
            delegated="hu_or_kong_scope").as_json()
    if profile.scope != "discard":
        from .root import evaluate_root_context
        from ..bot import choose_action
        root = evaluate_root_context(context, profile,
                                     legacy_action=choose_action(
                                         game, seat, evaluator="legacy"))
        return root.selected, root.as_json()
    # Keep the actual legacy choice beside the v2 result.  This is an
    # explanation field only; it does not alter the v2 candidate ordering.
    from ..bot import choose_action
    legacy_action = choose_action(game, seat, evaluator="legacy")
    result = evaluate_discard_context(context, profile,
                                      legacy_best=legacy_action)
    return result.selected, result.as_json()


evaluate_discard = evaluate_discard_context
evaluate_game = choose_game_action
