"""Task 1.2 验收:会话管理器状态机/并发上限/幂等停止/错误透出。

使用注入的 runner_factory(哨兵 runner)驱动会话,不依赖真实对弈。
"""

import time
import threading

import pytest

from mj.clientd.sessions import (
    SessionManager, STATUS_RUNNING, STATUS_FINISHED, STATUS_CANCELLED,
    STATUS_ERROR,
)
from mj.clientd.errors import ConflictError, NotFoundError, ValidationError


def _wait_status(mgr, sid, expected, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        s = mgr.get(sid)
        if s.status != STATUS_RUNNING:
            return s.status
        time.sleep(0.01)
    raise AssertionError(f"session {sid} never left running")


def _slow_runner(iterations=5, step=0.02):
    def _factory(kind, config):
        def runner(stop, session):
            for i in range(iterations):
                if stop():
                    return {"cancelled": i}
                time.sleep(step)
            return {"done": True}
        return runner
    return _factory


def test_state_machine_to_finished():
    mgr = SessionManager(runner_factory=_slow_runner(iterations=2))
    s = mgr.create("arena", {})
    assert s.status == STATUS_RUNNING
    assert _wait_status(mgr, s.id, STATUS_FINISHED) == STATUS_FINISHED
    assert mgr.get(s.id).result == {"done": True}


def test_cancel_at_boundary_keeps_result():
    mgr = SessionManager(runner_factory=_slow_runner(iterations=100, step=0.01))
    s = mgr.create("arena", {})
    time.sleep(0.05)
    mgr.stop(s.id)
    assert _wait_status(mgr, s.id, STATUS_CANCELLED) == STATUS_CANCELLED
    got = mgr.get(s.id)
    assert got.result is not None and got.result["cancelled"] < 100


def test_stop_idempotent_and_after_finish():
    mgr = SessionManager(runner_factory=_slow_runner(iterations=1))
    s = mgr.create("arena", {})
    _wait_status(mgr, s.id, STATUS_FINISHED)
    mgr.stop(s.id)  # 已结束再 stop 为无操作,状态不变
    assert mgr.get(s.id).status == STATUS_FINISHED


def test_error_surfaces_message():
    def _bad_factory(kind, config):
        def runner(stop, session):
            raise RuntimeError("exploded")
        return runner
    mgr = SessionManager(runner_factory=_bad_factory)
    s = mgr.create("tournament", {})
    assert _wait_status(mgr, s.id, STATUS_ERROR) == STATUS_ERROR
    assert "exploded" in mgr.get(s.id).error


def test_concurrency_cap_rejects():
    started = threading.Event()
    release = threading.Event()

    def _block_factory(kind, config):
        def runner(stop, session):
            started.set()
            release.wait(timeout=5)
            return {}
        return runner

    mgr = SessionManager(max_concurrent=2, runner_factory=_block_factory)
    a = mgr.create("arena", {})
    b = mgr.create("arena", {})
    started.wait(timeout=2)
    with pytest.raises(ConflictError):
        mgr.create("arena", {})
    release.set()
    _wait_status(mgr, a.id, STATUS_FINISHED)
    _wait_status(mgr, b.id, STATUS_FINISHED)


def test_kind_validation():
    class _Factory:
        def __call__(self, kind, config):
            def runner(stop, session):
                return {}
            return runner
    mgr = SessionManager(runner_factory=_Factory())
    with pytest.raises(ValidationError):
        mgr.create("bogus", {})


def test_get_missing_and_list():
    mgr = SessionManager(runner_factory=_slow_runner(iterations=1))
    with pytest.raises(NotFoundError):
        mgr.get("nope")
    s1 = mgr.create("arena", {"x": 1})
    _wait_status(mgr, s1.id, STATUS_FINISHED)
    listed = mgr.list()
    assert any(entry["id"] == s1.id and entry["status"] == STATUS_FINISHED
               for entry in listed)