"""分布式 job 数据模型 (task 6 infrastructure)。

这是所有训练 job kind(``legacy_bc_games`` / ``dagger_games`` /
``rl_rollout`` / ``paired_rows`` / ``search_*``)共享的运行时契约,与
``distributed-search-distillation-workers`` 的 Coordinator/lease/heartbeat/
immutable shard 语义一致:

- ``JobSpec.job_id`` 是稳定 fingerprint(不含 attempt),retry 复用同一 job_id;
- settings/attempt/worker 属于运行时状态,绝不进入语义身份;
- result manifest 必须带 policy/value/feature/provenance,缺失即不可 merge;
- 发布 immutable(shadow tmp → 最后提交 manifest)。

不在此层假设具体 job kind 的 payload;payload 由下游 handler 解释。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping

from ..decision.profile import fingerprint

DISTRIBUTED_SCHEMA = "search-cluster-job-v1"

# 状态机
JOB_PENDING = "PENDING"
JOB_RUNNING = "RUNNING"
JOB_SUCCEEDED = "SUCCEEDED"
JOB_FAILED = "FAILED"

# job kinds(v1 最小闭环先落地 legacy_bc_games,其余后续注册)
KIND_LEGACY_BC_GAMES = "legacy_bc_games"
KIND_DAGGER_GAMES = "dagger_games"
KIND_RL_ROLLOUT = "rl_rollout"
KIND_PAIRED_ROWS = "paired_rows"
KIND_SEARCH_TEACHER = "search_teacher"

ROLE_COORDINATOR = "coordinator"
ROLE_TRAINER = "trainer"
ROLE_TEACHER = "teacher"
ROLE_ROLLOUT = "rollout"
ROLE_PAIRED = "paired"

__all__ = [
    "DISTRIBUTED_SCHEMA", "JOB_PENDING", "JOB_RUNNING", "JOB_SUCCEEDED",
    "JOB_FAILED", "KIND_LEGACY_BC_GAMES", "KIND_DAGGER_GAMES",
    "KIND_RL_ROLLOUT", "KIND_PAIRED_ROWS", "KIND_SEARCH_TEACHER",
    "ROLE_COORDINATOR", "ROLE_TRAINER", "ROLE_TEACHER", "ROLE_ROLLOUT",
    "ROLE_PAIRED", "canonical_payload", "stable_job_id", "WorkerCapabilities",
    "JobSpec", "JobLease", "JobResultManifest", "ClusterCampaign",
]


def canonical_payload(payload: Mapping[str, Any]) -> dict:
    """递归排序规范化 payload,保证跨进程/跨端 JSON 稳定。"""
    return json.loads(json.dumps(payload, sort_keys=True, ensure_ascii=False))


def stable_job_id(campaign_id: str, kind: str, *,
                  payload: Mapping[str, Any],
                  input_fingerprints: Mapping[str, Any]) -> str:
    """job_id = fingerprint(campaign_id, kind, payload, input_fingerprints)。

    attempt/worker 不进入语义身份 → retry 复用同一 job_id,结果可幂等比较。
    """
    return fingerprint({
        "campaign_id": campaign_id,
        "kind": kind,
        "payload": canonical_payload(payload),
        "input_fingerprints": canonical_payload(input_fingerprints),
    })


@dataclass(frozen=True)
class WorkerCapabilities:
    """worker 注册声明;调度按 roles/capabilities 而非机器名。"""

    worker_id: str
    git_commit: str
    schema: str = "minisuphx-worker-cap-v1"
    hostname: str = ""
    python_version: str = ""
    cpu_logical: int = 0
    ram_gb: float = 0.0
    gpu_vendor: str = ""
    gpu_model: str = ""
    gpu_vram_gb: float = 0.0
    gpu_training: bool = False
    roles: tuple[str, ...] = ("rollout",)
    max_parallel: dict = field(default_factory=lambda: {"rollout": 1})

    def __post_init__(self):
        if not self.worker_id:
            raise ValueError("worker_id must not be empty")
        if self.gpu_training and self.gpu_vendor.lower() != "nvidia":
            raise ValueError(
                "v1 only supports CUDA training on NVIDIA; AMD RX 6600 "
                "registers gpu_training=false")
        for role in self.roles:
            if role not in (ROLE_COORDINATOR, ROLE_TRAINER, ROLE_TEACHER,
                            ROLE_ROLLOUT, ROLE_PAIRED):
                raise ValueError(f"unknown worker role: {role!r}")

    def payload(self) -> dict:
        return asdict(self)

    def can_run(self, required_role: str) -> bool:
        return required_role in self.roles


@dataclass(frozen=True)
class ClusterCampaign:
    """一次冻结的 campaign:git commit + 指纹 + job kind + 期望 job 数。"""

    campaign_id: str
    job_type: str
    git_commit: str
    schema: str = "minisuphx-campaign-v1"
    generation: int = 0
    policy_version: int = 0
    value_contract: str = "round-score-v2-normalized"
    action_scope: str = "discard-only-v1"
    input_fingerprints: dict = field(default_factory=dict)
    expected_jobs: int = 0
    created_at: float = 0.0

    def payload(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class JobSpec:
    """协调器派发的不可变 job 定义。"""

    job_id: str
    campaign_id: str
    kind: str
    generation: int
    payload: dict
    required_role: str = ROLE_ROLLOUT
    input_fingerprints: dict = field(default_factory=dict)
    attempt_limit: int = 3
    schema: str = DISTRIBUTED_SCHEMA

    def __post_init__(self):
        if not self.job_id or not self.campaign_id:
            raise ValueError("job/campaign identity must not be empty")
        if int(self.attempt_limit) < 1:
            raise ValueError("attempt_limit must be >= 1")

    @staticmethod
    def build(campaign_id: str, kind: str, generation: int, payload: Mapping,
              *, required_role: str = ROLE_ROLLOUT,
              input_fingerprints: Mapping | None = None,
              attempt_limit: int = 3) -> "JobSpec":
        inp = dict(input_fingerprints or {})
        return JobSpec(
            job_id=stable_job_id(campaign_id, kind, payload=payload,
                                 input_fingerprints=inp),
            campaign_id=campaign_id, kind=kind, generation=generation,
            payload=canonical_payload(payload), required_role=required_role,
            input_fingerprints=inp, attempt_limit=attempt_limit)

    def payload_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class JobLease:
    """worker 持有的一份 job 的租约。"""

    job_id: str
    worker_id: str
    expires_at: float
    attempt: int


@dataclass(frozen=True)
class JobResultManifest:
    """job 成功发布的不可变结果;merge 仅信任已提交 manifest。"""

    job_id: str
    campaign_id: str
    worker_id: str
    git_commit: str
    schema: str = "minisuphx-job-result-v1"
    kind: str = ""
    generation: int = 0
    rows: int = 0
    artifact_sha256: str = ""
    artifact_relpath: str = ""
    input_fingerprints: dict = field(default_factory=dict)
    policy_version: int = 0
    policy_fingerprint: str = ""
    value_contract: str = ""
    action_scope: str = ""
    status: str = JOB_SUCCEEDED

    def payload(self) -> dict:
        return asdict(self)

    @property
    def fingerprint(self) -> str:
        return fingerprint(self.payload(), 24)