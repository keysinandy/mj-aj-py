"""Bounded legal reaction roots and common-horizon score comparison."""
from dataclasses import dataclass

from .game import PASS, PONG, KONG_OPEN, CHOW_LOW, CHOW_MID, CHOW_HIGH
from .legacy_completion import CompletionEstimator
from .legacy_belief import LegacyDecisionFeatures
from .legacy_react import _claim_remove, reaction_tempo
from .legacy_value import standing_value, confident_winner
from .shanten import shanten
from .tiles import W
from .win import is_baotou


@dataclass(frozen=True)
class JointDiscardPlan:
    public_input_hash: str
    round_key: tuple
    profile_fingerprint: str
    discard: int


@dataclass
class QualityDecisionState:
    """Caller-owned public state, retained across projected Game/Mirror turns."""
    pending_discard: JointDiscardPlan | None = None


def claim_discard_plan(features, action, discard, profile):
    """Project a claim without stepping a full Game or reading hidden tiles."""
    c = features.context
    if action not in (PONG, CHOW_LOW, CHOW_MID, CHOW_HIGH):
        return None
    hand, melds, discards, chows = list(c.hand), list(c.melds), list(c.discards), list(c.chows)
    for tile, amount in _claim_remove(action, c.pending_tile):
        hand[tile] -= amount
    if discard is None or not 0 <= discard < 34 or hand[discard] <= 0:
        raise ValueError("joint_discard_plan_invalid")
    kind, tile = (("pong", c.pending_tile) if action == PONG else
                  ("chow", c.pending_tile-(CHOW_LOW-action)))
    melds[c.hero_seat] += ((kind, tile),)
    discards[c.pending_owner] = discards[c.pending_owner][:-1]
    if kind == "chow":
        chows[c.hero_seat] += 1
    counts = list(c.concealed_counts)
    counts[c.hero_seat] = sum(hand)
    projected = c.replace(hand=tuple(hand), locked=c.locked+1, melds=tuple(melds),
        discards=tuple(discards), chows=tuple(chows), concealed_counts=tuple(counts),
        phase="discard", turn=c.hero_seat, drawn=None, pending_owner=None, pending_tile=None,
        react_seq=(), react_index=None, react_claim_count=None,
        legal_actions=tuple(t for t, n in enumerate(hand) if n))
    normalized = LegacyDecisionFeatures(projected)
    return JointDiscardPlan(normalized.context.input_hash, (c.gid, c.round_no),
                            profile.fingerprint, discard)


def enumerate_roots(features, baseline_evaluation):
    c = features.context
    if c.pending_tile is None or c.pending_owner is None:
        raise ValueError("pending reaction is unavailable")
    roots = {PASS: [(None, c.hand, c.locked, reaction_tempo(c.hero_seat, c.pending_owner).pass_draw_index, False)]}
    if c.freeze > 0 and c.hero_seat != c.freezer:
        return roots
    for action in c.legal_actions:
        if action == PASS:
            continue
        hand = list(c.hand)
        if action == KONG_OPEN:
            gate = baseline_evaluation.get("kong") or {}
            if not gate.get("gate_passed") or not gate.get("continuation_complete", True):
                continue
            hand[c.pending_tile] -= 3
            roots[action] = [(None, tuple(hand), c.locked + 1, 1, True)]
            continue
        if action not in (PONG, CHOW_LOW, CHOW_MID, CHOW_HIGH):
            continue
        for tile, amount in _claim_remove(action, c.pending_tile):
            hand[tile] -= amount
        if min(hand) < 0:
            raise ValueError("invalid claim material")
        children = []
        for tile, amount in enumerate(hand):
            if amount:
                standing = list(hand)
                standing[tile] -= 1
                children.append((tile, tuple(standing), c.locked + 1, 4, False))
        roots[action] = children
    return roots


def choose_joint(features, baseline, evaluation, belief, profile, budget, *,
                 continuation=None, completion_estimator=None):
    roots = enumerate_roots(features, evaluation)
    if len(roots) > profile.root_cap or baseline not in roots:
        return baseline, {"fallback_reason": "joint_root_cap_or_baseline_gate"}
    before = shanten(features.context.hand, features.context.locked)
    if continuation is None and (before > 0 or any(
            min(shanten(row[1], row[2]) for row in candidates) > 0
            for candidates in roots.values())):
        # A loss-only next-draw value cannot authorize sacrificing offensive
        # progress. Missing tail evidence must abstain, not rate it at zero.
        return baseline, {"fallback_reason": "joint_nonterminal_tail_missing"}
    values, children = {}, {}
    accepted = {row["action"] for row in evaluation.get("candidates", ()) if row.get("accepted")}
    estimator = completion_estimator or CompletionEstimator(budget)
    for action, candidates in roots.items():
        budget.check()
        if action not in (PASS, KONG_OPEN):
            best_shanten = min(shanten(row[1], row[2]) for row in candidates)
            candidates = [row for row in candidates if shanten(row[1], row[2]) == best_shanten]
        # Rejected claims enter only a bounded same-or-better-shanten rescue.
        if action != PASS and action != baseline and action not in accepted:
            candidates = [row for row in candidates if shanten(row[1], row[2]) <= before]
        if not candidates:
            continue
        results = []
        for discard, hand, locked, delay, kong_draw in candidates:
            budget.check()
            c = features.context
            # Claim followed by an ordinary discard breaks chain; an open
            # kong doubles the future multiplier, with no immediate bonus.
            piao_discard = discard == W and is_baotou(hand, locked)
            chain = (c.chain_count or 0) + 1 if kong_draw or piao_discard else (c.chain_count or 0) if action == PASS else 0
            piao = (c.chain_piao or 0) + int(piao_discard) if kong_draw or piao_discard or action == PASS else 0
            value, completion = standing_value(features, hand, locked, belief,
                first_draw_delay=delay, chain=chain, chain_piao=piao,
                kong_draw=kong_draw, budget=budget, discard_tile=discard,
                continuation=continuation, completion_estimator=estimator)
            results.append((value, discard, completion))
        if not all(v.complete and v.calibrated for v, d, h in results):
            return baseline, {"fallback_reason": "joint_incomplete_or_uncalibrated"}
        value, discard, completion = max(results, key=lambda r: (r[0].total_ev, -(r[1] if r[1] is not None else -1)))
        values[action] = value
        children[action] = {"discard": discard, "completion": completion.as_json(),
                            "rescue": action not in accepted and action != PASS,
                            "children_evaluated": len(results)}
    selected, reason = confident_winner(values, baseline, profile)
    return selected, {"fallback_reason": reason if selected == baseline else None,
                      "override_reason": reason if selected != baseline else None,
                      "score_ev": {str(a): v.as_json() for a, v in values.items()},
                      "children": children, "complete": True}
