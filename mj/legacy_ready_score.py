"""Exact public next-draw settlement on a previously verified legal wait set."""
import time

from .scoring import hand_multiplier, settle


def next_draw_score(standing, locked, waits, remaining, *, seat, dealer, base=1,
                    chain=0, chain_piao=0, deadline=None):
    """Score legal waits from the existing progress gate, without a new search.

    Callers own YCBK/freeze legality and provide the complete legal wait set.
    No wall order or other player's concealed material is consulted.
    """
    if len(standing) != 34 or len(remaining) != 34 or sum(standing) != 13-3*locked:
        raise ValueError("invalid ready score material")
    if not 0 <= seat < 4 or not 0 <= dealer < 4:
        raise ValueError("invalid scoring seat")
    reward, winning, calls = 0, 0, 0
    for tile in sorted(set(waits)):
        if deadline is not None and time.perf_counter() >= deadline:
            raise TimeoutError("ready score deadline")
        if not 0 <= tile < 34 or standing[tile] >= 4:
            raise ValueError("invalid ready score wait")
        mass = int(remaining[tile])
        if not 0 <= mass <= 4:
            raise ValueError("invalid public remaining mass")
        if not mass:
            continue
        final = list(standing)
        final[tile] += 1
        multiplier, _ = hand_multiplier(final, standing, locked, chain, chain_piao)
        reward += mass * settle(seat, dealer, multiplier, base)[seat]
        winning += mass
        calls += 1
    total = sum(remaining)
    if deadline is not None and time.perf_counter() >= deadline:
        raise TimeoutError("ready score deadline")
    return {"value": reward/total if total else 0.0, "reward_mass": reward,
            "winning_mass": winning, "total_unseen": total, "score_calls": calls}
