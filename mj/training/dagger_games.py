"""DAgger distribution-repair data generation (design 5 / plan §15 step 5).

区别于 BC(`legacy_bc_games`)的语义:

* 样本状态来自一个**混合执行者**策略(legacy 与 learned 按概率混合)访问的真实
  分布,而不是纯 legacy 自博弈;
* 普通弃牌的**标签始终来自 T0(legacyV2-offline teacher)**,edits learned
  只是执行者,绝不改 label → ``teacher_policy == legacy``;
* 每条样本记录 provenance:`source_policy`(谁执行)、`executed_action`、
  `teacher_action`、`disagreement`(executed != teacher)。

DAgger 的目标是修复 BC 的状态分布偏移:策略在其自访问的状态上被 teacher 校正,
从而靠近 teacher 的真实决策面。

输出 shard 保持与 BC/streaming 相同的键(planes/scalars/mask/action/seat/score/
big_hand_shadow),可直接被 :mod:`streaming_bc` 载入训练,extra 是 provenance
列(``source_policy/teacher_action/executed_action/disagreement``)。

distributed handler:``dagger_games``(注册到 worker_runtime.HANDLERS)。
"""

from __future__ import annotations

import os

import numpy as np

from ..bc_data import (
    SHADOW_KEYS,
    TRAINING_BOT_EVALUATOR,
    big_hand_shadow_from_game,
    training_teacher_fingerprint,
)
from ..features import N_ACTIONS, action_to_flat, extract, legal_mask
from ..game import Game
from ..hybrid_policy import HybridPolicy

__all__ = [
    "generate_dagger_game", "execute_dagger_job",
    "dagger_shard_from_games", "dagger_manifest_meta",
]

DAGGER_SCHEMA = "minisuphx-dagger-v1"


def _load_discard_policy(ckpt: str, device: str = "cpu"):
    """从 public-75 BC checkpoint 载入弃牌执行者;(game, seat)->engine action。"""
    from ..evaluate import policy_player

    return policy_player(ckpt, device=device, temperature=0.0)


def generate_dagger_game(seed: int, *, you_cai_bi_kao: bool = False,
                         teacher_evaluator: str = TRAINING_BOT_EVALUATOR,
                         learned_policy=None, learned_exec_prob: float = 0.0,
                         hero: int = 0, dagger_rng_seed: int | None = None):
    """跑一局 DAgger 自博弈,返回样本数组与 per-sample provenance dict。

    每个 hero 决策点:
      * 标签 = gate 的 legacy_action(T0 teacher 在该状态的动作);
      * 执行者 = learned(若该决策是普通弃牌且掷得 learned) 或 teacher;
      * 记录 provenance 与 disagreement。

    ``learned_exec_prob`` 为在本局中 learned 充当执行者的概率
    (设计:DAgger D1 30% / D2 60% / D3 90% learned 执行 → 传 0.3/0.6/0.9)。
    """
    from ..bot import choose_action

    rng = np.random.default_rng(dagger_rng_seed
                                if dagger_rng_seed is not None else seed)
    g = Game(seed=seed, you_cai_bi_kao=you_cai_bi_kao)
    hybrid = HybridPolicy(evaluator=teacher_evaluator,
                          learned=learned_policy)

    rows = {
        "planes": [], "scalars": [], "mask": [], "action": [],
        "seat": [], "shadow": [], "source_policy": [],
        "teacher_action": [], "executed_action": [], "disagreement": [],
    }
    while not g.done:
        seat = g.current_seat()
        if seat != hero:
            # 对手一律 teacher
            act = choose_action(g, seat, evaluator=teacher_evaluator)
            if isinstance(act, tuple):
                act = act[0]
            if int(act) not in g.legal_actions():
                act = int(g.legal_actions()[0])
            g.step(int(act))
            continue

        decision = hybrid.gate(g, hero)
        teacher_action = decision.legacy_action
        if not decision.learned_allowed:
            # hero 非普通弃牌状态由 legacy gate 自动推进,不采 DAgger 监督样本
            g.step(teacher_action)
            continue

        # 仅 hero 普通弃牌状态是 DAgger 监督样本(与 discard-only RL 对齐)
        exec_learned = (learned_policy is not None and
                        rng.random() < float(learned_exec_prob))
        if exec_learned:
            learned_act = learned_policy(g, hero)
            if isinstance(learned_act, tuple):
                learned_act = learned_act[0]
            decided = hybrid.resolve(g, hero, int(learned_act),
                                     strict=False, decision=decision)
            executed = decided.selected_action
            source = (f"learned_fallback" if decided.fallback
                      else f"learned")
        else:
            executed = teacher_action
            source = "legacy"

        planes, scalars = extract(g, hero, oracle=False)
        mask = legal_mask(g)
        rows["planes"].append(planes.astype(np.float16))
        rows["scalars"].append(scalars)
        rows["mask"].append(mask)
        rows["action"].append(action_to_flat(teacher_action))
        rows["seat"].append(int(hero))
        rows["shadow"].append(big_hand_shadow_from_game(g, hero))
        rows["source_policy"].append(source)
        rows["teacher_action"].append(action_to_flat(teacher_action))
        rows["executed_action"].append(action_to_flat(executed))
        rows["disagreement"].append(
            1.0 if executed != teacher_action else 0.0)
        g.step(executed)

    scores = np.tile(np.asarray(g.scores, dtype=np.int32), (len(rows["action"]), 1))
    return {
        "planes": np.asarray(rows["planes"], dtype=np.float16),
        "scalars": np.asarray(rows["scalars"], dtype=np.float32),
        "mask": np.asarray(rows["mask"], dtype=np.bool_),
        "action": np.asarray(rows["action"], dtype=np.int16),
        "seat": np.asarray(rows["seat"], dtype=np.int8),
        "score": scores,
        "big_hand_shadow": np.asarray(rows["shadow"], dtype=np.float32),
        "source_policy": np.asarray(rows["source_policy"], dtype="U16"),
        "teacher_action": np.asarray(rows["teacher_action"], dtype=np.int16),
        "executed_action": np.asarray(rows["executed_action"], dtype=np.int16),
        "disagreement": np.asarray(rows["disagreement"], dtype=np.float32),
    }


