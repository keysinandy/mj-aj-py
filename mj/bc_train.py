"""BC(行为克隆)训练:masked CE + 花色置换增广 ×6 + value 辅助头。

value 目标 = clip(座位终局得分/24, -4, 4)/4,tanh 输出直接 MSE。
增广在批次内随机选置换(numpy 列重排,开销远小于前向)。

用法:
  python -m mj.bc_train --data 'data/bc/shard_*.npz' --epochs 10 \
      --blocks 4 --width 128 --out runs/bc0
"""

import argparse
import glob
import json
import os
import time

import numpy as np
import torch

from .features import N_PLANES_ORACLE, SUIT_PERMS
from .model import Net, masked_ce


def _pad_oracle(planes):
    """75 平面 → 91:oracle 段零填充(与 PPO 网络同构,checkpoint 互通)。"""
    n = planes.shape[0]
    if planes.shape[1] >= N_PLANES_ORACLE:
        return planes
    pad = np.zeros((n, N_PLANES_ORACLE - planes.shape[1], planes.shape[2]),
                   dtype=planes.dtype)
    return np.concatenate([planes, pad], axis=1)


def load_shards(pattern, val_ratio=0.1, seed=0):
    paths = sorted(glob.glob(pattern))
    assert paths, f"无数据分片: {pattern}"
    rng = np.random.default_rng(seed)
    idx = rng.permutation(len(paths))
    n_val = max(1, int(len(paths) * val_ratio))
    val_paths = [paths[i] for i in idx[:n_val]]
    train_paths = [paths[i] for i in idx[n_val:]]

    def stack(paths_):
        d = {k: [] for k in ("planes", "scalars", "mask", "action", "seat", "score")}
        for p in paths_:
            z = np.load(p)
            for k in d:
                d[k].append(z[k])
        out = {k: np.concatenate(v) for k, v in d.items()}
        out["planes"] = _pad_oracle(out["planes"])
        return out

    return stack(train_paths), stack(val_paths)


def value_target(score, seat):
    """座位终局得分 → tanh 目标:clip(score/24, ±4)/4。"""
    z = np.clip(score[np.arange(len(seat)), seat] / 24.0, -4, 4) / 4.0
    return z.astype(np.float32)


def augment_batch(planes, mask, action, rng):
    """批次内逐样本随机花色置换(×6 增广)。"""
    out_p = np.empty_like(planes)
    out_m = np.empty_like(mask)
    out_a = np.empty_like(action)
    for i in range(len(action)):
        q, a = SUIT_PERMS[rng.integers(len(SUIT_PERMS))]
        out_p[i] = planes[i][..., q]
        out_m[i][a] = mask[i]
        out_a[i] = a[action[i]]
    return out_p, out_m, out_a


def iterate(n, bs, shuffle=True, rng=None):
    order = rng.permutation(n) if shuffle else np.arange(n)
    for k in range(0, n, bs):
        yield order[k:k + bs]


def evaluate(model, d, device, bs=1024):
    """验证:masked CE、top-1 准确率、value MSE(无增广)。"""
    model.eval()
    tgt = value_target(d["score"], d["seat"])
    tot, n = 0.0, 0
    correct = 0
    with torch.no_grad():
        for sl in iterate(len(tgt), bs, shuffle=False):
            planes = torch.as_tensor(d["planes"][sl], dtype=torch.float32, device=device)
            scalars = torch.as_tensor(d["scalars"][sl], dtype=torch.float32, device=device)
            mask = torch.as_tensor(d["mask"][sl], device=device)
            action = torch.as_tensor(d["action"][sl], dtype=torch.long, device=device)
            v_t = torch.as_tensor(tgt[sl], device=device)
            logits, v = model(planes, scalars)
            tot += masked_ce(logits, mask, action).item() * len(sl)
            pred = logits.masked_fill(~mask, float("-inf")).argmax(dim=-1)
            correct += (pred == action).sum().item()
            n += len(sl)
    model.train()
    return tot / n, correct / n


def init_from_ckpt(path, device="cpu"):
    """读 checkpoint 架构与权重(微调口,--init)。

    BC 格式 {"state_dict", "blocks", "width"} 全量载入;PPO 格式
    {"net", "action_net", ...} 载主干+value 头,policy 头取
    action_net 前 68 列(p_conv 特征段;后 34 列是 v_conv 特征贡献,
    BC 头不含该输入,丢弃作暖启动)。返回 (blocks, width, 部分头?)。
    """
    ck = torch.load(path, map_location=device, weights_only=True)
    blocks = ck.get("blocks")
    width = ck.get("width")
    return ck, blocks, width


