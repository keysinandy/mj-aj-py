#!/usr/bin/env python3
"""Paired legacy/shape-v1/shape-v2 offline evaluation.

The default seed plan is the one frozen in
``openspec/changes/bot-shape-aware-evaluation/artifacts/calibration_plan.json``.
This command deliberately keeps the opponent callable bound to
``choose_action(..., evaluator='legacy')``; it never follows the process
default.  It is safe to run a smaller exploratory sample, but release claims
require ``--games 4096`` and the predeclared seed range.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from mj.bot import choose_action
from mj.game import Game
from mj.hand_eval import warmup


def _play(seed, seat, dealer, ycbk, evaluator):
    players = []
    for s in range(4):
        profile = evaluator if s == seat else "legacy"
        players.append(lambda g, current, p=profile:
                       choose_action(g, current, evaluator=p))
    g = Game(seed=seed, dealer=dealer, you_cai_bi_kao=ycbk)
    while not g.done:
        current = g.current_seat()
        action = players[current](g, current)
        if action not in g.legal_actions():
            raise RuntimeError(
                f"{evaluator} produced illegal action {action} at seed={seed}")
        g.step(action)
    score = float(g.scores[seat])
    win = bool(g.result and g.result[0] == seat)
    mult = g.result[1] if win else None
    return {"score": score, "win": win, "mult": mult,
            "draw": g.result is None}


def _bootstrap(values, rounds=2000, seed=20260913):
    if not values:
        return {"n": 0, "mean": 0.0, "low": 0.0, "high": 0.0}
    rng = random.Random(seed)
    n = len(values)
    means = []
    for _ in range(rounds):
        means.append(sum(values[rng.randrange(n)] for _ in range(n)) / n)
    means.sort()
    return {"n": n, "mean": statistics.fmean(values),
            "low": means[int(0.025 * (rounds - 1))],
            "high": means[int(0.975 * (rounds - 1))]}


def run(games=4096, seed_start=182048, ycbk=False, evaluator="shape-v1"):
    if evaluator == "shape-v1":
        warmup(evaluator)
    rows = []
    t0 = time.perf_counter()
    # Indexing by i gives all sixteen (seat, dealer) combinations equal
    # representation when games is a multiple of 16.
    for i in range(games):
        seat, dealer = i % 4, (i // 4) % 4
        seed = seed_start + i
        old = _play(seed, seat, dealer, ycbk, "legacy")
        new = _play(seed, seat, dealer, ycbk, evaluator)
        rows.append({"seed": seed, "seat": seat, "dealer": dealer,
                     "legacy": old, "shape": new,
                     "score_delta": new["score"] - old["score"],
                     "win_delta": int(new["win"]) - int(old["win"])})
    score = [r["score_delta"] for r in rows]
    win = [r["win_delta"] for r in rows]
    return {
        "games": games, "seed_start": seed_start, "ycbk": bool(ycbk),
        "evaluator": evaluator,
        "seat_dealer_combinations": 16,
        "score_delta": _bootstrap(score),
        "win_delta": _bootstrap(win),
        "shape_wins": sum(r["shape"]["win"] for r in rows),
        "legacy_wins": sum(r["legacy"]["win"] for r in rows),
        "shape_draws": sum(r["shape"]["draw"] for r in rows),
        "legacy_draws": sum(r["legacy"]["draw"] for r in rows),
        "elapsed_s": time.perf_counter() - t0,
    }


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=4096)
    ap.add_argument("--seed-start", type=int, default=182048)
    ap.add_argument("--you-cai-bi-kao", action="store_true")
    ap.add_argument("--evaluator", choices=("shape-v1", "shape-v2"),
                    default="shape-v1")
    ap.add_argument("--output")
    args = ap.parse_args(argv)
    result = run(args.games, args.seed_start, args.you_cai_bi_kao,
                 args.evaluator)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    print(text)
    if args.output:
        # The caller owns the output path; this script intentionally does not
        # overwrite a release artifact without an explicit --output.
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(text + "\n")


if __name__ == "__main__":
    main()
