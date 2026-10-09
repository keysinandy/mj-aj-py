"""Legacy KONG hard gates and v1 public score evaluation.

This module owns KONG semantics without importing ``mj.bot``.  Callers supply
the small policy callbacks used for progress, piao eligibility, and score
continuation so the compatibility layer can be moved independently.
"""

from __future__ import annotations

import time

from .game import KONG_ADD_BASE, KONG_CLOSED_BASE, KONG_OPEN, PONG
from .hand_eval import enumerate_decompositions
from .scoring import hand_multiplier, settle
from .shanten import shanten, ukeire
from .tiles import W
from .win import is_baotou, is_win


# Leave time inside the configured hard budget for unwinding and returning the
# transactional fallback after a slow native/scoring call.
KONG_CONTINUATION_DEADLINE_RESERVE_MS = 2.0


class _ContinuationBudgetExceeded(RuntimeError):
    """Internal signal used to abort an inner score loop transactionally."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def kong_action_tile(action):
    if KONG_CLOSED_BASE - 33 <= action <= KONG_CLOSED_BASE:
        return "closed", KONG_CLOSED_BASE - action
    if KONG_ADD_BASE - 33 <= action <= KONG_ADD_BASE:
        return "add", KONG_ADD_BASE - action
    return None


def kong_actions(actions):
    return tuple(action for action in actions
                 if kong_action_tile(action) is not None)


def _decomposition_target_usage(decomposition, tile):
    sequence_used = False
    pair_or_taatsu_used = False
    triplet_count = 0
    for kind, tiles, _wild in decomposition.melds:
        if kind == "triplet":
            triplet_count += sum(1 for value in tiles if value == tile)
        elif kind == "sequence" and tile in tiles:
            sequence_used = True
    if decomposition.pair and tile in decomposition.pair[0]:
        pair_or_taatsu_used = True
    for tiles, _wild in decomposition.extra_pairs:
        if tile in tiles:
            pair_or_taatsu_used = True
    for _kind, tiles, _wild in decomposition.taatsu:
        if tile in tiles:
            pair_or_taatsu_used = True
    single = tile in decomposition.singles
    return sequence_used, pair_or_taatsu_used, triplet_count, single


def structure_guard(hand, locked, kind, tile, *, on_decomposition=None):
    """Check target-tile material use in one optimal standard decomposition."""
    if on_decomposition is not None:
        on_decomposition()
    target = shanten(hand, locked)
    decompositions = enumerate_decompositions(hand, locked, optimal=True)
    optimal_standard = [
        decomposition for decomposition in decompositions
        if decomposition.kind == "standard" and decomposition.score == target
    ]
    if not optimal_standard:
        return {
            "structure_safe": False,
            "structure_reason": "no_optimal_standard",
        }

    safe = False
    saw_sequence = False
    saw_pair_or_taatsu = False
    for decomposition in optimal_standard:
        sequence_used, pair_or_taatsu_used, triplet_count, single = \
            _decomposition_target_usage(decomposition, tile)
        saw_sequence = saw_sequence or sequence_used
        saw_pair_or_taatsu = saw_pair_or_taatsu or pair_or_taatsu_used
        if sequence_used or pair_or_taatsu_used:
            continue
        if kind == "closed":
            safe = safe or (triplet_count >= 3 and single)
        elif kind == "add":
            safe = safe or single
        elif kind == "open":
            safe = safe or triplet_count >= 3
        if safe:
            break

    if safe:
        reason = "safe_triplet_plus_single"
    elif saw_sequence:
        reason = "tile_used_by_sequence"
    elif saw_pair_or_taatsu:
        reason = "tile_used_by_pair_or_taatsu"
    else:
        reason = "no_redundant_single"
    return {"structure_safe": safe, "structure_reason": reason}


def shape_gate(baseline, post):
    if post.shanten > baseline.shanten:
        return "shape_shanten_worse"
    if post.shanten == baseline.shanten and post.ukeire_live < baseline.ukeire_live:
        return "shape_ukeire_lower"
    if baseline.baotou_ready and not post.baotou_ready:
        return "shape_baotou_lost"
    return None


def shape_gate_v2(baseline, post):
    """Protect every known progress dimension without coercing unknown to 0."""
    reason = shape_gate(baseline, post)
    if reason is not None:
        return reason
    if (post.shanten == baseline.shanten
            and post.ukeire_types < baseline.ukeire_types):
        return "shape_ukeire_types_lower"
    if (baseline.baotou_ukeire_live is not None
            and post.baotou_ukeire_live is not None
            and post.baotou_ukeire_live < baseline.baotou_ukeire_live):
        return "shape_baotou_ukeire_worse"
    if (baseline.piao_draw_live is not None
            and post.piao_draw_live is not None
            and baseline.piao_draw_live > 0
            and post.piao_draw_live < baseline.piao_draw_live):
        return "shape_piao_worse"
    return None


def progress_not_worse_v2(left, right):
    if left.shanten > right.shanten:
        return False
    if left.shanten == right.shanten:
        if left.ukeire_live < right.ukeire_live:
            return False
        if left.ukeire_types < right.ukeire_types:
            return False
    if right.baotou_ready and not left.baotou_ready:
        return False
    if (left.baotou_ukeire_live is not None
            and right.baotou_ukeire_live is not None
            and left.baotou_ukeire_live < right.baotou_ukeire_live):
        return False
    if (left.piao_draw_live is not None
            and right.piao_draw_live is not None
            and left.piao_draw_live < right.piao_draw_live):
        return False
    return True


def select_pong_kong_same_unit(q_pong, q_kong, *, complete=True):
    """Return the conservative stable action for a same-unit comparison."""
    if not complete or q_pong is None or q_kong is None:
        return PONG, "same_unit_incomplete"
    q_pong = float(q_pong)
    q_kong = float(q_kong)
    delta = q_kong - q_pong
    if q_kong > q_pong + 1e-9:
        return KONG_OPEN, "kong_score_strictly_better"
    if q_kong >= q_pong:
        return PONG, "exact_tie_pong"
    return PONG, "pong_score_not_worse"


def winning_tiles(standing, locked, remaining):
    tiles = []
    mass = 0
    for tile, count in enumerate(remaining):
        if count <= 0:
            continue
        completed = list(standing)
        completed[tile] += 1
        if is_win(completed, locked):
            tiles.append(tile)
            mass += count
    return tuple(tiles), mass


def _winning_reward(game, seat, final, standing, locked, chain,
                    chain_piao, kong_draw, *, diagnostics=None):
    if not is_win(final, locked):
        return None
    if (bool(getattr(game, "you_cai_bi_kao", False))
            and final[W] > 0 and not kong_draw
            and not is_baotou(standing, locked)):
        return None
    if diagnostics is not None:
        diagnostics["score_calls"] += 1
    multiplier, _parts = hand_multiplier(
        final, standing, locked, chain, chain_piao)
    return float(settle(
        seat, getattr(game, "dealer", 0), multiplier,
        getattr(game, "base", 1))[seat])


def _one_draw_reward_by_tile(
        game, seat, standing, locked, chain, chain_piao,
        wait_remaining, *, kong_draw, budget_check=None,
        diagnostics=None):
    """Return exact HU rewards by tile, evaluating only public live waits.

    A follow-up standing with ``shanten != 0`` cannot win on the next draw,
    so scanning all 34 tiles would be pure work.  ``ukeire`` supplies the
    public wait set and keeps the exact score calls on the small winning
    frontier.  ``budget_check`` is called inside the loop so a slow scorer
    cannot overrun the continuation deadline by an entire nested scan. The
    returned score vector is independent of the exact unseen mass; callers
    reweight it for each replacement branch.
    """
    if sum(wait_remaining) <= 0:
        return {}
    if budget_check is not None:
        budget_check()
    if shanten(standing, locked) != 0:
        return {}

    visible = [4 - max(0, int(mass)) for mass in wait_remaining]
    _value, wait_tiles, _live = ukeire(standing, locked, visible)
    rewards = {}
    for tile in wait_tiles:
        if budget_check is not None:
            budget_check()
        # ``wait_tiles`` is restricted to root-visible live tiles. Every
        # follow-up distribution is that root mass minus one replacement
        # tile, so this is a safe superset for every branch being reweighted.
        final = list(standing)
        final[tile] += 1
        rewards[tile] = _winning_reward(
            game, seat, final, standing, locked, chain, chain_piao,
            kong_draw, diagnostics=diagnostics)
    return rewards


def public_score_continuation(
        game, seat, standing, locked, chain, chain_piao, kong_draw,
        remaining, *, first_draw_delay, post_discard_chain,
        node_budget=2048, soft_budget_ms=0.0, hard_budget_ms=15.0):
    """Evaluate two hero draw opportunities from public unseen mass only."""
    started = time.monotonic()
    from .legacy_budget import cap_ms
    hard_budget_ms = cap_ms(hard_budget_ms)
    soft_budget_ms = max(0.0, float(soft_budget_ms))
    hard_budget_ms = max(0.0, float(hard_budget_ms))
    deadline_reserve_ms = min(
        hard_budget_ms, KONG_CONTINUATION_DEADLINE_RESERVE_MS)
    effective_hard_budget_ms = max(
        0.0, hard_budget_ms - deadline_reserve_ms)
    diagnostics = {
        "replacement_types": sum(1 for mass in remaining if mass > 0),
        "non_hu_replacement_types": 0,
        "raw_discard_candidates": 0,
        "tenpai_discard_candidates": 0,
        "unique_standings": 0,
        "score_calls": 0,
        "cache_hits": 0,
        "cache_misses": 0,
        "soft_budget_hit": False,
        "hard_budget_hit": False,
        "hard_budget_ms": hard_budget_ms,
        "deadline_reserve_ms": deadline_reserve_ms,
    }
    standing_cache = {}
    unique_standings = set()
    root_remaining = tuple(int(mass) for mass in remaining)

    def elapsed_ms():
        return (time.monotonic() - started) * 1000.0

    def budget_reason():
        from .legacy_budget import expired
        if expired():
            return "continuation_hard_deadline"
        elapsed = elapsed_ms()
        if soft_budget_ms > 0 and elapsed >= soft_budget_ms:
            diagnostics["soft_budget_hit"] = True
        if node_budget >= 0 and nodes >= node_budget:
            return "continuation_node_budget"
        if elapsed >= effective_hard_budget_ms:
            return "continuation_hard_deadline"
        return None

    def check_budget():
        reason = budget_reason()
        if reason is not None:
            if reason == "continuation_hard_deadline":
                diagnostics["hard_budget_hit"] = True
            raise _ContinuationBudgetExceeded(reason)

    def one_draw_reward_cached(current_standing, current_locked,
                               current_chain, current_chain_piao,
                               current_remaining, *, reward_kong_draw=False):
        check_budget()
        total_mass = sum(current_remaining)
        if total_mass <= 0:
            return 0.0, 0, ()
        key = (
            tuple(current_standing), int(current_locked),
            int(current_chain), int(current_chain_piao),
            bool(reward_kong_draw),
        )
        if key in standing_cache:
            diagnostics["cache_hits"] += 1
            reward_by_tile = standing_cache[key]
        else:
            diagnostics["cache_misses"] += 1
            unique_standings.add((tuple(current_standing), int(current_locked)))
            reward_by_tile = _one_draw_reward_by_tile(
                game, seat, current_standing, current_locked,
                current_chain, current_chain_piao, root_remaining,
                kong_draw=reward_kong_draw, budget_check=check_budget,
                diagnostics=diagnostics)
            standing_cache[key] = reward_by_tile
        reward_sum = 0.0
        winning_mass = 0
        winning_tiles = []
        for tile, reward in reward_by_tile.items():
            mass = current_remaining[tile]
            if mass <= 0 or reward is None:
                continue
            reward_sum += mass * reward
            winning_mass += mass
            winning_tiles.append(tile)
        return (reward_sum / total_mass, winning_mass,
                tuple(winning_tiles))

    live_wall = max(0, int(game.live_wall_left()))
    if live_wall < first_draw_delay:
        return {
            "complete": True,
            "immediate_reward_ev": 0.0,
            "continuation_reward_ev": 0.0,
            "total_reward_ev": 0.0,
            "win_probability": 0.0,
            "winning_mass": 0,
            "winning_tiles": (),
            "continuation_nodes": 0,
            "elapsed_ms": 0.0,
            "fallback_reason": None,
            "wall_left": live_wall,
            **diagnostics,
        }
    total = sum(remaining)
    if total <= 0:
        return {
            "complete": True,
            "immediate_reward_ev": 0.0,
            "continuation_reward_ev": 0.0,
            "total_reward_ev": 0.0,
            "win_probability": 0.0,
            "winning_mass": 0,
            "winning_tiles": (),
            "continuation_nodes": 0,
            "elapsed_ms": 0.0,
            "fallback_reason": None,
            "wall_left": max(0, live_wall - first_draw_delay),
            **diagnostics,
        }

    immediate_sum = 0.0
    continuation_sum = 0.0
    winning_mass = 0
    winning = []
    nodes = 0

    def incomplete(reason):
        if reason == "continuation_hard_deadline":
            diagnostics["hard_budget_hit"] = True
        diagnostics["unique_standings"] = len(unique_standings)
        return {
            "complete": False,
            "immediate_reward_ev": 0.0,
            "continuation_reward_ev": 0.0,
            "total_reward_ev": 0.0,
            "win_probability": 0.0,
            "winning_mass": 0,
            "winning_tiles": (),
            "continuation_nodes": nodes,
            "elapsed_ms": elapsed_ms(),
            "fallback_reason": reason,
            "wall_left": max(0, live_wall - first_draw_delay),
            **diagnostics,
        }

    try:
        # A replacement can win immediately only when the pre-draw standing
        # is already tenpai.  Use the public wait set to avoid 34 full win
        # checks for every replacement type.
        check_budget()
        replacement_waits = set()
        if shanten(standing, locked) == 0:
            visible = [4 - max(0, int(mass)) for mass in remaining]
            _s, waits, _live = ukeire(standing, locked, visible)
            replacement_waits = set(waits)

        for drawn, mass in enumerate(remaining):
            if mass <= 0:
                continue
            check_budget()
            drawn_hand = list(standing)
            drawn_hand[drawn] += 1
            reward = None
            if drawn in replacement_waits:
                reward = _winning_reward(
                    game, seat, drawn_hand, standing, locked, chain,
                    chain_piao, kong_draw, diagnostics=diagnostics)
            if reward is not None:
                immediate_sum += mass * reward
                winning_mass += mass
                winning.append(drawn)
                continue

            diagnostics["non_hu_replacement_types"] += 1
            remaining_after = list(remaining)
            remaining_after[drawn] -= 1
            best_followup = 0.0
            # Only a post-draw discard that reaches tenpai can have a
            # non-zero reward on the next hero draw.  Rank all legal discards
            # by shanten first, then exact-score only the tenpai frontier.
            candidates = []
            for discard, count in enumerate(drawn_hand):
                if count <= 0:
                    continue
                check_budget()
                diagnostics["raw_discard_candidates"] += 1
                next_standing = list(drawn_hand)
                next_standing[discard] -= 1
                next_shanten = shanten(next_standing, locked)
                if next_shanten <= 0:
                    diagnostics["tenpai_discard_candidates"] += 1
                    candidates.append((discard, next_standing))
            for discard, next_standing in candidates:
                check_budget()
                nodes += 1
                next_chain, next_piao = post_discard_chain(
                    game, seat, discard, next_standing, locked)
                followup, _mass, _tiles = one_draw_reward_cached(
                    next_standing, locked, next_chain, next_piao,
                    remaining_after, reward_kong_draw=False)
                best_followup = max(best_followup, followup)
            continuation_sum += mass * best_followup
        # A final score call can itself cross the internal cutoff. Check after
        # the last replacement too, so the bounded path never reports a
        # complete EV after its effective deadline.
        check_budget()
    except _ContinuationBudgetExceeded as exc:
        return incomplete(exc.reason)

    diagnostics["unique_standings"] = len(unique_standings)
    elapsed = elapsed_ms()
    immediate = immediate_sum / total
    continuation = continuation_sum / total
    return {
        "complete": True,
        "immediate_reward_ev": immediate,
        "continuation_reward_ev": continuation,
        "total_reward_ev": immediate + continuation,
        "win_probability": winning_mass / total,
        "winning_mass": winning_mass,
        "winning_tiles": tuple(winning),
        "continuation_nodes": nodes,
        "elapsed_ms": elapsed,
        "fallback_reason": None,
        "wall_left": max(0, live_wall - first_draw_delay),
        **diagnostics,
    }


def kong_kai_gate(game, seat, standing, locked, post, remaining):
    try:
        live_wall = game.live_wall_left()
    except AttributeError:
        live_wall = 0
    if post.shanten != 0:
        return False, (), 0, "post_kong_not_tenpai"
    if live_wall <= 0:
        return False, (), 0, "no_replacement_draw"
    tiles, mass = winning_tiles(standing, locked, remaining)
    if mass <= 0:
        return False, tiles, 0, "no_live_winning_mass"
    return True, tiles, mass, None


def redundant_self_kong_candidate(results, baseline_tile, *, live_wall, hand):
    """Experimental early replacement for a tile the baseline would discard.

    This is a progress hypothesis, not a complete score EV. The caller must
    explicitly enable it. Existing structural and v2 progress checks stay in
    force; tenpai/HU choices continue through the original score comparison.
    """
    if live_wall < 16 or baseline_tile == W:
        return None
    for result in results:
        if (result["kind"] not in {"closed", "add"}
                or result["tile"] != baseline_tile
                or not result["structure_safe"]
                or not result["shape_preserved"]
                or result["rejection_reason"] != "post_kong_not_tenpai"):
            continue
        baseline, post = result["baseline_progress"], result["post_kong_progress"]
        if baseline["shanten"] not in (1, 2) or post["shanten"] != baseline["shanten"]:
            continue
        if (post["ukeire_live"] < baseline["ukeire_live"]
                or post["ukeire_types"] < baseline["ukeire_types"]):
            continue
        # Closed KONG moves a full natural triplet plus the discarded fourth;
        # added KONG moves just that redundant tile into an existing triplet.
        required = 4 if result["kind"] == "closed" else 1
        if hand[baseline_tile] != required:
            continue
        return result
    return None


def post_kong_state(game, seat, kind, tile, visible, *, shape_progress,
                    piao_allowed):
    standing = list(game.hands[seat])
    if kind == "closed":
        standing[tile] -= 4
        locked = len(game.melds[seat]) + 1
    elif kind == "add":
        standing[tile] -= 1
        locked = len(game.melds[seat])
    elif kind == "open":
        standing[tile] -= 3
        locked = len(game.melds[seat]) + 1
    else:
        raise ValueError(f"unknown kong kind: {kind}")
    progress = shape_progress(
        standing, locked, visible,
        include_baotou=False, piao_allowed=piao_allowed,
    )
    return standing, locked, progress


def _base_result(action, kind, tile, baseline, post, structure,
                 shape_reason, kai_passed, wait_tiles, winning_mass,
                 kai_reason):
    gate_passed = (
        structure["structure_safe"] and shape_reason is None and kai_passed)
    rejection_reason = None if gate_passed else (
        structure["structure_reason"]
        if not structure["structure_safe"]
        else shape_reason if shape_reason is not None else kai_reason
    )
    return {
        "action": action,
        "kind": kind,
        "tile": tile,
        "structure_safe": structure["structure_safe"],
        "structure_reason": structure["structure_reason"],
        "shape_preserved": shape_reason is None,
        "shape_rejection_reason": shape_reason,
        "kong_kai_passed": kai_passed,
        "kong_rejection_reason": kai_reason,
        "kong_wait_tiles": list(wait_tiles),
        "kong_winning_mass": winning_mass,
        "kong_win_probability": 0.0,
        "kong_expected_value": 0.0,
        "continuation_nodes": 0,
        "baseline_progress": baseline.as_json(),
        "post_kong_progress": post.as_json(),
        "gate_passed": gate_passed,
        "progress": post,
        "rejection_reason": rejection_reason,
        "reason": "kong_expected_value" if gate_passed else rejection_reason,
    }


def _continuation_diagnostics(evaluation):
    """Expose bounded-search counters without changing score semantics."""
    return {
        key: evaluation.get(key)
        for key in (
            "replacement_types", "non_hu_replacement_types",
            "raw_discard_candidates", "tenpai_discard_candidates",
            "unique_standings", "score_calls", "cache_hits", "cache_misses",
            "soft_budget_hit", "hard_budget_hit",
            "hard_budget_ms", "deadline_reserve_ms",
        )
    }


def evaluate_kong_open(game, seat, tile, baseline, visible, *,
                       structure_guard_fn, post_kong_state_fn,
                       expected_next_draw_reward, seat_value,
                       shape_gate_fn=shape_gate,
                       score_continuation_fn=None,
                       post_discard_chain=None,
                       continuation_node_budget=0,
                       continuation_soft_budget_ms=0.0,
                       continuation_hard_budget_ms=0.0):
    structure = structure_guard_fn(
        game.hands[seat], len(game.melds[seat]), "open", tile)
    standing, locked, post = post_kong_state_fn(
        game, seat, "open", tile, visible)
    shape_reason = shape_gate_fn(baseline, post)
    remaining = [max(0, 4 - count) for count in visible]
    kai_passed, wait_tiles, winning_mass, kai_reason = kong_kai_gate(
        game, seat, standing, locked, post, remaining)
    result = _base_result(
        KONG_OPEN, "open", tile, baseline, post, structure, shape_reason,
        kai_passed, wait_tiles, winning_mass, kai_reason)
    if not result["gate_passed"]:
        return result
    if score_continuation_fn is None:
        evaluation = expected_next_draw_reward(
            game, seat, standing, locked,
            seat_value(game.chain, seat) + 1,
            seat_value(game.chain_piao, seat),
            True, remaining, draw_delay=1,
        )
        evaluation = {
            **evaluation,
            "complete": True,
            "immediate_reward_ev": evaluation["value"],
            "continuation_reward_ev": 0.0,
            "total_reward_ev": evaluation["value"],
            "continuation_nodes": 0,
            "fallback_reason": None,
        }
    else:
        evaluation = score_continuation_fn(
            game, seat, standing, locked,
            seat_value(game.chain, seat) + 1,
            seat_value(game.chain_piao, seat),
            True, remaining, first_draw_delay=1,
            post_discard_chain=post_discard_chain,
            node_budget=continuation_node_budget,
            soft_budget_ms=continuation_soft_budget_ms,
            hard_budget_ms=continuation_hard_budget_ms,
        )
    result.update({
        "kong_win_probability": evaluation["win_probability"],
        "kong_expected_value": evaluation["total_reward_ev"],
        "value": evaluation["total_reward_ev"],
        "win_probability": evaluation["win_probability"],
        "wall_left": evaluation["wall_left"],
        "immediate_reward_ev": evaluation["immediate_reward_ev"],
        "continuation_reward_ev": evaluation["continuation_reward_ev"],
        "total_reward_ev": evaluation["total_reward_ev"],
        "continuation_nodes": evaluation["continuation_nodes"],
        "continuation_elapsed_ms": evaluation.get("elapsed_ms"),
        "continuation_complete": evaluation["complete"],
        "continuation_fallback_reason": evaluation["fallback_reason"],
        **_continuation_diagnostics(evaluation),
    })
    return result


def evaluate_self_kong(game, seat, action, baseline, visible, *,
                       structure_guard_fn, post_kong_state_fn,
                       evaluate_kong_next_draw_fn, shape_gate_fn=shape_gate):
    decoded = kong_action_tile(action)
    if decoded is None:
        return None
    kind, tile = decoded
    structure = structure_guard_fn(
        game.hands[seat], len(game.melds[seat]), kind, tile)
    _standing, _locked, post = post_kong_state_fn(
        game, seat, kind, tile, visible)
    shape_reason = shape_gate_fn(baseline, post)
    remaining = [max(0, 4 - count) for count in visible]
    kai_passed, wait_tiles, winning_mass, kai_reason = kong_kai_gate(
        game, seat, _standing, _locked, post, remaining)
    result = _base_result(
        action, kind, tile, baseline, post, structure, shape_reason,
        kai_passed, wait_tiles, winning_mass, kai_reason)
    if not result["gate_passed"]:
        return result
    evaluation = evaluate_kong_next_draw_fn(
        game, seat, action, remaining)
    result.update({
        "kong_win_probability": evaluation["win_probability"],
        "kong_expected_value": evaluation["value"],
        "value": evaluation["value"],
        "win_probability": evaluation["win_probability"],
        "wall_left": evaluation["wall_left"],
        "immediate_reward_ev": evaluation.get(
            "immediate_reward_ev", evaluation["value"]),
        "continuation_reward_ev": evaluation.get(
            "continuation_reward_ev", 0.0),
        "total_reward_ev": evaluation.get(
            "total_reward_ev", evaluation["value"]),
        "continuation_nodes": evaluation.get("continuation_nodes", 0),
        "continuation_elapsed_ms": evaluation.get("elapsed_ms"),
        "continuation_complete": evaluation.get("complete", True),
        "continuation_fallback_reason": evaluation.get("fallback_reason"),
        **_continuation_diagnostics(evaluation),
    })
    return result


def public_result(result):
    public = dict(result)
    public.pop("progress", None)
    return public


def evaluate_kong_next_draw(game, seat, action, remaining, *,
                            expected_next_draw_reward, seat_value,
                            score_continuation_fn=None,
                            post_discard_chain=None,
                            continuation_node_budget=0,
                            continuation_soft_budget_ms=0.0,
                            continuation_hard_budget_ms=0.0):
    decoded = kong_action_tile(action)
    if decoded is None:
        return None
    kind, tile = decoded
    standing = list(game.hands[seat])
    remove = 4 if kind == "closed" else 1
    if standing[tile] < remove:
        return None
    standing[tile] -= remove
    locked = len(game.melds[seat]) + (1 if kind == "closed" else 0)
    if score_continuation_fn is None:
        result = expected_next_draw_reward(
            game, seat, standing, locked,
            seat_value(game.chain, seat) + 1,
            seat_value(game.chain_piao, seat),
            True, remaining, draw_delay=1,
        )
        result = {
            **result,
            "complete": True,
            "immediate_reward_ev": result["value"],
            "continuation_reward_ev": 0.0,
            "total_reward_ev": result["value"],
            "continuation_nodes": 0,
            "fallback_reason": None,
        }
    else:
        result = score_continuation_fn(
            game, seat, standing, locked,
            seat_value(game.chain, seat) + 1,
            seat_value(game.chain_piao, seat),
            True, remaining, first_draw_delay=1,
            post_discard_chain=post_discard_chain,
            node_budget=continuation_node_budget,
            soft_budget_ms=continuation_soft_budget_ms,
            hard_budget_ms=continuation_hard_budget_ms,
        )
        result["value"] = result["total_reward_ev"]
    result.update({"action": action, "kind": kind, "tile": tile})
    return result


__all__ = [
    "evaluate_kong_next_draw",
    "evaluate_kong_open",
    "evaluate_self_kong",
    "kong_action_tile",
    "kong_actions",
    "kong_kai_gate",
    "post_kong_state",
    "public_score_continuation",
    "public_result",
    "progress_not_worse_v2",
    "shape_gate",
    "shape_gate_v2",
    "select_pong_kong_same_unit",
    "structure_guard",
    "winning_tiles",
]
