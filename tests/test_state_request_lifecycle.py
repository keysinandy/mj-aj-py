"""Regression coverage for the state-request lifecycle refactor."""

import io
import json
import threading
import urllib.error
from unittest import mock

import pytest

from mj.platform.state_demand import (
    PENDING,
    RESYNC,
    SATISFIED,
    SSE_DELTA,
    TERMINAL,
    WINDOW_CONFIRM,
    StateDemand,
    WindowId,
)
from mj.platform.api import Api, ApiError, _TLS
from mj.platform.bot_client import BotClient
from mj.platform.recorder import Recorder
from mj.platform.throttle import ThrottleTicket
from mj.platform.state_fetch import StateFetchCoordinator
from mj.platform.state_scheduler import StateScheduler
from mj.platform.throttle import StateThrottle
from mj.platform.window_confirmation import WindowConfirmation, WindowTiming
from scripts.window_acceptance import _request_count_metrics


def _window(seq=7, phase="response_chi"):
    return WindowId("g", 1, 2, seq, 5), phase


def test_queued_delta_is_upgraded_before_admission():
    demand = StateDemand(run_id="run-test")
    candidate = demand.queue_candidate(default_seq=90, default_kind=SSE_DELTA)
    window_id, phase = _window()
    demand.submit_window_confirm(window_id, phase, deadline=42.0)

    snapshot = demand.candidate_snapshot()
    assert snapshot["candidate_id"] == candidate.candidate_id
    assert snapshot["mode"] == "FULL"
    request = demand.admit_candidate()
    assert request.mode == "FULL"
    assert request.seq == 0
    assert set(request.reasons) == {SSE_DELTA, WINDOW_CONFIRM}


def test_queued_delta_keeps_local_cursor_when_sse_watermark_advances():
    """A later SSE wake must not turn a queued cursor into a skip-ahead seq."""
    demand = StateDemand(run_id="run-cursor-race", gid="g")
    candidate = demand.queue_candidate(default_seq=31, default_kind=SSE_DELTA)
    demand.submit_sse(34)

    snapshot = demand.candidate_snapshot()
    assert snapshot["default_seq"] == 31
    assert demand.watermark_target == 34
    request = demand.admit_candidate()
    assert request is not None
    assert request.seq == 31
    assert request.reasons[SSE_DELTA]["wanted_seq"] == 34
    assert request.candidate_id == candidate.candidate_id


def test_request_ids_are_unique_across_games_and_restart_scope():
    left = StateDemand(run_id="run-test")
    right = StateDemand(run_id="run-test")
    first = left.start_request(default_seq=1)
    second = right.start_request(default_seq=1)
    restarted = StateDemand(run_id="run-test")
    third = restarted.start_request(default_seq=1)
    assert len({first.logical_request_id, second.logical_request_id,
                third.logical_request_id}) == 3
    assert all(item.logical_request_id.startswith("run-test:state:")
               for item in (first, second, third))
    candidates = [
        StateDemand(run_id="run-test", gid="g").queue_candidate(
            default_seq=1).candidate_id
        for _ in range(2)
    ]
    assert len(set(candidates)) == 2


def test_finished_close_clears_initial_resync():
    demand = StateDemand()
    demand.submit_resync(cause="initial")
    demand.start_request(default_seq=0, default_kind=RESYNC)
    demand.close("game_finished")
    snapshot = demand.request_snapshot()
    assert snapshot["reason_mask"] == 0
    assert snapshot["in_flight"] is False
    assert snapshot["reasons"][RESYNC]["status"] == TERMINAL
    assert snapshot["reasons"][RESYNC]["terminal_reason"] == "game_finished"
    assert demand.close("game_finished") is False


