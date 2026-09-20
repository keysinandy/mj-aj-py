"""统一 ReplaySession/ReplayStep 模型的回放契约。"""

import json

from mj.clientd.replay import local_session, online_session


def _local_record():
    return {
        "seed": 7,
        "dealer": 0,
        "base": 1,
        "you_cai_bi_kao": False,
        "actions": [],
        "local_requests": {
            "0": [{"kind": "state_request", "type": "GET_STATE"}],
        },
        "diagnostics": {
            "0": [{"code": "fixture", "severity": "info"}],
        },
    }


def _snapshot(seq=10):
    return {
        "type": "snapshot",
        "seq": seq,
        "snap": {
            "seat": 0,
            "dealer": 0,
            "round_no": 1,
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
        {"type": "meta", "base": 1, "you_cai_bi_kao": False},
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
    assert len(session["steps"]) == 1
    step = session["steps"][0]
    assert step["seq_no"] == 10
    assert step["local_requests"][0]["type"] == "GET_STATE"
    assert step["diagnostics"][0]["code"] == "claim_miss"
    assert session["metadata"]["capabilities"]["local_requests"] is True
    assert session["metadata"]["capabilities"]["diagnostics"] is True
