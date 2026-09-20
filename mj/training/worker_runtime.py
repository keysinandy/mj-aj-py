"""Worker runtime:PC-A/PC-B 的计算 worker supervisor。

职责:
- 启动时向 coordinator register 能力(roles/capability);
- 循环 lease → 按 kind 分派到本地 handler → 本地 stage → 不可变发布 →
  complete;期间后台线程 heartbeat 续租;
- 失败按 job state machine 报 fail(attempt 内回 PENDING 重领);
- 后端可切换:真实双机用 HTTP CoordinatorClient;单机/测试用进程内 Coordinator。

v1 关键约束:
- PC-A 默认 ``gpu_training=true``(唯一 learner 侧);PC-B RX 6600 注册
  ``gpu_training=false``,绝不分配梯度更新;
- PC-B 每个 rollout 进程 ``torch.set_num_threads(1)``,避免 N×cores 线程爆炸。
"""

from __future__ import annotations

import importlib
import os
import threading
import time

from .artifact_store import publish_result, sha256_file
from .distributed_jobs import WorkerCapabilities
from .distributed_bc import HANDLERS

__all__ = ["HANDLERS", "WorkerSupervisor", "run_worker_loop"]


class WorkerSupervisor:
    def __init__(self, coordinator, caps: WorkerCapabilities, *,
                 cache_dir: str, result_root: str, git_commit: str,
                 heartbeat_every: float = 2.0, max_idle: float = 0.2,
                 generation: int | None = None, kinds=None):
        self.coordinator = coordinator
        self.caps = caps
        self.cache_dir = cache_dir
        self.result_root = result_root
        self.git_commit = git_commit
        self.heartbeat_every = heartbeat_every
        self.max_idle = max_idle
        self.generation = generation
        self.kinds = kinds
        self._hb_stop = threading.Event()
        self._active: str | None = None
        self._hb = None

    def _register(self):
        payload = WorkerCapabilities(
            worker_id=self.caps.worker_id, git_commit=self.git_commit,
            hostname=self.caps.hostname, cpu_logical=self.caps.cpu_logical,
            ram_gb=self.caps.ram_gb, gpu_vendor=self.caps.gpu_vendor,
            gpu_model=self.caps.gpu_model, gpu_vram_gb=self.caps.gpu_vram_gb,
            gpu_training=self.caps.gpu_training,
            roles=tuple(self.caps.roles),
            max_parallel=dict(self.caps.max_parallel))
        self.coordinator.register(payload)

    def _lease(self):
        res = self.coordinator.lease(self.caps.worker_id,
                                     generation=self.generation,
                                     kinds=self.kinds)
        if res is None or not res.get("job"):
            return None
        return res["job"]

    def _execute(self, job: dict) -> dict:
        kind = job["kind"]
        handler = HANDLERS.get(kind)
        if handler is None:
            raise ValueError(f"no handler for job kind {kind!r}")
        return handler(job, cache_dir=self.cache_dir) or {}

    def _publish(self, job: dict, local_dir: str, meta: dict) -> dict:
        """把本地 stage 的 rollout.npz + manifest 发布到共享结果区。"""
        npz = os.path.join(local_dir, "rollout.npz")
        if not os.path.exists(npz):
            raise FileNotFoundError(f"missing staged artifact {npz}")
        manifest = {
            "schema": "minisuphx-job-result-v1",
            "job_id": job["job_id"],
            "campaign_id": job["campaign_id"],
            "worker_id": self.caps.worker_id,
            "git_commit": self.git_commit,
            "kind": job["kind"],
            "generation": job["generation"],
            "artifact_relpath": "rollout.npz",
            "artifact_sha256": sha256_file(npz),
            "rows": int(meta.get("transition_count", os.path.getsize(npz))),
            "status": "SUCCEEDED",
        }
        manifest.update(meta)
        publish_result(self.result_root, job["campaign_id"], job["job_id"],
                       files={npz: "rollout.npz"}, manifest=manifest)
        return manifest

    def run_once(self) -> int:
        """执行最多一个已租约 job;返回执行数量(0=无 job 可领)。"""
        job = self._lease()
        if job is None:
            return 0
        job_id = job["job_id"]
        self._active = job_id
        try:
            local_dir = f"{self.cache_dir}/{job_id}"
            os.makedirs(local_dir, exist_ok=True)
            meta = self._execute(job)
            manifest = self._publish(job, local_dir, meta)
            self.coordinator.complete(job_id, self.caps.worker_id, manifest)
            return 1
        except Exception as exc:  # noqa: BLE001 - report worker failure
            self.coordinator.fail(job_id, self.caps.worker_id,
                                  f"{type(exc).__name__}: {exc}")
            return 1
        finally:
            self._active = None

    # -- heartbeat:run_forever 期间对当前活跃 job 续租 ----------------

    def _start_heartbeat(self):
        self._hb_stop.clear()

        def loop():
            while not self._hb_stop.is_set():
                time.sleep(self.heartbeat_every)
                active = self._active
                if active is not None:
                    self.coordinator.heartbeat(active, self.caps.worker_id)

        self._hb = threading.Thread(target=loop, daemon=True,
                                    name="worker-heartbeat")
        self._hb.start()

    def _stop_heartbeat(self):
        self._hb_stop.set()
        if self._hb is not None:
            self._hb.join(timeout=self.heartbeat_every + 1)
            self._hb = None

    def run_forever(self, *, initial: bool = True):
        if initial:
            self._register()
        self._start_heartbeat()
        try:
            while not self._hb_stop.is_set():
                if self.run_once() == 0:
                    time.sleep(self.max_idle)
        finally:
            self._stop_heartbeat()


def run_worker_loop(coordinator, caps: WorkerCapabilities, *, cache_dir,
                    result_root, git_commit, **kw):
    WorkerSupervisor(coordinator, caps, cache_dir=cache_dir,
                     result_root=result_root, git_commit=git_commit,
                     **kw).run_forever()