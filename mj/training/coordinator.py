"""Coordinator:分布式训练的权威调度器(HTTP + SQLite JobStore)。

职责边界:
- 只做调度的"权威状态源"(登记 worker、派发 job、收租约心跳、收结果);
- 不执行任何 job、不做模型推理、不做 merge 计算;
- 结果只接受已提交的 JobResultManifest;merge 的校验属于 learner/merge 侧。

进程内(测试)直接调 ``Coordinator`` 方法;真实双机用 ``CoordinatorServer``
挂一个轻量 ``ThreadingHTTPServer``,client 在 :mod:`~.worker_runtime`。

安全:生产可传 ``token`` 做 Bearer 校验;未配则仅限可信局域网(默认绑定
127.0.0.1,脚本显式 ``--bind 0.0.0.0``)。
"""

from __future__ import annotations

import json
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .distributed_jobs import JOB_FAILED, JOB_PENDING, JOB_SUCCEEDED, \
    WorkerCapabilities, JobSpec
from .job_store import JobStore

__all__ = ["Coordinator", "CoordinatorServer", "CoordinatorClient",
           "CoordinatorApiError"]


class Coordinator:
    """进程内调度核心(无网络依赖,REST 层薄封装在它之上)。"""

    def __init__(self, store: JobStore):
        self.store = store
        self.workers = {}
        self.heartbeats = {}   # job_id -> (worker_id, last)
        self._lock = threading.Lock()

    # -- campaign/job enqueue ----------------------------------------------

    def create_campaign(self, campaign: dict) -> dict:
        cid = campaign["campaign_id"]
        self.store.put_campaign(cid, campaign)
        return {"campaign_id": cid, "ok": True}

    def enqueue(self, campaign_id: str, specs) -> dict:
        self.store.add_jobs(specs)
        return {"campaign_id": campaign_id, "added": [s.job_id for s in specs]}

    # -- worker API ---------------------------------------------------------

    def register(self, caps: WorkerCapabilities) -> dict:
        self.store.upsert_worker(caps)
        return {"worker_id": caps.worker_id, "ok": True}

    def lease(self, worker_id: str, *, generation: int | None = None,
              kinds=None) -> dict | None:
        picked = self.store.lease_next_job(
            worker_id, generation=generation, kinds=kinds)
        if picked is None:
            return None
        spec, lease = picked
        with self._lock:
            self.heartbeats[spec.job_id] = (worker_id, time.time())
        return {
            "job": spec.payload_dict(),
            "lease": {
                "job_id": lease.job_id, "worker_id": lease.worker_id,
                "expires_at": lease.expires_at, "attempt": lease.attempt,
            },
        }

    def heartbeat(self, job_id: str, worker_id: str) -> bool:
        ok = self.store.heartbeat(job_id, worker_id)
        if ok:
            with self._lock:
                self.heartbeats[job_id] = (worker_id, time.time())
        return ok

    def complete(self, job_id: str, worker_id: str,
                 result: dict) -> dict:
        # result 已是 worker 本地校验过的发布 manifest;complete 只负责把
        # 持有者作业标记 SUCCEEDED 并归档 manifest。merge 侧对磁盘
        # manifest.json 做严格 contract 校验(而非在此重建 dataclass)。
        ok = self.store.complete(job_id, worker_id, result)
        return {"job_id": job_id, "ok": ok}

    def fail(self, job_id: str, worker_id: str, error: str) -> dict:
        target = self.store.fail(job_id, worker_id, error)
        return {"job_id": job_id, "target": target}

    # -- admin --------------------------------------------------------------

    def reap(self) -> int:
        return self.store.reap_expired()

    def campaign_status(self, campaign_id: str) -> dict:
        campaign = self.store.get_campaign(campaign_id)
        stats = self.store.stats(campaign_id)
        jobs = self.store.list_jobs(campaign_id)
        return {
            "campaign": campaign,
            "stats": stats,
            "jobs": [j.payload_dict() for j in jobs],
        }


def _json(resp):
    return json.dumps(resp, ensure_ascii=False).encode("utf-8")


