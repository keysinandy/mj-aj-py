"""distributed job kinds:``legacy_bc_games``(BC 数据生成的分布式 job)。

handler 契约(与 worker_runtime.WorkerSupervisor 对齐):
    ``fn(job: dict, *, cache_dir: str) -> dict | None``
    把本 job 的产物写到 ``cache_dir/<job_id>/rollout.npz``(shared publish 会
    以 manifest-last 发布),返回要并入 result manifest 的 provenance meta。

``legacy_bc_games`` 复用 :mod:`mj.bc_data` 的 ``generate_game`` /
``_write_shard`` 原语——改变的是"执行/发布"方式,而不是数据生成语义:
单 worker 并行 vs 双机租约并行得到的语义样本集在 frozen seed 下一致(任务 6.5
政策 version-lock 与 6.4 provenance 的 BC 侧对应)。
"""

from __future__ import annotations

import os

from ..bc_data import TRAINING_BOT_EVALUATOR, _write_shard

HANDLERS: dict = {}

__all__ = ["HANDLERS", "execute_legacy_bc_games"]


def execute_legacy_bc_games(job: dict, *, cache_dir: str) -> dict:
    payload = job.get("payload") or {}
    seed_start = int(payload.get("seed_start", 0))
    games = int(payload.get("games", 1))
    ycbk = bool(payload.get("you_cai_bi_kao", False))
    evaluator = str(payload.get("evaluator", TRAINING_BOT_EVALUATOR))
    scope = str(payload.get("scope", "all-root"))
    include_meta = bool(payload.get("include_metadata", False))
    allow_fallback = bool(payload.get("allow_search_fallback", False))
    local_dir = os.path.join(cache_dir, job["job_id"])
    os.makedirs(local_dir, exist_ok=True)
    out = os.path.join(local_dir, "rollout.npz")
    # 复用 bc_data 的单 worker 分片原语(_write_shard 返回 (path, n_samples))
    _, n_samples = _write_shard(
        [seed_start, games, out, ycbk, include_meta, evaluator, scope,
         allow_fallback])
    return {
        "campaign_id": job["campaign_id"],
        "transition_count": int(n_samples),
        "seed_start": seed_start,
        "games": int(games),
        "value_contract": "round-score-v2-normalized",
        "action_scope": "discard-only-v1",
    }


HANDLERS["legacy_bc_games"] = execute_legacy_bc_games
