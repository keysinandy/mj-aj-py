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
    assert report["state_by_kind"]["WINDOW_PENG"]["queue_ms"]["p95"] is None
    assert report["state_by_kind"]["WINDOW_PENG"]["queue_ms_unknown"] == 1
    assert report["physical_state_evidence"]["status"] == {"429": 1, "200": 1}
    assert report["physical_state_evidence"]["max_starts_per_rolling_second"] == 2
    assert report["window_confirm_records"] == {"requested": 1, "closed": 1}
    assert report["games"][0]["window_confirm_seq0"] == 1
    assert report["games"][0]["window_confirm_physical_attempts"] == 2
    assert report["claim_miss_records"] == {
        "response_peng/window_confirm_phase_changed": 1}


def test_partial_settlement_does_not_upgrade_multi_round_game(tmp_path):
    records = [
        {"type": "meta", "gid": "g", "tid": "room"},
        {"type": "snapshot", "seq": 0, "snap": _snapshot(round_no=1)},
        {"type": "events", "seq_to": 1,
         "events": [{"seq": 1, "type": "round_ended", "seat": None,
                      "tile": None, "data": {}}]},
        {"type": "snapshot", "seq": 2, "snap": _snapshot(round_no=2)},
        {"type": "end", "reason": "finished", "game_status": "complete",
         "demand": {"reason_mask": 0, "in_flight": False, "reasons": {}}},
    ]
    path = tmp_path / "partial-settlement.jsonl"
    path.write_text("\n".join(json.dumps(row) for row in records))

    report = summarize([path])

    assert report["games"][0]["status"]["transport_status"] == "complete"
    assert report["games"][0]["status"]["window_status"] == "complete"
    assert report["games"][0]["status"]["game_status"] == "partial"


def test_report_sums_per_attempt_queue_and_separates_backoff(tmp_path):
    records = [{
        "type": "req", "seq": 0, "status": 200, "attempts": 2,
        "latency_ms": 40,
        "transport": {
            "state_physical_attempts": 2, "backoff_ms": 5,
            "state_attempts": [
                {"timing": {"total_ms": 2},
                 "throttle": {"queue_wait_ms": 10}},
                {"timing": {"total_ms": 3},
                 "throttle": {"queue_wait_ms": 20}},
            ],
        },
    }]
    path = tmp_path / "timing.jsonl"
    path.write_text(json.dumps(records[0]))

    report = summarize([path])
    group = report["state_all"]

    assert group["queue_ms"]["p95"] == 30
    assert group["http_ms"]["p95"] == 5
    assert group["backoff_ms"]["p95"] == 5
    assert group["queue_source"] == {"physical_attempts": 1}


def test_report_uses_layered_status_and_demand_denominators(tmp_path):
    records = [
        {"type": "req", "seq": 4, "status": 200, "attempts": 1,
         "transport": {"state_physical_attempts": 2},
         "demand": {"logical_demands": 4, "coalesced_demands": 2,
                     "physical_state_requests": 1,
                     "suppressed_duplicates": 1}},
        {"type": "end", "reason": "finished",
         "demand": {"logical_demands": 4, "coalesced_demands": 2,
                     "physical_state_requests": 1,
                     "suppressed_duplicates": 1}},
    ]
    path = tmp_path / "layered.jsonl"
    path.write_text("\n".join(json.dumps(row) for row in records))
    report = summarize([path])
    assert report["games"][0]["status"] == {
        "transport_status": "complete",
        "window_status": "complete",
        "game_status": "protocol_skipped",
    }
    assert report["layer_metrics"]["transport"]["denominator"] == 1
    assert report["layer_metrics"]["window"]["denominator"] == 1
    assert report["layer_metrics"]["game"]["denominator"] == 0
    assert report["layer_metrics"]["transport"]["demand"][
        "coalescing_ratio"] == 0.75


def test_error_end_does_not_make_all_layers_complete(tmp_path):
    records = [{"type": "end", "reason": "error",
                "error": "worker crashed"}]
    path = tmp_path / "error.jsonl"
    path.write_text(json.dumps(records[0]))

    report = summarize([path])

    assert report["games"][0]["status"] == {
        "transport_status": "partial",
        "window_status": "partial",
        "game_status": "partial",
    }


