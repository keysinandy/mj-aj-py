"""会话管理器:创建/查询/停止本地批次、线上对弈等长任务会话。

一个 Session 封装一段长运行工作(runner 可调用对象),带:
- 状态机 created → running → finished | cancelled | error;
- stop_event(线程安全);runner 在局边界检查它实现"局边界取消";
- 并发会话上限:运行中会话数超限时 create 抛 ConflictError;
- 幂等 stop:对已结束会话重复 stop 为无操作;
- 错误信息透出:runner 抛异常 → error 状态并保存消息,list/get 可见。

runner 协议:runner_factory(session) -> callable(stop, session) -> result
取消约定:runner 正常返回时,若 stop 已被请求则归为 cancelled,否则 finished;
runner 抛异常归为 error。会话线程为 daemon,进程退出不阻塞。
"""

from __future__ import annotations

import threading
import time
import uuid

from .errors import ConflictError, NotFoundError, ValidationError

__all__ = ["Session", "SessionManager", "KINDS"]

STATUS_CREATED = "created"
STATUS_RUNNING = "running"
STATUS_FINISHED = "finished"
STATUS_CANCELLED = "cancelled"
STATUS_ERROR = "error"

KINDS = ("arena", "match", "tournament", "test")


class Session:
    def __init__(self, kind, config, runner):
        if kind not in KINDS:
            raise ValidationError(
                f"unknown session kind {kind!r}; expected {list(KINDS)}")
        self.id = uuid.uuid4().hex[:12]
        self.kind = kind
        self.config = config or {}
        self.status = STATUS_CREATED
        self.created_at = time.time()
        self.finished_at = None
        self.result = None
        self.error = None
        self.progress = None       # 长任务可在运行期更新(由 runner 写入)
        self.stop_event = threading.Event()
        self._runner = runner

    @property
    def stop_requested(self):
        return self.stop_event.is_set()

    def as_dict(self):
        return {
            "id": self.id, "kind": self.kind, "status": self.status,
            "config": self.config, "created_at": round(self.created_at, 3),
            "finished_at": round(self.finished_at, 3) if self.finished_at
            else None,
            "result": self.result, "error": self.error,
            "progress": self.progress,
        }


class SessionManager:
    def __init__(self, max_concurrent=16, runner_factory=None, lock_factory=None):
        self.max_concurrent = max_concurrent
        self.runner_factory = runner_factory
        self._sessions = {}
        self._lock = threading.Lock()

    def create(self, kind, config=None):
        """创建并异步启动一个会话;并发超限抛 ConflictError。"""
        with self._lock:
            running = sum(1 for s in self._sessions.values()
                          if s.status == STATUS_RUNNING)
            if running >= self.max_concurrent:
                raise ConflictError(
                    f"too many concurrent sessions ({running}/{self.max_concurrent})")
            if self.runner_factory is None:
                raise ValidationError("session runner_factory not configured")
            runner = self.runner_factory(kind, config or {})
            session = Session(kind, config or {}, runner)
            self._sessions[session.id] = session
            session.status = STATUS_RUNNING
        thread = threading.Thread(target=self._run, args=(session.id,),
                                  name=f"clientd-session-{session.id}",
                                  daemon=True)
        thread.start()
        return session

    def _run(self, sid):
        session = self._sessions[sid]
        try:
            result = session._runner(
                lambda: session.stop_requested, session)
            session.result = result
            session.status = (STATUS_CANCELLED if session.stop_requested
                              else STATUS_FINISHED)
        except Exception as exc:  # noqa: BLE001 - capture any runner failure
            session.error = f"{type(exc).__name__}: {exc}"
            session.status = STATUS_ERROR
        finally:
            session.finished_at = time.time()

    def get(self, sid):
        sess = self._sessions.get(sid)
        if sess is None:
            raise NotFoundError(f"session {sid!r} not found")
        return sess

    def stop(self, sid):
        sess = self._sessions.get(sid)
        if sess is None:
            raise NotFoundError(f"session {sid!r} not found")
        # 幂等:对已终止会话置 stop event 也不改变其结果
        sess.stop_event.set()
        return sess

    def list(self):
        return [s.as_dict() for s in self._sessions.values()]