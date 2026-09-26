#!/usr/bin/env python3
"""Measure Python and native standing-shape helper latency on legal hands."""

from __future__ import annotations

import argparse
import json
import os
import random
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from mj.shape_quality import (SHAPE_QUALITY_VERSION, standing_shape_quality,
                              rust_standing_shape_quality)
from mj.shanten import kernel_runtime_diagnostic


def _percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * float(fraction)
    low = int(position)
    high = min(len(ordered) - 1, low + 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def _hands(count, seed):
    rng = random.Random(int(seed))
    deck = [tile for tile in range(34) for _ in range(4)]
    result = []
    for _ in range(int(count)):
        locked = rng.randrange(5)
        size = 13 - 3 * locked
        hand = [0] * 34
        for tile in rng.sample(deck, size):
            hand[tile] += 1
        result.append((hand, locked))
    return result


def _measure(function, fixtures, iterations):
    durations = []
    for index in range(100):
        hand, locked = fixtures[index % len(fixtures)]
        function(hand, locked=locked)
    for index in range(int(iterations)):
        hand, locked = fixtures[index % len(fixtures)]
        started = time.perf_counter_ns()
        function(hand, locked=locked)
        durations.append((time.perf_counter_ns() - started) / 1_000_000)
    return {
        "calls": len(durations), "unit": "ms/call",
        "p50": _percentile(durations, 0.50),
        "p95": _percentile(durations, 0.95),
        "p99": _percentile(durations, 0.99),
        "max": max(durations) if durations else None,
        "mean": statistics.fmean(durations) if durations else None,
    }


def run(iterations=10000, fixtures=256, seed=20260926):
    if int(iterations) <= 0 or int(fixtures) <= 0:
        raise ValueError("iterations and fixtures must be positive")
    cases = _hands(fixtures, seed)
    python = _measure(standing_shape_quality, cases, iterations)
    try:
        native = _measure(
            lambda hand, locked: rust_standing_shape_quality(hand),
            cases, iterations)
    except RuntimeError as exc:
        native = {"calls": 0, "unavailable": str(exc), "p50": None,
                  "p95": None, "p99": None, "max": None}
    return {
        "schema": "legacy-v2-shape-aware-two-ply/shape-microbench-v1",
        "shape_quality_version": SHAPE_QUALITY_VERSION,
        "iterations_per_kernel": int(iterations),
        "fixture_count": int(fixtures), "seed": int(seed),
        "python_reference": python, "rust_native": native,
        "kernel_runtime": kernel_runtime_diagnostic(),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=10000)
    parser.add_argument("--fixtures", type=int, default=256)
    parser.add_argument("--seed", type=int, default=20260926)
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    encoded = json.dumps(run(args.iterations, args.fixtures, args.seed),
                         ensure_ascii=False, indent=2)
    print(encoded)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(encoded + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
