"""Mini-Suphx 分布式集群 CLI:coordinator / worker / submit(campaign + jobs)。

第一版最小闭环:一个通用 Distributed Job Runtime,PPO rollout 只是其中一种
job kind。当前只落地 ``legacy_bc_games``(BC 数据生成);BC/DAgger/RL/Search/
Paired 后续按同一机制追加 handler。

PC-A(coordinator + learner):
  python scripts/minisuphx_cluster.py coordinator \
      --bind 0.0.0.0 --port 8765 --db D:\\mj-cluster\\cluster.sqlite \
      --artifact-root \\\\PC-A\\mj-cluster

PC-A/B(worker):
  python scripts/minisuphx_cluster.py worker \
      --coordinator http://127.0.0.1:8765 --worker-id pc-a-9400f \
      --cache-dir D:\\mj-worker-cache --result-root \\\\PC-A\\mj-cluster\\results

PC-A(提交一个小 BC campaign 验证闭环):
  python scripts/minisuphx_cluster.py submit \\pc-config --campaign demo --kind legacy_bc_games

Windows spawn 守则:任何多进程入口先 set_start_method("spawn");worker 内
rollout 进程 torch.set_num_threads(1),避免 N×cores CPU 线程爆炸。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mj.bc_data import TRAINING_BOT_EVALUATOR as TRAINING_EVALUATOR


def _cmd_coordinator(args):
    from mj.training.coordinator import Coordinator, CoordinatorServer
    from mj.training.job_store import JobStore
    store = JobStore(args.db, expiry_seconds=args.lease_s,
                     heartbeat_every=args.heartbeat_s)
    coord = Coordinator(store)
    server = CoordinatorServer(coord, host=args.bind, port=args.port,
                               token=args.token)
    server.start()
    print(f"coordinator listening on {server.url}", flush=True)
    try:
        while True:
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()
        store.close()


def _cmd_worker(args):
    from mj.training.coordinator import Coordinator, CoordinatorClient
    from mj.training.distributed_jobs import WorkerCapabilities
    if args.pc_a:
        from mj.training.job_store import JobStore
        from mj.training.worker_runtime import WorkerSupervisor
        store = JobStore(args.db, expiry_seconds=args.lease_s)
        coord = Coordinator(store)          # 同进程 SQLite:单机冒烟
    else:
        from mj.training.worker_runtime import WorkerSupervisor
        coord = CoordinatorClient(args.coordinator, token=args.token)

    caps = WorkerCapabilities(
        worker_id=args.worker_id, git_commit=_git(), hostname=args.worker_id,
        cpu_logical=os.cpu_count() or 1, ram_gb=float(args.ram_gb),
        gpu_vendor="nvidia" if args.gpu_training else "amd",
        gpu_model=args.gpu_model, gpu_vram_gb=8.0,
        gpu_training=args.gpu_training,
        roles=tuple(args.roles.split(",")) if args.roles else ("rollout",),
        max_parallel={"rollout": int(args.rollout_processes)})

    WorkerSupervisor(coord, caps, cache_dir=args.cache_dir,
                     result_root=args.result_root, git_commit=_git(),
                     kinds=(args.kind,) if args.kind else None,
                     generation=args.generation).run_forever()


def _cmd_submit(args):
    """打印/写入一个 frozen BC campaign 的 job 列表(本地 coordinator 用它建档)。"""
    from mj.training.coordinator import Coordinator
    from mj.training.distributed_jobs import JobSpec
    payload = {
        "seed_start": args.seed_start, "games": args.games,
        "you_cai_bi_kao": args.you_cai_bi_kao,
        "evaluator": args.evaluator, "scope": args.scope,
        "allow_search_fallback": args.allow_search_fallback,
        "include_metadata": True,
    }
    specs = []
    per = args.per_shard
    for k in range(args.shards):
        specs.append(JobSpec.build(
            args.campaign, "legacy_bc_games", 0,
            dict(payload, seed_start=args.seed_start + k * per, games=per),
            required_role="rollout"))
    if args.out_dir:
        os.makedirs(args.out_dir, exist_ok=True)
        with open(os.path.join(args.out_dir, "jobs.json"), "w",
                  encoding="utf-8") as f:
            json.dump([s.payload_dict() for s in specs], f, ensure_ascii=False)
    print(f"campaign={args.campaign} jobs={len(specs)} "
          f"job_ids={[s.job_id for s in specs]}")
    return specs


def _git() -> str:
    try:
        import subprocess
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                              check=True, text=True).stdout.strip()
    except Exception:  # noqa: BLE001
        return ""


def main(argv=None):
    ap = argparse.ArgumentParser(prog="minisuphx_cluster",
                                 description="Mini-Suphx 分布式集群 CLI")
    sub = ap.add_subparsers(dest="cmd", required=True)

    pc = sub.add_parser("coordinator")
    pc.add_argument("--bind", default="0.0.0.0")
    pc.add_argument("--port", type=int, default=8765)
    pc.add_argument("--db", default="local/minisuphx/cluster.sqlite")
    pc.add_argument("--lease-s", type=float, default=300.0)
    pc.add_argument("--heartbeat-s", type=float, default=30.0)
    pc.add_argument("--token", default=None)
    pc.add_argument("--cache-dir", default="local/minisuphx/cache")
    pc.add_argument("--result-root", default="local/minisuphx/results")
    pc.set_defaults(func=_cmd_coordinator)

    wk = sub.add_parser("worker")
    wk.add_argument("--worker-id", required=True)
    wk.add_argument("--coordinator", default="http://127.0.0.1:8765")
    wk.add_argument("--token", default=None)
    wk.add_argument("--cache-dir", default="local/minisuphx/cache")
    wk.add_argument("--result-root", default="local/minisuphx/results")
    wk.add_argument("--rollout-processes", type=int, default=6)
    wk.add_argument("--ram-gb", type=float, default=64.0)
    wk.add_argument("--gpu-model", default="Radeon RX 6600")
    wk.add_argument("--gpu-training", action="store_true",
                    help="PC-A 特有:允许 CUDA learner 梯度(默认 false)")
    wk.add_argument("--roles", default="rollout")
    wk.add_argument("--kind", default=None)
    wk.add_argument("--generation", type=int, default=None)
    wk.add_argument("--pc-a", action="store_true",
                    help="把这个 worker 跑在 PC-A 同进程 SQLite(测试/单机)")
    wk.add_argument("--db", default="local/minisuphx/cluster.sqlite")
    wk.add_argument("--lease-s", type=float, default=300.0)
    wk.set_defaults(func=_cmd_worker)

    sj = sub.add_parser("submit")
    sj.add_argument("--campaign", required=True)
    sj.add_argument("--shards", type=int, default=4)
    sj.add_argument("--per-shard", type=int, default=25)
    sj.add_argument("--seed-start", type=int, default=0)
    sj.add_argument("--games", type=int, default=100)
    sj.add_argument("--evaluator", default=TRAINING_EVALUATOR,
                    help="训练标签评价器;默认离线 legacyV2(不因预算回退)")
    sj.add_argument("--allow-search-fallback", action="store_true",
                    help="允许 legacyV2 回退 legacy(默认禁止)")
    sj.add_argument("--scope", default="all-root")
    sj.add_argument("--you-cai-bi-kao", action="store_true")
    sj.add_argument("--out-dir", default="local/minisuphx")
    sj.set_defaults(func=_cmd_submit)

    args = ap.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