def test_dirty_demand_downgrades_transport_status(tmp_path):
    records = [
        {"type": "req", "seq": 4, "status": 200,
         "demand": {"reason_mask": 1, "in_flight": True,
                     "reasons": {"SSE_DELTA": {"status": "PENDING"}}}},
        {"type": "end", "reason": "inaccessible",
         "demand": {"reason_mask": 1, "in_flight": False,
                     "reasons": {"SSE_DELTA": {"status": "PENDING"}}}},
    ]
    path = tmp_path / "dirty-demand.jsonl"
    path.write_text("\n".join(json.dumps(row) for row in records))

    report = summarize([path])

    assert report["games"][0]["demand_terminal_status"] == "dirty"
    assert report["games"][0]["status"]["transport_status"] == "partial"
    assert report["games"][0]["status"]["window_status"] == "partial"


def test_report_recovers_demand_from_req_when_end_snapshot_is_missing(tmp_path):
    records = [
        {"type": "req", "seq": 4, "status": 200, "attempts": 1,
         "demand": {"logical_demands": 4, "coalesced_demands": 2,
                     "successor_requests": 1,
                     "physical_state_requests": 2,
                     "suppressed_duplicates": 1}},
        {"type": "req", "seq": 5, "status": 200, "attempts": 1,
         "demand": {"logical_demands": 5, "coalesced_demands": 2,
                     "successor_requests": 1,
                     "physical_state_requests": 3,
                     "suppressed_duplicates": 1}},
        {"type": "end", "reason": "inaccessible"},
    ]
    path = tmp_path / "legacy-demand.jsonl"
    path.write_text("\n".join(json.dumps(row) for row in records))

    report = summarize([path])

    game = report["games"][0]
    assert game["demand_source"] == "req_fallback"
    assert game["demand"]["logical_demands"] == 5
    assert game["demand"]["physical_state_requests"] == 3
    assert report["demand_source_counts"] == {"req_fallback": 1}


def test_report_preserves_recorder_end_fallback_provenance(tmp_path):
    demand = {"logical_demands": 3, "physical_state_requests": 2}
    records = [
        {"type": "req", "seq": 4, "status": 200, "attempts": 1,
         "demand": demand},
        {"type": "end", "reason": "inaccessible", "demand": demand,
         "demand_source": "req_fallback"},
    ]
    path = tmp_path / "fallback-end.jsonl"
    path.write_text("\n".join(json.dumps(row) for row in records))

    report = summarize([path])

    assert report["games"][0]["demand_source"] == "req_fallback"
    assert report["demand_source_counts"] == {"req_fallback": 1}


def test_eligible_windows_are_authoritative_and_phase_independent(tmp_path):
    window_id = {
        "game_id": "g", "round_id": 1, "discard_owner": 2,
        "source_discard_seq": 44, "tile": 5,
        "identity_status": "authoritative",
    }
    legacy_id = dict(window_id, source_discard_seq=None,
                     identity_status="legacy_unresolved")
    records = [
        {"type": "window_confirm", "outcome": "requested",
         "phase": "response_peng", "window_id": window_id,
         "legal": [1]},
        {"type": "window_confirm", "outcome": "open",
         "phase": "response_chi", "window_id": window_id,
         "legal": [2], "logical_request_id": "different"},
        {"type": "window_confirm", "outcome": "requested",
         "phase": "response_peng", "window_id": legacy_id,
         "legal": [1]},
    ]
    path = tmp_path / "eligible.jsonl"
    path.write_text("\n".join(json.dumps(row) for row in records))

    report = summarize([path])

    assert report["games"][0]["eligible_windows"] == 1


