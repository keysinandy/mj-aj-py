#!/usr/bin/env python3
"""Bounded Piao Search fast-feature benchmark.

The benchmark clears the structural caches before each sample so the reported
latency includes the worst first-use mask construction.  It deliberately
does not run the HU-window score evaluator.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time

from mj import bot
from mj.shanten import clear_caches
from mj.tiles import counts


CASES = (
    "11m44m11p2s33s6sBBB",
    "55m8m888p99pFFBBB",
    "11m44m11p22s33sBBB",
)


def percentile(values, q):
    values = sorted(values)
    if not values:
        return 0.0
    return values[min(len(values) - 1, int(len(values) * q))]


def run(samples=200):
    timings = []
    features = []
    for index in range(int(samples)):
        hand = counts(CASES[index % len(CASES)])
        visible = list(hand)
        clear_caches()
        started = time.perf_counter()
        feature = bot._piao_search_fast_feature(
            hand, 0, visible, live_wall=40)
        timings.append((time.perf_counter() - started) * 1000.0)
        features.append(feature)
    return {
        "schema": "piao-search-fast-feature-benchmark-v1",
        "samples": int(samples),
        "p95_ms": round(percentile(timings, 0.95), 6),
        "p99_ms": round(percentile(timings, 0.99), 6),
        "mean_ms": round(statistics.fmean(timings), 6),
        "max_ms": round(max(timings), 6),
        "eligible_samples": sum(
            1 for feature in features if feature["piao_search_eligible"]),
        "nodes_max": max((feature["nodes"] for feature in features),
                          default=0),
        "performance_gate": {
            "p95_ms": 1.0,
            "p99_ms": 2.0,
            "passed": (percentile(timings, 0.95) <= 1.0 and
                       percentile(timings, 0.99) <= 2.0),
        },
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples", type=int, default=200)
    parser.add_argument("--out")
    args = parser.parse_args()
    result = run(args.samples)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            json.dump(result, handle, ensure_ascii=False, indent=2)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
