"""Custom discard-only PPO learner with a frozen BC prior."""

from __future__ import annotations

import hashlib
import json
import os
import random
from typing import Mapping

import numpy as np
import torch
import torch.nn.functional as F

from ..model import Net
from .minisuphx_manifest import (
    ACTION_SCOPE_DISCARD,
    FEATURE_CONTRACT,
    VALUE_CONTRACT,
    PolicyManifest,
)

__all__ = [
    "PPOLearner", "compute_gae", "load_shard_arrays", "norm_entropy",
    "bc_kl_coefficient", "shaping_coefficient", "EntropyController",
]


def bc_kl_coefficient(discard_decisions: int) -> float:
    """Default versioned BC-prior schedule from the v1 design."""
    step = max(0, int(discard_decisions))
    if step <= 100_000:
        return 1.0
    if step <= 300_000:
        return 1.0 + (0.3 - 1.0) * (step - 100_000) / 200_000
    if step <= 600_000:
        return 0.3 + (0.1 - 0.3) * (step - 300_000) / 300_000
    return 0.05


def shaping_coefficient(discard_decisions: int) -> float:
    """Default potential-shaping schedule; it is zero after 300k steps."""
    step = max(0, int(discard_decisions))
    if step <= 100_000:
        return 0.005
    if step <= 300_000:
        return 0.005 * (300_000 - step) / 200_000
    return 0.0


def norm_entropy(probs: torch.Tensor, mask: torch.Tensor) -> torch.Tensor:
    """Normalize entropy by the number of legal actions for each state."""
    mask = mask.to(dtype=torch.bool)
    legal_count = mask.sum(-1).clamp(min=2).float()
    raw = -(probs * probs.clamp_min(1e-12).log()).sum(-1)
    return raw / legal_count.log()


def compute_gae(rewards, values, dones, gamma=1.0, lam=0.95):
    """Compute GAE while resetting at every terminal transition."""
    rewards = np.asarray(rewards, dtype=np.float32)
    values = np.asarray(values, dtype=np.float32)
    dones = np.asarray(dones, dtype=bool)
    if not (len(rewards) == len(values) == len(dones)):
        raise ValueError("reward/value/done arrays must have the same length")
    advantages = np.zeros_like(rewards, dtype=np.float32)
    last_advantage = 0.0
    for index in range(len(rewards) - 1, -1, -1):
        terminal = bool(dones[index])
        next_value = 0.0 if terminal or index + 1 >= len(values) \
            else float(values[index + 1])
        nonterminal = 0.0 if terminal else 1.0
        delta = float(rewards[index]) + gamma * next_value * nonterminal \
            - float(values[index])
        last_advantage = delta + gamma * lam * nonterminal * last_advantage
        advantages[index] = last_advantage
    return advantages, (advantages + values).astype(np.float32)