def test_report_classifies_gaps_and_marks_decision_impact(tmp_path):
    records = [
        {"type": "req", "seq": 10, "requested_seq": 0,
         "request_kind": "RESYNC", "res": {"gap": True,
                                              "snapshot": True,
                                              "seq": 12}},
        {"type": "snapshot", "seq": 12, "snap": _snapshot()},
        {"type": "req", "seq": 12, "requested_seq": 12,
         "request_kind": "SSE_DELTA", "res": {"gap": True,
                                                 "snapshot": False,
                                                 "seq": 15}},
        {"type": "events", "seq_to": 15,
         "events": [{"seq": 15, "type": "tile_drawn"}]},
        {"type": "decision", "id": 1, "seq": 15,
         "phase": "draw", "legal": [-1], "action": -1},
        {"type": "end", "reason": "finished",
         "demand": {"reason_mask": 0, "in_flight": False, "reasons": {}}},
    ]
    path = tmp_path / "gaps.jsonl"
    path.write_text("\n".join(json.dumps(row) for row in records))

    report = summarize([path])

    assert report["gap_classification"]["by_reason"] == {
        "snapshot_reanchor": 1,
        "event_discontinuity": 1,
    }
    assert report["gap_classification"]["decision_impact"] == {
        "false": 1, "true": 1}
    assert report["gap_classification"]["strong_risk"] == 1
    assert report["games"][0]["gap_decision_impact"] == 1


def test_gap_without_recovery_keeps_decision_impact_unknown(tmp_path):
    records = [
        {"type": "req", "seq": 12, "requested_seq": 12,
         "res": {"gap": True, "snapshot": False, "seq": 15}},
        {"type": "events", "seq_to": 15,
         "events": [{"seq": 15, "type": "tile_drawn"}]},
        {"type": "req", "seq": 15, "requested_seq": 15,
         "res": {"gap": False, "snapshot": False, "seq": 15}},
        {"type": "end", "reason": "finished"},
    ]
    path = tmp_path / "unknown-gap.jsonl"
    path.write_text("\n".join(json.dumps(row) for row in records))

    report = summarize([path])

    assert report["gap_classification"]["decision_impact"] == {
        "unknown": 1}
    assert report["games"][0]["gap_decision_impact_unknown"] == 1


