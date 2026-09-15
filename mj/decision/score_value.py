"""Rule-backed score and root-transition adapters for Fast EV."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..game import HU
from ..scoring import hand_multiplier, settle
from ..win import is_baotou, is_win
from ..tiles import W


# A conservative rule-derived ceiling for a single game.  The live wall starts
# with 63 drawable tiles; a hero action chain cannot contain more state-changing
# kong/piao events than that.  Add the maximum two independent doubling flags
# and the largest seven-pairs multiplier.  This is intentionally loose (and
# therefore rarely prunes) but is never based on observed maxima.
MAX_RULE_CHAIN_EVENTS = 63
MAX_RULE_EXTRA_DOUBLINGS = 9


def theoretical_reward_bound(base=1):
    """Return a conservative absolute hero settlement bound in score units."""
    return float(24 * max(1, int(base)) *
                 (2 ** (MAX_RULE_CHAIN_EVENTS + MAX_RULE_EXTRA_DOUBLINGS)))


@dataclass(frozen=True)
class ScoreBreakdown:
    legal: bool
    reward: Optional[float]
    multiplier: Optional[int]
    parts: tuple = ()
    reason: str = ""
    settlement: Optional[tuple] = None

    def as_json(self):
        return {
            "legal": self.legal, "reward": self.reward,
            "multiplier": self.multiplier, "parts": list(self.parts),
            "reason": self.reason,
            "settlement": list(self.settlement) if self.settlement is not None else None,
        }


class ScoreValue:
    """Single source of truth for HU legality and hero round reward.

    This adapter deliberately calls the existing win/multiplier/settlement
    functions.  It never adds to an imported room score: ``settle`` returns a
    fresh per-round delta vector and the hero component is the reward.
    """

    def __init__(self, dealer=0, base=1, you_cai_bi_kao=False, hero=0):
        self.dealer = int(dealer)
        self.base = int(base)
        self.you_cai_bi_kao = bool(you_cai_bi_kao)
        self.hero = int(hero)

    @staticmethod
    def standing_before_draw(hand, drawn):
        if drawn is None or not 0 <= int(drawn) < 34:
            return None
        hand = list(int(x) for x in hand)
        if len(hand) != 34 or hand[int(drawn)] <= 0:
            return None
        hand[int(drawn)] -= 1
        return tuple(hand)

    def can_hu(self, hand, standing13=None, locked=0, drawn=None,
               kong_draw=False, chain_count=0, chain_piao=0):
        hand = tuple(int(x) for x in hand)
        locked = int(locked)
        if len(hand) != 34 or not 0 <= locked <= 4:
            return False
        if drawn is None:
            return False
        drawn = int(drawn)
        if not 0 <= drawn < 34 or not is_win(hand, locked):
            return False
        standing = (self.standing_before_draw(hand, drawn)
                    if standing13 is None else tuple(int(x) for x in standing13))
        if (standing is None or len(standing) != 34 or
                sum(standing) != 13 - 3 * locked or
                any(standing[t] < 0 for t in range(34)) or
                any(standing[t] + (1 if t == drawn else 0) != hand[t]
                    for t in range(34))):
            return False
        if self.you_cai_bi_kao and hand[W] > 0:
            if not kong_draw and not is_baotou(standing, int(locked)):
                return False
        return True

    def hu(self, hand, standing13=None, locked=0, drawn=None,
           kong_draw=False, chain_count=0, chain_piao=0) -> ScoreBreakdown:
        standing = (self.standing_before_draw(hand, drawn)
                    if standing13 is None else tuple(int(x) for x in standing13))
        if not self.can_hu(hand, standing, locked, drawn, kong_draw,
                           chain_count, chain_piao):
            reason = "not_drawn_or_not_win"
            if (self.you_cai_bi_kao and drawn is not None and
                    len(tuple(hand)) == 34 and tuple(hand)[W] > 0 and
                    not kong_draw):
                reason = "you_cai_bi_kao_gate"
            return ScoreBreakdown(False, None, None, reason=reason)
        mult, parts = hand_multiplier(
            tuple(hand), standing, int(locked), int(chain_count),
            int(chain_piao))
        payment = tuple(settle(self.hero, self.dealer, mult, self.base))
        if sum(payment) != 0:
            raise ValueError("settlement vector is not zero-sum")
        return ScoreBreakdown(
            True, float(payment[self.hero]), int(mult), tuple(parts),
            reason="legal_hu", settlement=payment)

    def hu_from_game(self, game, seat=None):
        seat = self.hero if seat is None else int(seat)
        adapter = self if seat == self.hero else ScoreValue(
            dealer=game.dealer, base=game.base,
            you_cai_bi_kao=game.you_cai_bi_kao, hero=seat)
        drawn = game.drawn[seat]
        standing = adapter.standing_before_draw(game.hands[seat], drawn)
        return adapter.hu(game.hands[seat], standing,
                          len(game.melds[seat]), drawn,
                          bool(getattr(game, "_kong_draw", False)),
                          int(game.chain[seat]), int(game.chain_piao[seat]))

    def discard(self, hand, tile, locked=0, chain_count=0, chain_piao=0):
        """Apply a root discard and return value-only transition fields."""
        hand = list(int(x) for x in hand)
        tile = int(tile)
        if len(hand) != 34 or not 0 <= tile < 34 or hand[tile] <= 0:
            raise ValueError("illegal discard")
        piao = False
        if tile == W:
            after = list(hand)
            after[W] -= 1
            piao = is_baotou(after, int(locked))
        hand[tile] -= 1
        if piao:
            chain_count += 1
            chain_piao += 1
        else:
            chain_count = 0
            chain_piao = 0
        return tuple(hand), int(chain_count), int(chain_piao), bool(piao)


def score_value_for_game(game, seat):
    """Convenience function returning a serialisable HU score breakdown."""
    return ScoreValue(
        dealer=game.dealer, base=game.base,
        you_cai_bi_kao=game.you_cai_bi_kao, hero=seat).hu_from_game(game, seat)


# Explicit aliases keep calibration and fixture code on this adapter rather
# than introducing a second scoring path.
evaluate_hu = ScoreValue.hu
settle_hu = ScoreValue.hu
