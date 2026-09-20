"""Task 3.2 验收:线上 jsonl → 帧管线(Mirror 复用 + 跳段 + 自家视角)。

合成记录:meta + snapshot + events + decision;断言:
- 决策点重建合法集 == decision 记录合法集;
- 他家暗手不出现在帧(hands=None);
- 跳段(req snapshot 先于 events)产生 gap 标注而非报错。
"""

import json

from mj.clientd.replay import online_frames


def _snap(seq, round_no=1, seat=0, dealer=0, wall=60):
    return {
        "type": "snapshot", "seq": seq, "round_no": round_no,
        "snap": {
            "seat": seat, "dealer": dealer, "round_no": round_no,
            "phase": "discard", "turn": seat, "wall_remaining": wall,
            "my_hand": [], "discards": [[], [], [], []],
            "melds": [[], [], [], []],
        },
    }


def _events(seq_to, events):
    return {"type": "events", "seq_to": seq_to, "events": events}


def _decision(pid, seq, phase, legal, action):
    return {"type": "decision", "id": pid, "seq": seq, "phase": phase,
            "legal": legal, "action": action}


def test_online_frames_own_perspective_no_his_hands():
    recs = [
        {"type": "meta", "you_cai_bi_kao": False, "base": 1},
        _snap(10, round_no=1, seat=2),
        _events(12, []),
    ]
    frames, verify = online_frames(recs)
    assert frames  # 有帧
    for fr in frames:
        assert fr["info_kind"] == "online"
        assert fr["hands"] is None          # 他家暗手不可见
        assert fr["my_hand"] is not None    # 本家手牌可见
        assert fr["my_seat"] == 2
    assert verify == []


def test_decision_legal_verification():
    """忠实快照(真实 Game 状态)→ 决策点重建合法集 == 引擎合法集。"""
    from mj.game import Game
    from mj.platform.proto import tname
    g = Game(seed=3, dealer=0)
    seat = 0
    legal = sorted(g.legal_actions())
    my_hand = [tname(t) for t in range(34)
               for _ in range(g.hands[seat][t])]
    snap = _snap_from_game_hand(g, seat, my_hand)
    recs = [
        {"type": "meta", "you_cai_bi_kao": False, "base": 1},
        snap,
        _decision(1, 1, "draw", legal, legal[0]),
    ]
    frames, verify = online_frames(recs)
    assert len(verify) == 1
    assert verify[0]["recorded"] == legal
    assert verify[0]["rebuilt"] == legal
    assert verify[0]["ok"] is True


def _snap_from_game_hand(g, seat, my_hand):
    from mj.platform.proto import tname
    return {
        "type": "snapshot", "seq": 1, "round_no": 1,
        "snap": {
            "seat": seat, "dealer": g.dealer, "round_no": 1,
            "phase": "draw", "turn": seat,
            "drawn_tile": tname(g.drawn[seat]) if g.drawn[seat] is not None
            else None,
            "wall_remaining": g.live_wall_left(),
            "my_hand": my_hand,
            "discards": [[], [], [], []],
            "melds": [[], [], [], []],
            "hand_counts": [len(my_hand) if s == seat else 13 for s in range(4)],
        },
    }


def test_gap_marker_on_skipped_range():
    # 快照 seq=100 带 wall=60;随后一条 req snapshot 声明 seq 有跳段 → gap
    recs = [
        {"type": "meta", "you_cai_bi_kao": False, "base": 1},
        _snap(100, round_no=1, seat=0),
        # 跳段:req 从 seq 3 直接到 snapshot 视界 120
        {"type": "req", "seq": 3, "res": {"snapshot": True, "seq": 120}},
        _snap(120, round_no=1, seat=0),
        _events(121, []),
    ]
    frames, _v = online_frames(recs)
    gap_frames = [f for f in frames if f.get("gap")]
    assert gap_frames, "跳段后应有 gap 标注帧"
    assert frames[-1]["round_no"] == 1


def test_online_frames_preserve_verified_public_hand_counts():
    from mj.game import Game
    from mj.platform.proto import tname
    g = Game(seed=4, dealer=0)
    seat = 0
    my_hand = [tname(t) for t in range(34)
               for _ in range(g.hands[seat][t])]
    frames, _ = online_frames([_snap_from_game_hand(g, seat, my_hand)])
    assert frames
    counts = frames[0]["hand_counts"]
    assert counts[seat] == len(my_hand)
    assert all(isinstance(value, int) for value in counts)


def test_online_frames_preserve_open_meld_source_seat():
    recs = [
        {"type": "meta", "you_cai_bi_kao": False, "base": 1},
        {
            "type": "snapshot", "seq": 1,
            "snap": {
                "seat": 0, "dealer": 0, "round_no": 1,
                "phase": "response_peng", "turn": 0,
                "wall_remaining": 60, "my_hand": [],
                "discards": [["5w"], [], [], []],
                "melds": [[], [], [], []], "last_discard": "5w",
            },
        },
        {"type": "events", "seq_to": 2, "events": [
            {"type": "peng", "seq": 2, "seat": 1, "tile": "5w"},
        ]},
    ]
    frames, _ = online_frames(recs)
    assert frames[-1]["melds"][1][0]["from_seat"] == 0


def test_loss_recovery_no_crash():
    # events 应用抛 MirrorInconsistent → 置空等快照,不应崩溃
    recs = [
        {"type": "meta", "you_cai_bi_kao": False, "base": 1},
        _events(5, [{"type": "tile_drawn", "seq": 1}]),   # 无快照先行,忽略
        _snap(200, round_no=2, seat=1),
        _events(201, []),
    ]
    frames, _v = online_frames(recs)
    assert any(f["my_seat"] == 1 for f in frames)


def test_frames_serializable():
    recs = [{"type": "meta", "you_cai_bi_kao": False, "base": 1},
            _snap(10, seat=2), _events(12, [])]
    frames, _ = online_frames(recs)
    json.dumps(frames, ensure_ascii=False)  # 可序列化给前端