def test_completion_revision_cannot_finish_new_window():
    demand = StateDemand()
    first_id, phase = _window(7)
    demand.submit_window_confirm(first_id, phase)
    request = demand.start_request()
    first_revision = request.reasons[WINDOW_CONFIRM]["revision"]
    second_id, _ = _window(8)
    demand.submit_window_confirm(second_id, phase)
    assert demand.resolve_reason(
        WINDOW_CONFIRM, SATISFIED, expected_revision=first_revision,
        window_id=first_id) is False
    assert demand.reasons[WINDOW_CONFIRM]["status"] == PENDING
    assert demand.reasons[WINDOW_CONFIRM]["window_id"] == second_id


def test_close_and_admission_race_has_one_terminal_result():
    demand = StateDemand()
    demand.queue_candidate(default_seq=3)
    barrier = threading.Barrier(2)
    results = []

    def admit():
        barrier.wait()
        try:
            results.append(demand.admit_candidate())
        except RuntimeError:
            results.append(None)

    thread = threading.Thread(target=admit)
    thread.start()
    barrier.wait()
    demand.close("stopped")
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert demand.request_snapshot()["closed"] is True
    assert demand.request_snapshot()["reason_mask"] == 0
    assert results == [None]


def test_sse_revision_does_not_invalidate_confirmation():
    demand = StateDemand()
    window_id, phase = _window()
    demand.submit_window_confirm(window_id, phase)
    request = demand.start_request()
    revision = request.reasons[WINDOW_CONFIRM]["revision"]
    demand.submit_sse(100)
    assert demand.resolve_reason(
        WINDOW_CONFIRM, SATISFIED, expected_revision=revision,
        window_id=window_id) is True
    assert demand.reasons[WINDOW_CONFIRM]["status"] == SATISFIED


def test_pending_successor_is_counted_only_when_admitted():
    demand = StateDemand()
    demand.submit_window_confirm(*_window())
    first = demand.start_request()
    demand.reconcile(1, response_mode="FULL", snapshot={})
    assert demand.successor_requests == 0
    second = demand.start_request()
    assert second.logical_request_id != first.logical_request_id
    assert demand.successor_requests == 1


def test_scheduler_updates_one_waiter_without_a_second_limiter():
    now = [10.0]
    throttle = StateThrottle(rate=100.0, burst=1, clock=lambda: now[0])
    scheduler = StateScheduler(throttle, clock=lambda: now[0])
    demand = StateDemand(run_id="run-test", clock=lambda: now[0])
    candidate = scheduler.register("g", demand, default_seq=90,
                                   deadline=20.0)
    scheduler.update(candidate, deadline=11.0)
    assert throttle._waiters == []
    result = scheduler.acquire(candidate)
    assert result is not None
    assert result.request.seq == 90
    assert result.ticket.urgent is True


def test_candidate_inherits_a_reason_deadline_before_scheduler_admission():
    demand = StateDemand(run_id="run-deadline", gid="g")
    demand.submit_window_confirm(*_window(), deadline=20.0)
    candidate = demand.queue_candidate(default_seq=90)
    assert candidate.deadline == 20.0
    assert demand.candidate_snapshot()["deadline"] == 20.0


def test_coordinator_owns_fetch_until_reconciliation():
    class Api:
        def __init__(self):
            self.calls = []

        def game_state(self, gid, seq, **kwargs):
            self.calls.append((gid, seq, kwargs))
            return {"seq": seq, "events": []}

    demand = StateDemand(run_id="run-test")
    coordinator = StateFetchCoordinator("g", demand)
    api = Api()
    response = coordinator.fetch(api, default_seq=4)
    assert response["seq"] == 4
    assert api.calls[0][0:2] == ("g", 4)
    assert coordinator.active is None
    assert demand.request_snapshot()["in_flight"] is False


def test_window_timing_keeps_exact_deadline_separate_from_estimates():
    timing = WindowTiming(
        not_before=10.0,
        scheduler_deadline=20.0,
        observation_budget_deadline=30.0,
        exact_window_deadline=None,
        exact_deadline_source="missing",
    )
    confirmation = WindowConfirmation(None, "response_chi", timing=timing)
    assert confirmation.exact_deadline_authoritative is False
    assert confirmation.can_successor(now=29.0) is True
    assert confirmation.budget_exhausted(now=31.0) is True


