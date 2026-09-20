"""分布式运行时最小闭环:A-only 与 A+B 语义等价(任务 6.5 的守卫方向)。

frozen campaign(固定 job_id seed)跑 1 worker vs 2 worker,产物指纹逐 job
必须一致;任何 worker 计数/顺序改变不得改变语义结果集合。
"""

import os

from mj.training.artifact_store import find_results, load_manifest, sha256_file
from mj.training.coordinator import (
    Coordinator, CoordinatorClient, CoordinatorServer)
from mj.training.distributed_jobs import (
    JOB_SUCCEEDED, JobSpec, WorkerCapabilities)
from mj.training.job_store import JobStore
from mj.training.worker_runtime import WorkerSupervisor

GIT = "abc123def"

def _campaign_jobs(cid, n=3, per=2):
    return [JobSpec.build(cid, "legacy_bc_games", 0,
                          {"seed_start": i * per, "games": per,
                           "evaluator": "legacy", "scope": "all-root",
                           "include_metadata": True})
            for i in range(n)]


def _run_campaign(db, result_root, n_workers, n_jobs=3, cid="frozen-camp"):
    store = JobStore(str(db), expiry_seconds=20.0)
    coord = Coordinator(store)
    server = CoordinatorServer(coord, host="127.0.0.1", port=0).start()
    client = CoordinatorClient(server.url)
    coord.create_campaign({"campaign_id": cid, "job_type": "legacy_bc_games",
                           "git_commit": GIT})
    coord.enqueue(cid, _campaign_jobs(cid, n_jobs))
    caches = []
    workers = []
    for k in range(n_workers):
        cache = str(db.parent / f"cache-{k}")
        os.makedirs(cache, exist_ok=True)
        caps = WorkerCapabilities(worker_id=f"w{k}", git_commit=GIT,
                                  roles=("rollout",),
                                  max_parallel={"rollout": 1})
        workers.append(WorkerSupervisor(
            client, caps, cache_dir=cache, result_root=result_root,
            git_commit=GIT, kinds=("legacy_bc_games",), generation=0))
        caches.append(cache)
    # 轮询驱动:直到全部 SUCCEEDED(worker 分配动态,不固定 50/50)
    guard = 0
    while store.stats(cid)[JOB_SUCCEEDED] < n_jobs and guard < 500:
        progressed = False
        for w in workers:
            progressed |= (w.run_once() == 1)
        if not progressed:
            guard += 1
    success = store.stats(cid)[JOB_SUCCEEDED]
    server.stop()
    store.close()
    return success, cid


def _artifacts_by_job(result_root, cid):
    out = {}
    for job_id, mp in find_results(result_root, cid):
        m = load_manifest(mp)
        data = os.path.join(os.path.dirname(mp), m["artifact_relpath"])
        out[job_id] = (m["worker_id"], m["rows"], sha256_file(data))
    return out


def test_single_machine_runs_bc_jobs(tmp_path):
    result_root = str(tmp_path / "results")
    success, cid = _run_campaign(tmp_path / "a.sqlite", result_root,
                                 n_workers=1, n_jobs=4)
    assert success == 4
    arts = _artifacts_by_job(result_root, cid)
    assert len(arts) == 4
    # manifest 带完整 provenance
    _, rows, _ = list(arts.values())[0]
    assert rows > 0


def test_A_only_equals_A_plus_B(tmp_path):
    """同一 frozen campaign:1 worker vs 2 worker,产物指纹逐 job 一致。"""
    r1 = str(tmp_path / "res1")
    r2 = str(tmp_path / "res2")
    s1, c1 = _run_campaign(tmp_path / "b1.sqlite", r1, n_workers=1, n_jobs=3)
    s2, c2 = _run_campaign(tmp_path / "b2.sqlite", r2, n_workers=2, n_jobs=3,
                           cid="frozen-camp")
    assert s1 == s2 == 3
    assert c1 == c2 == "frozen-camp"
    a1 = _artifacts_by_job(r1, c1)
    a2 = _artifacts_by_job(r2, c2)
    assert set(a1) == set(a2)          # frozen campaign 下语义 job 集合一致
    for job_id in a1:
        w1, rows1, sha1 = a1[job_id]
        w2, rows2, sha2 = a2[job_id]
        assert rows1 == rows2
        assert sha1 == sha2            # frozen seed 下产物逐字节一致


def test_http_client_roundtrip(tmp_path):
    """CoordinatorServer + CoordinatorClient 契约最小往返。"""
    store = JobStore(str(tmp_path / "c.sqlite"))
    coord = Coordinator(store)
    server = CoordinatorServer(coord, host="127.0.0.1", port=0, token="t8k").start()
    caps = WorkerCapabilities(worker_id="pc-b", git_commit=GIT,
                              gpu_vendor="amd", gpu_model="RX 6600",
                              gpu_training=False, roles=("rollout",))
    client = CoordinatorClient(server.url, token="t8k")
    reg = client.register(caps)
    assert reg["ok"] is True
    bad = client.lease("pc-b", generation=0)
    assert bad["job"] is None      # 空队列
    coord.create_campaign({"campaign_id": "cx", "job_type": "x", "git_commit": GIT})
    from mj.training.distributed_jobs import JobSpec
    coord.enqueue("cx", [JobSpec.build("cx", "legacy_bc_games", 0,
                                       {"seed_start": 0, "games": 1})])
    got = client.lease("pc-b", generation=0)
    assert got["job"]["campaign_id"] == "cx"
    client.heartbeat(got["job"]["job_id"], "pc-b")
    client.fail(got["job"]["job_id"], "pc-b", "x")
    server.stop(); store.close()