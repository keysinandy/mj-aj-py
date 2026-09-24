"""BC 冷启动数据生成:启发式 bot 自博弈 → (obs, mask, action, value) 样本。

样本定义:每个决策点(摸打/吃碰杠窗)取当前行动者视角的特征平面、
合法动作掩码、teacher 动作;value 目标 = 该座位终局得分(训练时再归一)。

teacher 动作必须合法(断言),数据生成失败即暴露 bot bug。
多进程分片:每 worker 生成 per_shard 局写一个 .npz(savez 未压缩,
planes 用 float16 省一半磁盘)。

用法:
  python -m mj.bc_data --out data/bc --games 2000 --workers 8 --per-shard 100
"""

import argparse
import json
import os
import time
from concurrent.futures import ProcessPoolExecutor

import numpy as np

from .bot import choose_action
from .features import N_ACTIONS, N_PLANES, N_SCALARS, extract, legal_mask, action_to_flat
from .legacy_eval import (
    LEGACY_V2_EVALUATORS,
    LEGACY_V2_OFFLINE_EVALUATORS,
    WEIGHTED_OFFLINE_PROFILE_VERSION,
)

# 训练标签默认走"离线、不因预算回退"的 legacyV2 profile;legacy 与
# shape-* 评价器不受影响。
TRAINING_BOT_EVALUATOR = WEIGHTED_OFFLINE_PROFILE_VERSION
SEARCH_EVALUATORS = LEGACY_V2_EVALUATORS + LEGACY_V2_OFFLINE_EVALUATORS
# 设计内的作用域委托(不算预算回退):V1 契约只在 discard 相位给出搜索
# 结论,爆头/胡杠/吃碰窗以 ``*_scope`` 记录,唯一合法动作单独命名。
SCOPE_DELEGATION_REASONS = frozenset({"only_legal_action"})


def search_fallback_reason(evaluation):
    """返回搜索标签实际回退到 legacy 的原因;非回退返回 None。

    ``level == "legacy"`` 表示 selected 来自 legacy 启发式;``baotou_scope``
    与 ``only_legal_action`` 是设计内的委托,单例前沿(legacy-one-ply)则是
    评价器自己的结论,都不算回退。
    """
    if not isinstance(evaluation, dict):
        return None
    level = str(evaluation.get("level") or "")
    if level != "legacy":
        return None
    reason = evaluation.get("fallback_reason")
    if reason is None:
        return None
    reason = str(reason)
    if reason in SCOPE_DELEGATION_REASONS or reason.endswith("_scope"):
        return None
    return reason

