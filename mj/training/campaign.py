"""Mini-Suphx 训练 campaign 状态机与阶段编排(任务 12.1 / 13.11 / 13.15)。

定义一次 run 的磁盘身份与阶段推进:
  created -> bc -> (dagger rounds) -> ppo rounds -> gate/promote -> promoted

状态以 ``campaign.json`` 落地,原子写、可 resume。每个产物(checkpoint /
paired 报告)记录 sha256 与 fingerprint,阶段推进只增不改,便于审计与回滚。

刻意保持"编排"而非"算法":BC 训练、DAgger 生成、PPO update、paired 对弈
都委托给各自已测试的模块(streaming_bc / dagger_games / ppo_learner /
ppo_rollout / paired_eval)。单机/双机共用同一状态机,placement 差异不改变
训练身份(design R7)。
"""

from __future__ import annotations

import json
import math
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from .minisuphx_manifest import (
    FEATURE_CONTRACTS,
    FEATURE_PUBLIC,
    MiniSuphxRunManifest,
    feature_contract,
    fingerprint,
)

CAMPAIGN_SCHEMA = "minisuphx-campaign-state-v1"

GATE_LEVELS = {"smoke": 512, "fast": 1024, "full": 4096}


def sha256_file(path: str) -> str:
    import hashlib

    h = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass
class CampaignState:
    """一次 campaign 的可持久化身份与渐进产物清单。"""

    campaign_id: str
    out_dir: str
    feature_contract: str = FEATURE_PUBLIC
    blocks: int = 6
    width: int = 128
    policy_version: int = 0
    generation: int = 0
    phase: str = "created"
    run_manifest: dict = field(default_factory=dict)
    anchor: dict | None = None          # BC anchor: {path, sha256, fingerprint, kind}
    policies: list = field(default_factory=list)
    rollouts: list = field(default_factory=list)
    dagger_rounds: list = field(default_factory=list)
    paired: list = field(default_factory=list)
    champion: dict | None = None
    schema: str = CAMPAIGN_SCHEMA

    def __post_init__(self):
        if not self.campaign_id or not self.out_dir:
            raise ValueError("campaign_id and out_dir must not be empty")
        feature_contract(self.feature_contract)   # fail-loud unknown contract
        if int(self.blocks) <= 0 or int(self.width) <= 0:
            raise ValueError("blocks/width must be positive")

    def payload(self) -> dict:
        return {
            "schema": self.schema,
            "campaign_id": self.campaign_id,
            "out_dir": self.out_dir,
            "feature_contract": self.feature_contract,
            "blocks": int(self.blocks),
            "width": int(self.width),
            "policy_version": int(self.policy_version),
            "generation": int(self.generation),
            "phase": self.phase,
            "run_manifest": self.run_manifest,
            "anchor": self.anchor,
            "policies": list(self.policies),
            "rollouts": list(self.rollouts),
            "dagger_rounds": list(self.dagger_rounds),
            "paired": list(self.paired),
            "champion": self.champion,
        }

    @property
    def fingerprint(self) -> str:
        return fingerprint(self.payload(), 24)

    def save(self, path: str | None = None) -> str:
        path = path or os.path.join(self.out_dir, "campaign.json")
        payload = self.payload()
        payload["fingerprint"] = self.fingerprint
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
        return path

    @classmethod
    def load(cls, path: str) -> "CampaignState":
        with open(path, encoding="utf-8") as stream:
            data = json.load(stream)
        if data.get("schema") != CAMPAIGN_SCHEMA:
            raise ValueError(f"not a campaign state: schema={data.get('schema')!r}")
        supplied = data.pop("fingerprint", None)
        state = cls(**{key: data[key] for key in data if key in cls.__dataclass_fields__})
        if supplied is not None and supplied != state.fingerprint:
            raise ValueError("campaign state fingerprint mismatch (corrupt/resumed incompatibly)")
        return state

    # ---- 阶段推进 ----------------------------------------------------------

    def set_anchor(self, path: str, kind: str) -> dict:
        entry = {"path": os.path.abspath(path), "sha256": sha256_file(path),
                 "kind": kind, "feature_contract": self.feature_contract}
        self.anchor = entry
        if kind == "bc-v1":
            self.phase = "dagger" if self.dagger_rounds else "bc"
        else:
            self.phase = "bc"
        self.save()
        return entry

    def record_policy(self, path: str, *, manifest_fp: str = "",
                      opponent_pool_fingerprint: str = "") -> dict:
        version = self.policy_version
        entry = {"version": version, "path": os.path.abspath(path),
                 "sha256": sha256_file(path), "manifest_fp": manifest_fp,
                 "opponent_pool_fingerprint": opponent_pool_fingerprint}
        self.policies.append(entry)
        self.policy_version += 1
        self.phase = "ppo"
        self.save()
        return entry

    def record_dagger(self, round_name: str, *, n_samples: int,
                      learned_ckpt: str, learned_exec_prob: float,
                      data_dir: str, discrepancy_rate: float) -> dict:
        entry = {"round": round_name, "n_samples": int(n_samples),
                 "learned_ckpt": learned_ckpt,
                 "learned_exec_prob": float(learned_exec_prob),
                 "data_dir": os.path.abspath(data_dir),
                 "discrepancy_rate": float(discrepancy_rate)}
        self.dagger_rounds.append(entry)
        self.save()
        return entry

    def record_rollout(self, manifest: Mapping, *, artifact_path: str) -> dict:
        """Record one locally merged rollout without duplicating retries."""
        entry = {
            "job_id": str(manifest["job_id"]),
            "policy_version": int(manifest["policy_version"]),
            "policy_fingerprint": str(manifest["policy_fingerprint"]),
            "rows": int(manifest.get("rows", manifest.get("transition_count", 0))),
            "manifest_path": os.path.abspath(
                str(manifest.get("manifest_path", ""))),
            "artifact_path": os.path.abspath(artifact_path),
            "manifest_fingerprint": str(manifest.get("fingerprint", "")),
        }
        self.rollouts = [row for row in self.rollouts
                         if row.get("job_id") != entry["job_id"]]
        self.rollouts.append(entry)
        self.save()
        return entry

    def record_paired(self, *, candidate: str, level: str, verdict: str,
                      mean_delta: float, ci95, pairs: int,
                      meets_required: bool) -> dict:
        entry = {"candidate": os.path.abspath(candidate), "level": level,
                 "verdict": verdict, "mean_delta": float(mean_delta),
                 "ci95": [float(ci95[0]), float(ci95[1])], "pairs": int(pairs),
                 "meets_required": bool(meets_required)}
        self.paired.append(entry)
        self.save()
        return entry

    def set_champion(self, *, path: str, paired: dict) -> dict:
        self.champion = {"path": os.path.abspath(path), "sha256": sha256_file(path),
                         "paired": paired, "feature_contract": self.feature_contract}
        self.phase = "promoted"
        self.save()
        return self.champion


