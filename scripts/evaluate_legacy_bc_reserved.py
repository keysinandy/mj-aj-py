#!/usr/bin/env python3
"""Evaluate a frozen BC checkpoint on reserved legacy teacher domains.

The training corpus contains only the train seed domain.  This command
replays the frozen validation/final-test seed domains in memory, obtains the
same legacyV2-offline labels, and reports the public-v1 policy/value metrics
without writing another large BC corpus to disk.

The final-test invocation is evaluation-only by contract: this script never
changes a checkpoint or chooses hyperparameters.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import hashlib
import json
import os
from pathlib import Path
import sys
import time

import numpy as np
import torch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mj.bc_data import (  # noqa: E402
    DEFAULT_CONSECUTIVE_DEALS,
    DEFAULT_MATCH_ROUNDS,
    TRAINING_BOT_EVALUATOR,
    generate_game,
    training_teacher_fingerprint,
)
from mj.bc_train import value_target  # noqa: E402
from mj.model import masked_ce  # noqa: E402
from mj.training.minisuphx_manifest import FEATURE_PUBLIC  # noqa: E402
from mj.training.streaming_bc import (  # noqa: E402
    ACTION_SCOPE_DISCARD,
    ACTION_SCOPE_NON_DISCARD,
    N_DISCARD_ACTIONS,
    _validation_report,
)


SPLIT_RANGES = {
    "validation": (1_000_000, 1_001_999),
    "final-test": (2_000_000, 2_003_999),
}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _generate(seed: int) -> dict:
    """Process-pool entry point; keep it top-level for Windows spawn."""
    return generate_game(
        int(seed),
        you_cai_bi_kao=False,
        evaluator=TRAINING_BOT_EVALUATOR,
        scope="all-root",
        allow_search_fallback=False,
        rounds=DEFAULT_MATCH_ROUNDS,
        default_consecutive_deals=DEFAULT_CONSECUTIVE_DEALS,
    )


def _evaluate_game(model, game: dict, device: torch.device, batch_size: int,
                   totals: dict) -> None:
    planes = game["planes"].astype(np.float32, copy=False)
    scalars = game["scalars"].astype(np.float32, copy=False)
    mask = game["mask"].astype(bool, copy=False)
    action = game["action"].astype(np.int64, copy=False)
    target = value_target(game["score"], game["seat"])
    for start in range(0, len(action), batch_size):
        end = min(start + batch_size, len(action))
        p = torch.as_tensor(planes[start:end], device=device)
        s = torch.as_tensor(scalars[start:end], device=device)
        m = torch.as_tensor(mask[start:end], device=device)
        a = torch.as_tensor(action[start:end], device=device)
        y = torch.as_tensor(target[start:end], device=device)
        with torch.no_grad():
            logits, value = model(p, s)
            total_ce = masked_ce(logits, m, a).item() * (end - start)
            error = value - y
            total_mae = torch.mean(torch.abs(error)).item() * (end - start)
            total_mse = torch.mean(error ** 2).item() * (end - start)
            raw_pred = logits.argmax(dim=-1)
            pred = logits.masked_fill(~m, float("-inf")).argmax(dim=-1)
            raw_is_legal = m.gather(1, raw_pred.unsqueeze(1)).squeeze(1)
            label_is_legal = m.gather(1, a.unsqueeze(1)).squeeze(1)
            correct = pred == a
            scope = a < N_DISCARD_ACTIONS

        n = end - start
        totals["ce"] += float(total_ce)
        totals["mae"] += float(total_mae)
        totals["mse"] += float(total_mse)
        totals["correct"] += int(correct.sum().item())
        totals["illegal"] += int((~raw_is_legal).sum().item())
        totals["label_illegal"] += int((~label_is_legal).sum().item())
        totals["scope_counts"][ACTION_SCOPE_DISCARD] += int(scope.sum().item())
        totals["scope_counts"][ACTION_SCOPE_NON_DISCARD] += int((~scope).sum().item())
        totals["scope_correct"][ACTION_SCOPE_DISCARD] += int(
            (correct & scope).sum().item())
        totals["scope_correct"][ACTION_SCOPE_NON_DISCARD] += int(
            (correct & ~scope).sum().item())
        totals["samples"] += n


def evaluate(args) -> dict:
    split_lo, split_hi = SPLIT_RANGES[args.split]
    seed_start = split_lo if args.seed_start is None else int(args.seed_start)
    games = split_hi - split_lo + 1 if args.games is None else int(args.games)
    seed_end = seed_start + games - 1
    if seed_start < split_lo or seed_end > split_hi:
        raise ValueError(
            f"{args.split} seed range must stay inside [{split_lo}, {split_hi}], "
            f"got [{seed_start}, {seed_end}]")
    if games <= 0:
        raise ValueError("games must be positive")
    if int(args.workers) <= 0 or int(args.batch_size) <= 0:
        raise ValueError("workers and batch-size must be positive")

    checkpoint = Path(args.checkpoint).resolve()
    if not checkpoint.exists():
        raise FileNotFoundError(checkpoint)
    device = torch.device(args.device)
    ck = torch.load(checkpoint, map_location=device, weights_only=True)
    if ck.get("feature_contract") != FEATURE_PUBLIC:
        raise ValueError("reserved BC evaluation requires public-v1 checkpoint")
    from mj.model import Net

    model = Net(blocks=int(ck["blocks"]), width=int(ck["width"]),
                n_planes=75).to(device)
    model.load_state_dict(ck["state_dict"])
    model.eval()

    manifest = {}
    if args.campaign_manifest:
        manifest_path = Path(args.campaign_manifest).resolve()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("feature_contract") != FEATURE_PUBLIC:
            raise ValueError("campaign manifest is not public-v1")
        if manifest.get("match_rules", {}).get("rounds") != DEFAULT_MATCH_ROUNDS:
            raise ValueError("campaign match rounds do not match evaluator")
        if manifest.get("match_rules", {}).get("default_consecutive_deals") != \
                DEFAULT_CONSECUTIVE_DEALS:
            raise ValueError("campaign dealer-run rule does not match evaluator")

    training_teacher_fingerprint()
    totals = {
        "ce": 0.0, "mae": 0.0, "mse": 0.0, "correct": 0,
        "illegal": 0, "label_illegal": 0, "samples": 0,
        "scope_counts": {ACTION_SCOPE_DISCARD: 0,
                          ACTION_SCOPE_NON_DISCARD: 0},
        "scope_correct": {ACTION_SCOPE_DISCARD: 0,
                           ACTION_SCOPE_NON_DISCARD: 0},
    }
    started = time.perf_counter()
    seeds = range(seed_start, seed_end + 1)
    with ProcessPoolExecutor(max_workers=int(args.workers)) as pool:
        futures = [pool.submit(_generate, seed) for seed in seeds]
        for index, future in enumerate(as_completed(futures), start=1):
            _evaluate_game(model, future.result(), device,
                           int(args.batch_size), totals)
            if index % 100 == 0 or index == games:
                print(f"{args.split}: {index}/{games} games, "
                      f"{totals['samples']} samples", flush=True)

    report = _validation_report(
        total_ce=totals["ce"], total_mae=totals["mae"],
        total_mse=totals["mse"], correct=totals["correct"],
        illegal=totals["illegal"], label_illegal=totals["label_illegal"],
        n=totals["samples"], scope_counts=totals["scope_counts"],
        scope_correct=totals["scope_correct"])
    result = {
        "schema": "legacy-bc-v1-reserved-evaluation-v1",
        "checkpoint": {
            "path": str(checkpoint),
            "sha256": _sha256_file(checkpoint),
            "blocks": int(ck["blocks"]),
            "width": int(ck["width"]),
            "feature_contract": ck["feature_contract"],
            "dataset_fingerprint": ck.get("dataset_fingerprint"),
        },
        "campaign": {
            "manifest_path": (str(Path(args.campaign_manifest).resolve())
                               if args.campaign_manifest else None),
            "campaign_id": manifest.get("campaign_id"),
            "campaign_fingerprint": manifest.get("campaign_fingerprint"),
            "dataset_fingerprint": manifest.get("dataset_fingerprint"),
            "teacher": TRAINING_BOT_EVALUATOR,
            "teacher_fingerprint": manifest.get("teacher_fingerprint"),
            "match_rules": manifest.get("match_rules"),
        },
        "source": {
            "split": args.split,
            "seed_start": seed_start,
            "seed_end": seed_end,
            "games": games,
            "frozen_domain": [split_lo, split_hi],
            "complete_domain": (seed_start == split_lo and seed_end == split_hi),
            "used_for_training": False,
            "used_for_tuning": False,
            "used_for_checkpoint_selection": False if args.split == "final-test" else None,
        },
        "evaluation": report,
        "elapsed_seconds": time.perf_counter() - started,
    }
    from mj.decision.profile import fingerprint
    result["fingerprint"] = fingerprint(result, 24)
    if args.out:
        out = Path(args.out).resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        temporary = out.with_name(out.name + ".tmp")
        temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                             encoding="utf-8")
        os.replace(temporary, out)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--split", choices=tuple(SPLIT_RANGES), required=True)
    parser.add_argument("--seed-start", type=int, default=None)
    parser.add_argument("--games", type=int, default=None)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--batch-size", type=int, default=512)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--campaign-manifest", default=(
        "openspec/changes/legacy-bot-minisuphx-training/artifacts/"
        "legacy_bc_v1_30k_manifest_20260924.json"))
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)
    evaluate(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
