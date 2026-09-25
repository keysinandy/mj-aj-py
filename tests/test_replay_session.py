"""统一 ReplaySession/ReplayStep 模型的回放契约。"""

import json

from mj.clientd.replay import local_frames, local_session, online_session


def _local_record():
    return {
        "seed": 7,
        "dealer": 0,
        "base": 1,
        "you_cai_bi_kao": False,
        "seats": [{"strategy": "bot", "evaluator": "shape-v2"}],
        "roles": [0, 1, 2, 3],
        "actions": [],
        "local_requests": {
            "0": [{"kind": "state_request", "type": "GET_STATE"}],
        },
        "diagnostics": {
            "0": [{"code": "fixture", "severity": "info"}],
        },
    }


def _snapshot(seq=10, round_no=1):
    return {
        "type": "snapshot",
        "seq": seq,
        "snap": {
            "seat": 0,
            "dealer": 0,
            "round_no": round_no,
            "phase": "draw",
            "turn": 0,
            "my_hand": [],
            "discards": [[], [], [], []],
            "melds": [[], [], [], []],
        },
    }


def test_local_session_uses_derived_action_sequence_and_preserves_annotations():
    session = local_session(_local_record())

    assert session["metadata"]["source"] == "local"
    assert session["metadata"]["capabilities"]["full_information"] is True
    assert session["metadata"]["strategy"] == "bot"
    assert session["metadata"]["evaluator"] == "shape-v2"
    assert len(session["steps"]) == 1
    step = session["steps"][0]
    assert step["seq_no"] == 0
    assert step["seq_source"] == "local_initial"
    assert step["event"]["type"] == "session_start"
    assert step["local_requests"][0]["type"] == "GET_STATE"
    assert step["diagnostics"][0]["code"] == "fixture"
    json.dumps(session, ensure_ascii=False)

def test_online_side_records_attach_to_existing_step():
    records = [
        {"type": "meta", "base": 1, "you_cai_bi_kao": False,
         "strategy": "policy", "model_name": "best.onnx"},
        _snapshot(10),
        {
            "type": "req", "seq": 10, "status": 200,
            "latency_ms": 2.5, "res": {"seq": 10},
        },
        {
            "type": "claim_miss", "seq": 10, "phase": "response_peng",
            "reason": "timeout", "chosen": None,
        },
    ]
    session = online_session(records, session_id="g1")

    assert session["metadata"]["source"] == "online"
    assert session["metadata"]["strategy"] == "policy"
    assert session["metadata"]["model_name"] == "best.onnx"
    assert len(session["steps"]) == 1
    step = session["steps"][0]
    assert step["seq_no"] == 10
    assert step["local_requests"][0]["type"] == "GET_STATE"
    assert step["diagnostics"][0]["code"] == "claim_miss"
    assert session["metadata"]["capabilities"]["local_requests"] is True
    assert session["metadata"]["capabilities"]["diagnostics"] is True


def test_online_session_rounds_cover_ten_rounds_without_reindexing_steps():
    records = [{"type": "meta"}]
    records.extend(_snapshot(seq=100 + index, round_no=index + 1)
                   for index in range(10))
    session = online_session(records, session_id="ten-round-room")

    rounds = session["metadata"]["rounds"]
    assert len(rounds) == 10
    assert [round_["ordinal"] for round_ in rounds] == list(range(1, 11))
    assert [round_["round_no"] for round_ in rounds] == list(range(1, 11))
    assert [(round_["start_step_index"], round_["end_step_index"])
            for round_ in rounds] == [(index, index) for index in range(10)]
    assert [round_["start_seq_no"] for round_ in rounds] == list(range(100, 110))
    assert [step["step_index"] for step in session["steps"]] == list(range(10))
    assert [step["seq_no"] for step in session["steps"]] == list(range(100, 110))


def test_nonconsecutive_duplicate_round_number_stays_separate():
    round_numbers = [1, 2, 3, 4, 3]
    records = [{"type": "meta"}]
    records.extend(_snapshot(seq=20 + index, round_no=round_no)
                   for index, round_no in enumerate(round_numbers))
    rounds = online_session(records)["metadata"]["rounds"]

    assert [round_["round_no"] for round_ in rounds] == round_numbers
    assert rounds[2]["round_id"] != rounds[4]["round_id"]
    assert rounds[2]["start_step_index"] == 2
    assert rounds[4]["start_step_index"] == 4


def test_local_draw_marker_is_removed_after_discard():
    from mj.game import Game

    seed = 41
    game = Game(seed=seed, dealer=0)
    drawn_tile = game.drawn[game.turn]
    frames = local_frames({**_local_record(), "seed": seed, "actions": [drawn_tile]})

    assert frames[0]["drawn_tile"] == drawn_tile
    assert frames[0]["drawn_seat"] == 0
    assert frames[0]["draw_origin"] == "normal"
    assert frames[1]["drawn_tile"] is None
    assert frames[1]["drawn_seat"] is None


def test_local_winner_frame_uses_engine_result_only_after_done():
    from mj.clientd.replay import _local_frame
    from mj.game import Game

    game = Game(seed=8, dealer=0)
    before = _local_frame(game, 0, game.current_seat(), "初始")
    assert before["winner_seats"] == []
    assert before["round_ended"] is False

    game.result = (2, 1, {})
    game.done = True
    after = _local_frame(game, 0, game.current_seat(), "胡")
    assert after["winner_seats"] == [2]
    assert after["round_ended"] is True