def create(out_dir: str, campaign_id: str, *, blocks: int = 6, width: int = 128,
           feature_contract_name: str = FEATURE_PUBLIC, git_commit: str = "",
           seed_lo: int = 0, seed_hi: int = 29999) -> CampaignState:
    """创建新 campaign;写入 run_manifest(json) + campaign state。"""
    fc = feature_contract(feature_contract_name)
    from .minisuphx_manifest import SeedDomain

    domain = SeedDomain(train_lo=seed_lo, train_hi=seed_hi)
    run = MiniSuphxRunManifest(
        run_id=campaign_id, git_commit=git_commit,
        feature_contract=feature_contract_name,
        model_arch=f"resnet-{blocks}x{width}", seed_domain=domain)
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "run_manifest.json"), "w", encoding="utf-8") as stream:
        json.dump(run.to_dict(), stream, ensure_ascii=False, indent=2)
    state = CampaignState(campaign_id=campaign_id, out_dir=out_dir,
                          feature_contract=feature_contract_name,
                          blocks=blocks, width=width,
                          run_manifest=run.to_dict(),
                          phase="created")
    state.save()
    return state


def run_bc(state: CampaignState, bc_data: str, *, epochs: int = 10,
           batch_size: int = 512, device: str = "cpu", seed: int = 0,
           value_w: float = 0.5, out_sub: str = "bc0",
           anchor_kind: str = "bc0") -> str:
    """在 bc_data 上训练一个 BC checkpoint,并登记为 anchor。"""
    from ..training.streaming_bc import main as bc_main

    out = os.path.join(state.out_dir, out_sub)
    rv = bc_main(["--data", bc_data, "--out", out, "--epochs", str(epochs),
                  "--bs", str(batch_size), "--device", device,
                  "--blocks", str(state.blocks), "--width", str(state.width),
                  "--seed", str(seed), "--feature", state.feature_contract,
                  "--value-w", str(value_w)])
    if rv != 0:
        raise RuntimeError(f"BC 训练失败(out={out})")
    best = os.path.join(out, "best.pt")
    if not os.path.exists(best):
        raise FileNotFoundError(f"BC 训练未产出 best.pt: {best}")
    state.set_anchor(best, kind=anchor_kind)
    return best


