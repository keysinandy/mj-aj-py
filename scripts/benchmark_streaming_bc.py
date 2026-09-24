#!/usr/bin/env python3
"""Benchmark the two BC-v1 architecture candidates.

The benchmark uses the frozen public-v1 tensor shape and the same masked
policy/value loss as :mod:`mj.training.streaming_bc`.  It reports model size,
forward latency, and a complete optimizer step for 4x128 and 6x128.  This is
an architecture/throughput benchmark, not a claim about playing strength:
without a frozen validation-quality comparison the v1 selection remains the
design baseline, 6x128.

Example::

    python scripts/benchmark_streaming_bc.py --device cpu --steps 20 \
        --warmup 3 --json runs/minisuphx/bc_architecture_benchmark.json
"""

from __future__ import annotations

import argparse
import json
import math
import platform
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import torch

from mj.features import N_ACTIONS, N_PLANES, N_SCALARS
from mj.model import Net, masked_ce
from mj.training.streaming_bc import V1_DEFAULT_BLOCKS, V1_DEFAULT_WIDTH


def _synchronize(device: torch.device) -> None:
    if device.type == "cuda":
        torch.cuda.synchronize(device)


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1,
                max(0, int(math.ceil(float(fraction) * len(ordered))) - 1))
    return float(ordered[index])


def _summary(values: list[float]) -> dict:
    if not values:
        return {"count": 0, "mean_ms": None, "p50_ms": None,
                "p95_ms": None, "min_ms": None, "max_ms": None}
    return {
        "count": len(values),
        "mean_ms": float(statistics.fmean(values)),
        "p50_ms": _percentile(values, 0.50),
        "p95_ms": _percentile(values, 0.95),
        "min_ms": float(min(values)),
        "max_ms": float(max(values)),
    }


def _make_batch(batch_size: int, device: torch.device):
    """Create deterministic public-v1-shaped inputs with one legal action."""
    planes = torch.rand(batch_size, N_PLANES, 34, device=device)
    scalars = torch.rand(batch_size, N_SCALARS, device=device)
    mask = torch.zeros(batch_size, N_ACTIONS, dtype=torch.bool, device=device)
    mask[:, 0] = True
    action = torch.zeros(batch_size, dtype=torch.long, device=device)
    value_target = torch.zeros(batch_size, device=device)
    return planes, scalars, mask, action, value_target


def benchmark_architecture(*, blocks: int, width: int, device="cpu",
                           batch_size: int = 8, warmup: int = 3,
                           steps: int = 20, seed: int = 0) -> dict:
    """Measure one architecture using public-v1 forward and train paths."""
    if blocks <= 0 or width <= 0:
        raise ValueError("blocks and width must be positive")
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    if warmup < 0 or steps <= 0:
        raise ValueError("warmup must be non-negative and steps must be positive")

    device = torch.device(device)
    if device.type == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA benchmark requested but CUDA is unavailable")
    torch.manual_seed(int(seed))
    model = Net(blocks=int(blocks), width=int(width),
                n_planes=N_PLANES).to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=3e-4)
    batch = _make_batch(int(batch_size), device)
    planes, scalars, mask, action, value_target = batch

    def train_step() -> float:
        optimizer.zero_grad(set_to_none=True)
        logits, value = model(planes, scalars)
        loss = masked_ce(logits, mask, action) + \
            0.5 * torch.mean((value - value_target) ** 2)
        loss.backward()
        optimizer.step()
        return float(loss.detach().item())

    model.train()
    for _ in range(warmup):
        train_step()
    _synchronize(device)
    train_ms = []
    last_loss = None
    for _ in range(steps):
        _synchronize(device)
        started = time.perf_counter()
        last_loss = train_step()
        _synchronize(device)
        train_ms.append((time.perf_counter() - started) * 1000.0)

    model.eval()
    with torch.no_grad():
        for _ in range(warmup):
            model(planes, scalars)
    _synchronize(device)
    forward_ms = []
    with torch.no_grad():
        for _ in range(steps):
            _synchronize(device)
            started = time.perf_counter()
            model(planes, scalars)
            _synchronize(device)
            forward_ms.append((time.perf_counter() - started) * 1000.0)

    train_summary = _summary(train_ms)
    forward_summary = _summary(forward_ms)
    mean_train_ms = train_summary["mean_ms"]
    return {
        "architecture": f"{int(blocks)}x{int(width)}",
        "blocks": int(blocks),
        "width": int(width),
        "parameters": int(sum(p.numel() for p in model.parameters())),
        "device": str(device),
        "batch_size": int(batch_size),
        "warmup_steps": int(warmup),
        "measure_steps": int(steps),
        "forward": forward_summary,
        "train_step": train_summary,
        "train_samples_per_sec": (
            float(batch_size * 1000.0 / mean_train_ms)
            if mean_train_ms else None),
        "last_loss": last_loss,
    }


