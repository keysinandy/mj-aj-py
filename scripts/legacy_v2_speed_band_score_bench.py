#!/usr/bin/env python3
import json
import random
import statistics
import time

from mj.bot import choose_action
from mj.legacy_eval import DEFAULT_BOT_EVALUATOR
from mj.game import Game

BASELINE = "legacy-v2-baseline"
CANDIDATE = "legacy-v2-speed-band"


def percentile(values, p):
    values = sorted(values)
    if not values:
        return None
    return values[min(len(values) - 1, int((len(values) - 1) * p))]


def bootstrap(values, rounds=4000, seed=20260915):
    values = [float(value) for value in values]
    if not values:
        return {"n": 0, "mean": None, "low": None, "high": None}
    rng = random.Random(seed)
    n = len(values)
    means = []
    for _ in range(rounds):
        means.append(sum(values[rng.randrange(n)] for _ in range(n)) / n)
    means.sort()
    return {
        "n": n,
        "mean": statistics.fmean(values),
        "low": means[int(0.025 * (rounds - 1))],
        "high": means[int(0.975 * (rounds - 1))],
        "method": "source-game-bootstrap",
    }


def play(seed, dealer, evaluator):
    game = Game(seed=int(seed), dealer=int(dealer))
    decisions = 0
    started = time.perf_counter()
    while not game.done:
        seat = game.current_seat()
        if evaluator == CANDIDATE:
            action = choose_action(
                game, seat, evaluator=DEFAULT_BOT_EVALUATOR,
                speed_band_enabled=True, pareto_frontier_enabled=True)
        else:
            # Baseline is the current canonical legacyV2 profile with the new
            # speed-band/Pareto flags explicitly off. This keeps shape,
            # marginal, and other production defaults unchanged.
            action = choose_action(
                game, seat, evaluator=DEFAULT_BOT_EVALUATOR,
                speed_band_enabled=False, pareto_frontier_enabled=False)
        legal = tuple(game.legal_actions())
        if action not in legal:
            raise RuntimeError(
                f"illegal action evaluator={evaluator} seed={seed} "
                f"seat={seat} action={action} legal={legal}")
        game.step(action)
        decisions += 1
    if sum(game.scores) != 0:
        raise AssertionError(f"score conservation failed: {game.scores}")
    return {
        "scores": [int(value) for value in game.scores],
        "winner": game.result[0] if game.result else None,
        "draw": game.result is None,
        "decisions": decisions,
        "elapsed_ms": (time.perf_counter() - started) * 1000.0,
    }


def run(games=200, repeats=3, seed_start=20260926):
    total = int(games) * int(repeats)
    rows = []
    elapsed = {BASELINE: [], CANDIDATE: []}
    decisions = {BASELINE: [], CANDIDATE: []}
    wins = {BASELINE: 0, CANDIDATE: 0}
    draws = {BASELINE: 0, CANDIDATE: 0}
    score_totals = {BASELINE: [0, 0, 0, 0], CANDIDATE: [0, 0, 0, 0]}
    started = time.perf_counter()
    for index in range(total):
        seed = int(seed_start) + index
        dealer = index % 4
        hero = index % 4
        order = (BASELINE, CANDIDATE) if index % 2 == 0 else (CANDIDATE, BASELINE)
        games_by_eval = {}
        for evaluator in order:
            result = play(seed, dealer, evaluator)
            games_by_eval[evaluator] = result
            elapsed[evaluator].append(result["elapsed_ms"])
            decisions[evaluator].append(result["decisions"])
            wins[evaluator] += int(result["winner"] == hero)
            draws[evaluator] += int(result["draw"])
            for seat, score in enumerate(result["scores"]):
                score_totals[evaluator][seat] += score
        rows.append({
            "index": index,
            "seed": seed,
            "dealer": dealer,
            "hero": hero,
            "baseline": games_by_eval[BASELINE],
            "candidate": games_by_eval[CANDIDATE],
            "hero_score_delta": (
                games_by_eval[CANDIDATE]["scores"][hero] -
                games_by_eval[BASELINE]["scores"][hero]),
        })
        if (index + 1) % 50 == 0:
            print(f"score games {index + 1}/{total}", flush=True)
    deltas = [row["hero_score_delta"] for row in rows]
    summary = {}
    for evaluator in (BASELINE, CANDIDATE):
        values = elapsed[evaluator]
        summary[evaluator] = {
            "games": total,
            "wins_as_balanced_hero": wins[evaluator],
            "win_rate_as_balanced_hero": wins[evaluator] / total,
            "draws": draws[evaluator],
            "draw_rate": draws[evaluator] / total,
            "score_totals_by_seat": score_totals[evaluator],
            "score_mean_all_seats": sum(score_totals[evaluator]) / (4 * total),
            "decisions_per_game_mean": statistics.fmean(decisions[evaluator]),
            "elapsed_ms_per_game": {
                "mean": statistics.fmean(values),
                "p50": percentile(values, 0.50),
                "p95": percentile(values, 0.95),
                "p99": percentile(values, 0.99),
                "max": max(values),
            },
        }
    return {
        "schema": "legacy-v2-speed-band/local-four-bot-score-v1",
        "games_per_repeat": int(games),
        "repeats": int(repeats),
        "games_per_evaluator": total,
        "seed_start": int(seed_start),
        "dealer_schedule": "index % 4",
        "hero_schedule": "index % 4",
        "interleaved_order": "baseline-first on even index, candidate-first on odd index",
        "evaluators": summary,
        "paired_hero_score_delta": bootstrap(deltas),
        "candidate_minus_baseline_win_rate": (
            (wins[CANDIDATE] - wins[BASELINE]) / total),
        "runtime_s": time.perf_counter() - started,
        "rows": rows,
    }


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--games", type=int, default=200)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--seed-start", type=int, default=20260926)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    value = run(args.games, args.repeats, args.seed_start)
    encoded = json.dumps(value, ensure_ascii=False, indent=2)
    print(json.dumps({
        "paired_hero_score_delta": value["paired_hero_score_delta"],
        "candidate_minus_baseline_win_rate": value["candidate_minus_baseline_win_rate"],
        "evaluators": value["evaluators"],
        "runtime_s": value["runtime_s"],
    }, ensure_ascii=False, indent=2))
    with open(args.output, "w", encoding="utf-8") as handle:
        handle.write(encoded + "\n")