def test_reconcile_waits_for_response_application_boundary():
    demand = StateDemand()
    demand.submit_sse(10)
    request = demand.start_request(default_seq=0)
    demand.begin_reconcile(request)
    assert demand.reconcile(10, applied=False) is True
    assert demand.reasons[SSE_DELTA]["status"] == PENDING
    successor = demand.start_request(default_seq=0)
    assert successor is not None
    demand.reconcile(10, applied=True)
    assert demand.reasons[SSE_DELTA]["status"] == SATISFIED
    assert demand.request_snapshot()["satisfied_reasons"] == [SSE_DELTA]


def test_admitted_but_unsent_close_is_counted_as_cancelled():
    demand = StateDemand()
    demand.submit_resync(cause="stop")
    demand.start_request(default_kind=RESYNC)
    demand.close("stopped")
    assert demand.request_snapshot()["cancelled_before_send"] == 1
    assert demand.request_snapshot()["lifecycle"] == "CLOSED"


def test_close_blocks_late_reason_resolution():
    demand = StateDemand()
    demand.submit_resync(cause="action_409")
    demand.close("worker_error")
    assert demand.finish_resync(SATISFIED) is False
    assert demand.reasons[RESYNC]["status"] == TERMINAL


def test_resolved_queued_candidate_is_withdrawn_without_a_request():
    demand = StateDemand()
    candidate = demand.queue_candidate(default_seq=3)
    revision = demand.reasons[SSE_DELTA]["revision"]
    assert demand.resolve_reason(
        SSE_DELTA, SATISFIED, expected_revision=revision) is True
    assert demand.admit_candidate() is None
    assert demand.request_snapshot()["candidate"] is None
    assert demand.request_snapshot()["cancelled_before_send"] == 1


def test_api_retries_keep_logical_id_and_assign_unique_transport_ids():
    api = Api("https://example.invalid", "token")
    throttle = mock.Mock()
    throttle.acquire.return_value = ThrottleTicket(0.0, False, False, None)
    api.state_throttle = throttle
    response = mock.MagicMock()
    response.__enter__.return_value = response
    response.read.return_value = b'{"seq": 4}'
    response.headers = {}
    error = urllib.error.HTTPError(
        "https://example.invalid", 429, "busy", {}, io.BytesIO(b"{}"))
    requests = []

    def send(request, **kwargs):
        requests.append(request)
        if len(requests) == 1:
            raise error
        return response

    with mock.patch("urllib.request.urlopen", side_effect=send), \
            mock.patch("mj.platform.api.time.sleep"):
        result = api.game_state(
            "g", 4, logical_request_id="run:state:1",
            transport_request_id="run:state:1:attempt:1")
    assert result == {"seq": 4}
    assert len(requests) == 2
    assert [item.headers.get("X-client-request-id") for item in requests] == [
        "run:state:1", "run:state:1"]
    assert [item.headers.get("X-client-transport-request-id")
            for item in requests] == [
                "run:state:1:attempt:1", "run:state:1:attempt:2"]
    assert [_item["transport_request_id"]
            for _item in _TLS.request_meta["state_attempts"]] == [
                "run:state:1:attempt:1", "run:state:1:attempt:2"]


