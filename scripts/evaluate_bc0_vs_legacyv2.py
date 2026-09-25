#!/usr/bin/env python3
"""Paired eight-hand score comparison between BC0 and legacyV2-offline.

Each row uses the same source seed, hero seat, dealer and match rules.  The
candidate and baseline are played in separate deterministic matches with the
same three legacyV2-offline opponents.  The final-test report is strictly
evaluation-only and never selects or tunes a checkpoint.
"""

from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import time

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mj.bot import choose_action  # noqa: E402
from mj.bc_data import (  # noqa: E402
    DEFAULT_CONSECUTIVE_DEALS,
    DEFAULT_MATCH_ROUNDS,
    TRAINING_BOT_EVALUATOR,
    training_teacher_fingerprint,
)
from mj.decision.calibration import cluster_bootstrap  # noqa: E402
from mj.decision.profile import fingerprint  # noqa: E402
from mj.match import Match  # noqa: E402


SPLIT_RANGES = {
    "validation": (1_000_000, 1_001_999),
    "final-test": (2_000_000, 2_003_999),
}

_BC_PLAYER = None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _init_worker(checkpoint: str, device: str):
    global _BC_PLAYER
    import torch

    # Legacy evaluation is CPU-bound; avoid multiplying Torch intra-op pools
    # across evaluator workers.
    torch.set_num_threads(1)
    from mj.evaluate import policy_player

    _BC_PLAYER = policy_player(checkpoint, device=device, temperature=0.0,
                               weights_only=True)


def _legacy_player(game, seat):
    return int(choose_action(game, seat, evaluator=TRAINING_BOT_EVALUATOR))


def _play_match(seed: int, dealer: int, hero: int, hero_player, ycbk: bool):
    players = [_legacy_player] * 4
    players[int(hero)] = hero_player
    match = Match(seed=int(seed), dealer=int(dealer), you_cai_bi_kao=bool(ycbk),
                  rounds=DEFAULT_MATCH_ROUNDS,
                  default_consecutive_deals=DEFAULT_CONSECUTIVE_DEALS)
    match.play(players)
    return match


def _hand_summary(match: Match, hero: int) -> dict:
    multipliers = Counter()
    for hand in match.history:
        if hand["mult"] is None:
            multipliers["draw"] += 1
        else:
            multipliers[str(int(hand["mult"]))] += 1
    return {
        "hero_score": int(match.scores[hero]),
        "opponent_average_score": float(
            sum(match.scores[seat] for seat in range(4) if seat != hero) / 3.0),
        "hand_wins": int(sum(hand["winner"] == hero for hand in match.history)),
        "draws": int(sum(hand["draw"] for hand in match.history)),
        "multipliers": dict(sorted(multipliers.items())),
        "scores": [int(value) for value in match.scores],
    }


def _one_pair(task):
    seed, hero, dealer, ycbk = task
    if _BC_PLAYER is None:
        raise RuntimeError("BC player was not initialized in evaluator worker")
    candidate = _play_match(seed, dealer, hero, _BC_PLAYER, ycbk)
    baseline = _play_match(seed, dealer, hero, _legacy_player, ycbk)
    candidate_summary = _hand_summary(candidate, hero)
    baseline_summary = _hand_summary(baseline, hero)
    return {
        "seed": int(seed),
        "hero_seat": int(hero),
        "dealer": int(dealer),
        "you_cai_bi_kao": bool(ycbk),
        "candidate_score": candidate_summary["hero_score"],
        "baseline_score": baseline_summary["hero_score"],
        "delta": (candidate_summary["hero_score"] -
                   baseline_summary["hero_score"]),
        "candidate_opponent_average": candidate_summary["opponent_average_score"],
        "baseline_opponent_average": baseline_summary["opponent_average_score"],
        "candidate_hand_wins": candidate_summary["hand_wins"],
        "baseline_hand_wins": baseline_summary["hand_wins"],
        "candidate_draws": candidate_summary["draws"],
        "baseline_draws": baseline_summary["draws"],
        "candidate_multipliers": candidate_summary["multipliers"],
        "baseline_multipliers": baseline_summary["multipliers"],
        "candidate_scores": candidate_summary["scores"],
        "baseline_scores": baseline_summary["scores"],
    }


def _mean(values):
    return float(np.mean(np.asarray(values, dtype=np.float64))) if values else None


def _std(values):
    return float(np.std(np.asarray(values, dtype=np.float64), ddof=1)) \
        if len(values) > 1 else 0.0


