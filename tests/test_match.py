from unittest.mock import patch

import pytest

from mj.match import (
    DEFAULT_CONSECUTIVE_DEALS,
    DEFAULT_MATCH_ROUNDS,
    Match,
    MatchRules,
    dealer_after_hand,
)
from mj.scoring import settle


def test_match_defaults_and_dealer_transition():
    assert DEFAULT_MATCH_ROUNDS == 8
    assert DEFAULT_CONSECUTIVE_DEALS == 3
    assert dealer_after_hand(0, 1, 0) == (0, 2)
    assert dealer_after_hand(0, 2, None) == (0, 3)
    assert dealer_after_hand(0, 3, 2) == (2, 1)
    # Three is the default run marker, not a forced rotation cap.
    assert dealer_after_hand(0, 3, 0) == (0, 4)


def test_match_rejects_invalid_rules():
    with pytest.raises(ValueError):
        MatchRules(rounds=0)
    with pytest.raises(ValueError):
        MatchRules(default_consecutive_deals=0)
    with pytest.raises(ValueError):
        Match(dealer=4)


class _FakeGame:
    outcomes = []

    def __init__(self, *, seed, dealer, base, you_cai_bi_kao):
        del seed, you_cai_bi_kao
        self.dealer = dealer
        self.base = base
        self.done = False
        self.result = None
        self.scores = [0] * 4
        self._winner = self.outcomes.pop(0)

    def current_seat(self):
        return 0

    def legal_actions(self):
        return [0]

    def step(self, action):
        assert action == 0
        self.done = True
        if self._winner is not None:
            self.result = (self._winner, 1, ["平胡"])
            self.scores = settle(self._winner, self.dealer, 1, self.base)


def test_match_uses_eight_hands_and_result_based_dealer_succession():
    # 0 wins, draw, 0 wins, then 2 wins and takes the deal, then 2 retains it
    # through a win/draw, and finally 1 takes the last two deals.
    _FakeGame.outcomes = [0, None, 0, 2, 2, None, 1, 1]
    with patch("mj.match.Game", _FakeGame):
        match = Match(seed=7)
        while not match.done:
            assert match.current_seat() == 0
            match.step(0)

    assert len(match.history) == 8
    assert [row["dealer"] for row in match.history] == [
        0, 0, 0, 0, 2, 2, 2, 1,
    ]
    assert [row["dealer_run"] for row in match.history] == [
        1, 2, 3, 4, 1, 2, 3, 1,
    ]
    assert match.history[1]["draw"] is True
    assert match.history[3]["next_dealer"] == 2
    assert match.history[3]["next_dealer_run"] == 1
    assert match.history[-1]["next_dealer"] == 1
    assert sum(match.scores) == 0
    assert match.scores == [23, 9, 2, -34]


def test_match_keeps_existing_x8_settlement():
    _FakeGame.outcomes = [0]
    with patch("mj.match.Game", _FakeGame):
        match = Match(seed=11, rounds=1)
        match.step(0)
    assert match.history[0]["scores"] == [24, -8, -8, -8]
    assert match.scores == [24, -8, -8, -8]