def gen_dagger(state: CampaignState, dagger_data: str, round_name: str, *,
               learned_ckpt: str, learned_exec_prob: float, games: int,
               seed_start: int = 0, hero: int = 0, device: str = "cpu") -> dict:
    """生成一轮 DAgger shard 到 dagger_data/<round>/;返回 round 目录与统计。"""
    from ..training.dagger_games import (
        dagger_manifest_meta, execute_dagger_job,
    )

    out_dir_path = os.path.join(dagger_data, round_name)
    os.makedirs(out_dir_path, exist_ok=True)
    # 复用 handler 原语(确定性 seed:*round* 命名空间,避免跨 round 冲突)
    job = {
        "job_id": f"dagger-{round_name}", "campaign_id": state.campaign_id,
        "kind": "dagger_games", "generation": state.generation,
        "payload": {
            "seed_start": seed_start, "games": games, "hero": hero,
            "learned_ckpt": learned_ckpt, "learned_exec_prob": learned_exec_prob,
            "device": device,
        },
    }
    meta = execute_dagger_job(job, cache_dir=out_dir_path)
    # handler 写到 cache_dir/<job_id>/rollout.npz;挪到 round 顶层便于作为 data dir
    staged = os.path.join(out_dir_path, job["job_id"], "rollout.npz")
    shutil.move(staged, os.path.join(out_dir_path, "shard_00000.npz"))
    shutil.rmtree(os.path.join(out_dir_path, job["job_id"]), ignore_errors=True)
    state.record_dagger(round_name, n_samples=meta["transition_count"],
                        learned_ckpt=learned_ckpt,
                        learned_exec_prob=learned_exec_prob, data_dir=out_dir_path,
                        discrepancy_rate=meta.get("disagreement_rate", 0.0))
    return meta


_TENOR = {
    "D1": 0.3, "D2": 0.6, "D3": 0.9,
}


def _checkpoint_identity(path: str):
    """Return (policy fingerprint, manifest, resumable) for a trusted ckpt."""
    import torch

    checkpoint = torch.load(path, map_location="cpu", weights_only=False)
    manifest = checkpoint.get("manifest", {}) \
        if isinstance(checkpoint, Mapping) else {}
    manifest = dict(manifest) if isinstance(manifest, Mapping) else {}
    policy_fp = str(manifest.get("fingerprint", "")) or sha256_file(path)
    resumable = ("optimizer" in checkpoint and "learner_state" in checkpoint
                 if isinstance(checkpoint, Mapping) else False)
    return policy_fp, manifest, resumable


