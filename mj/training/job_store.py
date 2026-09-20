"""SQLite job store:分布式调度的权威状态源。

单一进程(coordinator)持有 DB 的写权。关键不变量:

- ``lease_next_job`` 原子地从 PENDING 领走一个 job(→RUNNING),
  同一时刻只有一份 lease(单 worker 单 attempt);
- worker crash ⇒ lease 过期 ⇒ ``reap_expired`` 把 RUNNING 回 PENDING(attempt+1);
- 同一 job_id 幂等:重试复用原 job_id,不产生重复语义 job;
- 新 job 只能追加,不能改/删。
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time

from .distributed_jobs import (
    JOB_FAILED, JOB_PENDING, JOB_RUNNING, JOB_SUCCEEDED, JobLease, JobSpec,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS workers (
    worker_id TEXT PRIMARY KEY,
    json TEXT NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS campaigns (
    campaign_id TEXT PRIMARY KEY,
    json TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    json TEXT NOT NULL,
    status TEXT NOT NULL,
    attempt INTEGER NOT NULL DEFAULT 0,
    lease_owner TEXT,
    lease_expires_at REAL,
    last_heartbeat_at REAL,
    result_json TEXT,
    last_error TEXT,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_campaign ON jobs(campaign_id);
"""

__all__ = ["JobStore"]


