"""Collect policy-version-locked discard rollouts and publish shards."""

from __future__ import annotations

import json
import os

import numpy as np
import torch

from .rl_discard_env import MahjongDiscardEnv

__all__ = ["gather_rollout", "write_rollout_shard"]

_ARRAY_KEYS = (
    "planes", "scalars", "mask", "action", "old_log_prob", "old_value",
    "reward", "done", "episode_start", "seed", "episode_id", "dealer",
    "agent_seat", "terminal_score",
)


def _empty_arrays():
    return {key: [] for key in _ARRAY_KEYS}


def gather_rollout(policy, *, n_decisions: int, seed: int = 0, hero: int = 0,
                   ycbk: bool = False, max_episodes: int = 0,
                   shape_k: float = 0.0, strict_learned: bool = True):
    """Collect complete episodes until at least ``n_decisions`` are present.

    The final episode is allowed to finish after the target count so every
    shard has a terminal boundary for GAE.  The sampled action and old values
    are recorded from the exact frozen policy used by the actor.

    ``max_episodes`` is a fail-loud ceiling, not a rollout budget.  When it is
    ``<= 0`` it is scaled from ``n_decisions`` so that large rollouts are not
    silently truncated by a fixed small cap (a fixed ``500`` capped real runs
    at ~4.3k discard decisions).
    """
    if int(n_decisions) <= 0:
        raise ValueError("n_decisions must be positive")
    if int(max_episodes) <= 0:
        max_episodes = int(n_decisions) * 3 + 300
    try:
        device = next(policy.parameters()).device
    except (StopIteration, AttributeError):
        device = torch.device("cpu")
    env = MahjongDiscardEnv(
        you_cai_bi_kao=ycbk, seed=seed, hero=hero, shape_k=shape_k,
        strict_learned=strict_learned)
    out = _empty_arrays()
    collected = 0
    episodes = 0
    ep_seed = int(seed)
    episode_meta = []
    was_training = getattr(policy, "training", None)
    if hasattr(policy, "eval"):
        policy.eval()
    try:
        while collected < int(n_decisions) and episodes < int(max_episodes):
            obs = env.reset(seed=ep_seed)
            episode_seed = ep_seed
            ep_seed += 1
            episode_id = episodes
            episodes += 1
            episode_start = True
            done = False
            dealer = int(env.game.dealer)
            while not done:
                planes, scalars = obs
                mask = env.action_mask().astype(bool, copy=True)
                if not bool(mask.any()):
                    raise RuntimeError("rollout reached a state with no legal discard")
                planes_t = torch.as_tensor(
                    planes, dtype=torch.float32, device=device).unsqueeze(0)
                scalars_t = torch.as_tensor(
                    scalars, dtype=torch.float32, device=device).unsqueeze(0)
                mask_t = torch.as_tensor(
                    mask, dtype=torch.bool, device=device).unsqueeze(0)
                with torch.no_grad():
                    logits, value = policy(planes_t, scalars_t)
                    masked_logits = logits.masked_fill(~mask_t, float("-inf"))
                    dist = torch.distributions.Categorical(logits=masked_logits)
                    action_t = dist.sample()
                    action = int(action_t.item())
                    if not bool(mask[action]):
                        raise RuntimeError(
                            f"policy sampled illegal discard {action}")
                    log_prob = float(dist.log_prob(action_t).item())
                    old_value = float(value.reshape(-1)[0].item())
                obs, _next_mask, reward, done, info = env.step(
                    action, strict=strict_learned)
                terminal_score = (float(info["terminal_score"])
                                  if done and info.get("terminal_score") is not None
                                  else np.nan)
                for key, value_to_append in {
                    "planes": np.asarray(planes, dtype=np.float16),
                    "scalars": np.asarray(scalars, dtype=np.float32),
                    "mask": mask,
                    "action": action,
                    "old_log_prob": log_prob,
                    "old_value": old_value,
                    "reward": float(reward),
                    "done": bool(done),
                    "episode_start": bool(episode_start),
                    "seed": episode_seed,
                    "episode_id": episode_id,
                    "dealer": dealer,
                    "agent_seat": int(hero),
                    "terminal_score": terminal_score,
                }.items():
                    out[key].append(value_to_append)
                episode_start = False
                collected += 1
            episode_meta.append({
                "episode_id": episode_id,
                "seed": episode_seed,
                "dealer": dealer,
                "agent_seat": int(hero),
                "terminal_score": float(env.game.scores[hero]),
                "discard_decisions": int(env.discard_decisions),
            })
        if collected < int(n_decisions):
            raise RuntimeError(
                f"rollout collected {collected} decisions in {episodes} episodes; "
                f"required {n_decisions}")
    finally:
        if was_training is True and hasattr(policy, "train"):
            policy.train()
    result = {
        "planes": np.asarray(out["planes"], dtype=np.float16),
        "scalars": np.asarray(out["scalars"], dtype=np.float32),
        "mask": np.asarray(out["mask"], dtype=bool),
        "action": np.asarray(out["action"], dtype=np.int16),
        "old_log_prob": np.asarray(out["old_log_prob"], dtype=np.float32),
        "old_value": np.asarray(out["old_value"], dtype=np.float32),
        "reward": np.asarray(out["reward"], dtype=np.float32),
        "done": np.asarray(out["done"], dtype=bool),
        "episode_start": np.asarray(out["episode_start"], dtype=bool),
        "seed": np.asarray(out["seed"], dtype=np.int32),
        "episode_id": np.asarray(out["episode_id"], dtype=np.int32),
        "dealer": np.asarray(out["dealer"], dtype=np.int8),
        "agent_seat": np.asarray(out["agent_seat"], dtype=np.int8),
        "terminal_score": np.asarray(out["terminal_score"], dtype=np.float32),
        "n_episodes": episodes,
        "episode_meta": episode_meta,
        "shape_k": float(shape_k),
    }
    return result


def write_rollout_shard(out_dir: str, data: dict, manifest: dict):
    """Atomically write ``rollout.npz`` and publish ``manifest.json`` last."""
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "rollout.npz")
    tmp_path = path + ".tmp.npz"
    arrays = {}
    for key in _ARRAY_KEYS:
        if key not in data:
            raise ValueError(f"rollout missing array {key!r}")
        arrays[key] = data[key]
    np.savez(tmp_path, **arrays)
    os.replace(tmp_path, path)

    manifest_path = os.path.join(out_dir, "manifest.json")
    tmp_manifest = manifest_path + ".tmp"
    with open(tmp_manifest, "w", encoding="utf-8") as stream:
        json.dump(dict(manifest), stream, ensure_ascii=False,
                  sort_keys=True, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp_manifest, manifest_path)
    return manifest_path