def _publish_local_rollout(state: CampaignState, data: dict, *,
                           policy_version: int, policy_fingerprint: str,
                           seed: int, opponent_pool_fingerprint: str,
                           git_commit: str) -> tuple[dict, str]:
    """Stage, publish and return one local rollout manifest plus its artifact."""
    from .artifact_store import publish_result
    from .minisuphx_manifest import (
        ACTION_SCOPE_DISCARD, VALUE_CONTRACT, RolloutManifest,
    )
    from .ppo_rollout import write_rollout_shard

    job_id = f"local-ppo-{int(policy_version):06d}-{int(seed)}"
    stage_dir = os.path.join(state.out_dir, "rollout_staging", job_id)
    result_root = os.path.join(state.out_dir, "rollout_results")
    write_rollout_shard(stage_dir, data, {
        "status": "STAGED", "campaign_id": state.campaign_id,
        "job_id": job_id, "policy_version": int(policy_version),
        "policy_fingerprint": policy_fingerprint,
    })
    staged_artifact = os.path.join(stage_dir, "rollout.npz")
    artifact_sha = sha256_file(staged_artifact)
    commit = str(git_commit or state.run_manifest.get("git_commit") or "local")
    rollout = RolloutManifest(
        campaign_id=state.campaign_id, job_id=job_id, worker_id="local",
        policy_version=int(policy_version),
        policy_fingerprint=policy_fingerprint, git_commit=commit,
        generation=int(state.generation), value_contract=VALUE_CONTRACT,
        action_scope=ACTION_SCOPE_DISCARD,
        feature_contract=state.feature_contract,
        opponent_pool_fingerprint=str(opponent_pool_fingerprint),
        transition_count=int(len(data["action"])), artifact_sha256=artifact_sha,
        seed_start=int(seed),
    )
    manifest = rollout.to_dict()
    manifest.update({
        "status": "SUCCEEDED", "kind": "rl_rollout",
        "artifact_relpath": "rollout.npz", "artifact_sha256": artifact_sha,
        "rows": int(len(data["action"])), "worker_id": "local",
        "git_commit": commit,
        "episode_count": int(data.get("n_episodes", 0)),
        "episode_meta": data.get("episode_meta", []),
        "shape_k": float(data.get("shape_k", 0.0)),
    })
    manifest_path = publish_result(
        result_root, state.campaign_id, job_id,
        files={staged_artifact: "rollout.npz"}, manifest=manifest)
    manifest["manifest_path"] = manifest_path
    artifact_path = os.path.join(
        result_root, state.campaign_id, job_id, "rollout.npz")
    return manifest, artifact_path


def run_ppo_round(state: CampaignState, *, policy_ckpt: str, rollout_n: int,
                  seed: int = 100, hero: int = 0, device: str = "cpu",
                  opponent_pool_fingerprint: str = "", git_commit: str = "",
                  batch_size: int = 1024, ppo_epochs: int = 4,
                  bc_reg: float | None = 0.5) -> tuple[str, dict]:
    """一个单机 on-policy PPO round:gather → GAE → update → 发布 policy_N+1。"""
    from ..training.ppo_learner import PPOLearner, load_shard_arrays
    from ..training.ppo_rollout import gather_rollout

    if state.anchor is None:
        raise RuntimeError("PPO 需要已登记的 BC anchor(先跑 bc/ dagger 阶段)")
    anchor = state.anchor["path"]
    policy_fingerprint, source_manifest, resumable = _checkpoint_identity(
        policy_ckpt)
    source_version = (int(source_manifest["policy_version"])
                      if source_manifest.get("policy_version") is not None
                      else int(state.policy_version))
    if resumable and state.policies:
        expected_source = int(state.policies[-1]["version"])
        if source_version != expected_source:
            raise ValueError(
                f"resume policy version {source_version} != campaign latest "
                f"{expected_source}")
    learner = PPOLearner(
        policy_version=state.policy_version, blocks=state.blocks,
        width=state.width, anchor=anchor, policy_path=policy_ckpt,
        device=device, generation=state.generation,
        action_scope="discard-only-v1",
        feature_contract=state.feature_contract, batch_size=batch_size,
        ppo_epochs=ppo_epochs, bc_reg=bc_reg,
        bc_anchor_fingerprint=state.anchor["sha256"])
    if resumable:
        learner.load_checkpoint(
            policy_ckpt,
            expected_bc_anchor_fingerprint=state.anchor["sha256"])
        # The checkpoint identifies the frozen input policy.  The campaign
        # counter identifies the next immutable policy to publish.
        learner.policy_version = int(state.policy_version)

    data = gather_rollout(learner.net, n_decisions=rollout_n, seed=seed,
                          hero=hero, strict_learned=True)
    manifest, artifact_path = _publish_local_rollout(
        state, data, policy_version=source_version,
        policy_fingerprint=policy_fingerprint, seed=seed,
        opponent_pool_fingerprint=opponent_pool_fingerprint,
        git_commit=git_commit)

    from .merge_guard import validate_merge
    commit = str(git_commit or state.run_manifest.get("git_commit") or "local")
    ok, errors = validate_merge(
        [manifest], expected_policy_fingerprint=policy_fingerprint,
        expected_policy_version=source_version,
        expected_campaign_id=state.campaign_id,
        expected_generation=state.generation,
        expected_value_contract="round-score-v2-normalized",
        expected_feature_contract=state.feature_contract,
        expected_git=commit, expected_action_scope="discard-only-v1")
    if not ok:
        raise ValueError("local rollout merge rejected: " + "; ".join(errors))
    state.record_rollout(manifest, artifact_path=artifact_path)
    tensors, _metadata = load_shard_arrays([artifact_path], device=device)
    stats = learner.update(tensors)

    # 逐 episode 计算 GAE(gamma=1.0,不得跨 episode bootstrap)
    policies_dir = os.path.join(state.out_dir, "policies")
    checkpoint, manifest = learner.save_policy(
        policies_dir, opponent_pool_fingerprint=opponent_pool_fingerprint,
        git_commit=commit, bc_anchor_fingerprint=state.anchor["sha256"])
    entry = state.record_policy(checkpoint, manifest_fp=manifest.get("fingerprint", ""),
                                opponent_pool_fingerprint=opponent_pool_fingerprint)
    return checkpoint, {**stats, "policy_version": entry["version"],
                        "rollout_policy_version": source_version,
                        "rollout_fingerprint": policy_fingerprint}