def generate_game(seed, you_cai_bi_kao=False, evaluator=TRAINING_BOT_EVALUATOR,
                  scope="all-root", teacher_metadata=None,
                  allow_search_fallback=False):
    """跑一局 bot 自博弈,返回 dict(样本数组 + 终局得分)。

    每个决策点采当前行动者一个样本;合法动作只有一个时也采
    (网络仍需学会该局面下唯一解,且分布里有大量此类窗口)。
    """
    from .game import Game

    g = Game(seed=seed, you_cai_bi_kao=you_cai_bi_kao)
    planes, scalars, masks, actions, seats = [], [], [], [], []
    context_hashes, label_sources, evaluator_names = [], [], []
    scopes, teacher_confidence, teacher_ev = [], [], []
    label_levels, label_fallbacks = [], []
    oracle, counterfactual = [], []
    teacher_metadata = teacher_metadata or {}
    while not g.done:
        seat = g.current_seat()
        # PublicDecisionContext deliberately projects only the actor hand and
        # public material.  The hash is useful metadata, not an oracle input.
        from .decision.context import PublicDecisionContext
        context_hash = PublicDecisionContext.from_game(g, seat).context_hash
        p, s = extract(g, seat, oracle=False)
        mask = legal_mask(g)
        result = choose_action(
            g, seat, evaluator=evaluator,
            return_evaluation=evaluator not in (None, "legacy"))
        evaluation = None
        if isinstance(result, tuple):
            act, evaluation = result
        else:
            act = result
        # 训练标签必须来自搜索本身:一旦 legacyV2 系评价器回退到 legacy,
        # 立刻失败,而不是把 legacy 动作当成搜索标签写进数据集。
        if evaluator in SEARCH_EVALUATORS:
            fallback_reason = search_fallback_reason(evaluation)
            if fallback_reason and not allow_search_fallback:
                raise RuntimeError(
                    f"legacyV2 搜索回退 {fallback_reason} "
                    f"(seed {seed}, seat {seat});"
                    " 训练标签不允许回退,请检查预算或显式允许回退")
            label_levels.append(str((evaluation or {}).get("level") or ""))
            label_fallbacks.append(fallback_reason or "")
        else:
            label_levels.append("")
            label_fallbacks.append("")
        legal = g.legal_actions()
        assert act in legal, f"teacher 非法动作 {act} (seat {seat}, seed {seed})"
        planes.append(p)
        scalars.append(s)
        masks.append(mask)
        actions.append(action_to_flat(act))
        seats.append(seat)
        context_hashes.append(context_hash)
        # evaluator=None 走 bot 的 legacy 启发式,记录时按 legacy 记名。
        evaluator_names.append(str(evaluator or "legacy"))
        scopes.append(str(scope))
        label_sources.append("online_policy" if evaluator in (None, "legacy")
                             else "online_evaluator")
        # Teacher EV is a separate label channel and is never inferred from
        # an online/Q explanation.  Missing values are NaN, not zero.
        teacher = teacher_metadata.get(context_hash, {})
        teacher_confidence.append(float(teacher.get("confidence", np.nan)))
        teacher_ev.append(float(teacher.get("EV", np.nan)))
        oracle.append(False)
        counterfactual.append(False)
        g.step(act)
    return {
        "planes": np.asarray(planes, dtype=np.float16),
        "scalars": np.asarray(scalars, dtype=np.float32),
        "mask": np.asarray(masks, dtype=np.bool_),
        "action": np.asarray(actions, dtype=np.int16),
        "seat": np.asarray(seats, dtype=np.int8),
        # 逐样本终局得分(该样本所属局的四家分),value_target 直接索引
        "score": np.tile(np.asarray(g.scores, dtype=np.int32), (len(seats), 1)),
        "context_hash": np.asarray(context_hashes, dtype="U16"),
        "label_source": np.asarray(label_sources, dtype="U32"),
        "evaluator": np.asarray(evaluator_names, dtype="U24"),
        "scope": np.asarray(scopes, dtype="U16"),
        "label_level": np.asarray(label_levels, dtype="U24"),
        "label_fallback_reason": np.asarray(label_fallbacks, dtype="U32"),
        "teacher_confidence": np.asarray(teacher_confidence, dtype=np.float32),
        "teacher_ev": np.asarray(teacher_ev, dtype=np.float32),
        "oracle": np.asarray(oracle, dtype=np.bool_),
        "counterfactual": np.asarray(counterfactual, dtype=np.bool_),
    }


def _write_shard(args):
    seed0, n_games, path, ycbk = args[:4]
    include_metadata = bool(args[4]) if len(args) > 4 else False
    evaluator = str(args[5]) if len(args) > 5 else TRAINING_BOT_EVALUATOR
    scope = str(args[6]) if len(args) > 6 else "all-root"
    allow_fallback = bool(args[7]) if len(args) > 7 else False
    parts = [generate_game(seed0 + i, you_cai_bi_kao=ycbk,
                           evaluator=evaluator, scope=scope,
                           allow_search_fallback=allow_fallback)
             for i in range(n_games)]
    payload = dict(
        planes=np.concatenate([p["planes"] for p in parts]),
        scalars=np.concatenate([p["scalars"] for p in parts]),
        mask=np.concatenate([p["mask"] for p in parts]),
        action=np.concatenate([p["action"] for p in parts]),
        seat=np.concatenate([p["seat"] for p in parts]),
        score=np.concatenate([p["score"] for p in parts]),
    )
    if include_metadata:
        for key in ("context_hash", "label_source", "evaluator", "scope",
                    "label_level", "label_fallback_reason",
                    "teacher_confidence", "teacher_ev", "oracle",
                    "counterfactual"):
            payload[key] = np.concatenate([p[key] for p in parts])
    # Keep the historical six-array shard shape for direct legacy callers;
    # production CLI jobs opt into the versioned metadata contract below.
    np.savez(path, **payload)
    return path, sum(len(p["action"]) for p in parts)


