"""3.14:摸后弃牌听口只使用公开信息。"""

from copy import deepcopy

import pytest

from mj.clientd.replay import _local_frame, _online_frame
from mj.clientd.wait_hints import PublicMaterialError, analyze_discard_hints
from mj.platform.mirror import Mirror
from mj.win import is_baotou


class _HintGame:
    def __init__(self, hand, *, drawn, legal=None, discards=None,
                 melds=None, hidden=None, ycbk=False, phase="discard",
                 done=False):
        self.hands = [list(hand)] + [list(hidden or [0] * 34) for _ in range(3)]
        self.drawn = [drawn, None, None, None]
        self.turn = 0
        self.phase = phase
        self.done = done
        self.you_cai_bi_kao = ycbk
        self.discards = discards or [[], [], [], []]
        self.melds = melds or [[], [], [], []]
        self._legal = list(legal if legal is not None else [drawn])
        self.scores = [0, 0, 0, 0]
        self.dealer = 0
        self.pending = None

    def legal_actions(self):
        return list(self._legal)

    def current_seat(self):
        return self.turn

    def live_wall_left(self):
        return 40


def _hand(*pairs):
    out = [0] * 34
    for tile, count in pairs:
        out[tile] = count
    return out


def test_wait_and_public_unseen_count_with_dead_wait():
    # 111 222 333 44 55 + 摸 6,弃 6 -> 听 4;手中已有两张 4,
    # 牌河再有两张 4,仍须保留 0 张的死听提示。
    game = _HintGame(
        _hand((0, 3), (1, 3), (2, 3), (3, 2), (4, 2), (5, 1)),
        drawn=5,
        discards=[[3, 3], [], [], []],
    )
    hints = analyze_discard_hints(game, 0)
    assert [hint["discard_tile"] for hint in hints] == [5]
    wait_by_tile = {item["tile"]: item["unseen"]
                    for item in hints[0]["legal_waits"]}
    assert wait_by_tile[3] == 0
    assert hints[0]["total_legal_unseen"] >= 0

    frame = _local_frame(game, 0, 0, "摸", seq_no=10)
    assert frame["discard_hints"] == hints


def test_meld_and_kong_count_once_without_opponent_hand_identity():
    # 弃 8 后听 4/7。4 在本家手里两张且另有一组吃牌中的一张;
    # 7 被一组杠占满。吃牌已从牌河移除,不重复扣除。
    game = _HintGame(
        _hand((0, 3), (1, 3), (2, 3), (3, 2), (4, 1), (5, 1), (7, 1)),
        drawn=7,
        melds=[[], [("kong_open", 6)], [("chow", 2)], []],
        hidden=_hand((33, 13)),
    )
    hints = analyze_discard_hints(game, 0)
    wait_by_tile = {item["tile"]: item["unseen"]
                    for item in hints[0]["legal_waits"]}
    assert wait_by_tile[3] == 1  # 手牌 2 + 吃牌中的 1
    assert wait_by_tile[6] == 0  # 杠按 4 张计

    changed = deepcopy(game)
    changed.hands[1] = _hand((0, 4), (1, 4), (2, 4), (33, 1))
    assert analyze_discard_hints(changed, 0) == hints


def test_freeze_uses_engine_legal_discard_set():
    # 抓打圈只允许刚摸的 6,即便其它牌也能形成听口也不能生成建议。
    game = _HintGame(
        _hand((0, 3), (1, 3), (2, 3), (3, 2), (4, 2), (5, 1)),
        drawn=5,
        legal=[5],
    )
    assert [hint["discard_tile"] for hint in analyze_discard_hints(game, 0)] == [5]


def test_ycbk_keeps_structural_wait_but_blocks_ordinary_hu():
    game = _HintGame(
        _hand((0, 3), (1, 3), (2, 3), (3, 2), (4, 1), (33, 1), (7, 1)),
        drawn=7,
        ycbk=True,
    )
    assert not is_baotou(game.hands[0][:], 0)
    hint = analyze_discard_hints(game, 0)[0]
    assert hint["status"] == "rule_blocked_tenpai"
    assert hint["structural_waits"]
    assert hint["legal_waits"] == []


def test_non_draw_or_settled_state_has_no_hint():
    game = _HintGame(
        _hand((0, 3), (1, 3), (2, 3), (3, 2), (4, 2), (5, 1)),
        drawn=5,
        phase="react",
    )
    assert analyze_discard_hints(game, 0) == []
    game.phase = "discard"
    game.done = True
    assert analyze_discard_hints(game, 0) == []


def test_gap_frame_clears_online_hint_even_with_drawn_projection():
    mirror = Mirror(my_seat=0, dealer=0, base=1, you_cai_bi_kao=False)
    mirror.my_hand = _hand((0, 3), (1, 3), (2, 3), (3, 2), (4, 2), (5, 1))
    mirror.drawn = 5
    frame = _online_frame(
        mirror, [0, 0, 0, 0], 0, "摸", gap=True, seq_no=20,
    )
    assert frame["discard_hints"] == []
    assert frame["hands"] is None

    recovered = _online_frame(
        mirror, [0, 0, 0, 0], 1, "快照", gap=True, seq_no=21,
        event={"type": "snapshot", "seq": 21},
    )
    assert recovered["discard_hints"]


def test_invalid_public_material_hides_analysis_instead_of_clamping():
    game = _HintGame(
        _hand((0, 3), (1, 3), (2, 3), (3, 2), (4, 2), (5, 1)),
        drawn=5,
        discards=[[3, 3, 3], [], [], []],
    )
    with pytest.raises(PublicMaterialError, match="超过四张"):
        analyze_discard_hints(game, 0)