def dagger_shard_from_games(parts):
    """多局 dict → 单分片 npz payload(逐键 concat)。"""
    keys = ("planes", "scalars", "mask", "action", "seat", "score",
            "big_hand_shadow", "source_policy", "teacher_action",
            "executed_action", "disagreement")
    out = {}
    for key in keys:
        if key in parts[0]:
            out[key] = np.concatenate([p[key] for p in parts])
    return out


def dagger_manifest_meta(seed_start: int, games: int, n_samples: int,
                         learned_ckpt: str, learned_exec_prob: float,
                         teacher_evaluator: str) -> dict:
    """handler 返回的 provenance meta(并入结果 manifest)。"""
    return {
        "schema": DAGGER_SCHEMA,
        "campaign_id": "",
        "seed_start": int(seed_start),
        "games": int(games),
        "transition_count": int(n_samples),
        "teacher_policy": "legacy",
        "teacher_evaluator": teacher_evaluator,
        "teacher_fingerprint": training_teacher_fingerprint(),
        "learned_ckpt": str(learned_ckpt),
        "learned_exec_prob": float(learned_exec_prob),
        "value_contract": "round-score-v2-normalized",
        "action_scope": "discard-only-v1",
        "feature_contract": "public-v1",
        "big_hand_shadow_contract": "big-hand-shadow-v1",
        "big_hand_shadow_keys": list(SHADOW_KEYS),
    }


def execute_dagger_job(job: dict, *, cache_dir: str) -> dict:
    payload = job.get("payload") or {}
    seed_start = int(payload.get("seed_start", 0))
    games = int(payload.get("games", 1))
    ycbk = bool(payload.get("you_cai_bi_kao", False))
    hero = int(payload.get("hero", 0))
    teacher_evaluator = str(payload.get("teacher_evaluator", TRAINING_BOT_EVALUATOR))
    learned_exec_prob = float(payload.get("learned_exec_prob", 0.0))
    learned_ckpt = str(payload.get("learned_ckpt", "") or "")
    device = str(payload.get("device", "cpu"))

    learned_policy = None
    if learned_ckpt and os.path.exists(learned_ckpt):
        learned_policy = _load_discard_policy(learned_ckpt, device=device)

    local_dir = os.path.join(cache_dir, job["job_id"])
    os.makedirs(local_dir, exist_ok=True)
    out = os.path.join(local_dir, "rollout.npz")
    parts = [
        generate_dagger_game(
            seed_start + i, you_cai_bi_kao=ycbk,
            teacher_evaluator=teacher_evaluator,
            learned_policy=learned_policy,
            learned_exec_prob=learned_exec_prob, hero=hero,
            dagger_rng_seed=seed_start + i)
        for i in range(games)
    ]
    payload_out = dagger_shard_from_games(parts)
    np.savez(out, **payload_out)
    n_samples = int(len(payload_out["action"]))
    meta = dagger_manifest_meta(seed_start, games, n_samples,
                                learned_ckpt, learned_exec_prob,
                                teacher_evaluator)
    # per-shard source/disagreement 汇总
    src = np.concatenate([p["source_policy"] for p in parts])
    disc = np.concatenate([p["disagreement"] for p in parts])
    learned_msk = src == np.asarray("learned", dtype="U16")
    meta["n_learned_exec"] = int(learned_msk.sum())
    meta["disagreement_rate"] = float(np.mean(disc)) if len(disc) else 0.0
    return meta


from .distributed_bc import HANDLERS  # noqa: E402

HANDLERS["dagger_games"] = execute_dagger_job
__all__ = [
    "generate_dagger_game", "execute_dagger_job", "dagger_manifest_meta",
    "dagger_shard_from_games",
]