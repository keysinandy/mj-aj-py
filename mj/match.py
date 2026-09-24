"""Match-level dealer succession for the local rule engine.

``Game`` remains the single-hand rules engine and keeps its existing scoring
contract (including the dealer ``x8`` settlement).  ``Match`` is the small
stateful layer above it: one match contains eight hands by default, a dealer
win or draw retains the dealer, and the first winner who is not the dealer
becomes the dealer for the next hand.

The default three-deal rule is represented by ``default_consecutive_deals``
and the observable ``dealer_run`` counter.  It is a default run marker, not a
hard cap: dealer wins and draws may continue the same run beyond three.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any, Callable, Sequence

from .game import Game

DEFAULT_MATCH_ROUNDS = 8
DEFAULT_CONSECUTIVE_DEALS = 3
SEAT_COUNT = 4


def _seat(value: Any, name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer seat")
    try:
        value = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be an integer seat") from exc
    if not 0 <= value < SEAT_COUNT:
        raise ValueError(f"{name} must be between 0 and {SEAT_COUNT - 1}")
    return value


def dealer_after_hand(dealer: int, dealer_run: int,
                      winner: int | None) -> tuple[int, int]:
    """Return the dealer and run number for the next hand.

    ``winner is None`` is a draw.  A dealer win and a draw both retain the
    dealer and extend the current run.  A non-dealer winner starts a new run
    at one; the caller may use ``DEFAULT_CONSECUTIVE_DEALS`` as the default
    three-deal reporting threshold without imposing a forced rotation.
    """
    dealer = _seat(dealer, "dealer")
    if isinstance(dealer_run, bool) or int(dealer_run) < 1:
        raise ValueError("dealer_run must be a positive integer")
    if winner is None or _seat(winner, "winner") == dealer:
        return dealer, int(dealer_run) + 1
    return _seat(winner, "winner"), 1


@dataclass(frozen=True)
class MatchRules:
    """Fixed match-level rules; hand scoring remains in :mod:`mj.scoring`."""

    rounds: int = DEFAULT_MATCH_ROUNDS
    default_consecutive_deals: int = DEFAULT_CONSECUTIVE_DEALS

    def __post_init__(self) -> None:
        if isinstance(self.rounds, bool) or int(self.rounds) < 1:
            raise ValueError("rounds must be a positive integer")
        if (isinstance(self.default_consecutive_deals, bool)
                or int(self.default_consecutive_deals) < 1):
            raise ValueError(
                "default_consecutive_deals must be a positive integer")
        object.__setattr__(self, "rounds", int(self.rounds))
        object.__setattr__(self, "default_consecutive_deals",
                           int(self.default_consecutive_deals))


class Match:
    """Run a fixed-length sequence of :class:`~mj.game.Game` hands.

    The public ``scores`` field is the cumulative match score.  Each child
    ``Game`` still exposes its own per-hand ``scores`` and uses the existing
    ``settle`` implementation unchanged.
    """

    def __init__(self, seed=None, dealer=0, base=1, you_cai_bi_kao=False,
                 rounds=DEFAULT_MATCH_ROUNDS,
                 default_consecutive_deals=DEFAULT_CONSECUTIVE_DEALS):
        self.rules = MatchRules(
            rounds=rounds,
            default_consecutive_deals=default_consecutive_deals,
        )
        self.seed = seed
        self.base = base
        self.you_cai_bi_kao = bool(you_cai_bi_kao)
        self.dealer = _seat(dealer, "dealer")
        self.dealer_run = 1
        self.round_no = 0
        self.scores = [0] * SEAT_COUNT
        self.history: list[dict[str, Any]] = []
        self.last_hand: dict[str, Any] | None = None
        self.done = False
        self.game: Game | None = None
        self._rng = random.Random(seed)
        self._start_hand()

    @property
    def current_game(self) -> Game | None:
        """The active hand, or the final hand after the match is done."""
        return self.game

    def current_seat(self) -> int:
        if self.done or self.game is None:
            raise RuntimeError("match is complete")
        return int(self.game.current_seat())

    def legal_actions(self):
        if self.done or self.game is None:
            return []
        return self.game.legal_actions()

    def _start_hand(self) -> None:
        if self.round_no >= self.rules.rounds:
            self.done = True
            return
        self.round_no += 1
        # Derive independent deterministic hand seeds from the match seed;
        # dealer succession is stateful while tile generation remains replayable.
        hand_seed = self._rng.randrange(2 ** 31)
        self.game = Game(
            seed=hand_seed,
            dealer=self.dealer,
            base=self.base,
            you_cai_bi_kao=self.you_cai_bi_kao,
        )

    def _finish_hand(self) -> dict[str, Any]:
        game = self.game
        if game is None or not game.done:
            raise RuntimeError("cannot finish an active hand")
        hand_scores = [int(value) for value in game.scores]
        for seat, value in enumerate(hand_scores):
            self.scores[seat] += value
        winner = int(game.result[0]) if game.result is not None else None
        hand_dealer = int(game.dealer)
        hand_run = int(self.dealer_run)
        next_dealer, next_run = dealer_after_hand(
            hand_dealer, hand_run, winner)
        result = {
            "round_no": self.round_no,
            "dealer": hand_dealer,
            "dealer_run": hand_run,
            "winner": winner,
            "draw": winner is None,
            "mult": (int(game.result[1]) if game.result is not None else None),
            "parts": (list(game.result[2])
                      if game.result is not None else []),
            "scores": hand_scores,
            "cumulative_scores": list(self.scores),
            "next_dealer": next_dealer,
            "next_dealer_run": next_run,
        }
        self.history.append(result)
        self.last_hand = result
        self.dealer = next_dealer
        self.dealer_run = next_run
        if self.round_no >= self.rules.rounds:
            self.done = True
        else:
            self._start_hand()
        return result

    def step(self, action: int) -> dict[str, Any] | None:
        """Advance the active hand and return a result at hand boundaries."""
        if self.done or self.game is None:
            raise RuntimeError("match is complete")
        self.game.step(action)
        if not self.game.done:
            return None
        return self._finish_hand()

    def play(self, players: Sequence[Callable[[Game, int], int]]) -> "Match":
        """Drive all eight hands with one callable per physical seat."""
        if len(players) != SEAT_COUNT:
            raise ValueError("players must contain exactly four callables")
        while not self.done:
            seat = self.current_seat()
            action = players[seat](self.game, seat)
            if action not in self.legal_actions():
                raise ValueError(f"illegal action {action} for seat {seat}")
            self.step(action)
        return self


__all__ = [
    "DEFAULT_MATCH_ROUNDS",
    "DEFAULT_CONSECUTIVE_DEALS",
    "MatchRules",
    "Match",
    "dealer_after_hand",
]