def test_coordinator_counts_one_logical_request_and_two_http_attempts():
    throttle = StateThrottle(rate=1000.0, burst=1)
    scheduler = StateScheduler(throttle)
    demand = StateDemand(run_id="run-retry", gid="g")
    coordinator = StateFetchCoordinator("g", demand, scheduler=scheduler)
    api = Api("https://example.invalid", "token", state_throttle=throttle)
    response = mock.MagicMock()
    response.__enter__.return_value = response
    response.read.return_value = b'{"seq": 1, "events": []}'
    response.headers = {}
    error = urllib.error.HTTPError(
        "https://example.invalid", 429, "busy", {}, io.BytesIO(b"{}"))
    sends = []

    def send(request, **kwargs):
        sends.append(request)
        if len(sends) == 1:
            raise error
        return response

    with mock.patch("urllib.request.urlopen", side_effect=send), \
            mock.patch("mj.platform.api.time.sleep"):
        result = coordinator.fetch(api, default_seq=1)
    snapshot = demand.request_snapshot()
    assert result["seq"] == 1
    assert snapshot["logical_state_requests"] == 1
    assert snapshot["physical_state_attempts"] == 2
    assert len(sends) == 2


def test_coordinator_releases_owner_when_response_application_fails():
    demand = StateDemand()
    coordinator = StateFetchCoordinator("g", demand)
    request = coordinator.begin(default_seq=2)
    assert request is not None
    coordinator.mark_transport_started()

    def fail(_response):
        raise ValueError("mirror rejected")

    assert coordinator.complete(
        response={"seq": 2}, response_seq=2, apply=fail) is True
    assert coordinator.active is None
    assert demand.request_snapshot()["in_flight"] is False
    assert demand.has_pending is True


def test_shared_scheduler_has_one_permit_queue_for_multiple_gids():
    throttle = StateThrottle(rate=1000.0, burst=10)
    scheduler = StateScheduler(throttle)
    calls = []
    lock = threading.Lock()

    class Api:
        def game_state(self, gid, seq, *, state_ticket=None, **kwargs):
            assert state_ticket is not None
            with lock:
                calls.append((gid, seq, state_ticket))
            return {"seq": seq, "events": []}

    def fetch(gid):
        demand = StateDemand(run_id="run-shared", gid=gid)
        return StateFetchCoordinator(
            gid, demand, scheduler=scheduler).fetch(Api(), default_seq=0)

    threads = [threading.Thread(target=fetch, args=(f"g{i}",))
               for i in range(10)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)
        assert not thread.is_alive()
    assert sorted(item[0] for item in calls) == [f"g{i}" for i in range(10)]
    assert throttle._waiters == []


def test_recorder_keeps_frozen_reason_and_application_metadata(tmp_path):
    recorder = Recorder(root=str(tmp_path))
    recorder.req(
        "g", 0, 200, 1.0, attempts=1,
        logical_request_id="run:state:1",
        candidate_id="run:candidate:g:1",
        transport_request_id="run:state:1:attempt:1",
        reason_revisions={WINDOW_CONFIRM: 2},
        evaluated_revisions={WINDOW_CONFIRM: 2},
        satisfied_reasons=[WINDOW_CONFIRM],
        metric_version="state-request-lifecycle-v1",
        metric_source="StateDemand",
        effective_deadline=42.0,
        deadline_source="reason_effective",
    )
    recorder.close_all()
    rows = list((tmp_path).glob("**/*.jsonl"))
    assert len(rows) == 1
    record = json.loads(rows[0].read_text().splitlines()[0])
    assert record["logical_request_id"] == "run:state:1"
    assert record["transport_request_id"].endswith(":attempt:1")
    assert record["satisfied_reasons"] == [WINDOW_CONFIRM]
    assert record["deadline_source"] == "reason_effective"