def _policy_callable(path: str, device: str = "cpu"):
    """加载候选/基线/对手策略;兼容 BC(best.pt)与 PPO(policy_N)格式。"""
    from ..evaluate import policy_player

    return policy_player(path, device=device, temperature=0.0,
                         weights_only=False)


def run_gate(state: CampaignState, candidate: str, *, level: str = "smoke",
             baseline: str | None = None, device: str = "cpu",
             seed: int = 0, games: int | None = None, required_pairs: int | None = None) -> dict:
    """candidate vs baseline(默认 legacy 对手)的 paired gate。"""
    from ..training.paired_eval import (
        PairedSchedule, paired_score_report, play_pair,
    )

    if level not in GATE_LEVELS:
        raise ValueError(f"unknown gate level {level!r}; expected {sorted(GATE_LEVELS)}")
    if state.anchor is None:
        raise RuntimeError("paired gate 需要已登记的 BC anchor 作为对手基线")
    need = required_pairs or GATE_LEVELS[level]
    baseline_callable = _policy_callable(baseline or state.anchor["path"], device)
    candidate_callable = _policy_callable(candidate, device)
    opponents = [_policy_callable(state.anchor["path"], device)] * 4

    variants = (False,)
    game_count = max(int(games or 0),
                     int(math.ceil(need / len(variants))))
    schedule = PairedSchedule(seed_start=seed, games=game_count,
                              ycbk_variants=variants)
    rows = [
        play_pair(row, candidate=candidate_callable, baseline=baseline_callable,
                  opponents=opponents, hero_seat=int(row["seat"]))
        for row in schedule.rows(need)
    ]
    report = paired_score_report(rows, required_pairs=need, seed=seed,
                                 matrix="candidate_vs_baseline")
    state.record_paired(candidate=candidate, level=level, verdict=report["verdict"],
                        mean_delta=report["mean_delta"], ci95=report["ci95"],
                        pairs=report["pairs"], meets_required=report["meets_required_pairs"])
    return report


def promote(state: CampaignState, candidate: str, *, level: str = "full",
            device: str = "cpu", seed: int = 0, required_pairs: int | None = None) -> dict:
    """full gate 通过才晋级 Champion;否则不覆盖 champion。"""
    report = run_gate(state, candidate, level=level, device=device, seed=seed,
                      required_pairs=required_pairs)
    if report["verdict"] != "superior":
        return report
    entry = {"candidate": os.path.abspath(candidate), "level": level,
             "verdict": report["verdict"], "mean_delta": report["mean_delta"],
             "ci95": report["ci95"], "sha256": sha256_file(candidate),
             "feature_contract": state.feature_contract}
    state.set_champion(path=candidate, paired=report)
    return report


__all__ = [
    "CAMPAIGN_SCHEMA", "CampaignState", "create", "run_bc", "gen_dagger",
    "run_ppo_round", "run_gate", "promote", "sha256_file", "GATE_LEVELS",
]


def _meta_display(contract: str):
    fc = feature_contract(contract)
    return f"{contract} ({fc.display})"
