"""Benchmark the optional native legacy two-ply evaluator.

Example:
    PYTHONPATH=/tmp/mj-kernel-test:. python3 scripts/legacy_two_ply_rust_bench.py

The benchmark deliberately measures the complete ``choose_discard`` call, not
just the Rust function, so root filtering and explanation overhead remain
visible in the evidence.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
import statistics
import time

from mj.bot import choose_discard
from mj.game import Game
from mj.legacy_eval import LegacyTwoPlyProfile
from mj.shanten import LEGACY_TWO_PLY_KERNEL_VERSION


def _quantile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(len(ordered) * fraction))
    return ordered[index]


def run(states, seed0, budget_ms, node_budget):
    durations = []
    nodes = []
    complete = 0
    illegal = 0
    reasons = Counter()
    for offset in range(states):
        game = Game(seed=seed0 + offset)
        seat = game.current_seat()
        profile = LegacyTwoPlyProfile(
            kernel="rust",
            node_budget=node_budget,
            time_budget_ms=budget_ms,
        )
        started = time.perf_counter()
        try:
            action, info = choose_discard(
                game, seat, return_info=True, profile=profile)
        except Exception as exc:  # benchmark must report malformed states
            reasons[type(exc).__name__] += 1
            continue
        durations.append((time.perf_counter() - started) * 1000.0)
        nodes.append(int(info.get("future_nodes", 0)))
        complete += int(bool(info.get("complete")))
        if info.get("fallback_reason"):
            reasons[str(info["fallback_reason"])] += 1
        if action not in [tile for tile, count in enumerate(game.hands[seat])
                          if count > 0]:
            illegal += 1
    return {
        "states": states,
        "budget_ms": budget_ms,
        "node_budget": node_budget,
        "complete_decisions": complete,
        "fallback_decisions": len(durations) - complete,
        "illegal_actions": illegal,
        "p50_ms": _quantile(durations, 0.50),
        "p95_ms": _quantile(durations, 0.95),
        "p99_ms": _quantile(durations, 0.99),
        "nodes_total": sum(nodes),
        "nodes_p50": statistics.median(nodes) if nodes else None,
        "nodes_max": max(nodes) if nodes else None,
        "fallback_reasons": dict(reasons),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--states", type=int, default=100)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--node-budget", type=int, default=4096)
    parser.add_argument("--budgets", type=float, nargs="+",
                        default=[8.0, 50.0, 1000.0])
    args = parser.parse_args()
    print(json.dumps({
        "kernel_version": LEGACY_TWO_PLY_KERNEL_VERSION,
        "results": [run(args.states, args.seed, budget, args.node_budget)
                    for budget in args.budgets],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
