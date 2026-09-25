"""distributed job kind:``rl_rollout``(双机 PPO rollout / policy-version-locked)。

handler 把 job payload 里冻结的 policy 版本加载为推理网,用
:func:`~.ppo_rollout.gather_rollout` 收集该版本专用 shard。
返回并入 result manifest 的 provenance(policy_fingerprint / policy_version /
value_contract / transition_count);merge 侧
``merge_guard.validate_merge`` 据此拒绝 stale / mixed shard。

``HANDLERS`` 注册表唯一宿在 :mod:`~.distributed_bc`(worker_runtime 由此消费);
本模块把 ``rl_rollout`` 注册进同一张表。
"""

from __future__ import annotations

import os

import torch

from .distributed_bc import HANDLERS
from .minisuphx_manifest import (
    ACTION_SCOPE_DISCARD,
    FEATURE_CONTRACT,
    VALUE_CONTRACT,
)
from .ppo_rollout import gather_rollout, write_rollout_shard

__all__ = ["execute_rl_rollout", "load_discard_policy"]


def load_discard_policy(path: str, blocks: int, width: int,
                        device: str = "cpu", *, return_manifest: bool = False):
    """加载冻结弃发策略 Net(``{"state_dict","blocks","width"}`` 格式)。

    discard env 用 oracle=False → n_planes=75 与 BC anchor 同构。
    """
    from ..model import Net
    ck = torch.load(path, map_location=device, weights_only=False)
    b = int(ck.get("blocks", blocks))
    w = int(ck.get("width", width))
    net = Net(blocks=b, width=w, n_planes=75)
    net.load_state_dict(ck["state_dict"])
    net.eval()
    if return_manifest:
        return net, ck.get("manifest") or {}
    return net


def execute_rl_rollout(job: dict, *, cache_dir: str, device: str = "cpu",
                       policy_path: str | None = None) -> dict:
    # Rollout workers are CPU actors by default; keep one torch thread per
    # actor so PC-B's process count does not multiply into a thread storm.
    torch.set_num_threads(1)
    payload = job.get("payload") or {}
    policy_path = policy_path or payload.get("policy_path")
    if not policy_path or not os.path.exists(policy_path):
        raise FileNotFoundError(f"policy checkpoint missing: {policy_path!r}")
    policy_fingerprint = str(payload.get("policy_fingerprint", ""))
    if not policy_fingerprint:
        raise ValueError("rl_rollout payload missing policy_fingerprint")
    if "policy_version" not in payload:
        raise ValueError("rl_rollout payload missing policy_version")

    net, policy_manifest = load_discard_policy(
        policy_path, int(payload.get("blocks", 6)),
        int(payload.get("width", 128)), device=device, return_manifest=True)
    stored_fingerprint = str(policy_manifest.get("fingerprint", ""))
    required_manifest = (
        "fingerprint", "policy_version", "model_arch", "value_contract",
        "feature_contract", "action_scope",
    )
    missing_manifest = [key for key in required_manifest
                        if key not in policy_manifest
                        or policy_manifest.get(key) in (None, "")]
    if missing_manifest:
        raise ValueError(
            f"policy checkpoint missing manifest fields {missing_manifest}")
    if stored_fingerprint and stored_fingerprint != policy_fingerprint:
        raise ValueError(
            "rl_rollout policy fingerprint does not match checkpoint manifest")
    stored_version = policy_manifest.get("policy_version")
    if stored_version is not None and int(stored_version) != int(payload["policy_version"]):
        raise ValueError("rl_rollout policy version does not match checkpoint")
    if policy_manifest.get("value_contract") != VALUE_CONTRACT:
        raise ValueError("rl_rollout policy value contract is incompatible")
    if policy_manifest.get("feature_contract") != FEATURE_CONTRACT:
        raise ValueError("rl_rollout policy feature contract is incompatible")
    if policy_manifest.get("action_scope") != ACTION_SCOPE_DISCARD:
        raise ValueError("rl_rollout policy action scope is incompatible")
    expected_arch = f"resnet-{int(payload.get('blocks', 6))}x{int(payload.get('width', 128))}"
    if policy_manifest.get("model_arch") != expected_arch:
        raise ValueError(
            f"rl_rollout policy architecture {policy_manifest.get('model_arch')!r} "
            f"!= expected {expected_arch!r}")
    expected_git = str(payload.get("git_commit", ""))
    if expected_git and policy_manifest.get("git_commit") != expected_git:
        raise ValueError("rl_rollout policy git commit does not match campaign")
    net = net.to(device)

    def policy(planes, scalars):
        return net(planes, scalars)

    shape_k = float(payload.get("shape_k", 0.0))
    promotion_eval = bool(payload.get("promotion_eval", False))
    if promotion_eval and shape_k != 0.0:
        raise ValueError("promotion evaluation must use unshaped terminal reward")
    data = gather_rollout(
        policy, n_decisions=int(payload.get("n_decisions", 1024)),
        seed=int(payload.get("seed_start", 0)), hero=int(payload.get("hero", 0)),
        ycbk=bool(payload.get("you_cai_bi_kao", False)),
        max_episodes=int(payload.get("max_episodes", 0)),
        shape_k=shape_k,
        strict_learned=True)
    # 写入 cache/<job_id>/rollout.npz → 由 worker._publish stage+publish
    local_dir = os.path.join(cache_dir, job["job_id"])
    os.makedirs(local_dir, exist_ok=True)
    write_rollout_shard(local_dir, data, {
        "status": "STAGED",
        "job_id": job["job_id"],
        "campaign_id": job["campaign_id"],
        "policy_version": int(payload["policy_version"]),
        "policy_fingerprint": policy_fingerprint,
    })
    episode_meta = data.get("episode_meta", [])
    return {
        "campaign_id": job["campaign_id"],
        "job_id": job["job_id"],
        "transition_count": int(len(data["action"])),
        "policy_version": int(payload.get("policy_version", 0)),
        "policy_fingerprint": policy_fingerprint,
        "generation": int(job.get("generation", 0)),
        "value_contract": VALUE_CONTRACT,
        "feature_contract": FEATURE_CONTRACT,
        "action_scope": ACTION_SCOPE_DISCARD,
        "opponent_pool_fingerprint":
            str(payload.get("opponent_pool_fingerprint", "")),
        "seed_start": int(payload.get("seed_start", 0)),
        "seed_end": int(data["seed"][-1]) if len(data["seed"]) else
        int(payload.get("seed_start", 0)),
        "episode_count": int(data["n_episodes"]),
        "episode_meta": episode_meta,
        "opponent_profile": str(payload.get("opponent_profile", "legacy-v1")),
        "opponent_versions": payload.get("opponent_versions", {}),
        "env_profile": {
            "action_scope": ACTION_SCOPE_DISCARD,
            "you_cai_bi_kao": bool(payload.get("you_cai_bi_kao", False)),
            "hero": int(payload.get("hero", 0)),
            "shape_k": shape_k,
        },
        "promotion_eval": promotion_eval,
        "shaped": bool(shape_k),
    }


# 注册进共享 HANDLERS 注册表(与 legacy_bc_games 同表)
HANDLERS["rl_rollout"] = execute_rl_rollout