class JobStore:
    def __init__(self, db: str, *, expiry_seconds: float = 300.0,
                 heartbeat_every: float = 30.0):
        self.db = str(db)
        self.expiry_seconds = float(expiry_seconds)
        self.heartbeat_every = float(heartbeat_every)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.db, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self):
        try:
            self._conn.close()
        except sqlite3.Error:
            pass

    def _execute(self, sql, params=()):
        cur = self._conn.execute(sql, params)
        self._conn.commit()
        return cur

    # -- campaigns / workers -------------------------------------------------

    def put_campaign(self, campaign_id: str, campaign_json: dict):
        with self._lock:
            self._execute(
                "INSERT OR REPLACE INTO campaigns(campaign_id, json, created_at)"
                " VALUES(?,?,?)",
                (campaign_id, json.dumps(campaign_json, ensure_ascii=False),
                 time.time()))

    def get_campaign(self, campaign_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT json FROM campaigns WHERE campaign_id=?",
                (campaign_id,)).fetchone()
            return json.loads(row["json"]) if row else None

    def upsert_worker(self, worker):
        with self._lock:
            self._execute(
                "INSERT OR REPLACE INTO workers(worker_id, json, updated_at)"
                " VALUES(?,?,?)",
                (worker.worker_id,
                 json.dumps(worker.payload(), ensure_ascii=False), time.time()))

    # -- jobs ----------------------------------------------------------------

    def add_jobs(self, specs):
        """幂等追加 job。已存在 job_id 则跳过(不覆盖)。"""
        with self._lock:
            now = time.time()
            for spec in specs:
                self._execute(
                    "INSERT OR IGNORE INTO jobs"
                    " (job_id, campaign_id, kind, json, status, attempt,"
                    "  created_at) VALUES(?,?,?,?,?,0,?)",
                    (spec.job_id, spec.campaign_id, spec.kind,
                     json.dumps(spec.payload_dict(), ensure_ascii=False),
                     JOB_PENDING, now))

    def list_jobs(self, campaign_id: str | None = None,
                  status: str | None = None) -> "list[JobSpec]":
        with self._lock:
            sql = "SELECT json FROM jobs WHERE 1=1"
            params = []
            if campaign_id:
                sql += " AND campaign_id=?"
                params.append(campaign_id)
            if status:
                sql += " AND status=?"
                params.append(status)
            sql += " ORDER BY created_at, job_id"
            rows = self._conn.execute(sql, params).fetchall()
            return [JobSpec(**json.loads(r["json"])) for r in rows]

    def lease_next_job(self, worker_id: str, *,
                       generation: int | None = None,
                       kinds=None) -> "tuple[JobSpec, JobLease] | None":
        """原子领走一个 PENDING job;返回 (spec, lease),无则 None。"""
        with self._lock:
            now = time.time()
            rows = self._conn.execute(
                "SELECT job_id, json FROM jobs WHERE status=? "
                " ORDER BY created_at, job_id",
                (JOB_PENDING,)).fetchall()
            for row in rows:
                spec = JobSpec(**json.loads(row["json"]))
                if kinds and spec.kind not in kinds:
                    continue
                if generation is not None and spec.generation != generation:
                    continue
                expires = now + self.expiry_seconds
                cur = self._conn.execute(
                    "UPDATE jobs SET status=?, attempt=attempt+1, lease_owner=?,"
                    " lease_expires_at=?, last_heartbeat_at=? "
                    " WHERE job_id=? AND status=?",
                    (JOB_RUNNING, worker_id, expires, now, spec.job_id,
                     JOB_PENDING))
                if cur.rowcount == 1:
                    self._conn.commit()
                    return spec, JobLease(spec.job_id, worker_id, expires,
                                          int(spec.attempt_limit))
            return None

    def heartbeat(self, job_id: str, worker_id: str) -> bool:
        with self._lock:
            now = time.time()
            cur = self._conn.execute(
                "UPDATE jobs SET last_heartbeat_at=?, lease_expires_at=? "
                " WHERE job_id=? AND lease_owner=? AND status=?",
                (now, now + self.expiry_seconds, job_id, worker_id,
                 JOB_RUNNING))
            self._conn.commit()
            return cur.rowcount == 1

    def complete(self, job_id: str, worker_id: str, result_json: dict) -> bool:
        """置为 SUCCEEDED 并附 result manifest(仅当前 lease 持有者可提交)。"""
        with self._lock:
            cur = self._conn.execute(
                "UPDATE jobs SET status=?, result_json=?, last_error=NULL "
                " WHERE job_id=? AND lease_owner=? AND status=?",
                (JOB_SUCCEEDED, json.dumps(result_json, ensure_ascii=False),
                 job_id, worker_id, JOB_RUNNING))
            self._conn.commit()
            return cur.rowcount == 1

    def fail(self, job_id: str, worker_id: str, error: str) -> str:
        """job 失败:attempt 未超时回 PENDING,否则 FAILED。返回目标状态。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT attempt, json FROM jobs WHERE job_id=? AND lease_owner=? "
                " AND status=?",
                (job_id, worker_id, JOB_RUNNING)).fetchone()
            if row is None:
                return JOB_FAILED
            spec = JobSpec(**json.loads(row["json"]))
            used = int(row["attempt"])
            target = JOB_PENDING if used < int(spec.attempt_limit) else JOB_FAILED
            self._execute(
                "UPDATE jobs SET status=?, lease_owner=NULL,"
                " lease_expires_at=NULL, last_error=? WHERE job_id=?",
                (target, error[:400], job_id))
            return target

    def reap_expired(self, now: float | None = None) -> int:
        """把租约过期的 RUNNING job 回 PENDING(尝试满则 FAILED),返回数量。"""
        now = now or time.time()
        with self._lock:
            rows = self._conn.execute(
                "SELECT job_id, attempt, json FROM jobs WHERE status=? "
                " AND lease_expires_at<?",
                (JOB_RUNNING, now)).fetchall()
            n = 0
            for row in rows:
                spec = JobSpec(**json.loads(row["json"]))
                used = int(row["attempt"])
                target = JOB_PENDING if used < int(spec.attempt_limit) \
                    else JOB_FAILED
                self._execute(
                    "UPDATE jobs SET status=?, lease_owner=NULL,"
                    " lease_expires_at=NULL WHERE job_id=?",
                    (target, row["job_id"]))
                n += 1
            return n

    def stats(self, campaign_id: str | None = None) -> dict:
        with self._lock:
            sql = "SELECT status, COUNT(*) c FROM jobs"
            params = []
            if campaign_id:
                sql += " WHERE campaign_id=?"
                params.append(campaign_id)
            sql += " GROUP BY status"
            rows = self._conn.execute(sql, params).fetchall()
            counts = {r["status"]: r["c"] for r in rows}
            for s in (JOB_PENDING, JOB_RUNNING, JOB_SUCCEEDED, JOB_FAILED):
                counts.setdefault(s, 0)
            return counts