def load_shard_arrays(npz_paths, device="cpu"):
    """Load and validate rollout arrays without retaining open NPZ handles."""
    paths = list(npz_paths)
    if not paths:
        raise ValueError("at least one rollout shard is required")
    required = (
        "planes", "scalars", "mask", "action", "old_log_prob",
        "old_value", "reward", "done",
    )
    collected = {key: [] for key in required}
    optional = {key: [] for key in (
        "episode_start", "seed", "episode_id", "dealer", "agent_seat",
        "terminal_score",
    )}
    for path in paths:
        with np.load(path, allow_pickle=False) as shard:
            missing = [key for key in required if key not in shard]
            if missing:
                raise ValueError(f"rollout {path!r} missing arrays {missing}")
            for key in required:
                collected[key].append(np.asarray(shard[key]))
            for key in optional:
                if key in shard:
                    optional[key].append(np.asarray(shard[key]))
    arrays = {
        key: np.concatenate(values, axis=0) for key, values in collected.items()
    }
    rows = int(arrays["action"].shape[0])
    for key, value in arrays.items():
        if int(value.shape[0]) != rows:
            raise ValueError(f"rollout array {key!r} has inconsistent length")
    masks = arrays["mask"].astype(bool, copy=False)
    actions = arrays["action"].astype(np.int64, copy=False)
    if masks.ndim != 2 or masks.shape[1] != 109:
        raise ValueError(f"rollout mask must have shape [N,109], got {masks.shape}")
    if np.any(actions < 0) or np.any(actions >= 109):
        raise ValueError("rollout contains an out-of-range action")
    if np.any(~masks[np.arange(rows), actions]):
        raise ValueError("rollout contains an illegal masked action")
    dones = arrays["done"].astype(bool, copy=False)
    if optional["episode_start"]:
        episode_start = np.concatenate(optional["episode_start"], axis=0)
    else:
        episode_start = np.zeros(rows, dtype=bool)
        if rows:
            episode_start[0] = True
            episode_start[1:] = dones[:-1]
    if len(episode_start) != rows:
        raise ValueError("episode_start has inconsistent length")
    advantages, returns = compute_gae(
        arrays["reward"], arrays["old_value"], dones)
    tensors = {
        "planes": torch.as_tensor(arrays["planes"].astype(np.float32),
                                   device=device),
        "scalars": torch.as_tensor(arrays["scalars"].astype(np.float32),
                                    device=device),
        "mask": torch.as_tensor(masks, dtype=torch.bool, device=device),
        "action": torch.as_tensor(actions, dtype=torch.long, device=device),
        "old_log_prob": torch.as_tensor(
            arrays["old_log_prob"].astype(np.float32), device=device),
        "old_value": torch.as_tensor(
            arrays["old_value"].astype(np.float32), device=device),
        "advantage": torch.as_tensor(advantages, device=device),
        "returns": torch.as_tensor(returns, device=device),
    }
    metadata = {
        "rows": rows,
        "episode_start": episode_start,
        "dones": dones,
    }
    return tensors, metadata


class EntropyController:
    """Move normalized entropy toward a target band with bounded coefficient."""

    def __init__(self, target_low=0.30, target_high=0.35, coef=0.01,
                 min_coef=1e-4, max_coef=0.05, alpha=0.05):
        if not 0.0 <= target_low <= target_high:
            raise ValueError("entropy target band is invalid")
        self.target_low = float(target_low)
        self.target_high = float(target_high)
        self.coef = float(coef)
        self.min_coef = float(min_coef)
        self.max_coef = float(max_coef)
        self.alpha = float(alpha)
        self.ema: float | None = None

    @property
    def target(self) -> float:
        return (self.target_low + self.target_high) / 2.0

    def update(self, normalized_entropy: float) -> float:
        value = float(normalized_entropy)
        self.ema = (value if self.ema is None else
                    self.alpha * value + (1.0 - self.alpha) * self.ema)
        if self.ema < self.target_low:
            self.coef = min(self.max_coef, self.coef * 1.1)
        elif self.ema > self.target_high:
            self.coef = max(self.min_coef, self.coef / 1.1)
        return self.coef

    def state_dict(self) -> dict:
        return {
            "target_low": self.target_low,
            "target_high": self.target_high,
            "coef": self.coef,
            "min_coef": self.min_coef,
            "max_coef": self.max_coef,
            "alpha": self.alpha,
            "ema": self.ema,
        }

    def load_state_dict(self, state: Mapping) -> None:
        self.coef = float(state.get("coef", self.coef))
        self.ema = (None if state.get("ema") is None
                    else float(state["ema"]))


def _load_checkpoint(path: str, device: str):
    return torch.load(path, map_location=device, weights_only=False)


def _state_dict(checkpoint):
    if isinstance(checkpoint, Mapping) and "state_dict" in checkpoint:
        return checkpoint["state_dict"]
    return checkpoint


def _stored_arch(checkpoint, fallback_blocks: int, fallback_width: int):
    manifest = checkpoint.get("manifest", {}) if isinstance(checkpoint, Mapping) \
        else {}
    blocks = int(checkpoint.get("blocks", fallback_blocks)) \
        if isinstance(checkpoint, Mapping) else fallback_blocks
    width = int(checkpoint.get("width", fallback_width)) \
        if isinstance(checkpoint, Mapping) else fallback_width
    model_arch = manifest.get("model_arch") if isinstance(manifest, Mapping) \
        else None
    if model_arch and model_arch != f"resnet-{blocks}x{width}":
        raise ValueError(
            f"checkpoint architecture metadata mismatch: {model_arch!r} "
            f"vs resnet-{blocks}x{width}")
    return blocks, width


