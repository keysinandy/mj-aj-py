import json

from mj.platform.bot_client import BotClient
from scripts.window_acceptance import summarize
from test_window_recovery import _snapshot


def test_report_distinguishes_recovered_boundary_from_final_miss(tmp_path):
    snap = _snapshot(phase="response_chi", turn=3, responding=[0],
                     discards=[[], [], [], ["6b"]], last_discard="6b")
    mirror = BotClient(None, "b", None)._mirror_from_snapshot(snap)
    legal = sorted(mirror.build_game("response_chi").legal_actions())
    chosen = next(a for a in legal if a != -1)
    records = [
        {"type": "meta", "gid": "g", "name": "b"},
        {"type": "snapshot", "seq": 10, "snap": snap},
        {"type": "decision", "id": 1, "seq": 10,
         "phase": "response_chi", "legal": legal, "action": chosen},
        {"type": "claim_miss", "phase": "response_chi",
         "reason": "decision_boundary_resync", "chosen": chosen},
        {"type": "reset", "reason": "confirm"},
        {"type": "req", "seq": 0, "attempts": 2, "status": 200,
         "res": {"snapshot": True, "seq": 10},
         "transport": {"retry_429": 1}, "latency_ms": 70},
        {"type": "snapshot", "seq": 10, "snap": snap},
        {"type": "decision", "id": 2, "seq": 10,
         "phase": "response_chi", "legal": legal, "action": chosen},
        {"type": "action", "phase": "response_chi", "decision": 2,
         "payload": {"action": "chi"}, "ok": True},
        {"type": "end", "reason": "finished", "scores": [0, 0, 0, 0]},
    ]
    path = tmp_path / "g.jsonl"
    path.write_text("\n".join(json.dumps(row) for row in records))
    report = summarize([path])
    assert report["legacy_boundary_misses_later_succeeded"] == 1
    assert report["transport"]["retry_429"] == 1
    assert report["coverage"]["decisions_checked"] == 2
    assert report["games"][0]["replay_rounds_settled"] == 0
    assert report["games"][0]["replay_illegal"] == 0
    assert report["games"][0]["replay_clean"] is False
    assert "UNKNOWN_LEGACY" in report["state_by_kind"]


def test_report_keeps_physical_429_and_window_request_group(tmp_path):
    records = [
        {"type": "req", "seq": 0, "status": 200, "attempts": 2,
         "request_kind": "WINDOW_PENG", "latency_ms": 600,
         "throttle": {"queue_wait_ms": 42, "urgent": True},
         "transport": {"retry_429": 1, "state_physical_attempts": 2,
                       "state_attempts": [
                           {"started_epoch": 100, "status": 429},
                           {"started_epoch": 100.5, "status": 200}]}},
        {"type": "window_confirm", "outcome": "requested"},
        {"type": "window_confirm", "outcome": "closed"},
        {"type": "claim_miss", "phase": "response_peng",
         "reason": "window_confirm_phase_changed", "chosen": None},
    ]
    path = tmp_path / "g.jsonl"
    path.write_text("\n".join(json.dumps(row) for row in records))
    report = summarize([path])
    assert report["transport"]["physical_attempts"] == 2
    assert report["state_by_kind"]["WINDOW_PENG"]["queue_ms"]["p95"] == 42
    assert report["physical_state_evidence"]["status"] == {"429": 1, "200": 1}
    assert report["physical_state_evidence"]["max_starts_per_rolling_second"] == 2
    assert report["window_confirm_records"] == {"requested": 1, "closed": 1}
    assert report["claim_miss_records"] == {
        "response_peng/window_confirm_phase_changed": 1}