def test_claim_miss_per_window_classification(tmp_path):
    """claim_miss 按窗口关联 decision/action/回声证据分类(修复计划 §6)。

    同窗动作 200 或我方认领回声 → success;action_rejected/action_uncertain
    → rejected/uncertain(回声优先);同窗决策过 → strategy_pass;他家
    认领同弃牌 → opponent_preempted;仅有候选 → unsubmitted_candidate;
    无窗口身份(旧日志)→ unknown。raw_record_count 保留原始计数。
    """
    def wid(owner, seq, tile):
        return {"game_id": "g", "round_id": 1, "discard_owner": owner,
                "source_discard_seq": seq, "tile": tile,
                "identity_status": "authoritative",
                "identity_origin": "tile_discard_event_seq",
                "first_seen_via": "event"}

    def wkey(owner, seq, tile, phase):
        return {"window_id": wid(owner, seq, tile), "phase": phase}

    snap = _snapshot(hand=["6b", "6b", "1w", "2w", "3w", "4w", "5w",
                           "6w", "1t", "2t", "3t", "4t", "9w"], round_no=1)
    records = [
        {"type": "meta", "gid": "g", "name": "b"},
        {"type": "snapshot", "seq": 0, "snap": snap},
        # 我方碰认领回声:同窗 POST 结果未知,但回声证明已落地
        {"type": "events", "seq_to": 51, "events": [
            {"seq": 50, "type": "tile_discarded", "seat": 3, "tile": "6b",
             "data": None},
            {"seq": 51, "type": "peng", "seat": 0, "tile": "6b",
             "data": None}]},
        # 他家碰抢走同一弃牌:对手优先级终止我方吃窗
        {"type": "events", "seq_to": 61, "events": [
            {"seq": 60, "type": "tile_discarded", "seat": 1, "tile": "1b",
             "data": None},
            {"seq": 61, "type": "peng", "seat": 2, "tile": "1b",
             "data": None}]},
        # strategy_pass:同窗决策明确为过
        {"type": "decision", "id": 1, "seq": 10, "phase": "response_chi",
         "legal": [-1], "action": -1,
         "window_attempt_key": wkey(2, 10, 5, "response_chi")},
        {"type": "claim_miss", "phase": "response_chi",
         "reason": "window_confirm_closed", "chosen": None,
         "window_id": wid(2, 10, 5),
         "window_attempt_key": wkey(2, 10, 5, "response_chi")},
        # success:同窗动作 HTTP 200(确认陈旧是误报)
        {"type": "claim_miss", "phase": "response_chi",
         "reason": "window_confirm_stale", "chosen": -2,
         "window_id": wid(2, 20, 6),
         "window_attempt_key": wkey(2, 20, 6, "response_chi")},
        {"type": "action", "phase": "response_chi", "ok": True, "status": 200,
         "payload": {"action": "chi"},
         "window_attempt_key": wkey(2, 20, 6, "response_chi")},
        # rejected:非 pass POST 被 409 拒绝
        {"type": "claim_miss", "phase": "response_peng",
         "reason": "action_rejected", "chosen": -5, "status": 409,
         "window_id": wid(1, 30, 7),
         "window_attempt_key": wkey(1, 30, 7, "response_peng")},
        {"type": "action", "phase": "response_peng", "ok": False,
         "status": 409, "payload": {"action": "peng"},
         "window_attempt_key": wkey(1, 30, 7, "response_peng")},
        # uncertain:POST 结果未知且无回声
        {"type": "claim_miss", "phase": "response_peng",
         "reason": "action_uncertain", "chosen": -5, "status": 0,
         "window_id": wid(1, 40, 8),
         "window_attempt_key": wkey(1, 40, 8, "response_peng")},
        # mine echo:action_uncertain 但同窗我方碰回声 → success
        {"type": "claim_miss", "phase": "response_peng",
         "reason": "action_uncertain", "chosen": -5, "status": 0,
         "window_id": wid(3, 50, 14),
         "window_attempt_key": wkey(3, 50, 14, "response_peng")},
        # opponent_preempted:他家碰走我方候选吃窗的弃牌
        {"type": "claim_miss", "phase": "response_chi",
         "reason": "server_timeout_chi", "chosen": None,
         "window_id": wid(1, 60, 9),
         "window_attempt_key": wkey(1, 60, 9, "response_chi")},
        # unsubmitted_candidate:合法候选存在,未形成非 pass POST
        {"type": "claim_miss", "phase": "response_peng",
         "reason": "server_timeout_peng", "chosen": None,
         "window_id": wid(3, 70, 10),
         "window_attempt_key": wkey(3, 70, 10, "response_peng")},
        # unknown:无窗口身份(旧日志形态,无法关联证据)
        {"type": "claim_miss", "phase": "response_peng",
         "reason": "server_timeout_peng", "chosen": None},
        {"type": "end", "reason": "finished",
         "demand": {"reason_mask": 0, "in_flight": False, "reasons": {}}},
    ]
    path = tmp_path / "classification.jsonl"
    path.write_text("\n".join(json.dumps(row) for row in records))

    report = summarize([path])

    classification = report["claim_miss_classification"]
    assert classification["raw_record_count"] == 8
    assert classification["window_linked"] == 7
    assert classification["by_category"] == {
        "success": 2, "strategy_pass": 1, "opponent_preempted": 1,
        "unsubmitted_candidate": 1, "rejected": 1, "uncertain": 1,
        "unknown": 1}
    assert report["games"][0]["claim_miss_classification"] == classification
    assert (report["layer_metrics"]["transport"]
            ["claim_miss_classification"] == classification)


def test_canonical_resolution_success_beats_late_claim_miss(tmp_path):
    window_id = {
        "game_id": "g", "round_id": 1, "discard_owner": 2,
        "source_discard_seq": 44, "tile": 5,
        "identity_status": "authoritative",
        "identity_origin": "tile_discard_event_seq",
        "first_seen_via": "event",
    }
    attempt = {"window_id": window_id, "phase": "response_chi"}
    records = [
        {"type": "window_confirm", "outcome": "requested",
         "phase": "response_chi", "window_id": window_id,
         "window_attempt_key": attempt, "legal": [7]},
        {"type": "window_confirm", "outcome": "open",
         "phase": "response_chi", "window_id": window_id,
         "window_attempt_key": attempt, "legal": [7],
         "authorization_snapshot_seq": 45,
         "exact_deadline_at": 1001.0},
        {"type": "decision", "id": 3, "phase": "response_chi",
         "window_id": window_id, "window_attempt_key": attempt,
         "legal": [7], "action": 7},
        {"type": "action", "phase": "response_chi", "decision": 3,
         "window_id": window_id, "window_attempt_key": attempt,
         "payload": {"action": "chi"}, "ok": True, "status": 200,
         "outcome": "SUCCESS"},
        {"type": "claim_miss", "phase": "response_chi",
         "window_id": window_id, "window_attempt_key": attempt,
         "reason": "server_timeout_chi", "chosen": 7},
        {"type": "end", "reason": "finished"},
    ]
    path = tmp_path / "canonical-success.jsonl"
    path.write_text("\n".join(json.dumps(row) for row in records))

    report = summarize([path])

    resolution = report["games"][0]["canonical_resolutions"][0]
    assert resolution["outcome"] == "SUCCESS"
    assert resolution["loss_stage"] == "NONE"
    assert report["games"][0]["false_claim_miss_count"] == 1
    assert report["window_attribution"]["canonical_client_loss_count"] == 0


