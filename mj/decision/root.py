"""Explicit root-action scope dispatch for shape-v2.

Ordinary discard remains the only default online optimisation scope.  The
explicit root helper can additionally compare HU/财飘, self KONG replacement,
and complete-cursor reaction transitions only for a calibrated profile; any
incomplete layer still delegates with a truthful reason.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import math

from ..game import (PASS, PONG, CHOW_LOW, CHOW_MID, CHOW_HIGH, HU,
                    KONG_OPEN, KONG_CLOSED_BASE, KONG_ADD_BASE)
from .context import ContextError, PublicDecisionContext, _visible
from .fast_ev import (BudgetExceeded, DecisionBudget, evaluate_discard_context,
                      future_values)
from .frontier import discard_frontier
from .profile import ProfileSpec
from .score_value import ScoreValue


def _is_kong(action):
    return (action == KONG_OPEN or
            KONG_CLOSED_BASE - 33 <= action <= KONG_CLOSED_BASE or
            KONG_ADD_BASE - 33 <= action <= KONG_ADD_BASE)


def _decode_kong(action):
    """Return the public self-kong transition encoded by ``action``."""
    if KONG_CLOSED_BASE - 33 <= action <= KONG_CLOSED_BASE:
        return "closed", KONG_CLOSED_BASE - int(action)
    if KONG_ADD_BASE - 33 <= action <= KONG_ADD_BASE:
        return "add", KONG_ADD_BASE - int(action)
    return None


def _legal_post_kong_discards(context, hand, drawn):
    """Return the legal discard subset after a replacement draw.

    A freeze belongs to the public transition and must not be dropped merely
    because this is an offline root comparison.  KONG itself is already in
    the root legal set, so this helper only returns ordinary tile actions.
    """
    if (context.freeze > 0 and context.hero_seat != context.freezer):
        return ((drawn,) if drawn is not None and hand[drawn] > 0 else ())
    return tuple(tile for tile, count in enumerate(hand) if count > 0)


def _ordinary_actions(context):
    """Return only ordinary discards authorised by the current root set."""
    if context.legal_actions:
        return tuple(action for action in context.legal_actions
                     if 0 <= int(action) < 34)
    # An empty legal set is not a wildcard for root evaluation.  This fallback
    # is retained only for value-object callers that explicitly omit a legal
    # set; ``evaluate_root_context`` rejects that state before reaching here.
    return tuple(tile for tile, count in enumerate(context.hand) if count > 0)


def _discard_transition(context, tile, chain, chain_piao, is_piao):
    """Serialize the rule-owned chain/catch-play part of a discard root.

    The EV model intentionally does not simulate opponent actions.  It still
    records the exact Game transition that determines the next legal window:
    W starts a three-response catch-play ring, while a non-W discard consumes
    one existing freeze slot.  Chain values come from ``ScoreValue.discard``
    (the same adapter used by ``Game._do_discard``).
    """
    wild = int(tile) == 33
    freeze_before = max(0, int(context.freeze))
    freeze_after = 3 if wild else max(0, freeze_before - 1)
    return {
        "kind": "discard", "tile": int(tile),
        "is_piao": bool(is_piao),
        "chain": chain, "chain_piao": chain_piao,
        "catch_play": wild,
        "freezer_after_discard": (context.hero_seat if wild
                                   else context.freezer),
        "freeze_after_discard": freeze_after,
        "live_wall_after_discard": context.live_wall,
        "next_hero_draws": 4,
    }


def _chow_start(tile, action):
    if tile < 0 or tile >= 27:
        return None
    pos = CHOW_LOW - int(action)
    start = int(tile) - pos
    suit_start = int(tile) - int(tile) % 9
    if start < suit_start or start + 2 >= suit_start + 9:
        return None
    return start


def _root_action_errors(context, actions):
    """Check supplied candidates without replacing Game's legal authority.

    Contexts are allowed to carry a deliberately narrowed candidate subset
    for offline comparison, so this is a structural/rule check rather than an
    equality check against a newly fabricated Game.  It prevents a malformed
    public snapshot (for example a KONG in the dead-wall tail) from becoming a
    seemingly valid one-action root.
    """
    errors = []
    reaction = context.phase == "react" or str(context.phase).startswith(
        "response")
    if not actions:
        return ("legal_actions_missing",)
    if reaction:
        if context.phase not in ("react", "response_peng", "response_chi"):
            return ("unknown_reaction_window",)
        ready = _reaction_context_ready(context)
        # A missing cursor is a delegation condition, not a reason to infer a
        # response order.  Keep all details in the explanation while using a
        # stable root reason at the dispatch boundary.
        if ready:
            errors.extend(ready)
        if (context.freeze > 0 and
                context.hero_seat != context.freezer):
            errors.append("reaction_frozen_actor")
        tile = context.pending_tile
        mode = None
        if context.react_index is not None and context.react_claim_count is not None:
            mode = ("claim" if int(context.react_index) < int(
                context.react_claim_count) else "chow")
        for action in actions:
            action = int(action)
            if action == PASS:
                continue
            if tile is None or tile == 33:
                errors.append("wild_reaction_action")
                continue
            if mode == "claim":
                if action == PONG and context.hand[tile] < 2:
                    errors.append("illegal_pong")
                elif action == KONG_OPEN and (
                        context.hand[tile] != 3 or
                        context.live_wall is None or context.live_wall <= 0):
                    errors.append("illegal_open_kong")
                elif action not in (PONG, KONG_OPEN):
                    errors.append("reaction_action_mode_mismatch")
            elif mode == "chow":
                start = _chow_start(tile, action)
                if action in (CHOW_LOW, CHOW_MID, CHOW_HIGH):
                    if (start is None or
                            context.chows[context.hero_seat] >= 2 or
                            any(context.hand[value] <= 0 for value in
                                (start, start + 1, start + 2)
                                if value != tile)):
                        errors.append("illegal_chow")
                else:
                    errors.append("reaction_action_mode_mismatch")
            else:
                errors.append("response_cursor_missing")
        return tuple(sorted(set(errors)))

    if context.phase not in ("discard", "draw"):
        return ("unknown_root_phase",)
    for action in actions:
        action = int(action)
        if 0 <= action < 34:
            if context.hand[action] <= 0:
                errors.append("discard_not_in_hand")
            elif (context.freeze > 0 and
                  context.hero_seat != context.freezer and
                  context.drawn != action):
                errors.append("freeze_discard_mismatch")
            continue
        if action == HU:
            if context.drawn is None:
                errors.append("hu_without_draw")
            elif all(value is not None for value in (
                    context.dealer, context.base, context.chain_count,
                    context.chain_piao)):
                scorer = ScoreValue(
                    context.dealer, context.base,
                    bool(context.you_cai_bi_kao), context.hero_seat)
                breakdown = scorer.hu(
                    context.hand,
                    scorer.standing_before_draw(context.hand, context.drawn),
                    context.locked, context.drawn, context.kong_draw,
                    context.chain_count, context.chain_piao)
                if not breakdown.legal:
                    errors.append("hu_not_legal")
            continue
        decoded = _decode_kong(action)
        if decoded is None:
            errors.append("unknown_root_action")
            continue
        kind, tile = decoded
        if tile >= 33 or context.drawn is None:
            errors.append("illegal_kong_tile_or_draw")
        elif context.live_wall is None or context.live_wall <= 0:
            errors.append("kong_wall_tail")
        elif kind == "closed" and context.hand[tile] != 4:
            errors.append("illegal_closed_kong")
        elif kind == "add" and (
                context.hand[tile] != 1 or
                not any(meld == ("pong", tile)
                        for meld in context.melds[context.hero_seat]) or
                (context.freeze > 0 and context.hero_seat != context.freezer)):
            errors.append("illegal_add_kong")
    return tuple(sorted(set(errors)))


def _context_after_kong_draw(context, kind, tile, draw):
    """Project a public KONG plus replacement draw transition.

    The KONG exchanges concealed material for an exposed meld, so the visible
    count is unchanged until the replacement tile is drawn.  No wall order or
    opponent hand identity is copied into this value object.
    """
    if (context.live_wall is None or context.live_wall <= 0 or
            not 0 <= int(draw) < 34 or context.remaining[int(draw)] <= 0):
        return None
    hand = list(context.hand)
    remove = 4 if kind == "closed" else 1
    if hand[tile] < remove:
        return None
    hand[tile] -= remove
    melds = [list(row) for row in context.melds]
    hero_melds = melds[context.hero_seat]
    if kind == "closed":
        hero_melds.append(("kong_closed", tile))
    else:
        for index, meld in enumerate(hero_melds):
            if meld == ("pong", tile):
                hero_melds[index] = ("kong_add", tile)
                break
        else:
            return None
    hand[draw] += 1
    visible = list(context.visible)
    visible[draw] += 1
    chains = list(context.chain_counts)
    piao_counts = list(context.chain_piao_counts)
    if context.chain_count is None:
        return None
    chains[context.hero_seat] = int(context.chain_count) + 1
    if context.chain_piao is not None:
        piao_counts[context.hero_seat] = int(context.chain_piao)
    concealed = list(context.concealed_counts)
    if concealed[context.hero_seat] is not None:
        concealed[context.hero_seat] = 13 - 3 * len(hero_melds) + 1
    live_wall = (None if context.live_wall is None
                 else max(0, int(context.live_wall) - 1))
    return context.replace(
        hand=tuple(hand), melds=tuple(tuple(row) for row in melds),
        locked=len(hero_melds), visible=tuple(visible), drawn=int(draw),
        concealed_counts=tuple(concealed),
        kong_draw=True, chain_counts=tuple(chains),
        chain_piao_counts=tuple(piao_counts),
        chain_count=chains[context.hero_seat],
        chain_piao=piao_counts[context.hero_seat], live_wall=live_wall,
        turn=context.hero_seat, phase="discard", legal_actions=())


def _reaction_context_ready(context):
    """Validate the public response cursor before evaluating a root action."""
    reasons = []
    if context.phase != "react" and not str(context.phase).startswith(
            "response"):
        reasons.append("not_reaction_phase")
    if context.pending_owner is None or context.pending_tile is None:
        reasons.append("pending_missing")
    if context.turn != context.hero_seat:
        reasons.append("hero_not_response_actor")
    if not context.react_seq or context.react_index is None:
        reasons.append("response_order_missing")
    elif not 0 <= int(context.react_index) < len(context.react_seq):
        reasons.append("response_cursor_invalid")
    elif context.react_seq[int(context.react_index)] != context.hero_seat:
        reasons.append("response_cursor_not_hero")
    if context.react_claim_count is None:
        reasons.append("response_claim_count_missing")
    elif context.react_index is not None:
        in_claim_slot = int(context.react_index) < int(
            context.react_claim_count)
        legal = set(int(action) for action in context.legal_actions)
        if in_claim_slot:
            allowed = {PASS, PONG, KONG_OPEN}
        else:
            allowed = {PASS, CHOW_LOW, CHOW_MID, CHOW_HIGH}
        if not legal.issubset(allowed):
            reasons.append("response_action_mode_mismatch")
    if (context.pending_owner is not None and
            context.pending_owner == context.hero_seat):
        reasons.append("hero_pending_owner")
    if context.pending_owner is not None and context.pending_tile is not None:
        river = context.discards[context.pending_owner]
        if not river or river[-1] != context.pending_tile:
            reasons.append("pending_not_river_top")
    if PASS not in context.legal_actions:
        reasons.append("pass_missing_from_legal_set")
    return tuple(sorted(set(reasons)))


def _reaction_action_name(action):
    return {
        PASS: "PASS", PONG: "PONG", KONG_OPEN: "KONG_OPEN",
        CHOW_LOW: "CHOW_LOW", CHOW_MID: "CHOW_MID",
        CHOW_HIGH: "CHOW_HIGH",
    }.get(int(action), str(int(action)))


def _context_after_claim(context, action):
    """Apply a PONG/CHOW claim without inventing hidden state."""
    if context.pending_tile is None or context.pending_owner is None:
        return None
    tile = int(context.pending_tile)
    hand = list(context.hand)
    melds = [list(row) for row in context.melds]
    discards = [list(row) for row in context.discards]
    river = discards[context.pending_owner]
    if not river or river[-1] != tile:
        return None
    if action == PONG:
        if hand[tile] < 2:
            return None
        hand[tile] -= 2
        meld = ("pong", tile)
        chow_inc = 0
    elif action in (CHOW_LOW, CHOW_MID, CHOW_HIGH):
        if tile >= 27 or context.chows[context.hero_seat] >= 2:
            return None
        pos = CHOW_LOW - int(action)
        start = tile - pos
        suit_start = tile - tile % 9
        if start < suit_start or start + 2 >= suit_start + 9:
            return None
        for value in (start, start + 1, start + 2):
            if value != tile:
                if hand[value] <= 0:
                    return None
                hand[value] -= 1
        meld = ("chow", start)
        chow_inc = 1
    else:
        return None
    river.pop()
    melds[context.hero_seat].append(meld)
    locked = len(melds[context.hero_seat])
    if locked > 4:
        return None
    # Pending is removed from the old river and added to the new meld, while
    # the two consumed hero tiles were already visible.  Public material is
    # therefore unchanged by a claim.
    projected_visible = tuple(context.visible)
    if _visible(tuple(hand), tuple(tuple(row) for row in discards),
                tuple(tuple(row) for row in melds)) != projected_visible:
        return None
    concealed = list(context.concealed_counts)
    if concealed[context.hero_seat] is not None:
        concealed[context.hero_seat] = 13 - 3 * locked + 1
    legal = tuple(t for t, count in enumerate(hand) if count > 0)
    return context.replace(
        hand=tuple(hand), locked=locked,
        melds=tuple(tuple(row) for row in melds),
        chows=tuple(int(value) + (chow_inc if seat == context.hero_seat else 0)
                    for seat, value in enumerate(context.chows)),
        discards=tuple(tuple(row) for row in discards),
        visible=projected_visible, concealed_counts=tuple(concealed),
        drawn=None, kong_draw=False, turn=context.hero_seat, phase="discard",
        pending_owner=None, pending_tile=None, react_seq=(), react_index=None,
        react_claim_count=None, legal_actions=legal)


def _context_after_open_kong_draw(context, tile, draw):
    """Project a KONG_OPEN claim followed by its replacement draw."""
    if (context.pending_tile != tile or context.pending_owner is None or
            context.live_wall is None or context.live_wall <= 0 or
            not 0 <= int(draw) < 34 or context.remaining[int(draw)] <= 0):
        return None
    hand = list(context.hand)
    if hand[tile] < 3:
        return None
    melds = [list(row) for row in context.melds]
    discards = [list(row) for row in context.discards]
    river = discards[context.pending_owner]
    if not river or river[-1] != tile:
        return None
    hand[tile] -= 3
    river.pop()
    melds[context.hero_seat].append(("kong_open", tile))
    hand[int(draw)] += 1
    visible = list(context.visible)
    visible[int(draw)] += 1
    if _visible(tuple(hand), tuple(tuple(row) for row in discards),
                tuple(tuple(row) for row in melds)) != tuple(visible):
        return None
    chains = list(context.chain_counts)
    piao_counts = list(context.chain_piao_counts)
    if context.chain_count is None or context.chain_piao is None:
        return None
    chains[context.hero_seat] = int(context.chain_count) + 1
    piao_counts[context.hero_seat] = int(context.chain_piao)
    concealed = list(context.concealed_counts)
    if concealed[context.hero_seat] is not None:
        concealed[context.hero_seat] = 13 - 3 * len(
            melds[context.hero_seat]) + 1
    return context.replace(
        hand=tuple(hand), locked=len(melds[context.hero_seat]),
        melds=tuple(tuple(row) for row in melds),
        discards=tuple(tuple(row) for row in discards),
        visible=tuple(visible), concealed_counts=tuple(concealed),
        drawn=int(draw), kong_draw=True, turn=context.hero_seat,
        phase="discard", pending_owner=None, pending_tile=None,
        chain_counts=tuple(chains), chain_piao_counts=tuple(piao_counts),
        chain_count=chains[context.hero_seat],
        chain_piao=piao_counts[context.hero_seat],
        live_wall=max(0, int(context.live_wall) - 1)
        if context.live_wall is not None else None,
        react_seq=(), react_index=None, react_claim_count=None,
        legal_actions=tuple(t for t, count in enumerate(hand) if count > 0))


def _evaluate_claim_context(context, action, profile, budget):
    post = _context_after_claim(context, action)
    if post is None:
        return None
    if (post.chain_count is None or post.chain_piao is None or
            post.dealer is None or post.base is None):
        return None
    scorer = ScoreValue(post.dealer, post.base,
                        bool(post.you_cai_bi_kao), post.hero_seat)
    values = []
    for discard in post.legal_discards:
        budget.consume()
        next_hand, chain, piao, is_piao = scorer.discard(
            post.hand, discard, post.locked, post.chain_count,
            post.chain_piao)
        child = post.replace(
            hand=next_hand, drawn=None, kong_draw=False,
            chain_count=chain, chain_piao=piao,
            chain_counts=tuple(chain if i == post.hero_seat else value
                               for i, value in enumerate(post.chain_counts)),
            chain_piao_counts=tuple(
                piao if i == post.hero_seat else value
                for i, value in enumerate(post.chain_piao_counts)),
            legal_actions=())
        ev1, ev2 = future_values(
            child, next_hand, post.locked, chain, piao,
            horizon=profile.horizon, budget=budget)
        value = (ev2 if profile.horizon >= 2 else ev1)
        if value is None:
            return None
        values.append((float(value), int(discard), bool(is_piao)))
    if not values:
        return None
    best = max(values, key=lambda row: (row[0], -row[1]))
    return {
        "action": int(action), "name": _reaction_action_name(action),
        "value": best[0], "best_discard": best[1],
        "is_piao": best[2], "post_locked": post.locked,
        "post_chain": post.chain_count, "post_chain_piao": post.chain_piao,
        "transition": _discard_transition(
            post, best[1], post.chain_count, post.chain_piao, best[2]),
        "source": "reaction_claim_transition",
        "evaluated_discards": len(values),
    }


def _evaluate_open_kong_reaction(context, profile, budget):
    if (context.live_wall is None or context.live_wall <= 0 or
            context.chain_count is None or context.chain_piao is None or
            context.dealer is None or context.base is None):
        return None
    tile = context.pending_tile
    if tile is None or context.hand[tile] != 3:
        return None
    remaining = tuple(int(x) for x in context.remaining)
    total_mass = sum(max(0, x) for x in remaining)
    if total_mass <= 0:
        return {"action": KONG_OPEN, "name": "KONG_OPEN", "value": 0.0,
                "replacement_draw_EV": 0.0, "continuation_EV": 0.0,
                "winning_tiles": (), "source": "reaction_kong_transition"}
    before = list(context.hand)
    before[tile] -= 3
    locked = context.locked + 1
    chain = int(context.chain_count) + 1
    piao = int(context.chain_piao)
    scorer = ScoreValue(context.dealer, context.base,
                        bool(context.you_cai_bi_kao), context.hero_seat)
    total = immediate = continuation = 0.0
    winning_mass = 0
    winning_tiles = []
    for draw, left in enumerate(remaining):
        if left <= 0:
            continue
        budget.consume()
        hand_draw = list(before)
        hand_draw[draw] += 1
        probability = left / total_mass
        breakdown = scorer.hu(
            hand_draw, scorer.standing_before_draw(hand_draw, draw),
            locked, draw, True, chain, piao)
        if breakdown.legal:
            total += probability * float(breakdown.reward)
            immediate += probability * float(breakdown.reward)
            winning_mass += left
            winning_tiles.append(draw)
            continue
        if profile.horizon < 2 or context.live_wall - 1 < 4:
            continue
        drawn = _context_after_open_kong_draw(context, tile, draw)
        if drawn is None:
            return None
        best = 0.0
        legal_discards = _legal_post_kong_discards(
            drawn, hand_draw, draw)
        for discard in legal_discards:
            budget.consume()
            next_hand, next_chain, next_piao, _ = scorer.discard(
                hand_draw, discard, locked, chain, piao)
            child = drawn.replace(hand=next_hand, drawn=None,
                                  kong_draw=False, legal_actions=())
            ev1, _ev2 = future_values(
                child, next_hand, locked, next_chain, next_piao,
                horizon=1, budget=budget)
            if ev1 is None:
                return None
            best = max(best, float(ev1))
        total += probability * best
        continuation += probability * best
    return {
        "action": KONG_OPEN, "name": "KONG_OPEN", "value": total,
        "replacement_draw_EV": immediate,
        "continuation_EV": continuation,
        "winning_tiles": tuple(winning_tiles),
        "win_probability": winning_mass / total_mass,
        "source": "reaction_kong_transition",
        "post_locked": locked, "post_chain": chain,
        "post_chain_piao": piao,
        "transition": {
            "kind": "kong_open", "replacement_draw": True,
            "locked": locked, "chain": chain, "chain_piao": piao,
            "live_wall_after_replacement": max(
                0, int(context.live_wall) - 1),
        },
    }


def _evaluate_kong_context(context, action, profile, budget):
    """Evaluate one legal closed/add-kong using the public replacement draw.

    This is the all-root counterpart of the existing v33 legacy helper.  It
    evaluates the replacement draw in score units and, when the replacement
    is not HU, gives the actor one further own draw through the same EV1
    transition used by discard scope.  The current horizon is two future
    hero draws, so a KONG replacement consumes the first one.
    """
    decoded = _decode_kong(action)
    if (decoded is None or context.drawn is None or
            context.live_wall is None or context.live_wall <= 0):
        return None
    if (context.dealer is None or context.base is None or
            context.chain_count is None or context.chain_piao is None):
        return None
    kind, kong_tile = decoded
    before = list(context.hand)
    remove = 4 if kind == "closed" else 1
    if kong_tile >= 33 or before[kong_tile] < remove:
        return None
    if kind == "add" and not any(
            meld == ("pong", kong_tile)
            for meld in context.melds[context.hero_seat]):
        return None
    if (kind == "add" and context.freeze > 0 and
            context.hero_seat != context.freezer):
        return None
    before[kong_tile] -= remove
    locked = context.locked + (1 if kind == "closed" else 0)
    chain = int(context.chain_count) + 1
    chain_piao = int(context.chain_piao)
    remaining = tuple(int(x) for x in context.remaining)
    total_mass = sum(max(0, x) for x in remaining)
    if total_mass <= 0:
        return {"action": int(action), "kind": kind, "tile": kong_tile,
                "value": 0.0, "replacement_draw_EV": 0.0,
                "continuation_EV": 0.0, "winning_tiles": (),
                "win_probability": 0.0, "source": "public_kong_transition"}
    scorer = ScoreValue(context.dealer, context.base,
                        bool(context.you_cai_bi_kao), context.hero_seat)
    total = 0.0
    immediate = 0.0
    continuation = 0.0
    winning_mass = 0
    winning_tiles = []
    for draw, left in enumerate(remaining):
        if left <= 0:
            continue
        budget.consume()
        hand_draw = list(before)
        hand_draw[draw] += 1
        probability = left / total_mass
        standing = scorer.standing_before_draw(hand_draw, draw)
        breakdown = scorer.hu(
            hand_draw, standing, locked, draw, True, chain, chain_piao)
        if breakdown.legal:
            value = float(breakdown.reward)
            total += probability * value
            immediate += probability * value
            winning_mass += left
            winning_tiles.append(draw)
            continue
        # The replacement draw is the first future draw. If fewer than four
        # live tiles remain afterwards, the rules/model cannot return to this
        # actor for another draw; do not spend frontier work on a guaranteed
        # zero continuation.
        if (profile.horizon < 2 or
                context.live_wall - 1 < 4):
            continue
        drawn_context = _context_after_kong_draw(
            context, kind, kong_tile, draw)
        if drawn_context is None:
            return None
        legal_discards = _legal_post_kong_discards(
            drawn_context, hand_draw, draw)
        if not legal_discards:
            continue
        frontier = discard_frontier(
            hand_draw, locked=locked, visible=drawn_context.visible,
            legal_discards=legal_discards, use_rust=True)
        if any(not item.kernel.startswith("python-") for item in frontier):
            budget.kernel()
        best = 0.0
        for item in frontier:
            budget.consume()
            next_hand, next_chain, next_piao, _is_piao = scorer.discard(
                hand_draw, item.tile, locked, chain, chain_piao)
            ev1, _ev2 = future_values(
                drawn_context, next_hand, locked, next_chain, next_piao,
                horizon=1, budget=budget)
            if ev1 is None:
                return None
            best = max(best, float(ev1))
        total += probability * best
        continuation += probability * best
    return {
        "action": int(action), "kind": kind, "tile": kong_tile,
        "value": total, "replacement_draw_EV": immediate,
        "continuation_EV": continuation,
        "winning_tiles": tuple(winning_tiles),
        "win_probability": winning_mass / total_mass,
        "source": "public_kong_transition",
        "transition": {"kind": "kong_" + kind,
                        "replacement_draw": True, "locked": locked,
                        "chain": chain,
                        "chain_piao": chain_piao,
                        "live_wall_after_replacement": max(
                            0, int(context.live_wall) - 1)},
    }


def _evaluate_reaction_context(context, profile, budget):
    """Compare a complete response window in one score unit.

    Fast EV assumes later response slots PASS; it still requires the
    authoritative response cursor so an online partial snapshot can never be
    turned into a fabricated claim value.  Full hidden-world opponent claim
    effects remain an offline teacher concern.
    """
    reasons = _reaction_context_ready(context)
    if reasons:
        return _delegated(context, profile, "reaction_context_incomplete",
                          budget=budget)
    if (context.chain_count is None or context.chain_piao is None or
            context.dealer is None or context.base is None):
        return _delegated(context, profile, "reaction_score_context_missing",
                          budget=budget)
    try:
        owner = int(context.pending_owner)
        first_draws = (int(context.hero_seat) - owner) % 4
        if first_draws <= 0:
            first_draws = 4
        pass_ev1, pass_ev2 = future_values(
            context, context.hand, context.locked, context.chain_count,
            context.chain_piao, horizon=profile.horizon, budget=budget,
            first_draws=first_draws)
        pass_value = (pass_ev2 if profile.horizon >= 2 else pass_ev1)
        if pass_value is None:
            return _delegated(context, profile,
                              "reaction_pass_layer_incomplete", budget=budget)
        unknown = context.unknown_pool
        candidates = [{
            "action": PASS, "name": "PASS", "legal": True,
            "value": float(pass_value), "Q": float(pass_value),
            "source": "reaction_pass_baseline",
            "threshold_unit": profile.reward_units,
            "threshold_formula": "Q_claim-Q_pass >= tau[action]",
            "unknown_pool": unknown,
            "response_index": int(context.react_index),
            "remaining_response_order": list(
                context.react_seq[int(context.react_index) + 1:]),
        }]
        for action in context.legal_actions:
            if action == PASS:
                continue
            if action == KONG_OPEN:
                candidate = _evaluate_open_kong_reaction(
                    context, profile, budget)
            elif action == PONG or action in (
                    CHOW_LOW, CHOW_MID, CHOW_HIGH):
                candidate = _evaluate_claim_context(
                    context, action, profile, budget)
            else:
                return _delegated(context, profile, "reaction_unknown_action",
                                  budget=budget)
            if candidate is None or not math.isfinite(float(candidate["value"])):
                return _delegated(context, profile,
                                  "reaction_layer_incomplete", budget=budget)
            candidate.update({
                "legal": True, "Q": candidate["value"],
                "threshold_unit": profile.reward_units,
                "threshold_formula": "Q_claim-Q_pass >= tau[action]",
                "unknown_pool": context.unknown_pool,
                "response_index": int(context.react_index),
                "remaining_response_order": list(
                    context.react_seq[int(context.react_index) + 1:]),
            })
            candidates.append(candidate)

        def tau_for(action):
            name = _reaction_action_name(action)
            for key in (name, name.lower(), str(int(action))):
                if key in profile.tau:
                    return float(profile.tau[key])
            return 0.0

        accepted = []
        for candidate in candidates:
            if candidate["action"] == PASS:
                accepted.append(candidate)
                continue
            tau = tau_for(candidate["action"])
            delta = float(candidate["value"]) - float(pass_value)
            candidate["pass_value"] = float(pass_value)
            candidate["delta_vs_pass"] = delta
            candidate["tau"] = tau
            if unknown <= 0 and tau <= 0:
                candidate["accepted"] = False
                continue
            candidate["accepted"] = delta >= tau
            if candidate["accepted"]:
                accepted.append(candidate)
        if not accepted:
            selected = PASS
        else:
            # The legal order is supplied by Game and intentionally makes
            # PASS win exact ties, so a zero-value reaction cannot silently
            # become a new claim.
            order = {int(action): index for index, action in
                     enumerate(context.legal_actions)}
            selected = max(
                accepted,
                key=lambda row: (float(row["value"]),
                                 -order.get(int(row["action"]), 10**6)))["action"]
        return RootEvaluation(
            profile=profile.name, profile_fingerprint=profile.fingerprint,
            scope=profile.scope, context_hash=context.context_hash,
            level="V2-ROOT", selected=int(selected),
            candidates=tuple(candidates), reason="shape_v2_reaction_same_unit",
            missing_fields=(), complete=True, nodes=budget.nodes,
            kernel_calls=budget.kernel_calls,
            elapsed_ms=round(budget.elapsed_ms, 3),
            budget={"node_limit": budget.limit,
                    "time_limit_ms": budget.time_ms},
            bound_version=profile.bound_version,
            bound_mode=profile.bound_mode)
    except (BudgetExceeded, ContextError, ValueError, IndexError):
        return _delegated(context, profile, "reaction_layer_incomplete",
                          budget=budget)


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
    nodes: int = 0
    kernel_calls: int = 0
    elapsed_ms: float = 0.0
    budget: dict | None = None
    bound_version: str = "reward-envelope-v1"
    bound_mode: str = "unknown"

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
            "nodes": self.nodes, "kernel_calls": self.kernel_calls,
            "elapsed_ms": self.elapsed_ms, "budget": self.budget,
            "bound_version": self.bound_version,
            "bound_mode": self.bound_mode,
        }


def _delegated(context, profile, reason, selected=None, *, budget=None):
    return RootEvaluation(
        profile=profile.name, profile_fingerprint=profile.fingerprint,
        scope=profile.scope, context_hash=context.context_hash,
        level="legacy", selected=selected, candidates=(), reason=reason,
        delegated_reason=reason, missing_fields=tuple(context.missing_fields),
        complete=(reason == "only_legal_action"),
        nodes=budget.nodes if budget is not None else 0,
        kernel_calls=budget.kernel_calls if budget is not None else 0,
        elapsed_ms=round(budget.elapsed_ms, 3) if budget is not None else 0.0,
        budget=({"node_limit": budget.limit,
                 "time_limit_ms": budget.time_ms}
                if budget is not None else None),
        bound_version=profile.bound_version, bound_mode=profile.bound_mode)


def _discard_profile(profile):
    if profile.scope == "discard":
        return profile
    return replace(profile, scope="discard")


def evaluate_root_context(context: PublicDecisionContext,
                          profile: ProfileSpec | None = None,
                          *, legacy_action=None):
    """Evaluate a supported root or return an explicit scope delegation."""
    profile = profile or ProfileSpec.shape_v2_all_root()
    if profile.scope == "discard":
        raise ContextError("root evaluator requires hu-piao or all-root scope")
    try:
        context.validate_for("fast")
    except ContextError:
        # Unsupported public windows and an explicitly unknown platform
        # material projection are normal delegation boundaries. Truly
        # malformed value-object material still raises here so offline callers
        # cannot mistake corruption for a valid root evaluation.
        if "public_hand_counts_unknown" in context.missing_fields:
            return _delegated(context, profile, "context_material_unknown",
                              legacy_action)
        if "public_hand_counts_malformed" in context.missing_fields:
            return _delegated(context, profile, "context_material_malformed",
                              legacy_action)
        if context.missing_fields or context.unsupported:
            return _delegated(context, profile, "context_unsupported",
                              legacy_action)
        raise
    actions = tuple(context.legal_actions)
    if not actions:
        return _delegated(context, profile, "legal_actions_missing",
                          legacy_action)
    if context.phase in ("react", "response_peng", "response_chi"):
        # A complete response action set is still not enough to evaluate the
        # root when the ordered cursor/pending river is missing.  Check this
        # before the one-action shortcut and before action-shape validation so
        # an incomplete snapshot cannot be mislabeled as an authoritative
        # special-action decision.
        readiness = _reaction_context_ready(context)
        context_reasons = {
            "pending_missing", "hero_not_response_actor",
            "response_order_missing", "response_cursor_invalid",
            "response_cursor_not_hero", "response_claim_count_missing",
            "hero_pending_owner", "pending_not_river",
        }
        if any(reason in context_reasons for reason in readiness):
            return _delegated(context, profile,
                              "reaction_context_incomplete", legacy_action)
    action_errors = _root_action_errors(context, actions)
    if action_errors:
        return _delegated(context, profile, "root_legal_action_invalid",
                          legacy_action)
    if len(actions) == 1:
        return _delegated(context, profile, "only_legal_action", actions[0])
    if context.phase not in ("discard", "draw"):
        if profile.scope == "all-root" and profile.calibrated:
            return _evaluate_reaction_context(
                context, profile,
                DecisionBudget(profile.node_budget, profile.time_budget_ms))
        return _delegated(context, profile, "scope_delegated_reaction",
                          legacy_action)
    if any(_is_kong(action) for action in actions) and profile.scope != "all-root":
        return _delegated(context, profile,
                          "scope_delegated_kong_transition", legacy_action)
    if profile.scope == "all-root" and any(
            action < 0 and action != HU and not _is_kong(action)
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
    budget = DecisionBudget(profile.node_budget, profile.time_budget_ms)
    ordinary = _ordinary_actions(context)
    candidates = []
    if ordinary:
        try:
            discard_result = evaluate_discard_context(
                context, _discard_profile(profile), level="EV2", budget=budget,
                legacy_best=(legacy_action if legacy_action is not None and
                             0 <= int(legacy_action) < 34 else None),
                legal_discards=ordinary)
        except BudgetExceeded:
            return _delegated(context, profile, "root_budget_exceeded",
                              legacy_action, budget=budget)
        if discard_result.level not in ("V2-EV1", "V2-EV2") or any(
                c.value is None for c in discard_result.candidates):
            return _delegated(context, profile,
                              "root_discard_layer_incomplete", legacy_action,
                              budget=budget)
        candidates.extend({
            "action": c.tile, "legal": True, "value": c.value,
            "Q": c.value, "EV1": c.ev1, "EV2": c.ev2,
            "shanten": c.shanten, "U1": c.u1,
            "waits": list(c.waits), "q0": c.q0,
            "post_chain": c.post_chain,
            "post_chain_piao": c.post_chain_piao,
            "is_piao": c.is_piao, "discarded_wild": c.discarded_wild,
            "post_locked": c.post_locked,
            "reward_envelope": c.reward_envelope,
            "bound_version": ((c.reward_envelope or {}).get("version")
                              if c.reward_envelope else None),
            "bound_mode": ((c.reward_envelope or {}).get("mode")
                            if c.reward_envelope else None),
            "fast_upper": ((c.reward_envelope or {}).get("fast_upper")
                            if c.reward_envelope else None),
            "rollout_lower": ((c.reward_envelope or {}).get("rollout_lower")
                              if c.reward_envelope else None),
            "rollout_upper": ((c.reward_envelope or {}).get("rollout_upper")
                              if c.reward_envelope else None),
            "rollout_abs": ((c.reward_envelope or {}).get("rollout_abs")
                            if c.reward_envelope else None),
            "transition": _discard_transition(
                context, c.tile, c.post_chain, c.post_chain_piao, c.is_piao),
            "source": "discard_scope",
        } for c in discard_result.candidates)
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
                              legacy_action, budget=budget)
        candidates.append({
            "action": HU, "legal": True, "value": breakdown.reward,
            "Q": breakdown.reward, "instant_hu": breakdown.as_json(),
            "transition": {"kind": "hu", "immediate": True,
                           "live_wall_after_action": context.live_wall},
            "source": "score_value",
        })
    for action in actions:
        if not _is_kong(action):
            continue
        try:
            kong = _evaluate_kong_context(context, action, profile, budget)
        except (BudgetExceeded, ContextError, ValueError):
            return _delegated(context, profile, "root_kong_layer_incomplete",
                              legacy_action, budget=budget)
        if kong is None or not math.isfinite(float(kong["value"])):
            return _delegated(context, profile, "root_kong_layer_incomplete",
                              legacy_action, budget=budget)
        candidates.append({
            "action": int(action), "legal": True,
            "value": float(kong["value"]), "Q": float(kong["value"]),
            "kong_evaluation": kong, "source": "score_value",
        })
    action_order = {int(action): index for index, action in
                    enumerate(context.legal_actions)}
    selected = max(candidates, key=lambda row: (
        float(row["value"]),
        -action_order.get(int(row["action"]), 10**6))).get("action")
    return RootEvaluation(
        profile=profile.name, profile_fingerprint=profile.fingerprint,
        scope=profile.scope, context_hash=context.context_hash,
        level="V2-ROOT", selected=selected, candidates=tuple(candidates),
        reason="shape_v2_root_same_unit", missing_fields=(), complete=True,
        nodes=budget.nodes, kernel_calls=budget.kernel_calls,
        elapsed_ms=round(budget.elapsed_ms, 3),
        budget={"node_limit": budget.limit,
                "time_limit_ms": budget.time_ms},
        bound_version=profile.bound_version, bound_mode=profile.bound_mode)


def choose_root_game_action(game, seat, profile=None):
    """Project a game, obtain a legacy fallback, then evaluate the root."""
    profile = profile or ProfileSpec.shape_v2_all_root()
    context = PublicDecisionContext.from_game(game, seat)
    from ..bot import choose_action
    legacy = choose_action(game, seat, evaluator="legacy")
    result = evaluate_root_context(context, profile, legacy_action=legacy)
    return result.selected, result.as_json()


evaluate_root = evaluate_root_context