def _write_manifest(args, total_samples: int, n_shards: int) -> None:
    """写出数据集身份 manifest(json + fingerprint),供 streaming BC 校验。

    task 13.6 / 2.1:数据集身份由 teacher(evaluator/scope)、seed_domain、
    特征契约、样本量共同冻结;streaming trainer 在 resume 时用它做防呆。
    """
    from .decision.profile import fingerprint
    from .training.minisuphx_manifest import FEATURE_PUBLIC, git_head

    domain = {
        "train_seed_lo": args.seed0,
        "train_seed_hi": args.seed0 + args.games - 1,
        "games": args.games,
    }
    payload = {
        "schema": "minisuphx-bc-data-v1",
        "feature_contract": FEATURE_PUBLIC,
        "evaluator": args.evaluator,
        "scope": args.scope,
        "you_cai_bi_kao": bool(args.you_cai_bi_kao),
        "allow_search_fallback": bool(args.allow_search_fallback),
        "seed_domain": domain,
        "games": args.games,
        "n_shards": n_shards,
        "per_shard": args.per_shard,
        "samples": int(total_samples),
        "git_commit": git_head(),
    }
    manifest = dict(payload)
    manifest["fingerprint"] = fingerprint(payload, 24)
    out = os.path.join(args.out, "manifest.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    print(f"manifest: {out} ({manifest['fingerprint']})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/bc")
    ap.add_argument("--games", type=int, default=2000)
    ap.add_argument("--per-shard", type=int, default=100)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--seed0", type=int, default=0)
    ap.add_argument("--legacy-layout", action="store_true",
                    help="兼容旧的六数组 NPZ 布局(默认写入 provenance 元数据)")
    ap.add_argument("--evaluator", choices=("legacy", "legacy-two-ply-v1",
                                             "legacyV2", "legacy-v2",
                                             "legacyV2-offline",
                                             "legacy-v2-offline",
                                             "weighted-two-ply-frontier-v1",
                                             "shape-v1", "shape-v2"),
                    default=TRAINING_BOT_EVALUATOR)
    ap.add_argument("--allow-search-fallback", action="store_true",
                    help="允许 legacyV2 系评价器回退 legacy(默认禁止,失败即报错)")
    ap.add_argument("--scope", choices=("discard", "hu-piao", "all-root"),
                    default="all-root")
    ap.add_argument("--you-cai-bi-kao", action="store_true",
                    help="有财必拷响(手有财神须爆头/杠开才可胡)")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    n_shards = (args.games + args.per_shard - 1) // args.per_shard
    jobs = []
    for k in range(n_shards):
        n = min(args.per_shard, args.games - k * args.per_shard)
        jobs.append((
            args.seed0 + k * args.per_shard,
            n,
            os.path.join(args.out, f"shard_{k:05d}.npz"),
            args.you_cai_bi_kao,
            not args.legacy_layout,
            args.evaluator,
            args.scope,
            args.allow_search_fallback,
        ))
    t0 = time.time()
    total = 0
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        for path, n in ex.map(_write_shard, jobs):
            total += n
            print(f"{path}: {n} 样本 ({time.time() - t0:.0f}s)", flush=True)
    if not args.legacy_layout:
        _write_manifest(args, total, n_shards)
    print(f"共 {total} 样本, {n_shards} 分片, {time.time() - t0:.0f}s, "
          f"{total / (time.time() - t0):.0f} 样本/秒")


if __name__ == "__main__":
    main()
