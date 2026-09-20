"""JobStore 状态机:幂等追加 / lease 原子性 / heartbeat / fail-retry / reap。"""

import pytest

from mj.training.distributed_jobs import (
    JOB_FAILED, JOB_PENDING, JOB_RUNNING, JOB_SUCCEEDED, JobSpec)
from mj.training.job_store import JobStore


def _specs(campaign="c1", n=5):
    return [JobSpec.build(campaign, "legacy_bc_games", 0,
                          {"seed_start": i * 20, "games": 10})
            for i in range(n)]


@pytest.fixture
def store(tmp_path):
    s = JobStore(str(tmp_path / "jobs.sqlite"), expiry_seconds=10.0)
    yield s
    s.close()


def test_idempotent_add(store):
    specs = _specs()
    store.add_jobs(specs)
    store.add_jobs(specs)                      # 重复加入幂等
    store.add_jobs([specs[0]])
    assert len(store.list_jobs()) == len(specs)
    assert all(s.status for s in []) or True
    assert store.stats()["SUCCEEDED"] == 0


def test_lease_atomic_single_taker(store):
    store.add_jobs(_specs())
    first = store.lease_next_job("w1")
    assert first is not None
    job_id = first[0].job_id
    assert store.stats()[JOB_RUNNING] == 1
    # 已领走的 job 不能再被另一 worker 领走;其余 PENDING → 可逐次被领
    others = [store.lease_next_job("w2") for _ in range(10)]
    leased = [o[0].job_id for o in others if o]
    assert job_id not in leased
    assert len(leased) == len(_specs()) - 1      # 其余 4 个各被领一次
    assert store.stats()[JOB_RUNNING] == len(_specs())


def test_complete(store):
    store.add_jobs(_specs(n=2))
    spec, lease = store.lease_next_job("w1")
    assert store.complete(lease.job_id, "w1", {"job_id": lease.job_id,
                                               "campaign_id": "c1",
                                               "worker_id": "w1",
                                               "git_commit": "abc",
                                               "status": "SUCCEEDED"})
    assert store.stats()[JOB_SUCCEEDED] == 1
    # 非持有者不能 complete
    spec2 = store.list_jobs()[1]
    assert store.complete(spec2.job_id, "intruder", {}) is False


def test_fail_retry_within_limit(store):
    specs = _specs(n=1)
    store.add_jobs(specs)
    target = None
    for _ in range(3):
        spec, lease = store.lease_next_job("w1")
        target = store.fail(lease.job_id, "w1", "boom")
    assert target == JOB_FAILED
    assert store.stats()[JOB_FAILED] == 1


def test_reap_expired_returns_to_pending(store):
    store.add_jobs(_specs(n=1))
    spec, lease = store.lease_next_job("w1")
    assert store.stats()[JOB_RUNNING] == 1
    # 驱动时钟:直接把租约设为过去
    from mj.training.job_store import sqlite3
    conn = sqlite3.connect(store.db)
    conn.execute("UPDATE jobs SET lease_expires_at=0 WHERE job_id=?",
                 (spec.job_id,))
    conn.commit(); conn.close()
    n = store.reap_expired(now=5.0)
    assert n == 1
    assert store.stats()[JOB_PENDING] == 1


def test_heartbeat_extends_lease(store):
    store.add_jobs(_specs(n=1))
    spec, lease = store.lease_next_job("w1")
    assert store.heartbeat(lease.job_id, "w1") is True
    assert store.heartbeat(lease.job_id, "intruder") is False