def test_state_reconcile_records_application_boundary(tmp_path):
    recorder = Recorder(root=str(tmp_path))
    demand = StateDemand(run_id="run-reconcile", gid="g")
    callbacks = []

    def on_reconciled(request, snapshot, pending, error):
        callbacks.append((request.logical_request_id, pending, error))
        BotClient._record_state_reconcile(
            recorder, "g", request, snapshot, pending, error)

    coordinator = StateFetchCoordinator(
        "g", demand, on_reconciled=on_reconciled)
    request = coordinator.begin(default_seq=1)
    assert request is not None
    coordinator.mark_transport_started()
    coordinator.complete(response={"seq": 1}, response_seq=1)
    recorder.close_all()

    assert callbacks == [(request.logical_request_id, False, None)]
    rows = list(tmp_path.glob("**/*.jsonl"))
    records = [json.loads(line) for line in rows[0].read_text().splitlines()]
    reconcile = [item for item in records
                 if item["type"] == "state_reconcile"]
    assert len(reconcile) == 1
    assert reconcile[0]["logical_request_id"] == request.logical_request_id
    assert reconcile[0]["satisfied_reasons"] == [SSE_DELTA]
    assert reconcile[0]["pending"] is False


def test_acceptance_deduplicates_logical_and_transport_ids():
    rows = [
        {
            "logical_request_id": "l1",
            "transport": {
                "state_attempts": [
                    {"transport_request_id": "l1:attempt:1"}]},
        },
        {
            "logical_request_id": "l1",
            "transport": {
                "state_attempts": [
                    {"transport_request_id": "l1:attempt:1"},
                    {"transport_request_id": "l1:attempt:2"}]},
        },
    ]
    metrics = _request_count_metrics(rows)
    assert metrics["logical"] == 1
    assert metrics["physical"] == 2
    assert metrics["duplicate_logical"] == 1
    assert metrics["duplicate_physical"] == 1


def test_acceptance_keeps_physical_attempts_missing_without_evidence():
    metrics = _request_count_metrics([
        {"logical_request_id": "run:state:g:1", "attempts": 3},
    ])
    assert metrics["logical"] == 1
    assert metrics["physical"] is None
    assert metrics["source"] == "lifecycle_ids_without_attempt_ids"


def test_scheduler_withdraw_wins_before_admission():
    throttle = StateThrottle(rate=100.0, burst=1)
    scheduler = StateScheduler(throttle)
    demand = StateDemand(run_id="run-race", gid="g")
    candidate = scheduler.register("g", demand, default_seq=1)
    assert scheduler.withdraw(candidate, reason="stopped") is True
    assert scheduler.acquire(candidate) is None
    assert demand.request_snapshot()["cancelled_before_send"] == 1
    assert scheduler.candidates == {}


def test_close_during_state_transport_blocks_the_next_retry():
    throttle = StateThrottle(rate=1000.0, burst=1)
    scheduler = StateScheduler(throttle)
    demand = StateDemand(run_id="run-cancel", gid="g")
    coordinator = StateFetchCoordinator("g", demand, scheduler=scheduler)
    request = coordinator.begin(default_seq=1)
    assert request is not None
    transport_id = coordinator.mark_transport_started()
    operation = coordinator.active
    api = Api("https://example.invalid", "token", state_throttle=throttle)
    response = mock.MagicMock()
    response.__enter__.return_value = response
    response.read.return_value = b'{"seq": 1}'
    response.headers = {}
    retry = urllib.error.HTTPError(
        "https://example.invalid", 429, "busy", {}, io.BytesIO(b"{}"))
    sends = []

    def send(req, **kwargs):
        sends.append(req)
        coordinator.close("stopped")
        raise retry

    with mock.patch("urllib.request.urlopen", side_effect=send), \
            mock.patch("mj.platform.api.time.sleep"):
        with pytest.raises(ApiError) as raised:
            api.game_state(
                "g", 1, logical_request_id=request.logical_request_id,
                transport_request_id=transport_id,
                state_ticket=operation.ticket,
                candidate_id=operation.candidate_id,
                state_throttle=throttle,
                cancel_check=coordinator.cancel_requested)
    assert raised.value.status == 0
    assert len(sends) == 1
    assert demand.request_snapshot()["physical_state_attempts"] == 1
    coordinator.complete(error=raised.value)
    coordinator.finalize_close("stopped")
    assert demand.request_snapshot()["lifecycle"] == "CLOSED"
