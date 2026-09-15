"""自记日志 → BC npz 分片:离线重放重建 obs,严格口径只收干净样本。

用法:
  python3 -m mj.log2data --root local/games --out data/bc_platform \
      [--per-shard 200]

严格过滤(log_replay.replay_game 的 clean 判定 + 决策级配对):
- 对局:无 illegal、无 reset(镜像失步)、有终局积分(end 记录);
- 样本:决策配对的 action 提交成功(非 409;未提交的隐式过窗不收)。

npz 与 mj.bc_data 同构(75 平面;bc_train._pad_oracle 补零到 91),
planes float16 / mask bool / action int16 / seat int8;value 目标
= 终局四家分(score 列,与 bc_data 口径一致)。
"""

import argparse
import glob
import os
import sys

import numpy as np

from .log_replay import replay_game
from .logview import load_records


def collect(paths, mode=None):
    """重放日志 → 干净样本列表 [(samples, end_scores, my_seat)]。

    mode 过滤对局来源(meta.mode):"match"=仅自由对战,"test"=仅非
    自由对战(测试房/正式赛,含无 mode 字段的存量日志),None=全部。
    """
    out, skipped = [], []
    for p in paths:
        try:
            recs = load_records(p)
        except (OSError, ValueError) as e:
            skipped.append((p, f"读取失败: {e}"))
            continue
        if mode is not None:
            meta = next((r for r in recs if r.get("type") == "meta"), None)
            m = (meta or {}).get("mode")
            if (mode == "match") != (m == "match"):
                skipped.append((p, f"mode 不符: {m or '无标记'}"))
                continue
        rep = replay_game(recs)
        if not rep["clean"]:
            skipped.append((p, f"不干净: illegal={len(rep['illegal'])} "
                              f"warnings={len(rep['warnings'])} "
                              f"end={rep['end_scores']}"))
            continue
        if not rep["samples"]:
            skipped.append((p, "无样本"))
            continue
        out.append((rep["samples"], rep["end_scores"], rep["my_seat"]))
    return out, skipped


def write_shards(collected, out_dir, per_shard=200, *, metadata=False):
    """样本 → npz 分片(与 bc_data 同构;跨局拼接)。

    ``metadata=True`` adds the versioned provenance arrays.  The default keeps
    the six-array layout readable by older training jobs; the CLI always
    enables the new contract.
    """
    os.makedirs(out_dir, exist_ok=True)
    flat = []
    for samples, scores, seat in collected:
        for s in samples:
            flat.append((s, scores, seat))
    n_shards = (len(flat) + per_shard - 1) // per_shard
    for k in range(n_shards):
        part = flat[k * per_shard:(k + 1) * per_shard]
        path = os.path.join(out_dir, f"shard_{k:05d}.npz")
        planes = np.asarray([s["planes"] for s, _, _ in part],
                            dtype=np.float16)
        data = {
            "planes": planes,
            "scalars": np.asarray([s["scalars"] for s, _, _ in part],
                                  dtype=np.float32),
            "mask": np.asarray([s["mask"] for s, _, _ in part],
                                dtype=np.bool_),
            "action": np.asarray([s["action_flat"] for s, _, _ in part],
                                  dtype=np.int16),
            "seat": np.asarray([seat for _, _, seat in part], dtype=np.int8),
            "score": np.asarray([scores for _, scores, _ in part],
                                 dtype=np.int32),
        }
        if metadata:
            data.update({
                "context_hash": np.asarray([
                    s.get("context_hash", "") for s, _, _ in part], dtype="U16"),
                "label_source": np.asarray([
                    s.get("label_source", "online_submitted") for s, _, _ in part],
                    dtype="U32"),
                "evaluator": np.asarray([
                    s.get("evaluator", "unknown") for s, _, _ in part], dtype="U24"),
                "scope": np.asarray([
                    s.get("scope", "discard") for s, _, _ in part], dtype="U16"),
                "teacher_confidence": np.asarray([
                    s.get("teacher_confidence", np.nan) for s, _, _ in part],
                    dtype=np.float32),
                "teacher_ev": np.asarray([
                    s.get("teacher_ev", np.nan) for s, _, _ in part],
                    dtype=np.float32),
                "oracle": np.asarray([
                    s.get("oracle", False) for s, _, _ in part], dtype=np.bool_),
                "counterfactual": np.asarray([
                    s.get("counterfactual", False) for s, _, _ in part],
                    dtype=np.bool_),
            })
        np.savez(
            path,
            **data,
        )
        yield path, len(part)
    return n_shards


def main(argv=None):
    ap = argparse.ArgumentParser(description="自记日志 → BC npz(严格口径)")
    ap.add_argument("--root", default="local/games", help="日志根目录")
    ap.add_argument("--out", default="data/bc_platform")
    ap.add_argument("--per-shard", type=int, default=200)
    ap.add_argument("--mode", default="all",
                    choices=("all", "match", "test"),
                    help="对局来源过滤:match=自由对战,test=其他(含存量)")
    ap.add_argument("--legacy-layout", action="store_true",
                    help="兼容旧的六数组 NPZ 布局(默认写入元数据)")
    args = ap.parse_args(argv)
    paths = sorted(glob.glob(os.path.join(args.root, "**", "*.jsonl"),
                             recursive=True))
    if not paths:
        print(f"{args.root} 下无日志", file=sys.stderr)
        return 1
    collected, skipped = collect(paths,
                                 mode=None if args.mode == "all" else args.mode)
    total = 0
    for path, n in write_shards(collected, args.out, args.per_shard,
                                metadata=not args.legacy_layout):
        total += n
        print(f"{path}: {n} 样本")
    print(f"共 {total} 样本 ({len(collected)}/{len(paths)} 局干净, "
          f"{len(skipped)} 局跳过)")
    for p, why in skipped[:20]:
        print(f"  跳过 {p}: {why}")
    if len(skipped) > 20:
        print(f"  … 其余 {len(skipped) - 20} 局略")
    return 0


if __name__ == "__main__":
    sys.exit(main())
