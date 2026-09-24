"""Streaming behavior-clone trainer (Mini-Suphx v1, task 13.6 + 13.2).

与 ``mj/bc_train.py`` 的区别(正式 BC-v1 campaign 用本模块):
  * 真·按分片流式:DataLoader 逐分片取样本(LRU 缓存少量分片),禁止训练前
    把全部训练分片 ``np.concatenate`` 进单一常驻数组 —— 30k 局 ≈ 550 万
    样本,planes 全量需 >28GB;
  * 直接训练 ``public-v1``(75 公共平面)网络,不做 75→91 补零(R3/13.2)。
    网络宽度经 ``verify_feature_planes`` 与冻结契约绑定;
  * AMP + pin_memory + num_workers;
  * 严格 resume:checkpoint 携带 dataset/teacher fingerprint + feature
    contract + 架构 + RNG/进度;任一不匹配 fail-loud,拒绝"尽量加载"。

配套数据目录须含 ``manifest.json``(由 :mod:`mj.bc_data` 在 --out 写),
携带 evaluator/scope/seed_domain 等 teacher 身份供指纹校验;缺失时退化到
按分片路径集合 + teacher 常量指纹(仍可对 resume 进行防呆)。

用法:
  python -m mj.training.streaming_bc --data data/bc --epochs 10 \
      --blocks 6 --width 128 --out runs/minisuphx/bc0 --threads 0 --device cpu
续训:
  python -m mj.training.streaming_bc --out runs/minisuphx/bc0 \
      --resume runs/minisuphx/bc0/last.pt
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

import numpy as np
import torch

from ..bc_train import value_target
from ..decision.profile import fingerprint
from ..features import N_ACTIONS, N_PLANES, N_SCALARS, SUIT_PERMS
from ..model import Net, masked_ce
from .minisuphx_manifest import (
    ACTION_SCOPE_DISCARD,
    FEATURE_PUBLIC,
    feature_contract,
    verify_feature_planes,
)

MAX_CACHED_SHARDS = 2
N_DISCARD_ACTIONS = 34
ACTION_SCOPE_NON_DISCARD = "non-discard"
ACTION_SCOPE_NAMES = (ACTION_SCOPE_DISCARD, ACTION_SCOPE_NON_DISCARD)
V1_DEFAULT_BLOCKS = 6
V1_DEFAULT_WIDTH = 128


# ===== 数据 ====================================================================

@dataclass(frozen=True)
class TrainSpec:
    """BC 训练的输入身份(供 checkpoint resume + 指纹校验)。"""

    data_dir: str
    feature_contract: str
    teacher: str
    blocks: int
    width: int
    epochs: int
    batch_size: int
    val_pct: float
    seed: int

    def payload(self) -> dict:
        # epochs 是运行长度而非身份:续训可延长轮数,不纳入指纹。
        return {
            "data_dir": str(self.data_dir),
            "feature_contract": str(self.feature_contract),
            "teacher": str(self.teacher),
            "blocks": int(self.blocks),
            "width": int(self.width),
            "batch_size": int(self.batch_size),
            "val_pct": float(self.val_pct),
            "seed": int(self.seed),
        }

    @property
    def spec_fingerprint(self) -> str:
        return fingerprint(self.payload(), 24)


def load_manifest(data_dir: str) -> dict:
    """读 bc_data 写出的 manifest.json;缺失/损坏返回空 dict。"""
    path = Path(data_dir) / "manifest.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def dataset_fingerprint(data_dir: str, teacher: str) -> str:
    """数据集身份指纹:manifest 在场则冻结其身份,否则按分片路径集合。"""
    manifest = load_manifest(data_dir)
    if manifest:
        keys = ("schema", "feature_contract", "big_hand_shadow_contract",
                "evaluator", "teacher_version",
                "teacher_fingerprint", "scope", "you_cai_bi_kao",
                "seed_domain", "games", "n_shards", "git_commit",
                # Match-level rule identity is part of the value-label
                # semantics; campaign fields make a resumed corpus fail loud
                # if it was generated under a different frozen campaign.
                "per_shard", "match_rules", "campaign_id",
                "campaign_fingerprint", "shards")
        payload = {k: manifest[k] for k in keys if k in manifest}
        payload["teacher"] = teacher
        return fingerprint(payload, 24)
    paths = sorted(glob.glob(str(Path(data_dir) / "shard_*.npz")))
    return fingerprint({"shards": paths, "teacher": teacher, "teacher_fp": _frozen_teacher_fp()}, 24)


def _frozen_teacher_fp() -> str:
    """当前冻结的 legacyV2-offline teacher 指纹(供无 manifest 兜底)。"""
    try:
        from ..bc_data import TRAINING_TEACHER_FINGERPRINT, training_teacher_fingerprint
        training_teacher_fingerprint()  # fail-loud:live 已漂移则直接拒绝
        return TRAINING_TEACHER_FINGERPRINT
    except (ImportError, RuntimeError):
        return ""


def _find_shards(data_dir: str) -> list[str]:
    paths = sorted(glob.glob(str(Path(data_dir) / "shard_*.npz")))
    if not paths:
        raise FileNotFoundError(f"数据目录无分片: {data_dir}")
    return paths


def _shard_sample_counts(paths: Sequence[str]) -> list[int]:
    sizes = []
    for p in paths:
        with np.load(p) as z:
            sizes.append(int(len(z["action"])))
    return sizes


class ShardStreamingDataset(torch.utils.data.Dataset):
    """分片流式 Dataset:LRU 少量分片,全局索引 → (shard, local)。

    构造时按 ``shard_flags`` 保留分片(train/val 划分按分片,不打散单分片)。
    真实平面数据只在访问命中分片时载入并缓存(``max_cached`` 限制内),
    内存绑定在少数分片而非全量。
    """

    def __init__(self, data_dir: str, *, shard_flags: Sequence[bool] | None = None,
                 augmented: bool = True, seed: int = 0, max_cached: int = MAX_CACHED_SHARDS):
        self.data_dir = data_dir
        self.paths = _find_shards(data_dir)
        sizes = _shard_sample_counts(self.paths)
        if shard_flags is None:
            shard_flags = [True] * len(self.paths)
        self._flags = list(shard_flags)
        if len(self._flags) != len(self.paths):
            raise ValueError("shard_flags length must match shard count")
        # 被保留分片的全局序号与其在子集内的 [start, end) 边界(本地累计)
        self._kept: list[int] = []
        self._kept_start: list[int] = []
        self._kept_end: list[int] = []
        local = 0
        for i, ok in enumerate(self._flags):
            if ok:
                self._kept.append(i)
                self._kept_start.append(local)
                self._kept_end.append(local + sizes[i])
                local += sizes[i]
        if not self._kept:
            raise ValueError("no shards selected by shard_flags")
        self.n = local
        self._kept_start = np.asarray(self._kept_start, dtype=np.int64)
        self._kept_end = np.asarray(self._kept_end, dtype=np.int64)
        self.augmented = bool(augmented)
        self.rng = np.random.default_rng(seed)
        self._cache: dict[int, dict] = {}
        self._cache_order: list[int] = []
        self._max_cached = max(1, max_cached)

    def __len__(self) -> int:
        return self.n

    def _load_shard(self, global_i: int) -> dict:
        if global_i in self._cache:
            self._cache_order.remove(global_i)
            self._cache_order.append(global_i)
            return self._cache[global_i]
        with np.load(self.paths[global_i]) as z:
            shard = {k: z[k] for k in ("planes", "scalars", "mask",
                                       "action", "seat", "score")}
        self._cache[global_i] = shard
        self._cache_order.append(global_i)
        while len(self._cache) > self._max_cached:
            evict = self._cache_order.pop(0)
            self._cache.pop(evict, None)
        return shard

    def _sample(self, shard: dict, i: int):
        planes = np.asarray(shard["planes"][i], dtype=np.float32)
        scalars = np.asarray(shard["scalars"][i], dtype=np.float32)
        mask = np.asarray(shard["mask"][i], dtype=bool)
        action = int(shard["action"][i])
        target = float(value_target(
            np.asarray(shard["score"][i])[None],
            np.asarray([int(shard["seat"][i])]))[0])
        return planes, scalars, mask, action, target

    def __getitem__(self, idx: int):
        if idx < 0 or idx >= self.n:
            raise IndexError(idx)
        pos = int(np.searchsorted(self._kept_end, idx, side="right"))
        if pos >= len(self._kept) or self._kept_start[pos] > idx:
            pos -= 1
        global_i = self._kept[pos]
        i = int(idx - self._kept_start[pos])
        planes, scalars, mask, action, target = self._sample(
            self._load_shard(global_i), i)
        if self.augmented:
            planes, mask, action = _augment_sample(
                planes, mask, action,
                SUIT_PERMS[int(self.rng.integers(len(SUIT_PERMS)))])
        return planes, scalars, mask, action, target


def _augment_sample(planes, mask, action, perm):
    """单样本花色置换增广(75 平面,无 oracle 补零)—— 方向与 features 一致。"""
    q, a = perm
    m2 = np.empty_like(mask)
    m2[a] = mask
    return planes[..., q], m2, int(a[action])


def collate(batch):
    """0D sample 列表 → 批张量 dict。"""
    planes = np.stack([b[0] for b in batch]).astype(np.float32)
    scalars = np.stack([b[1] for b in batch]).astype(np.float32)
    mask = np.stack([b[2] for b in batch])
    action = np.array([b[3] for b in batch], dtype=np.int64)
    target = np.array([b[4] for b in batch], dtype=np.float32)
    return {
        "planes": torch.as_tensor(planes),
        "scalars": torch.as_tensor(scalars),
        "mask": torch.as_tensor(mask),
        "action": torch.as_tensor(action),
        "value_target": torch.as_tensor(target),
    }


def build_dataloaders(data_dir: str, *, batch_size: int, seed: int,
                      val_pct: float, num_workers: int, pin_memory: bool,
                      augmented: bool = True):
    """按分片划分 train/val(不打散单分片),返回 (train_loader, val_loader)。

    分片充足时保持分片级划分(至少留 1 个训练分片);仅当数据只有 1 个分片
    (smoke/单机小数据)时退化为样本级划分,保证 train/val 均非空。
    划分种子区分于训练 seed,train/val 分配稳定且与训练样本顺序无关。
    """
    n_shards = len(_find_shards(data_dir))

    if n_shards >= 2:
        rng = np.random.default_rng(0x5EED ^ seed)
        order = rng.permutation(n_shards)
        n_val = max(1, min(int(round(n_shards * val_pct)), n_shards - 1))
        val_flags = [False] * n_shards
        for i in order[:n_val]:
            val_flags[int(i)] = True
        train_flags = [not f for f in val_flags]

        train_ds = ShardStreamingDataset(
            data_dir, shard_flags=train_flags, augmented=augmented, seed=seed)
        val_ds = ShardStreamingDataset(
            data_dir, shard_flags=val_flags, augmented=False, seed=seed)
        train = torch.utils.data.DataLoader(
            train_ds, batch_size=batch_size, shuffle=True,
            num_workers=num_workers, pin_memory=pin_memory,
            collate_fn=collate, drop_last=False)
        val = torch.utils.data.DataLoader(
            val_ds, batch_size=batch_size, shuffle=False, num_workers=0,
            pin_memory=pin_memory, collate_fn=collate, drop_last=False)
        return train, val

    # 单分片:样本级划分
    full = ShardStreamingDataset(data_dir, augmented=False, seed=seed)
    n = len(full)
    n_val = max(1, int(round(n * val_pct)))
    rng = np.random.default_rng(0x5EED ^ seed)
    idx = rng.permutation(n)
    val_idx = sorted(idx[:n_val].tolist())
    train_idx = sorted(idx[n_val:].tolist())

    def _loader(subset_idx, shuffle, aug):
        ds = ShardStreamingDataset(data_dir, augmented=aug, seed=seed)
        subset = torch.utils.data.Subset(ds, subset_idx)
        return torch.utils.data.DataLoader(
            subset, batch_size=batch_size, shuffle=shuffle, num_workers=0,
            pin_memory=pin_memory, collate_fn=collate, drop_last=False)

    return (_loader(train_idx, True, augmented),
            _loader(val_idx, False, False))


# ===== 训练 ====================================================================

def build_model(spec: TrainSpec, device: torch.device) -> Net:
    net = Net(blocks=spec.blocks, width=spec.width,
              n_planes=N_PLANES).to(device)
    return net


def _safe_ratio(total: float, count: int):
    """Return a JSON-friendly mean, using null for an empty partition."""
    return float(total / count) if count else None


def _format_metric(value) -> str:
    """Format nullable report values for the human-readable epoch log."""
    return "nan" if value is None else f"{float(value):.4f}"


def _validation_report(*, total_ce: float, total_mae: float,
                       total_mse: float, correct: int, illegal: int,
                       label_illegal: int, n: int,
                       scope_counts: Mapping[str, int],
                       scope_correct: Mapping[str, int]) -> dict:
    """Build the stable validation report consumed by logs and config files.

    ``top1`` and the per-scope accuracies use the legal-mask prediction.  The
    separate ``illegal_rate`` deliberately uses the raw, unmasked policy
    argmax; otherwise masking would make this safety diagnostic meaningless.
    ``label_illegal_rate`` is retained as a data-quality diagnostic for shards
    whose teacher action is not present in their legal mask.
    """
    action_scope = {
        name: {
            "count": int(scope_counts[name]),
            "accuracy": _safe_ratio(scope_correct[name], scope_counts[name]),
        }
        for name in ACTION_SCOPE_NAMES
    }
    return {
        "samples": int(n),
        "ce": _safe_ratio(total_ce, n),
        "top1": _safe_ratio(correct, n),
        "illegal_rate": _safe_ratio(illegal, n),
        "illegal_count": int(illegal),
        "label_illegal_rate": _safe_ratio(label_illegal, n),
        "label_illegal_count": int(label_illegal),
        "value_mae": _safe_ratio(total_mae, n),
        "value_mse": _safe_ratio(total_mse, n),
        "action_scope": action_scope,
        # Flat accuracy mapping makes the report convenient for tables while
        # ``action_scope`` retains per-scope sample counts for interpretation.
        "action_scope_accuracy": {
            name: values["accuracy"] for name, values in action_scope.items()
        },
    }


def evaluate(model: Net, loader: torch.utils.data.DataLoader, device: torch.device,
             value_w: float, *, detailed: bool = False):
    """Validate without augmentation and report policy/value safety metrics.

    The default three-value return is kept for callers of the original
    streaming trainer API.  ``detailed=True`` returns the full report used by
    the training entrypoint: masked CE/top-1, action-scope accuracy, raw
    illegal-action rate, and value MAE/MSE.
    """
    del value_w  # retained in the public signature for backward compatibility
    model.eval()
    total_ce = total_mae = total_mse = 0.0
    n = correct = illegal = label_illegal = 0
    scope_counts = {name: 0 for name in ACTION_SCOPE_NAMES}
    scope_correct = {name: 0 for name in ACTION_SCOPE_NAMES}
    with torch.no_grad():
        for batch in loader:
            planes = batch["planes"].to(device)
            scalars = batch["scalars"].to(device)
            mask = batch["mask"].to(device).bool()
            action = batch["action"].to(device)
            target = batch["value_target"].to(device)
            logits, v = model(planes, scalars)
            total_ce += masked_ce(logits, mask, action).item() * len(action)
            error = v - target
            total_mae += float(torch.mean(torch.abs(error)).item()) * len(action)
            total_mse += float(torch.mean(error ** 2).item()) * len(action)

            raw_pred = logits.argmax(dim=-1)
            pred = logits.masked_fill(~mask, float("-inf")).argmax(dim=-1)
            raw_is_legal = mask.gather(1, raw_pred.unsqueeze(1)).squeeze(1)
            label_is_legal = mask.gather(1, action.unsqueeze(1)).squeeze(1)
            row_correct = pred == action
            row_scope = action < N_DISCARD_ACTIONS
            for name, selected in (
                    (ACTION_SCOPE_DISCARD, row_scope),
                    (ACTION_SCOPE_NON_DISCARD, ~row_scope)):
                scope_counts[name] += int(selected.sum().item())
                scope_correct[name] += int((row_correct & selected).sum().item())
            correct += int(row_correct.sum().item())
            illegal += int((~raw_is_legal).sum().item())
            label_illegal += int((~label_is_legal).sum().item())
            n += len(action)
    report = _validation_report(
        total_ce=total_ce, total_mae=total_mae, total_mse=total_mse,
        correct=correct, illegal=illegal, label_illegal=label_illegal, n=n,
        scope_counts=scope_counts, scope_correct=scope_correct)
    model.train()
    if detailed:
        return report
    if n == 0:
        return float("nan"), float("nan"), float("nan")
    return report["ce"], report["top1"], report["value_mse"]


def save_checkpoint(path: str, model: Net, opt: torch.optim.Optimizer,
                    sched, *, global_step: int, epoch: int, best_val: float,
                    spec: TrainSpec, dataset_fp: str, source_state: dict,
                    rng: Mapping[str, str], validation_metrics: Mapping | None = None,
                    best_validation_metrics: Mapping | None = None):
    """保存含完整训练身份与 RNG 状态的 checkpoint(严格 resume 用)。"""
    torch.save({
        "schema": "minisuphx-streaming-bc-ckpt-v1",
        "state_dict": model.state_dict(),
        "blocks": spec.blocks,
        "width": spec.width,
        "feature_contract": spec.feature_contract,
        "optimizer": opt.state_dict(),
        "scheduler": sched.state_dict(),
        "global_step": int(global_step),
        "epoch": int(epoch),
        "best_val_top1": float(best_val),
        "validation_metrics": (dict(validation_metrics)
                               if validation_metrics is not None else None),
        "best_validation_metrics": (dict(best_validation_metrics)
                                    if best_validation_metrics is not None else None),
        "spec": spec.payload(),
        "spec_fingerprint": spec.spec_fingerprint,
        "dataset_fingerprint": dataset_fp,
        "source_state": source_state,
        "rng": dict(rng),
    }, path)


def load_checkpoint(path: str, spec: TrainSpec, dataset_fp: str, device: torch.device):
    """严格加载 LR checkpoint;身份不匹配 fail-loud(拒绝尽量加载)。"""
    ck = torch.load(path, map_location=device, weights_only=True)
    if ck["schema"] != "minisuphx-streaming-bc-ckpt-v1":
        raise ValueError(f"checkpoint schema 不符: {ck['schema']!r}")
    if ck["feature_contract"] != spec.feature_contract:
        raise ValueError(
            f"checkpoint feature contract {ck['feature_contract']!r} != "
            f"{spec.feature_contract!r};禁止跨契约续训(拒绝 75↔91 伪装)")
    if (int(ck["blocks"]), int(ck["width"])) != (spec.blocks, spec.width):
        raise ValueError("checkpoint 架构与当前 spec 不符,拒绝续训")
    if ck["spec_fingerprint"] != spec.spec_fingerprint:
        raise ValueError("checkpoint training spec 指纹不符(超参/seed 变更),"
                         "拒绝续训")
    if ck["dataset_fingerprint"] != dataset_fp:
        raise ValueError("checkpoint dataset/teacher 指纹不符,拒绝续训")
    return ck


def train_epoch(model: Net, loader: torch.utils.data.DataLoader, spec: TrainSpec,
                opt: torch.optim.Optimizer, value_w: float,
                scaler, autocast, device: torch.device, steps_done: int):
    """跑一个 epoch;返回本 epoch 总 loss 与样本数(含 AMP 自动混合精度)。"""
    loss_sum = n_seen = 0
    for batch in loader:
        planes = batch["planes"].to(device)
        scalars = batch["scalars"].to(device)
        mask = batch["mask"].to(device)
        action = batch["action"].to(device)
        target = batch["value_target"].to(device)
        opt.zero_grad()
        with autocast:  # torch.cuda.amp.autocast 或 nullcontext;二者均以 `with x:` 使用
            logits, v = model(planes, scalars)
            loss = masked_ce(logits, mask, action) + \
                value_w * torch.mean((v - target) ** 2)
        scaler.scale(loss).backward()
        scaler.step(opt)
        scaler.update()
        loss_sum += float(loss.item()) * len(action)
        n_seen += len(action)
        steps_done += 1
    return loss_sum, n_seen, steps_done


def _snapshot_rng() -> dict:
    """序列化 torch + numpy RNG 状态(字节串,weights_only-safe)。"""
    state = {}
    try:
        state["torch_cpu"] = torch.random.get_rng_state().flatten() \
            .cpu().numpy().tobytes().hex()
    except RuntimeError:
        state["torch_cpu"] = ""
    if torch.cuda.is_available():
        try:
            state["torch_cuda"] = torch.cuda.random.get_rng_state_all()[0] \
                .cpu().numpy().tobytes().hex()
        except RuntimeError:
            state["torch_cuda"] = ""
    import pickle
    state["numpy"] = pickle.dumps(np.random.get_state()).hex()
    return state


def restore_rng(snapshot: Mapping[str, str], device: torch.device) -> None:
    """续训时恢复 RNG 状态;来源为空则置确定性初值(不报错)。"""
    import pickle
    torch.manual_seed(0)
    if snapshot.get("torch_cpu"):
        try:
            state = torch.tensor(list(bytes.fromhex(snapshot["torch_cpu"])),
                                 dtype=torch.uint8)
            torch.random.set_rng_state(state)
        except (ValueError, RuntimeError):
            pass
    if torch.cuda.is_available() and snapshot.get("torch_cuda"):
        try:
            state = torch.tensor(list(bytes.fromhex(snapshot["torch_cuda"])),
                                 dtype=torch.uint8)
            torch.cuda.random.set_rng_state_all([state])
        except (ValueError, RuntimeError):
            pass
    if snapshot.get("numpy"):
        try:
            np.random.set_state(pickle.loads(bytes.fromhex(snapshot["numpy"])))
        except (ValueError, pickle.PickleError, EOFError):
            pass


def main(argv=None):
    ap = argparse.ArgumentParser(prog="streaming_bc")
    ap.add_argument("--data", default="data/bc")
    ap.add_argument("--epochs", type=int, default=10)
    ap.add_argument("--bs", type=int, default=512)
    ap.add_argument("--lr", type=float, default=3e-4)
    ap.add_argument("--blocks", type=int, default=V1_DEFAULT_BLOCKS)
    ap.add_argument("--width", type=int, default=V1_DEFAULT_WIDTH)
    ap.add_argument("--value-w", type=float, default=0.5)
    ap.add_argument("--val-pct", type=float, default=0.1)
    ap.add_argument("--workers", type=int, default=0)
    ap.add_argument("--no-pin-memory", action="store_true")
    ap.add_argument("--no-amp", action="store_true")
    ap.add_argument("--feature", default=FEATURE_PUBLIC,
                    help=f"训练特征契约(默认 {FEATURE_PUBLIC};v1 只支持 public)")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    ap.add_argument("--threads", type=int, default=0)
    ap.add_argument("--out", required=True)
    ap.add_argument("--resume", default=None,
                    help="从 --out/last.pt(或显式路径)严格续训")
    args = ap.parse_args(argv)

    # 冻结特征契约:v1 只训练 public-v1,禁止 oracle 补零直训。
    fc = feature_contract(args.feature)
    verify_feature_planes(args.feature, N_PLANES)
    if args.feature != FEATURE_PUBLIC:
        raise ValueError(
            f"streaming BC v1 只支持 {FEATURE_PUBLIC} 契约,got {args.feature!r}"
            f"(oracle-v1 仅 oracle guiding 阶段由专用管线使用)")

    # teacher 身份:训练标签来源的训练评价器常量(bc_data.TRAINING_BOT_EVALUATOR)。
    from ..bc_data import TRAINING_BOT_EVALUATOR as TEACHER
    from ..bc_data import training_teacher_fingerprint
    training_teacher_fingerprint()   # fail-loud:live teacher 漂移则拒绝训练

    torch.manual_seed(args.seed)
    if args.threads:
        torch.set_num_threads(args.threads)
    device = torch.device(args.device)
    os.makedirs(args.out, exist_ok=True)

    pin_memory = bool(device.type == "cuda") and not args.no_pin_memory

    spec = TrainSpec(data_dir=args.data, feature_contract=args.feature,
                     teacher=TEACHER, blocks=args.blocks, width=args.width,
                     epochs=args.epochs, batch_size=args.bs, val_pct=args.val_pct,
                     seed=args.seed)
    ds_fp = dataset_fingerprint(args.data, TEACHER)
    train, val = build_dataloaders(
        args.data, batch_size=args.bs, seed=args.seed, val_pct=args.val_pct,
        num_workers=args.workers, pin_memory=pin_memory)

    model = build_model(spec, device)
    opt = torch.optim.Adam(model.parameters(), lr=args.lr)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=args.epochs)
    use_amp = bool(device.type == "cuda") and not args.no_amp
    if use_amp:
        scaler = torch.amp.GradScaler("cuda", enabled=True)
        autocast = torch.autocast("cuda", enabled=True)
    else:
        import contextlib
        scaler = torch.amp.GradScaler("cuda", enabled=False)
        autocast = contextlib.nullcontext()

    start_epoch, global_step, best_val = 1, 0, 0.0
    last_validation_metrics = None
    best_validation_metrics = None
    source_state = {}
    if args.resume:
        ck = load_checkpoint(args.resume, spec, ds_fp, device)
        model.load_state_dict(ck["state_dict"])
        opt.load_state_dict(ck["optimizer"])
        sched.load_state_dict(ck["scheduler"])
        start_epoch = int(ck["epoch"]) + 1
        global_step = int(ck["global_step"])
        best_val = float(ck["best_val_top1"])
        last_validation_metrics = ck.get("validation_metrics")
        best_validation_metrics = ck.get("best_validation_metrics")
        if best_validation_metrics is None:
            # Checkpoints written before the detailed report existed only
            # carried the last scalar validation score.
            best_validation_metrics = last_validation_metrics
        source_state = ck.get("source_state", {})
        restore_rng(ck.get("rng", {}), device)
        print(f"续训自 {args.resume}: epoch {start_epoch}, "
              f"global_step {global_step}, best {best_val:.4f}")

    n_train = len(train.dataset)
    def _kept(split):
        ds = split.dataset if isinstance(split, torch.utils.data.Subset) else split
        return getattr(ds, "_kept", "sample-split")
    print(f"训练 {n_train} / 验证 {len(val.dataset)} 样本 "
          f"(shards {_kept(train.dataset)}/{len(_find_shards(args.data))}), "
          f"feature={args.feature} ({fc.display}), amp={use_amp}, "
          f"workers={args.workers}")

    last_path = os.path.join(args.out, "last.pt")
    best_path = os.path.join(args.out, "best.pt")
    t0 = time.time()
    for ep in range(start_epoch, args.epochs + 1):
        model.train()
        loss_sum, n_seen, global_step = train_epoch(
            model, train, spec, opt, args.value_w, scaler, autocast,
            device, global_step)
        sched.step()
        validation_metrics = evaluate(
            model, val, device, args.value_w, detailed=True)
        last_validation_metrics = validation_metrics
        ce = validation_metrics["ce"]
        acc = validation_metrics["top1"]
        mse = validation_metrics["value_mse"]
        ck_msg = ""
        if acc is not None and acc > best_val:
            best_val = acc
            best_validation_metrics = validation_metrics
            torch.save({"state_dict": model.state_dict(),
                        "blocks": spec.blocks, "width": spec.width,
                        "feature_contract": spec.feature_contract,
                        "validation_metrics": validation_metrics},
                       best_path)
            ck_msg = "  saved"
        save_checkpoint(last_path, model, opt, sched, global_step=global_step,
                        epoch=ep, best_val=best_val, spec=spec, dataset_fp=ds_fp,
                        source_state=source_state, rng=_snapshot_rng(),
                        validation_metrics=last_validation_metrics,
                        best_validation_metrics=best_validation_metrics)
        scope = validation_metrics["action_scope_accuracy"]
        scope_text = "  ".join((
            f"val_discard_top1 {_format_metric(scope[ACTION_SCOPE_DISCARD])}",
            f"val_non_discard_top1 "
            f"{_format_metric(scope[ACTION_SCOPE_NON_DISCARD])}"))
        print(f"epoch {ep:3d}: train_loss {loss_sum / n_seen:.4f}  "
              f"val_CE {_format_metric(ce)}  val_top1 {_format_metric(acc)}  "
              f"{scope_text}  "
              f"val_illegal_rate {_format_metric(validation_metrics['illegal_rate'])}  "
              f"val_MAE {_format_metric(validation_metrics['value_mae'])}  "
              f"val_MSE {_format_metric(mse)}"
              f"{ck_msg}  ({time.time() - t0:.0f}s)", flush=True)

    with open(os.path.join(args.out, "config.json"), "w") as f:
        json.dump({
            "program": "streaming_bc",
            "spec": spec.payload(),
            "feature_contract_display": fc.display,
            "feature_contract_fingerprint": fc.fingerprint,
            "dataset_fingerprint": ds_fp,
            "best_val_top1": best_val,
            "validation_metrics": last_validation_metrics,
            "best_validation_metrics": best_validation_metrics,
            "global_step": global_step,
            "amp": use_amp,
            "workers": args.workers,
        }, f, indent=2)
    print(f"完成:best val_top1 {best_val:.4f},用 {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
