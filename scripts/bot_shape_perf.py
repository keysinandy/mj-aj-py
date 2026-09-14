#!/usr/bin/env python3
"""Deterministic local timing probe for legacy vs shape-v1 decisions."""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from mj.bot import choose_action
from mj.game import Game
from mj.hand_eval import warmup


def _percentile(values, p):
    if not values:
        return None
    values = sorted(values)
    return values[min(len(values) - 1, int((len(values) - 1) * p))]


def run(games=200, seed_start=190000, evaluator="shape-v1"):
    if evaluator == "shape-v1":
        warmup(evaluator)
    samples = {"discard": [], "react": []}
    fallbacks = {}
    levels = {"discard": {}, "react": {}}
    fallback_by_phase = {"discard": {}, "react": {}}
    q_pruned = {"discard": 0, "react": 0}
    t0 = time.perf_counter()
    for i in range(games):
        g = Game(seed=seed_start + i, dealer=i % 4,
                 you_cai_bi_kao=bool(i % 2))
        while not g.done:
            phase = "discard" if g.phase == "discard" else "react"
            start = time.perf_counter()
            result = choose_action(g, g.current_seat(), evaluator=evaluator,
                                   return_evaluation=True)
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            act, ev = result if isinstance(result, tuple) else (result, None)
            if act not in g.legal_actions():
                raise AssertionError(f"illegal {act} phase={g.phase}")
            samples[phase].append(elapsed_ms)
            reason = getattr(ev, "fallback_reason", None)
            if isinstance(ev, dict):
                reason = ev.get("fallback_reason")
                level = ev.get("level")
            else:
                level = getattr(ev, "level", None)
            if level:
                levels[phase][level] = levels[phase].get(level, 0) + 1
            if isinstance(ev, dict):
                q_pruned[phase] += sum(
                    1 for item in ev.get("candidates", ())
                    if item.get("q_pruned"))
            elif ev is not None:
                q_pruned[phase] += sum(
                    1 for item in getattr(ev, "candidates", ())
                    if item.get("q_pruned"))
            if reason:
                fallbacks[reason] = fallbacks.get(reason, 0) + 1
                phase_reasons = fallback_by_phase[phase]
                phase_reasons[reason] = phase_reasons.get(reason, 0) + 1
            g.step(act)
    summary = {"games": games, "seed_start": seed_start,
               "evaluator": evaluator,
               "elapsed_s": time.perf_counter() - t0,
               "fallbacks": fallbacks,
               "fallbacks_by_phase": fallback_by_phase,
               "levels": levels,
               "q_pruned_candidates": q_pruned}
    for phase, values in samples.items():
        summary[phase] = {
            "n": len(values), "mean_ms": statistics.fmean(values) if values else None,
            "p50_ms": _percentile(values, .50),
            "p95_ms": _percentile(values, .95),
            "p99_ms": _percentile(values, .99),
            "max_ms": max(values) if values else None,
        }
    return summary


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=200)
    ap.add_argument("--seed-start", type=int, default=190000)
    ap.add_argument("--evaluator", choices=("legacy", "shape-v1"),
                    default="shape-v1")
    args = ap.parse_args(argv)
    print(json.dumps(run(args.games, args.seed_start, args.evaluator),
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