class PPOLearner:
    """Synchronous PPO update on shards produced by one frozen policy."""

    def __init__(self, *, policy_version: int, blocks: int = 6,
                 width: int = 128, anchor: str, policy_path: str | None = None,
                 device: str = "cpu", generation: int = 0,
                 actor_lr=3e-5, critic_lr=1e-4, gamma=1.0,
                 gae_lambda=0.95, clip_range=0.15, ppo_epochs=4,
                 batch_size=1024, max_grad_norm=0.5, bc_reg: float | None = None,
                 ent_target_low=0.30, ent_target_high=0.35,
                 ent_coef=0.01, action_scope: str = ACTION_SCOPE_DISCARD,
                 feature_contract: str = FEATURE_CONTRACT,
                 bc_anchor_fingerprint: str = ""):
        self.device = device
        self.policy_version = int(policy_version)
        self.generation = int(generation)
        self.action_scope = action_scope
        self.feature_contract = feature_contract
        self.bc_anchor_fingerprint = bc_anchor_fingerprint
        anchor_ck = _load_checkpoint(anchor, device)
        anchor_blocks, anchor_width = _stored_arch(anchor_ck, blocks, width)
        if (int(blocks), int(width)) != (anchor_blocks, anchor_width):
            raise ValueError(
                "learner architecture does not match BC anchor: "
                f"requested resnet-{blocks}x{width}, "
                f"anchor resnet-{anchor_blocks}x{anchor_width}")
        self.blocks, self.width = anchor_blocks, anchor_width
        self.net = Net(blocks=self.blocks, width=self.width,
                       n_planes=75).to(device)
        current_ck = (_load_checkpoint(policy_path, device)
                       if policy_path else anchor_ck)
        current_blocks, current_width = _stored_arch(
            current_ck, self.blocks, self.width)
        if (current_blocks, current_width) != (self.blocks, self.width):
            raise ValueError("current policy architecture differs from BC anchor")
        self.net.load_state_dict(_state_dict(current_ck))

        self.anchor = Net(blocks=self.blocks, width=self.width,
                          n_planes=75).to(device)
        self.anchor.load_state_dict(_state_dict(anchor_ck))
        self.anchor.eval()
        for parameter in self.anchor.parameters():
            parameter.requires_grad_(False)

        actor, critic = [], []
        for name, parameter in self.net.named_parameters():
            (critic if name.startswith("v_") else actor).append(parameter)
        self.params = {"actor": actor, "critic": critic}
        self.actor_lr = float(actor_lr)
        self.critic_lr = float(critic_lr)
        self.optimizer = torch.optim.Adam([
            {"params": actor, "lr": self.actor_lr},
            {"params": critic, "lr": self.critic_lr},
        ])
        self.hyper = {
            "gamma": float(gamma), "gae_lambda": float(gae_lambda),
            "clip_range": float(clip_range), "ppo_epochs": int(ppo_epochs),
            "batch_size": int(batch_size),
            "max_grad_norm": float(max_grad_norm),
        }
        self.bc_reg_override = None if bc_reg is None else float(bc_reg)
        self.entropy = EntropyController(
            target_low=ent_target_low, target_high=ent_target_high,
            coef=ent_coef)
        self.global_step = 0
        self.discard_decisions = 0

    def current_bc_kl_coefficient(self) -> float:
        return (self.bc_reg_override if self.bc_reg_override is not None else
                bc_kl_coefficient(self.discard_decisions))

    def _bc_kl(self, planes, scalars, mask):
        mask = mask.to(dtype=torch.bool)
        live_logits, _ = self.net(planes, scalars)
        with torch.no_grad():
            anchor_logits, _ = self.anchor(planes, scalars)
            anchor_logp = F.log_softmax(
                anchor_logits.masked_fill(~mask, float("-inf")), dim=-1)
        live_logp = F.log_softmax(
            live_logits.masked_fill(~mask, float("-inf")), dim=-1)
        live_prob = live_logp.exp()
        # The run contract is KL(pi_live || pi_BC), with BC as the prior.
        log_ratio = torch.where(
            mask, live_logp - anchor_logp, torch.zeros_like(live_logp))
        return (live_prob * log_ratio).sum(-1).mean()

    def _train_epoch(self, tensors):
        n = int(tensors["planes"].shape[0])
        if n == 0:
            raise ValueError("cannot train on an empty rollout")
        permutation = torch.randperm(n, device=tensors["planes"].device)
        stats = []
        for start in range(0, n, self.hyper["batch_size"]):
            index = permutation[start:start + self.hyper["batch_size"]]
            planes = tensors["planes"][index]
            scalars = tensors["scalars"][index]
            mask = tensors["mask"][index].to(dtype=torch.bool)
            action = tensors["action"][index]
            old_log_prob = tensors["old_log_prob"][index]
            advantage = tensors["advantage"][index]
            returns = tensors["returns"][index]
            advantage = (advantage - advantage.mean()) / \
                (advantage.std(unbiased=False) + 1e-8)

            logits, value = self.net(planes, scalars)
            masked_logits = logits.masked_fill(~mask, float("-inf"))
            log_probabilities = F.log_softmax(masked_logits, dim=-1)
            probabilities = log_probabilities.exp()
            log_prob = log_probabilities.gather(
                -1, action.unsqueeze(-1)).squeeze(-1)
            ratio = (log_prob - old_log_prob).exp()
            clip_range = self.hyper["clip_range"]
            policy_loss = -torch.minimum(
                advantage * ratio,
                advantage * ratio.clamp(1.0 - clip_range, 1.0 + clip_range),
            ).mean()
            value_loss = F.mse_loss(value, returns)
            safe_log_probabilities = torch.where(
                mask, log_probabilities, torch.zeros_like(log_probabilities))
            entropy_raw = -(probabilities * safe_log_probabilities).sum(-1).mean()
            entropy_normalized = norm_entropy(probabilities, mask).mean()
            entropy_coef = self.entropy.update(entropy_normalized.item())
            bc_kl = self._bc_kl(planes, scalars, mask)
            bc_coef = self.current_bc_kl_coefficient()
            loss = (policy_loss + 0.5 * value_loss + bc_coef * bc_kl
                    - entropy_coef * entropy_normalized)
            self.optimizer.zero_grad(set_to_none=True)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(
                self.net.parameters(), self.hyper["max_grad_norm"])
            self.optimizer.step()
            self.global_step += 1
            stats.append({
                "loss": float(loss.item()),
                "pg": float(policy_loss.item()),
                "vf": float(value_loss.item()),
                "bc_kl": float(bc_kl.item()),
                "bc_kl_coef": float(bc_coef),
                "entropy_raw": float(entropy_raw.item()),
                "entropy_normalized": float(entropy_normalized.item()),
                "entropy_target": float(self.entropy.target),
                "entropy_coef": float(entropy_coef),
                "legal_action_count": float(mask.sum(-1).float().mean().item()),
            })
        result = {}
        for key in stats[0]:
            result[key] = float(np.mean([row[key] for row in stats]))
        result["entropy_ema"] = self.entropy.ema
        return result

    def update(self, tensors):
        """Run PPO epochs and advance the exposed discard counter once."""
        rows = int(tensors["planes"].shape[0])
        if rows <= 0:
            raise ValueError("cannot update with an empty rollout")
        self.net.train()
        stats = [self._train_epoch(tensors)
                 for _ in range(self.hyper["ppo_epochs"])]
        self.discard_decisions += rows
        self.net.eval()
        result = {}
        for key in stats[0]:
            values = [row[key] for row in stats]
            result[key] = (None if values[0] is None
                           else float(np.mean(values)))
        result["discard_decisions"] = self.discard_decisions
        return result

    def _manifest(self, *, git_commit: str, bc_anchor_fingerprint: str,
                  opponent_pool_fingerprint: str) -> PolicyManifest:
        return PolicyManifest(
            policy_version=self.policy_version,
            generation=self.generation,
            git_commit=git_commit,
            model_arch=f"resnet-{self.blocks}x{self.width}",
            action_scope=self.action_scope,
            value_contract=VALUE_CONTRACT,
            feature_contract=self.feature_contract,
            bc_anchor_fingerprint=(bc_anchor_fingerprint or
                                   self.bc_anchor_fingerprint),
            opponent_pool_fingerprint=opponent_pool_fingerprint,
        )

    def _rng_state(self) -> dict:
        return {
            "python": random.getstate(),
            "numpy": np.random.get_state(),
            "torch": torch.get_rng_state(),
            "cuda": (torch.cuda.get_rng_state_all()
                      if torch.cuda.is_available() else None),
        }

    def _checkpoint_payload(self, manifest: PolicyManifest) -> dict:
        return {
            "state_dict": self.net.state_dict(),
            "blocks": self.blocks,
            "width": self.width,
            "manifest": manifest.to_dict(),
            "optimizer": self.optimizer.state_dict(),
            "scheduler": None,
            "learner_state": {
                "global_step": self.global_step,
                "discard_decisions": self.discard_decisions,
                "policy_version": self.policy_version,
                "generation": self.generation,
                "entropy_controller": self.entropy.state_dict(),
                "rng": self._rng_state(),
            },
            "hyper": dict(self.hyper),
        }

    @staticmethod
    def _sha256(path: str) -> str:
        digest = hashlib.sha256()
        with open(path, "rb") as stream:
            for chunk in iter(lambda: stream.read(1 << 20), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def save_policy(self, out_dir: str, *, opponent_pool_fingerprint: str = "",
                    git_commit: str = "", bc_anchor_fingerprint: str = ""):
        """Save policy weights plus all state needed for strict resume."""
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, f"policy_{self.policy_version:06d}.pt")
        manifest = self._manifest(
            git_commit=git_commit,
            bc_anchor_fingerprint=bc_anchor_fingerprint,
            opponent_pool_fingerprint=opponent_pool_fingerprint)
        tmp_path = path + ".tmp"
        torch.save(self._checkpoint_payload(manifest), tmp_path)
        os.replace(tmp_path, path)
        manifest_json = manifest.to_dict()
        manifest_json["checkpoint_sha256"] = self._sha256(path)
        json_path = path.replace(".pt", ".json")
        tmp_json = json_path + ".tmp"
        with open(tmp_json, "w", encoding="utf-8") as stream:
            json.dump(manifest_json, stream, ensure_ascii=False,
                      sort_keys=True, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp_json, json_path)
        return path, manifest_json

    def load_checkpoint(self, path: str, *, expected_bc_anchor_fingerprint: str = "",
                        expected_policy_version: int | None = None) -> dict:
        """Restore model, optimizer, controller and RNG state strictly."""
        checkpoint = _load_checkpoint(path, self.device)
        manifest = checkpoint.get("manifest", {})
        if manifest.get("value_contract") != VALUE_CONTRACT:
            raise ValueError("checkpoint value contract is incompatible")
        if manifest.get("action_scope") != self.action_scope:
            raise ValueError("checkpoint action scope is incompatible")
        if manifest.get("model_arch") != f"resnet-{self.blocks}x{self.width}":
            raise ValueError("checkpoint model architecture is incompatible")
        anchor_fp = expected_bc_anchor_fingerprint or self.bc_anchor_fingerprint
        if anchor_fp and manifest.get("bc_anchor_fingerprint") != anchor_fp:
            raise ValueError("checkpoint BC anchor fingerprint is incompatible")
        state = checkpoint.get("learner_state", {})
        version = int(state.get("policy_version", manifest.get("policy_version", -1)))
        if expected_policy_version is not None and version != int(expected_policy_version):
            raise ValueError("checkpoint policy version is incompatible")
        self.net.load_state_dict(_state_dict(checkpoint))
        if "optimizer" not in checkpoint:
            raise ValueError("checkpoint has no optimizer state; strict resume refused")
        self.optimizer.load_state_dict(checkpoint["optimizer"])
        self.global_step = int(state.get("global_step", 0))
        self.discard_decisions = int(state.get("discard_decisions", 0))
        self.policy_version = version
        self.generation = int(state.get("generation", manifest.get("generation", 0)))
        self.entropy.load_state_dict(state.get("entropy_controller", {}))
        rng = state.get("rng", {})
        if rng.get("python") is not None:
            random.setstate(rng["python"])
        if rng.get("numpy") is not None:
            np.random.set_state(rng["numpy"])
        if rng.get("torch") is not None:
            torch_state = rng["torch"]
            if hasattr(torch_state, "cpu"):
                torch_state = torch_state.cpu()
            torch.set_rng_state(torch_state)
        if rng.get("cuda") is not None and torch.cuda.is_available():
            cuda_states = [s.cpu() if hasattr(s, "cpu") else s
                           for s in rng["cuda"]]
            torch.cuda.set_rng_state_all(cuda_states)
        return manifest