def _distribution(rows: list[dict]) -> dict:
    candidate = [float(row["candidate_score"]) for row in rows]
    baseline = [float(row["baseline_score"]) for row in rows]
    delta = [float(row["delta"]) for row in rows]
    groups = [f"game:{row['seed']}" for row in rows]
    ci = cluster_bootstrap(delta, groups, rounds=2000, seed=0)
    candidate_wins = sum(value > 0 for value in delta)
    baseline_wins = sum(value < 0 for value in delta)
    ties = len(delta) - candidate_wins - baseline_wins
    candidate_hand_wins = sum(int(row["candidate_hand_wins"]) for row in rows)
    baseline_hand_wins = sum(int(row["baseline_hand_wins"]) for row in rows)
    total_hands = len(rows) * DEFAULT_MATCH_ROUNDS
    return {
        "pairs": len(rows),
        "candidate_average_score": _mean(candidate),
        "baseline_average_score": _mean(baseline),
        "mean_score_delta": ci["mean"],
        "score_delta_std": _std(delta),
        "score_delta_min": min(delta),
        "score_delta_max": max(delta),
        "score_delta_ci95": [ci["low"], ci["high"]],
        "score_delta_ci_method": ci["method"],
        "candidate_pair_win_rate": candidate_wins / len(rows),
        "baseline_pair_win_rate": baseline_wins / len(rows),
        "pair_tie_rate": ties / len(rows),
        "candidate_hand_win_rate": candidate_hand_wins / total_hands,
        "baseline_hand_win_rate": baseline_hand_wins / total_hands,
        "candidate_draw_rate": sum(int(row["candidate_draws"]) for row in rows)
        / total_hands,
        "baseline_draw_rate": sum(int(row["baseline_draws"]) for row in rows)
        / total_hands,
        "illegal_actions": 0,
        "hands_per_match": DEFAULT_MATCH_ROUNDS,
    }


def _bucket(rows: list[dict], key: str) -> dict:
    buckets = {}
    for row in rows:
        value = str(row[key])
        buckets.setdefault(value, []).append(row)
    return {value: _distribution(group)
            for value, group in sorted(buckets.items())}


def evaluate(args) -> dict:
    lo, hi = SPLIT_RANGES[args.split]
    seed_start = lo if args.seed_start is None else int(args.seed_start)
    games = hi - lo + 1 if args.games is None else int(args.games)
    seed_end = seed_start + games - 1
    if seed_start < lo or seed_end > hi:
        raise ValueError(
            f"{args.split} seed range must stay inside [{lo}, {hi}], "
            f"got [{seed_start}, {seed_end}]")
    if games <= 0 or int(args.workers) <= 0:
        raise ValueError("games and workers must be positive")
    if args.ycbk not in ("off", "on"):
        raise ValueError("formal frozen campaign comparison uses --ycbk off")

    checkpoint = Path(args.checkpoint).resolve()
    if not checkpoint.exists():
        raise FileNotFoundError(checkpoint)
    training_teacher_fingerprint()
    tasks = [
        (seed, index % 4, (index // 4) % 4, args.ycbk == "on")
        for index, seed in enumerate(range(seed_start, seed_end + 1))
    ]
    started = time.perf_counter()
    rows = []
    with ProcessPoolExecutor(
            max_workers=int(args.workers),
            initializer=_init_worker,
            initargs=(str(checkpoint), str(args.device))) as pool:
        futures = [pool.submit(_one_pair, task) for task in tasks]
        for index, future in enumerate(as_completed(futures), start=1):
            rows.append(future.result())
            if index % 100 == 0 or index == len(tasks):
                print(f"{args.split}: {index}/{len(tasks)} pairs", flush=True)
    rows.sort(key=lambda row: (row["seed"], row["hero_seat"]))

    report = {
        "schema": "legacy-bc-v1-paired-score-comparison-v1",
        "checkpoint": {
            "path": str(checkpoint),
            "sha256": _sha256_file(checkpoint),
        },
        "baseline": {
            "name": TRAINING_BOT_EVALUATOR,
            "teacher_fingerprint": training_teacher_fingerprint(),
        },
        "rules": {
            "rounds": DEFAULT_MATCH_ROUNDS,
            "default_consecutive_deals": DEFAULT_CONSECUTIVE_DEALS,
            "dealer_continues_on": ["dealer_win", "draw"],
            "winner_becomes_dealer": True,
            "settlement": "dealer-x8",
            "you_cai_bi_kao": args.ycbk == "on",
        },
        "source": {
            "split": args.split,
            "seed_start": seed_start,
            "seed_end": seed_end,
            "games": games,
            "frozen_domain": [lo, hi],
            "complete_domain": seed_start == lo and seed_end == hi,
            "used_for_training": False,
            "used_for_tuning": False,
            "final_test_evaluation_only": args.split == "final-test",
        },
        "comparison": _distribution(rows),
        "by_hero_seat": _bucket(rows, "hero_seat"),
        "by_dealer": _bucket(rows, "dealer"),
        "paired_rows": rows,
        "elapsed_seconds": time.perf_counter() - started,
    }
    report["fingerprint"] = fingerprint(report, 24)
    if args.out:
        output = Path(args.out).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_name(output.name + ".tmp")
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                             encoding="utf-8")
        os.replace(temporary, output)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--split", choices=tuple(SPLIT_RANGES), required=True)
    parser.add_argument("--seed-start", type=int, default=None)
    parser.add_argument("--games", type=int, default=None)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--ycbk", choices=("off", "on"), default="off")
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)
    evaluate(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
