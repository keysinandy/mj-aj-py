"""CORS + 竞技场会话控制面 API 验收(Web 一键启动的前端通路)。

- CORS:HTTP 响应在命中放行 Origin 时携带 Access-Control-Allow-Origin,
  OPTIONS 预检返回允许头;未放行 Origin 不加 CORS 头;
- 会话 API:POST /api/sessions 创建(注入假 runner 不下真实对弈)、
  GET 列表/单条、POST :id/stop;
- 竞技场会话管理器:拒绝非 arena 种类,默认座位/规模注入。
"""

import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from mj.clientd.service import Router, Service
from mj.clientd.api import session_router
from mj.clientd.sessions import SessionManager
from mj.clientd.arena import (
    make_arena_session_manager, _normalize_seats, DEFAULT_SEATS,
)
from mj.clientd.errors import ValidationError


def _request(port, path, method="GET", body=None, origin=None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if origin is not None:
        headers["Origin"] = origin
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}",
                                 data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req) as resp:
            try:
                payload = json.loads(resp.read().decode())
            except ValueError:
                payload = resp.read().decode()
            return resp.status, payload, dict(resp.headers)
    except urllib.error.HTTPError as e:
        payload = json.loads(e.read().decode())
        return e.code, payload, dict(e.headers)


def _quick_manager():
    def _factory(kind, config):
        def runner(stop, session):
            session.progress = {"done": 1, "total": 1, "rate": 1.0,
                                "last_index": 0}
            time.sleep(0.02)
            return {"batch_id": "test", "completed": 1, "skipped": 0,
                    "cancelled": False}
        return runner
    return SessionManager(runner_factory=_factory)


def _service_with(router, cors_origins=("http://localhost:5173",),
                  tmp_path=None, discovery=None):
    discovery = discovery or "local/_test.ports.json"
    return Service(discovery=discovery, router=router,
                   cors_origins=cors_origins)


def test_cors_allowed_origin(tmp_path):
    svc = Service(discovery=str(tmp_path / "p.json"),
                  cors_origins=("http://localhost:5173",))
    svc.start()
    try:
        status, body, headers = _request(svc.ports["http"], "/health",
                                         origin="http://localhost:5173")
        assert status == 200
        assert headers.get("Access-Control-Allow-Origin") == "http://localhost:5173"
    finally:
        svc.stop()


def test_cors_blocked_origin(tmp_path):
    svc = Service(discovery=str(tmp_path / "p.json"),
                  cors_origins=("http://localhost:5173",))
    svc.start()
    try:
        status, body, headers = _request(svc.ports["http"], "/health",
                                         origin="http://evil.example")
        assert status == 200
        assert headers.get("Access-Control-Allow-Origin") is None
    finally:
        svc.stop()


def test_cors_preflight(tmp_path):
    svc = Service(discovery=str(tmp_path / "p.json"),
                  cors_origins=("http://localhost:5173",))
    svc.start()
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{svc.ports['http']}/api/sessions", method="OPTIONS",
            headers={"Origin": "http://localhost:5173",
                     "Access-Control-Request-Method": "POST"})
        try:
            with urllib.request.urlopen(req) as resp:
                assert resp.status in (204, 200)
                assert resp.headers.get("Access-Control-Allow-Origin") == \
                    "http://localhost:5173"
                assert "POST" in resp.headers.get("Access-Control-Allow-Methods", "")
        except urllib.error.HTTPError as e:
            assert e.code in (204, 200)
    finally:
        svc.stop()


def test_session_api_lifecycle(tmp_path):
    router = Router()
    for route in session_router(_quick_manager()).get_all():
        router.add(*route)
    svc = Service(discovery=str(tmp_path / "p.json"), router=router,
                  cors_origins=("http://localhost:5173",))
    svc.start()
    try:
        port = svc.ports["http"]
        status, sess, _ = _request(port, "/api/sessions", method="POST",
                                   body={"kind": "arena",
                                         "config": {"n_games": 2}},
                                   origin="http://localhost:5173")
        assert status == 200 and sess["status"] == "running"
        sid = sess["id"]

        deadline = time.time() + 5
        status = None
        while time.time() < deadline:
            status, sess2, _ = _request(port, f"/api/sessions/{sid}")
            if sess2["status"] != "running":
                break
            time.sleep(0.02)
        assert sess2["status"] == "finished"
        assert sess2["result"]["completed"] == 1
        assert sess2["progress"] is not None

        _, lst, _ = _request(port, "/api/sessions")
        assert any(s["id"] == sid for s in lst["sessions"])

        status, after_stop, _ = _request(port, f"/api/sessions/{sid}/stop",
                                         method="POST")
        assert status == 200 and after_stop["status"] == "finished"
    finally:
        svc.stop()


def test_arena_manager_rejects_non_arena():
    mgr = make_arena_session_manager()
    with pytest.raises(ValidationError):
        mgr.create("match", {})


def test_normalize_seats_defaults():
    cfg = _normalize_seats({})
    assert len(cfg["seats"]) == 4
    assert cfg["seats"] == DEFAULT_SEATS
    assert cfg["n_games"] == 16 and cfg["concurrency"] == 4
    with pytest.raises(ValidationError):
        _normalize_seats({"seats": [{"strategy": "random"}]})