def build_report(results: list[dict], *, device: str, batch_size: int,
                 warmup: int, steps: int, seed: int,
                 threads: int = 0) -> dict:
    """Build an auditable benchmark artifact and v1 selection decision."""
    selected = f"{V1_DEFAULT_BLOCKS}x{V1_DEFAULT_WIDTH}"
    measured = {row["architecture"] for row in results}
    return {
        "schema": "minisuphx-bc-architecture-benchmark-v1",
        "benchmark": {
            "kind": "public-v1-forward-and-train-step",
            "device": str(device),
            "batch_size": int(batch_size),
            "warmup_steps": int(warmup),
            "measure_steps": int(steps),
            "seed": int(seed),
            "threads": int(threads),
            "quality_comparison": False,
            "python_version": platform.python_version(),
            "torch_version": torch.__version__,
            "platform": platform.platform(),
        },
        "results": results,
        "decision": {
            "formal_v1_model": selected,
            "formal_v1_blocks": V1_DEFAULT_BLOCKS,
            "formal_v1_width": V1_DEFAULT_WIDTH,
            "measured_formal_v1_model": selected in measured,
            "rule": "keep-6x128-when-no-clear-quality-benefit",
            "reason": (
                "This artifact measures throughput and model size only; it "
                "does not establish validation or playing-strength benefit "
                "for 4x128. Keep the frozen 6x128 v1 baseline."),
        },
    }


def main(argv=None):
    parser = argparse.ArgumentParser(prog="benchmark_streaming_bc")
    parser.add_argument("--blocks", type=int, nargs="+", default=[4, 6],
                        help="residual block counts to compare (default: 4 6)")
    parser.add_argument("--width", type=int, default=V1_DEFAULT_WIDTH)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--warmup", type=int, default=3)
    parser.add_argument("--steps", type=int, default=20)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--threads", type=int, default=0)
    parser.add_argument("--device", default="cuda" if torch.cuda.is_available()
                        else "cpu")
    parser.add_argument("--json", type=Path, default=None,
                        help="optional output path for the benchmark artifact")
    args = parser.parse_args(argv)
    if args.threads:
        torch.set_num_threads(args.threads)
    if not args.blocks:
        raise ValueError("at least one block count is required")

    results = []
    for blocks in args.blocks:
        row = benchmark_architecture(
            blocks=blocks, width=args.width, device=args.device,
            batch_size=args.batch_size, warmup=args.warmup,
            steps=args.steps, seed=args.seed)
        results.append(row)
        print(f"{row['architecture']}: params={row['parameters']} "
              f"forward_p50={row['forward']['p50_ms']:.3f}ms "
              f"train_p50={row['train_step']['p50_ms']:.3f}ms "
              f"train_samples/s={row['train_samples_per_sec']:.2f}")

    report = build_report(
        results, device=args.device, batch_size=args.batch_size,
        warmup=args.warmup, steps=args.steps, seed=args.seed,
        threads=args.threads)
    print("formal_v1_model: " + report["decision"]["formal_v1_model"])
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                             encoding="utf-8")
        print(f"wrote {args.json}")
    return report


if __name__ == "__main__":
    main()
