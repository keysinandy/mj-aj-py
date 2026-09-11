from mj.platform.state_demand import (
    PENDING,
    RESYNC,
    SATISFIED,
    SSE_DELTA,
    StateDemand,
    WINDOW_CONFIRM,
    WindowAttemptKey,
    WindowId,
)


def test_full_snapshot_is_not_numeric_watermark_zero():
    demand = StateDemand()
    demand.submit_sse(120)
    demand.submit_window_confirm(
        WindowId("g", 1, 2, 7, 5), "response_peng", deadline=10.0)

    plan = demand.start_request()

    assert plan.seq == 0
    assert plan.mode == "FULL"
    assert demand.watermark_target == 120
    assert demand.full_snapshot_required is True


def test_each_reason_keeps_its_own_metadata_and_deadline_recomputes():
    demand = StateDemand()
    key = WindowAttemptKey(WindowId("g", 1, 2, 7, 5), "response_peng")
    demand.submit_sse(130)
    demand.submit_resync(cause="action_409")
    demand.submit_window_confirm(key.window_id, key.phase, deadline=5.0)

    assert demand.reasons[SSE_DELTA]["wanted_seq"] == 130
    assert demand.reasons[RESYNC]["cause"] == "action_409"
    assert demand.reasons[WINDOW_CONFIRM]["window_id"] == key.window_id
    assert demand.effective_deadline == 5.0

    demand.finish_window_confirm(SATISFIED)
    assert demand.effective_deadline is None
    assert demand.full_snapshot_required is True  # RESYNC still pending

    demand.reconcile(130, response_mode="FULL", snapshot={"seq": 130})
    assert demand.reasons[RESYNC]["status"] == PENDING
    demand.finish_resync(SATISFIED)
    assert demand.reasons[RESYNC]["status"] == SATISFIED
    assert demand.reasons[SSE_DELTA]["status"] == SATISFIED
    assert demand.full_snapshot_required is False


def test_resync_requires_snapshot_rebuild_ack_not_full_request_mode():
    demand = StateDemand()
    demand.submit_resync(cause="action_409")
    plan = demand.start_request(default_seq=42)
    assert plan.mode == "FULL"

    demand.reconcile(42, response_mode="FULL", snapshot=None)
    assert demand.has_pending is True
    assert demand.reasons[RESYNC]["status"] == PENDING

    demand.finish_resync(SATISFIED)
    assert demand.has_pending is False


def test_window_confirm_has_priority_over_resync_and_keeps_deadline():
    demand = StateDemand()
    demand.submit_resync(cause="action_409")
    demand.submit_window_confirm(
        WindowId("g", 1, 2, 7, 5), "response_peng", deadline=123.0)

    plan = demand.start_request(default_seq=11)

    assert plan.mode == "FULL"
    assert plan.seq == 0
    assert plan.kind == WINDOW_CONFIRM
    assert plan.effective_deadline == 123.0


def test_delta_uses_local_cursor_not_sse_wake_watermark():
    demand = StateDemand()
    demand.submit_sse(2)

    plan = demand.start_request(default_seq=1)

    assert demand.watermark_target == 2
    assert plan.mode == "DELTA"
    assert plan.seq == 1


def test_generation_change_only_successors_if_latest_reason_is_pending():
    demand = StateDemand()
    demand.submit_sse(100)
    first = demand.start_request()
    demand.submit_sse(103)

    # A response at 110 covers the newest demand.  The generation changed
    # while the request was in flight, but no successor is needed.
    assert demand.reconcile(110, snapshot={"seq": 110}) is False
    assert demand.start_request() is None
    assert first.started_generation < demand.generation

    demand.submit_sse(120)
    second = demand.start_request()
    demand.submit_sse(130)
    demand.reconcile(125, snapshot={"seq": 125})
    successor = demand.start_request(default_seq=125)
    assert successor is not None
    assert successor.seq == 125
    assert successor.logical_request_id != second.logical_request_id
    assert demand.successor_requests == 1
    assert demand.logical_demands >= 3


def test_duplicate_or_lower_watermark_does_not_advance_generation():
    demand = StateDemand()
    assert demand.submit_sse(103) is True
    generation = demand.generation
    assert demand.submit_sse(103) is False
    assert demand.submit_sse(102) is False
    assert demand.generation == generation
    assert demand.suppressed_duplicates == 2


def test_legacy_queue_wake_remains_compatible():
    demand = StateDemand()
    demand.put((12, False))
    demand.put((10, False))
    assert demand.get_nowait() == (12, False)
    demand.acknowledge(12)
    assert demand.qsize() == 0


def test_many_sse_wakes_coalesce_to_one_pending_physical_target():
    demand = StateDemand()
    for seq in range(1, 101):
        demand.put((seq, False))
    assert demand.qsize() == 1
    assert demand.watermark_target == 100
    assert demand.logical_demands == 100
    assert demand.coalesced_demands >= 99
    plan = demand.start_request()
    assert plan.seq == 100
    assert demand.physical_state_requests == 1


def test_two_games_have_independent_in_flight_state():
    left, right = StateDemand(), StateDemand()
    left.submit_sse(10)
    right.submit_sse(20)
    assert left.start_request().seq == 10
    assert right.start_request().seq == 20
    left.submit_sse(11)
    assert left.in_flight is True
    assert right.in_flight is True
