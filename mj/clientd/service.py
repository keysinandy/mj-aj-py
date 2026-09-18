"""clientd 本地服务:标准库 HTTP 控制面 + websockets WS 数据面。

进程内同时跑两个监听(分处线程 / asyncio 事件循环):
- HTTP 控制面  http.server.ThreadingHTTPServer,REST(健康检查/会话等);
- WS 数据面    websockets,推送房间流/批次进度流/日志流。

服务启动后把实际端口写入 local/clientd.ports.json 供壳(Tauri sidecar)
发现。**stop()** 优雅停机:停 HTTP accept、关 WS 循环、释放监听端口。
端口=0 表示占用系统空闲端口,实际端口回写 self.ports 与发现文件。
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .errors import ClientdError, NotFoundError

__all__ = ["Router", "Request", "ControlServer", "WsServer", "Service",
           "health_router", "make_ws_handler"]


def _json_response(payload):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return body, "application/json; charset=utf-8"


class Request:
    """一次 HTTP 请求的解析视图。"""

    def __init__(self, method, path, body=None, headers=None):
        self.method = method
        self.path = path
        self.body = body
        self.headers = headers or {}
        self.params = {}


class Router:
    """轻量精确/参数化路由;未知路径抛 NotFoundError。"""

    def __init__(self):
        self._routes = []  # (method, segments, handler)

    def add(self, method, path, handler):
        segments = tuple(p for p in path.split("/") if p)
        self._routes.append((method, segments, handler))
        return handler

    def get(self, path):
        def deco(fn):
            self.add("GET", path, fn)
            return fn
        return deco

    def post(self, path):
        def deco(fn):
            self.add("POST", path, fn)
            return fn
        return deco

    def delete(self, path):
        def deco(fn):
            self.add("DELETE", path, fn)
            return fn
        return deco

    def get_all(self):
        """返回已注册路由列表 (method, path_segments, handler),供合并。"""
        return [(m, "/" + "/".join(seg) if seg else "/", h)
                for m, seg, h in self._routes]

    def dispatch(self, request):
        raw_path = request.path.split("?")[0]
        segments = tuple(p for p in raw_path.split("/") if p)
        for method, wanted, handler in self._routes:
            if method != request.method or len(wanted) != len(segments):
                continue
            params = {}
            match = True
            for want, have in zip(wanted, segments):
                if want.startswith(":"):
                    params[want[1:]] = have
                elif want != have:
                    match = False
                    break
            if not match:
                continue
            request.params.update(params)
            status, payload = handler(request)
            return status, payload
        raise NotFoundError(f"no route for {request.method} {raw_path}")


class _ControlHandler(BaseHTTPRequestHandler):
    router = None

    def _send(self, status, payload):
        body, ctype = _json_response(payload)
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _handle(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        raw = self.rfile.read(length) if length else b""
        body = None
        if raw:
            try:
                body = json.loads(raw.decode("utf-8"))
            except ValueError as exc:
                self._send(400, {"error": "BAD_JSON",
                                 "message": f"invalid json body: {exc}"})
                return
        request = Request(self.command, self.path, body=body,
                          headers={k: v for k, v in self.headers.items()})
        try:
            status, payload = self.router.dispatch(request)
        except NotFoundError:
            status, payload = (404, {"error": "NOT_FOUND",
                                     "message": f"no route for "
                                                f"{self.command} {self.path}"})
        except ClientdError as exc:
            status, payload = exc.status, {"error": exc.code,
                                            "message": str(exc)}
        except Exception as exc:  # noqa: BLE001 - boundary converts to JSON
            status, payload = (500, {"error": "INTERNAL",
                                     "message": f"{type(exc).__name__}: {exc}"})
        self._send(status, payload)

    def do_GET(self):
        self._handle()

    def do_POST(self):
        self._handle()

    def do_DELETE(self):
        self._handle()

    def log_message(self, *args):
        pass


class ControlServer:
    """HTTP 控制面服务器(线程内 serve_forever)。"""

    def __init__(self, host, router, port=0):
        self.host = host
        self.port = port
        self.router = router
        _ControlHandler.router = router
        self.server = ThreadingHTTPServer((host, port), _ControlHandler)
        self.port = self.server.server_address[1]
        self._thread = None

    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    def start(self):
        self._thread = threading.Thread(target=self.server.serve_forever,
                                        name="clientd-http", daemon=True)
        self._thread.start()

    def stop(self):
        if self.server is None:
            return
        try:
            self.server.shutdown()
        except OSError:
            pass
        self.server.server_close()
        self.server = None


def make_ws_handler(on_message=None, on_open=None):
    """构造默认的 WS handler:握手后发 hello,回显字符串消息。"""
    async def _handle(websocket):
        if on_open is not None:
            try:
                await on_open(websocket)
            except Exception:  # noqa: BLE001 - a bad open hook must not kill loop
                pass
        try:
            await websocket.send(json.dumps({"type": "hello"}, ensure_ascii=False))
            async for message in websocket:
                if on_message is None:
                    await websocket.send(message)
                else:
                    try:
                        payload = json.loads(message)
                    except ValueError:
                        payload = {"raw": message}
                    await on_message(websocket, payload)
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            return

    return _handle


class WsServer:
    """WS 数据面服务器(asyncio,in 后台线程)。"""

    def __init__(self, host, handler, port=0):
        self.host = host
        self.port = port
        self.handler = handler
        self._loop = None
        self._thread = None
        self._stop_wait = None
        self._server = None

    def start(self):
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, name="clientd-ws",
                                        daemon=True)
        self._thread.start()

    def _run(self):
        import websockets.asyncio.server as wsserver  # lazy: optional dep
        self._stop_wait = self._loop.create_future()

        async def _amain():
            async with wsserver.serve(self.handler, self.host, self.port) as server:
                self._server = server
                sock = server.sockets[0] if server.sockets else None
                if sock is not None:
                    self.port = sock.getsockname()[1]
                await self._stop_wait

        self._loop.run_until_complete(_amain())

    def _set_stop(self):
        if self._stop_wait is not None and not self._stop_wait.done():
            self._stop_wait.set_result(None)

    def stop(self):
        if self._loop is None:
            return
        try:
            self._loop.call_soon_threadsafe(self._set_stop)
        except RuntimeError:
            pass
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._loop = None
        self._thread = None
        self._server = None


# ---------------------------------------------------------------------------
# Service: 编排 HTTP + WS
# ---------------------------------------------------------------------------

DEFAULT_DISCOVERY = "local/clientd.ports.json"


def health_router():
    router = Router()

    @router.get("/health")
    def _health(request):
        return 200, {"status": "ok"}

    return router


class Service:
    """启动/停止 HTTP 与 WS,并写端口发现文件。"""

    def __init__(self, host="127.0.0.1", http_port=0, ws_port=0,
                 discovery=DEFAULT_DISCOVERY, router=None, ws_handler=None,
                 on_message=None, on_open=None):
        self.host = host
        self.http_port = http_port
        self.ws_port = ws_port
        self.discovery = discovery
        self.router = router if router is not None else health_router()
        if ws_handler is None:
            ws_handler = make_ws_handler(on_message=on_message, on_open=on_open)
        self.ws_handler = ws_handler
        self.http = None
        self.ws = None
        self.ports = {"http": http_port, "ws": ws_port, "pid": os.getpid()}
        self._running = False

    def start(self):
        if self._running:
            return
        self.ws = WsServer(self.host, self.ws_handler, self.ws_port)
        self.ws.start()
        self.http = ControlServer(self.host, self.router, self.http_port)
        self.http.start()
        self.ports["http"] = self.http.port
        self.ports["ws"] = self.ws.port
        if self.discovery:
            self._write_discovery()
        self._running = True

    def _write_discovery(self):
        d = os.path.dirname(self.discovery)
        if d:
            os.makedirs(d, exist_ok=True)
        tmp = self.discovery + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.ports, f, ensure_ascii=False)
        os.replace(tmp, self.discovery)

    def stop(self):
        if not self._running:
            return
        self._running = False
        try:
            if self.http is not None:
                self.http.stop()
        finally:
            if self.ws is not None:
                self.ws.stop()
        self.http = None
        self.ws = None
        if self.discovery and os.path.exists(self.discovery):
            try:
                os.remove(self.discovery)
            except OSError:
                pass

    @property
    def running(self):
        return self._running