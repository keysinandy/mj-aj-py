import random

import pytest

from mj.shanten import shanten, shanten_py, ukeire, ukeire_py
from mj.tiles import W, counts
from mj.win import is_win, is_chiitoi
from mj.hand_eval import enumerate_decompositions


@pytest.mark.parametrize("text", ["1122m3344p556677s", "1111m22p44p66s88sEE",
                                "11m33p55sEE SS FF ww"])
def test_complete_natural_or_wildcard_pairs_are_terminal(text):
    hand = counts(text)
    assert sum(hand) == 14
    assert is_win(hand) and is_chiitoi(hand)[0]
    assert shanten_py(hand) == shanten(hand) == -1
    assert any(row.kind == "chiitoi" and row.score == -1
               for row in enumerate_decompositions(hand))


def test_ready_pairs_include_natural_win_and_match_python():
    hand = counts("1122m3344p5566s7s")
    expected = [t for t in range(34) if hand[t] < 4 and
                is_win([n+int(i == t) for i, n in enumerate(hand)])]
    assert shanten(hand) == 0
    assert ukeire(hand)[1] == expected
    assert ukeire(hand) == ukeire_py(hand)
    assert expected == [24, W]


def test_sampled_chiitoi_completion_and_waits_agree_with_rule_engine():
    rng = random.Random(20261008)
    for _ in range(96):
        hand = [0]*34
        for _ in range(7):
            tile = rng.choice([t for t in range(33) if hand[t] <= 2])
            hand[tile] += 2
        for _ in range(rng.randrange(5)):
            tile = rng.choice([t for t in range(33) if hand[t]])
            hand[tile] -= 1
            hand[W] += 1
        assert is_chiitoi(hand)[0] and is_win(hand)
        assert shanten_py(hand) == shanten(hand) == -1
        removed = rng.choice([t for t in range(34) if hand[t]])
        hand[removed] -= 1
        expected = [t for t in range(34) if hand[t] < 4 and
                    is_win([n+int(i == t) for i, n in enumerate(hand)])]
        assert shanten_py(hand) == shanten(hand) == 0
        assert ukeire(hand)[1] == expected
        assert ukeire(hand) == ukeire_py(hand)