class _Handler(BaseHTTPRequestHandler):
    coordinator = None
    token = None

    def _auth(self) -> bool:
        if not self.token:
            return True
        got = self.headers.get("Authorization", "")
        return got == f"Bearer {self.token}"

    def _reply(self, status, body):
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            return json.loads(raw.decode("utf-8"))
        except ValueError:
            return {}

    def _handle(self):
        if not self._auth():
            self._reply(401, _json({"error": "UNAUTHORIZED"}))
            return
        parsed = urllib.parse.urlparse(self.path)
        path, method, body = parsed.path, self.command, self._read_json()
        c = self.coordinator
        try:
            if path == "/health" and method == "GET":
                self._reply(200, _json({"status": "ok"}))
            elif path == "/workers/register" and method == "POST":
                self._reply(200, _json(c.register(WorkerCapabilities(**body))))
            elif path == "/jobs/lease" and method == "POST":
                res = c.lease(body.get("worker_id"),
                              generation=body.get("generation"),
                              kinds=body.get("kinds"))
                if res is None:
                    self._reply(200, _json({"job": None}))
                else:
                    self._reply(200, _json(res))
            elif path == "/jobs/heartbeat" and method == "POST":
                self._reply(200, _json(
                    {"ok": c.heartbeat(body["job_id"], body["worker_id"])}))
            elif path == "/jobs/complete" and method == "POST":
                self._reply(200, _json(c.complete(
                    body["job_id"], body["worker_id"], body.get("result"))))
            elif path == "/jobs/fail" and method == "POST":
                self._reply(200, _json(
                    c.fail(body["job_id"], body["worker_id"],
                           str(body.get("error") or "error"))))
            elif path.startswith("/campaigns/") and method == "GET":
                cid = urllib.parse.unquote(path[len("/campaigns/"):])
                self._reply(200, _json(c.campaign_status(cid)))
            elif path == "/campaigns" and method == "POST":
                self._reply(200, _json(c.create_campaign(body)))
            else:
                self._reply(404, _json({"error": "NOT_FOUND"}))
        except KeyError as exc:
            self._reply(400, _json({"error": "BAD_REQUEST",
                                    "message": str(exc)}))
        except Exception as exc:  # noqa: BLE001 - boundary JSON
            self._reply(500, _json({"error": "INTERNAL",
                                    "message": f"{type(exc).__name__}: {exc}"}))

    def log_message(self, *args):
        pass

    def do_GET(self):
        self._handle()

    def do_POST(self):
        self._handle()


class CoordinatorServer:
    """把 Coordinator 暴露为局域网 HTTP;``coordinator`` 真实双机部署用。"""

    def __init__(self, coordinator, *, host="127.0.0.1", port=0, token=None):
        self.coordinator = coordinator
        _Handler.coordinator = coordinator
        _Handler.token = token or None
        self.host = host
        self.port = port
        self.token = token
        self.server = ThreadingHTTPServer((host, port), _Handler)
        self.port = self.server.server_address[1]
        self._thread = None

    @property
    def url(self):
        return f"http://{self.host}:{self.port}"

    def start(self):
        self._thread = threading.Thread(target=self.server.serve_forever,
                                        name="coordinator-http", daemon=True)
        self._thread.start()
        return self

    def stop(self):
        try:
            self.server.shutdown()
        except OSError:
            pass
        self.server.server_close()


class CoordinatorApiError(RuntimeError):
    """coordinator HTTP 非 2xx/网络错误。"""


class CoordinatorClient:
    """Worker 侧 HTTP client;``coordinator`` 调用统一返回 JSON body(dict),
    非 2xx 抛 :class:`CoordinatorApiError`,以便 WorkerSupervisor 对进程内
    Coordinator 与 HTTP CoordinatorClient 使用同一接口形状。"""

    def __init__(self, base_url: str, token: str | None = None,
                 timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def _call(self, method, path, body=None):
        import urllib.request
        data = json.dumps(body or {}).encode("utf-8") if body is not None \
            else None
        req = urllib.request.Request(
            self.base_url + path, data=data, method=method,
            headers={"Content-Type": "application/json"})
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
                if not resp.status == 200:
                    raise CoordinatorApiError(
                        f"coordinator {resp.status} on {path}")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8")
            raise CoordinatorApiError(f"coordinator {e.code} on {path}: {raw}")
        except urllib.error.URLError as e:
            raise CoordinatorApiError(
                f"coordinator network error on {path}: {e.reason}")

    def register(self, caps: WorkerCapabilities):
        return self._call("POST", "/workers/register", body=caps.payload())

    def lease(self, worker_id, generation=None, kinds=None):
        return self._call(
            "POST", "/jobs/lease",
            body={"worker_id": worker_id, "generation": generation,
                  "kinds": list(kinds or [])})

    def heartbeat(self, job_id, worker_id):
        return self._call("POST", "/jobs/heartbeat",
                          body={"job_id": job_id, "worker_id": worker_id})

    def complete(self, job_id, worker_id, result):
        return self._call("POST", "/jobs/complete",
                          body={"job_id": job_id, "worker_id": worker_id,
                                "result": result})

    def fail(self, job_id, worker_id, error):
        return self._call("POST", "/jobs/fail",
                          body={"job_id": job_id, "worker_id": worker_id,
                                "error": error})

    def campaign(self, campaign_id):
        return self._call("GET",
                          f"/campaigns/{urllib.parse.quote(campaign_id)}")