def test_canonical_resolution_distinguishes_decision_loss(tmp_path):
    window_id = {
        "game_id": "g", "round_id": 1, "discard_owner": 2,
        "source_discard_seq": 9, "tile": 5,
        "identity_status": "authoritative",
    }
    attempt = {"window_id": window_id, "phase": "response_peng"}
    records = [
        {"type": "window_confirm", "outcome": "open",
         "phase": "response_peng", "window_id": window_id,
         "window_attempt_key": attempt, "legal": [7],
         "authorization_snapshot_seq": 10,
         "exact_deadline_at": 1001.0},
        {"type": "claim_miss", "phase": "response_peng",
         "window_id": window_id, "window_attempt_key": attempt,
         "reason": "server_timeout_peng", "chosen": None},
        {"type": "end", "reason": "finished"},
    ]
    path = tmp_path / "canonical-decision-loss.jsonl"
    path.write_text("\n".join(json.dumps(row) for row in records))

    report = summarize([path])

    resolution = report["games"][0]["canonical_resolutions"][0]
    assert resolution["outcome"] == "CLIENT_LOSS"
    assert resolution["loss_stage"] == "DECISION"
    assert resolution["loss_reason"] == "decision_not_started_before_terminal"


def test_legacy_identity_and_window_409_are_reported_as_weak_evidence(tmp_path):
    legacy_id = {
        "game_id": "g", "round_id": 1, "discard_owner": 2,
        "source_discard_seq": None, "tile": 5,
        "identity_status": "legacy_unresolved",
        "identity_origin": "legacy_snapshot",
        "fallback": [3, [13, 13, 13, 13]],
    }
    key = {"window_id": legacy_id, "phase": "response_peng"}
    authoritative_id = dict(legacy_id, source_discard_seq=21,
                            identity_status="authoritative")
    authoritative_key = {"window_id": authoritative_id,
                         "phase": "response_peng"}
    records = [
        {"type": "window_confirm", "outcome": "requested",
         "phase": "response_peng", "window_id": legacy_id,
         "window_attempt_key": key, "legal": [7]},
        {"type": "action", "phase": "response_peng", "window_id": authoritative_id,
         "window_attempt_key": authoritative_key, "payload": {"action": "peng"},
         "ok": False, "status": 409, "outcome": "POST_REJECTED",
         "transport": {"action_attempts": [{"status": 409,
                                               "server_trace_id": "trace-1"}]}},
        {"type": "action", "phase": "draw", "payload": {"action": "discard"},
         "ok": False, "status": 409, "outcome": "POST_REJECTED"},
        {"type": "req", "requested_seq": 0, "seq": 22,
         "request_kind": "RESYNC", "status": 200,
         "res": {"snapshot": True, "seq": 22}},
        {"type": "snapshot", "seq": 22, "snap": _snapshot()},
        {"type": "end", "reason": "finished"},
    ]
    path = tmp_path / "identity-409.jsonl"
    path.write_text("\n".join(json.dumps(row) for row in records))

    report = summarize([path])

    game = report["games"][0]
    assert game["legacy_eligible_windows"] == 1
    assert game["window_evidence_status"] == "partial_identity"
    assert game["status"]["window_status"] == "window_partial_identity"
    assert report["window_409"]["window_409_count"] == 1
    assert report["window_409"]["all_action_409"] == 2
    assert report["window_409"]["normal_action_409"] == 1
    assert report["window_409"]["window_409_linked"] == 1
    assert report["window_409"]["duplicate_post_after_409"] == 0
    assert report["window_409"]["chains"][0]["gid"] == "g"
