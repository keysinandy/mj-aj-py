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
import os
import time
from concurrent.futures import ProcessPoolExecutor

import numpy as np

from .bot import choose_action
from .features import N_ACTIONS, N_PLANES, N_SCALARS, extract, legal_mask, action_to_flat


def generate_game(seed, you_cai_bi_kao=False):
    """跑一局 bot 自博弈,返回 dict(样本数组 + 终局得分)。

    每个决策点采当前行动者一个样本;合法动作只有一个时也采
    (网络仍需学会该局面下唯一解,且分布里有大量此类窗口)。
    """
    from .game import Game

    g = Game(seed=seed, you_cai_bi_kao=you_cai_bi_kao)
    planes, scalars, masks, actions, seats = [], [], [], [], []
    while not g.done:
        seat = g.current_seat()
        p, s = extract(g, seat)
        mask = legal_mask(g)
        act = choose_action(g, seat)
        legal = g.legal_actions()
        assert act in legal, f"teacher 非法动作 {act} (seat {seat}, seed {seed})"
        planes.append(p)
        scalars.append(s)
        masks.append(mask)
        actions.append(action_to_flat(act))
        seats.append(seat)
        g.step(act)
    return {
        "planes": np.asarray(planes, dtype=np.float16),
        "scalars": np.asarray(scalars, dtype=np.float32),
        "mask": np.asarray(masks, dtype=np.bool_),
        "action": np.asarray(actions, dtype=np.int16),
        "seat": np.asarray(seats, dtype=np.int8),
        # 逐样本终局得分(该样本所属局的四家分),value_target 直接索引
        "score": np.tile(np.asarray(g.scores, dtype=np.int32), (len(seats), 1)),
    }


def _write_shard(args):
    seed0, n_games, path, ycbk = args
    parts = [generate_game(seed0 + i, you_cai_bi_kao=ycbk) for i in range(n_games)]
    np.savez(
        path,
        planes=np.concatenate([p["planes"] for p in parts]),
        scalars=np.concatenate([p["scalars"] for p in parts]),
        mask=np.concatenate([p["mask"] for p in parts]),
        action=np.concatenate([p["action"] for p in parts]),
        seat=np.concatenate([p["seat"] for p in parts]),
        score=np.concatenate([p["score"] for p in parts]),
    )
    return path, sum(len(p["action"]) for p in parts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/bc")
    ap.add_argument("--games", type=int, default=2000)
    ap.add_argument("--per-shard", type=int, default=100)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--seed0", type=int, default=0)
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
        ))
    t0 = time.time()
    total = 0
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        for path, n in ex.map(_write_shard, jobs):
            total += n
            print(f"{path}: {n} 样本 ({time.time() - t0:.0f}s)", flush=True)
    print(f"共 {total} 样本, {n_shards} 分片, {time.time() - t0:.0f}s, "
          f"{total / (time.time() - t0):.0f} 样本/秒")


if __name__ == "__main__":
    main()