def load_init_weights(model, ck, device="cpu"):
    from .features import N_PLANES, N_SCALARS

    bc_format = "state_dict" in ck
    sd = ck["state_dict"] if bc_format else ck["net"]
    w = sd.get("stem.0.weight")
    if w is not None and w.shape[1] < model.stem[0].weight.shape[1]:
        # 老 75 平面 BC checkpoint → 91:stem 输入布局 [planes|scalars],
        # oracle 段插在 planes 与 scalars 之间——planes 权重原位保留、
        # oracle 段补零(输入恒零)、scalars 权重后移到新 planes 段之后
        n_p = w.shape[1] - N_SCALARS
        new_n = model.stem[0].weight.shape[1] - N_SCALARS
        pad = torch.zeros_like(model.stem[0].weight)
        pad[:, :n_p] = w[:, :n_p]
        pad[:, new_n:new_n + N_SCALARS] = w[:, n_p:]
        sd = dict(sd)
        sd["stem.0.weight"] = pad
    model.load_state_dict(sd)
    if bc_format:
        return False
    # PPO 格式:policy 头取 action_net 前 68 列近似暖启动
    w2 = ck["action_net"]["weight"]  # [109, 102] = p_conv(68)|v_conv(34)
    with torch.no_grad():
        model.p_fc.weight.copy_(w2[:, : model.p_fc.in_features])
        model.p_fc.bias.copy_(ck["action_net"]["bias"])
    return True  # policy 头为近似(暖启动)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/bc/shard_*.npz")
    ap.add_argument("--epochs", type=int, default=10)
    ap.add_argument("--bs", type=int, default=512)
    ap.add_argument("--lr", type=float, default=3e-4)
    ap.add_argument("--blocks", type=int, default=4)
    ap.add_argument("--width", type=int, default=128)
    ap.add_argument("--value-w", type=float, default=0.5)
    ap.add_argument("--out", default="runs/bc0")
    ap.add_argument("--init", default=None,
                    help="从 checkpoint 初始化再训(微调口;BC/PPO 格式"
                         "均可,架构参数随 checkpoint)")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--threads", type=int, default=0,
                    help="CPU 计算线程数;0=按 torch 默认")
    args = ap.parse_args()

    torch.manual_seed(args.seed)
    if args.threads:
        torch.set_num_threads(args.threads)
    rng = np.random.default_rng(args.seed)
    device = torch.device(args.device)
    os.makedirs(args.out, exist_ok=True)

    train, val = load_shards(args.data, seed=args.seed)
    n = len(train["action"])
    print(f"训练 {n} 样本 / 验证 {len(val['action'])} 样本 "
          f"({len(glob.glob(args.data))} 分片)")

    blocks, width = args.blocks, args.width
    init_ck = None
    if args.init:
        init_ck, ck_blocks, ck_width = init_from_ckpt(args.init, device)
        if ck_blocks and (ck_blocks, ck_width) != (blocks, width):
            blocks, width = ck_blocks, ck_width
            print(f"--init 采用 checkpoint 架构 blocks={blocks} width={width}")
        assert (blocks, width) == (ck_blocks, ck_width), \
            f"checkpoint 架构 {ck_blocks}/{ck_width} 与参数 {args.blocks}/" \
            f"{args.width} 不符,微调须同构(--init 时省略 --blocks/--width 即随 ck)"

    model = Net(blocks=blocks, width=width,
                n_planes=N_PLANES_ORACLE).to(device)
    if init_ck is not None:
        approx = load_init_weights(model, init_ck, device)
        print(f"--init 已载入 {args.init}"
              f"{'(policy 头为近似暖启动)' if approx else ''}")
    opt = torch.optim.Adam(model.parameters(), lr=args.lr)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=args.epochs)
    vt = value_target(train["score"], train["seat"])

    best_acc, t0 = 0.0, time.time()
    for ep in range(1, args.epochs + 1):
        model.train()
        loss_sum = n_seen = 0
        for sl in iterate(n, args.bs, rng=rng):
            p, m, a = augment_batch(
                train["planes"][sl], train["mask"][sl], train["action"][sl], rng)
            planes = torch.as_tensor(p, dtype=torch.float32, device=device)
            scalars = torch.as_tensor(train["scalars"][sl], dtype=torch.float32, device=device)
            mask = torch.as_tensor(m, device=device)
            action = torch.as_tensor(a, dtype=torch.long, device=device)
            v_t = torch.as_tensor(vt[sl], device=device)

            logits, v = model(planes, scalars)
            loss = masked_ce(logits, mask, action) + args.value_w * torch.mean((v - v_t) ** 2)
            opt.zero_grad()
            loss.backward()
            opt.step()
            loss_sum += loss.item() * len(sl)
            n_seen += len(sl)
        sched.step()
        ce, acc = evaluate(model, val, device)
        ck = None
        if acc > best_acc:
            best_acc = acc
            ck = os.path.join(args.out, "best.pt")
            torch.save({"state_dict": model.state_dict(),
                        "blocks": args.blocks, "width": args.width}, ck)
        # 终训 checkpoint 供续训/评估(字段仅张量与标量,加载方用 weights_only)
        torch.save({"state_dict": model.state_dict(),
                    "blocks": args.blocks, "width": args.width},
                   os.path.join(args.out, "last.pt"))
        print(f"epoch {ep:3d}: train_loss {loss_sum / n_seen:.4f}  "
              f"val_CE {ce:.4f}  val_top1 {acc:.4f}"
              f"{'  saved' if ck else ''}  ({time.time() - t0:.0f}s)", flush=True)

    with open(os.path.join(args.out, "config.json"), "w") as f:
        json.dump(vars(args) | {"best_val_top1": best_acc}, f, indent=2)
    print(f"完成:best val_top1 {best_acc:.4f},用 {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
