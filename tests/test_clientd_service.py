"""Task 1.1 验收:clientd 服务骨架(健康端点/结构化 404/优雅停机)。

起真实服务线程(HTTP + WS,随机端口),用 urllib 查询,断言:
- GET /health → 200 {"status":"ok"};
- 未知路由 → 结构化 JSON 404(非 HTML);
- stop() 后端口可被重新绑定(监听 socket 已释放),发现文件被清理。
"""

import json
import os
import socket
import urllib.request

import pytest

from mj.clientd.errors import ValidationError
from mj.clientd.service import Router, Service


@pytest.fixture
def service(tmp_path):
    discovery = str(tmp_path / "clientd.ports.json")

    def _build():
        return Service(discovery=discovery)

    return _build, discovery


def _get(port, path):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}") as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def _post(port, path, body):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", data=data,
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def _test_client():
    from websockets.sync import client as wssync
    return wssync


def _assert_free(host, port):
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind((host, port))
    finally:
        s.close()


def test_health_and_discovery(service):
    build, discovery = service
    svc = build()
    svc.start()
    try:
        assert svc.running
        assert os.path.exists(discovery)
        with open(discovery, encoding="utf-8") as f:
            recorded = json.load(f)
        assert recorded["http"] == svc.ports["http"]
        assert recorded["ws"] == svc.ports["ws"]
        status, body = _get(svc.ports["http"], "/health")
        assert status == 200 and body == {"status": "ok"}
    finally:
        svc.stop()
    assert svc.running is False


def test_unknown_route_is_structured_json_404(service):
    build, _ = build, discovery = service
    svc = build()
    svc.start()
    try:
        status, body = _get(svc.ports["http"], "/does/not/exist")
        assert status == 404
        assert body["error"] == "NOT_FOUND"
        assert "message" in body
        status, body = _post(svc.ports["http"], "/health", {"x": 1})
        assert status == 404 and body["error"] == "NOT_FOUND"
    finally:
        svc.stop()


def test_custom_route_and_validation_error(service):
    build, discovery = service
    router = Router()

    @router.post("/echo")
    def _echo(request):
        return 200, {"got": request.body, "params": dict(request.params)}

    @router.get("/boom")
    def _boom(request):
        raise ValidationError("bad input")

    svc = Service(discovery=discovery, router=router)
    svc.start()
    try:
        status, body = _post(svc.ports["http"], "/echo", {"a": 1})
        assert status == 200 and body["got"] == {"a": 1}
        status, body = _get(svc.ports["http"], "/boom")
        assert status == 400 and body["error"] == "VALIDATION"
    finally:
        svc.stop()


def test_graceful_stop_releases_ports(service):
    build, discovery = service
    svc = build()
    svc.start()
    http_port, ws_port = svc.ports["http"], svc.ports["ws"]
    svc.stop()
    # 端口已释放:可立即重新绑定
    _assert_free("127.0.0.1", http_port)
    _assert_free("127.0.0.1", ws_port)
    assert not os.path.exists(discovery)


def test_ws_hello(service):
    build, discovery = service
    svc = build()
    svc.start()
    try:
        ws_t = svc.ports["ws"]
        import sys
        from websockets.sync import client as c
        with c.connect(f"ws://127.0.0.1:{ws_t}") as conn:
            hello = json.loads(conn.recv())
            assert hello.get("type") == "hello"
    finally:
        svc.stop()


def test_running_flag_idempotent(service):
    build, discovery = service
    svc = build()
    svc.start()
    svc.start()  # 幂等:不重复占用
    try:
        status, _ = _get(svc.ports["http"], "/health")
        assert status == 200
    finally:
        svc.stop()