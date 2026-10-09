#!/usr/bin/env python3
"""Paired settlement-score evidence for the legacyV2 shape experiment.

One hero plays against three frozen ``legacy-v2-baseline`` bots in both
arms.  Each pair rebuilds the same seed, hero seat, dealer, and rules; only
the hero evaluator changes.  This runner is offline evidence and never
changes the production evaluator default.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
import os
import statistics
import sys
import time
from concurrent.futures import ProcessPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from mj.bot import choose_action
from mj.decision.calibration import cluster_bootstrap
from mj.game import Game
from mj.legacy_eval import LegacyTwoPlyProfile
from mj.shanten import kernel_runtime_diagnostic


BASELINE = "legacy-v2-baseline"
STAGES = {
    1: {"candidate": "legacy-v2-shape-phase-a", "seed_start": 520000,
        "pairs": 4096},
    2: {"candidate": "legacy-v2-shape-phase-b", "seed_start": 820000,
        "pairs": 30720},
}
_SIGNATURE_FIELDS = (
    "complete_melds", "taatsu", "ryanmen", "central_kanchan",
    "edge_kanchan", "penchan", "pair_units", "isolated",
)


def _evaluation_payload(value):
    if isinstance(value, tuple) and len(value) == 2:
        action, evaluation = value
    else:
        action, evaluation = value, None
    return action, evaluation if isinstance(evaluation, dict) else {}


def _wall_bucket(wall_left):
    if wall_left <= 10:
        return "0-10"
    if wall_left <= 20:
        return "11-20"
    if wall_left <= 40:
        return "21-40"
    return "41+"


def _signature_delta(evaluation):
    """Name the largest positive taatsu-class change, if diagnostics permit."""
    selected = evaluation.get("selected")
    baseline = evaluation.get("shape_baseline_selected")
    rows = {row.get("tile"): row for row in evaluation.get("candidates", ())
            if isinstance(row, dict)}
    chosen = rows.get(selected, {})
    old = rows.get(baseline, {})
    new_signature = chosen.get("standing_shape_signature")
    old_signature = old.get("standing_shape_signature")
    if not (isinstance(new_signature, (list, tuple)) and
            isinstance(old_signature, (list, tuple)) and
            len(new_signature) == len(old_signature) == 8):
        return "signature_unavailable"
    deltas = [int(new_signature[index]) - int(old_signature[index])
              for index in range(8)]
    positive = [(_SIGNATURE_FIELDS[index], delta)
                for index, delta in enumerate(deltas[1:6], start=1)
                if delta > 0]
    if not positive:
        return "no_taatsu_class_gain"
    # A single label keeps the bucket stable when multiple dimensions move.
    return "+".join(f"{name}+{amount}" for name, amount in positive)


def _new_arm_stats():
    return {"wins": 0, "draws": 0, "winning_multipliers": [],
            "scores": [], "decisions": 0, "shape_used": 0,
            "shape_changed_decisions": 0, "stage_b_decisions": 0,
            "decision_scope": Counter(), "changed_by_scope": Counter(),
            "shanten": Counter(), "changed_by_shanten": Counter(),
            "open_melds": Counter(), "changed_by_open_meld": Counter(),
            "wall_left": Counter(), "changed_by_wall_left": Counter(),
            "taatsu_upgrades": Counter(),
            "changed_game_deltas": [], "unchanged_game_deltas": []}


def _profiles(stage):
    baseline = LegacyTwoPlyProfile.weighted_online(
        big_hand_enabled=False, big_hand_same_shanten_enabled=False,
        big_hand_plus_one_enabled=False, shape_quality_enabled=False,
        shape_quality_guard_enabled=False,
        marginal_structure_guard_enabled=False)
    candidate = LegacyTwoPlyProfile.weighted_online(
        big_hand_enabled=False, big_hand_same_shanten_enabled=False,
        big_hand_plus_one_enabled=False, shape_quality_enabled=True,
        shape_quality_stage="root" if int(stage) == 1 else "full",
        shape_quality_guard_enabled=True,
        marginal_structure_guard_enabled=False)
    return baseline, candidate


def _play(seed, hero_seat, dealer, evaluator):
    """Play one arm and retain only settlement plus public decision facts."""
    game = Game(seed=int(seed), dealer=int(dealer))
    hero_stats = _new_arm_stats()
    while not game.done:
        seat = game.current_seat()
        chosen_evaluator = evaluator if seat == hero_seat else BASELINE
        if seat == hero_seat and evaluator != BASELINE:
            action, evaluation = _evaluation_payload(choose_action(
                game, seat, evaluator=chosen_evaluator,
                return_evaluation=True))
            if game.phase == "discard":
                hero_stats["decisions"] += 1
                scope = str(evaluation.get("decision_scope", "legacy"))
                hero_stats["decision_scope"][scope] += 1
                changed = bool(evaluation.get("shape_changed_winner"))
                shape_used = bool(evaluation.get("shape_quality_used"))
                hero_stats["shape_used"] += int(shape_used)
                hero_stats["shape_changed_decisions"] += int(changed)
                hero_stats["stage_b_decisions"] += int(
                    bool(evaluation.get("stage_b_entered")))
                if changed:
                    hero_stats["changed_by_scope"][scope] += 1
                    hero_stats["taatsu_upgrades"][_signature_delta(
                        evaluation)] += 1
                candidates = evaluation.get("candidates") or ()
                selected = evaluation.get("selected")
                selected_row = next((row for row in candidates
                                     if row.get("tile") == selected), {})
                shanten = selected_row.get("shanten")
                shanten_bucket = None
                if shanten is not None:
                    shanten_bucket = str(shanten)
                    hero_stats["shanten"][shanten_bucket] += 1
                meld_bucket = str(len(game.melds[seat]))
                wall_bucket = _wall_bucket(int(game.live_wall_left()))
                hero_stats["open_melds"][meld_bucket] += 1
                hero_stats["wall_left"][wall_bucket] += 1
                if changed:
                    if shanten_bucket is not None:
                        hero_stats["changed_by_shanten"][shanten_bucket] += 1
                    hero_stats["changed_by_open_meld"][meld_bucket] += 1
                    hero_stats["changed_by_wall_left"][wall_bucket] += 1
        else:
            action = choose_action(game, seat, evaluator=chosen_evaluator)
        if action not in tuple(game.legal_actions()):
            raise RuntimeError(
                f"{chosen_evaluator} produced illegal action={action}: "
                f"seed={seed} hero={hero_seat} dealer={dealer} seat={seat} "
                f"phase={game.phase}")
        game.step(action)
    if sum(game.scores) != 0:
        raise AssertionError(f"score conservation failed: {game.scores}")
    hero_score = float(game.scores[hero_seat])
    winner = game.result[0] if game.result else None
    multiplier = (float(game.result[1]) if winner == hero_seat else None)
    return {
        "score": hero_score,
        "win": winner == hero_seat,
        "draw": game.result is None,
        "winning_multiplier": multiplier,
        "shape_decisions": hero_stats["shape_used"],
        "shape_changed_decisions": hero_stats["shape_changed_decisions"],
        "stage_b_decisions": hero_stats["stage_b_decisions"],
        "decision_scope": dict(hero_stats["decision_scope"]),
        "changed_by_scope": dict(hero_stats["changed_by_scope"]),
        "shanten": dict(hero_stats["shanten"]),
        "open_melds": dict(hero_stats["open_melds"]),
        "wall_left": dict(hero_stats["wall_left"]),
        "taatsu_upgrades": dict(hero_stats["taatsu_upgrades"]),
        "diagnostics": {
            "decision_scope": dict(hero_stats["decision_scope"]),
            "changed_by_scope": dict(hero_stats["changed_by_scope"]),
            "shanten": dict(hero_stats["shanten"]),
            "changed_by_shanten": dict(hero_stats["changed_by_shanten"]),
            "open_melds": dict(hero_stats["open_melds"]),
            "changed_by_open_meld": dict(
                hero_stats["changed_by_open_meld"]),
            "wall_left": dict(hero_stats["wall_left"]),
            "changed_by_wall_left": dict(
                hero_stats["changed_by_wall_left"]),
            "taatsu_upgrades": dict(hero_stats["taatsu_upgrades"]),
        },
    }


def _interval(values, *, rounds, seed, alpha=0.05):
    if not values:
        return {"n": 0, "mean": None, "low": None, "high": None,
                "method": "source-seed percentile bootstrap"}
    groups = [f"seed:{index}" for index in range(len(values))]
    result = cluster_bootstrap(values, groups, rounds=rounds, seed=seed,
                               alpha=alpha)
    return {"n": len(values), "mean": result["mean"],
            "low": result["low"], "high": result["high"],
            "alpha": alpha, "rounds": rounds, "seed": seed,
            "method": result["method"]}


def _paired_row(index, seed_start, candidate_evaluator):
    """Run one independent, internally alternating score pair."""
    seed = int(seed_start) + int(index)
    hero_seat = int(index) % 4
    dealer = (int(index) // 4) % 4
    order = ([BASELINE, candidate_evaluator] if int(index) % 2 == 0 else
             [candidate_evaluator, BASELINE])
    arms = {}
    try:
        for evaluator in order:
            arms[evaluator] = _play(seed, hero_seat, dealer, evaluator)
        baseline = arms[BASELINE]
        candidate = arms[candidate_evaluator]
        changed_count = int(candidate["shape_changed_decisions"])
        return {
            "row": {
                "index": int(index), "seed": seed,
                "hero_seat": hero_seat, "dealer": dealer,
                "execution_order": order,
                "baseline_score": baseline["score"],
                "candidate_score": candidate["score"],
                "score_delta": candidate["score"] - baseline["score"],
                "baseline_win": baseline["win"],
                "candidate_win": candidate["win"],
                "baseline_draw": baseline["draw"],
                "candidate_draw": candidate["draw"],
                "candidate_winning_multiplier": candidate[
                    "winning_multiplier"],
                "shape_evaluated_decisions": candidate["shape_decisions"],
                "shape_changed_decisions": changed_count,
                "stage_b_decisions": candidate["stage_b_decisions"],
                "changed_game": changed_count > 0,
                "candidate_diagnostics": candidate["diagnostics"],
            },
            "error": None,
        }
    except Exception as exc:
        return {"row": None, "error": {
            "index": int(index), "seed": seed,
            "hero_seat": hero_seat, "dealer": dealer,
            "error": f"{type(exc).__name__}:{exc}",
        }}


def run(*, stage=1, games=None, seed_start=None, bootstrap_rounds=2000,
        bootstrap_seed=20260926, alpha=0.05, progress_every=100, jobs=1):
    if int(stage) not in STAGES:
        raise ValueError("stage must be 1 or 2")
    protocol = STAGES[int(stage)]
    games = int(protocol["pairs"] if games is None else games)
    seed_start = int(protocol["seed_start"] if seed_start is None
                     else seed_start)
    jobs = int(jobs)
    if games <= 0 or bootstrap_rounds <= 0 or jobs <= 0:
        raise ValueError("games, bootstrap_rounds, and jobs must be positive")
    candidate_evaluator = str(protocol["candidate"])
    start = time.perf_counter()
    rows = []
    errors = []
    if jobs == 1:
        outcomes = (_paired_row(index, seed_start, candidate_evaluator)
                    for index in range(games))
        executor = None
    else:
        executor = ProcessPoolExecutor(max_workers=jobs)
        outcomes = executor.map(
            _paired_row, range(games),
            [seed_start] * games, [candidate_evaluator] * games,
            chunksize=1)
    try:
        for completed, outcome in enumerate(outcomes, start=1):
            if outcome["row"] is not None:
                rows.append(outcome["row"])
            if outcome["error"] is not None:
                errors.append(outcome["error"])
            if progress_every and completed % int(progress_every) == 0:
                print(f"paired games {completed}/{games}", file=sys.stderr,
                      flush=True)
    finally:
        if executor is not None:
            executor.shutdown(wait=True)

    deltas = [float(row["score_delta"]) for row in rows]
    lower_seed = int(bootstrap_seed)
    score_interval = _interval(deltas, rounds=int(bootstrap_rounds),
                               seed=lower_seed, alpha=float(alpha))
    changed = [row for row in rows if row["changed_game"]]
    unchanged = [row for row in rows if not row["changed_game"]]
    expected = protocol["pairs"]
    expected_seed = protocol["seed_start"]
    frozen_schedule = (games == expected and seed_start == expected_seed and
                       len(rows) == expected and not errors)
    if stage == 1:
        gate_passed = bool(
            frozen_schedule and score_interval["mean"] is not None and
            score_interval["mean"] >= -0.10 and
            score_interval["high"] is not None and
            score_interval["high"] >= 0 and
            sum(row["shape_changed_decisions"] for row in rows) > 0)
        gate_rule = ("4096 frozen pairs; mean >= -0.10 and 95% CI upper "
                     ">= 0; at least one shape-changed decision")
    else:
        gate_passed = bool(
            frozen_schedule and score_interval["mean"] is not None and
            score_interval["mean"] >= 0 and score_interval["low"] is not None
            and score_interval["low"] >= -0.10 and
            sum(row["shape_changed_decisions"] for row in rows) > 0)
        gate_rule = ("30720 independent frozen pairs; mean >= 0 and 95% CI "
                     "lower >= -0.10")
    candidate_wins = sum(bool(row["candidate_win"]) for row in rows)
    baseline_wins = sum(bool(row["baseline_win"]) for row in rows)
    candidate_draws = sum(bool(row["candidate_draw"]) for row in rows)
    baseline_draws = sum(bool(row["baseline_draw"]) for row in rows)
    score_buckets = {
        "games_with_shape_changed_decision": {
            "games": len(changed),
            "score_delta": _interval(
                [row["score_delta"] for row in changed],
                rounds=int(bootstrap_rounds), seed=lower_seed + 1,
                alpha=float(alpha)),
        },
        "games_without_shape_changed_decision": {
            "games": len(unchanged),
            "score_delta": _interval(
                [row["score_delta"] for row in unchanged],
                rounds=int(bootstrap_rounds), seed=lower_seed + 2,
                alpha=float(alpha)),
        },
    }
    candidate_eval = _play_stats_summary(rows)
    baseline_profile, candidate_profile = _profiles(stage)
    return {
        "schema": "legacy-v2-shape-aware-two-ply/paired-score-evidence-v1",
        "stage": int(stage), "baseline_evaluator": BASELINE,
        "candidate_evaluator": candidate_evaluator,
        "jobs": jobs,
        "opponents": "other three seats use legacy-v2-baseline in both arms",
        "games_requested": games, "games_completed": len(rows),
        "seed_start": seed_start, "seat_policy": "index % 4",
        "dealer_policy": "(index // 4) % 4",
        "execution_order": "alternates by pair index parity",
        "frozen_schedule_match": frozen_schedule,
        "primary_metric": "per-game hero settlement score points",
        "profiles": {"baseline": baseline_profile.as_json(),
                     "candidate": candidate_profile.as_json()},
        "kernel_runtime": kernel_runtime_diagnostic(),
        "score_delta_candidate_minus_baseline": score_interval,
        "outcomes": {
            "baseline": {"wins": baseline_wins,
                         "win_rate": baseline_wins / len(rows) if rows else None,
                         "draws": baseline_draws,
                         "draw_rate": baseline_draws / len(rows) if rows else None},
            "candidate": {"wins": candidate_wins,
                          "win_rate": candidate_wins / len(rows) if rows else None,
                          "draws": candidate_draws,
                          "draw_rate": candidate_draws / len(rows) if rows else None,
                          "winning_multiplier_mean": statistics.fmean(
                              float(row["candidate_winning_multiplier"])
                              for row in rows if row[
                                  "candidate_winning_multiplier"] is not None)
                              if any(row["candidate_winning_multiplier"] is not None
                                     for row in rows) else None},
        },
        "shape_changes": {
            "shape_evaluated_decisions": sum(
                row["shape_evaluated_decisions"] for row in rows),
            "changed_decisions": sum(row["shape_changed_decisions"]
                                     for row in rows),
            "shape_changed_action_rate": (
                sum(row["shape_changed_decisions"] for row in rows) /
                sum(row["shape_evaluated_decisions"] for row in rows)
                if sum(row["shape_evaluated_decisions"] for row in rows)
                else None),
            "stage_b_entered_decisions": sum(
                row["stage_b_decisions"] for row in rows),
            "games_changed": len(changed),
            "decision_scope": candidate_eval["decision_scope"],
            "changed_by_scope": candidate_eval["changed_by_scope"],
            "taatsu_upgrade_distribution": candidate_eval["taatsu_upgrades"],
            "shanten": candidate_eval["shanten"],
            "changed_by_shanten": candidate_eval["changed_by_shanten"],
            "open_meld_count": candidate_eval["open_melds"],
            "changed_by_open_meld_count": candidate_eval[
                "changed_by_open_meld"],
            "wall_left_bucket": candidate_eval["wall_left"],
            "changed_by_wall_left_bucket": candidate_eval[
                "changed_by_wall_left"],
        },
        "changed_unchanged_game_score_buckets": score_buckets,
        "gate": {"passed": gate_passed, "rule": gate_rule,
                 "default_remains_disabled": True},
        "errors": errors, "rows": rows,
        "bootstrap": {"rounds": int(bootstrap_rounds),
                      "seed": int(bootstrap_seed), "alpha": float(alpha),
                      "method": "source-seed percentile bootstrap"},
        "elapsed_s": time.perf_counter() - start,
        "offline_only": True, "hidden_information_used": False,
        "default_strategy_unchanged": True,
    }


def _play_stats_summary(rows):
    """Merge per-game public evaluation counters retained in outcome rows."""
    def merged(name):
        result = Counter()
        for row in rows:
            result.update((row.get("candidate_diagnostics") or {}).get(name, {}))
        return dict(sorted(result.items()))

    return {
        "decision_scope": merged("decision_scope"),
        "changed_by_scope": merged("changed_by_scope"),
        "shanten": merged("shanten"),
        "changed_by_shanten": merged("changed_by_shanten"),
        "open_melds": merged("open_melds"),
        "changed_by_open_meld": merged("changed_by_open_meld"),
        "wall_left": merged("wall_left"),
        "changed_by_wall_left": merged("changed_by_wall_left"),
        "taatsu_upgrades": merged("taatsu_upgrades"),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", type=int, choices=(1, 2), default=1)
    parser.add_argument("--games", type=int)
    parser.add_argument("--seed-start", type=int)
    parser.add_argument("--bootstrap-rounds", type=int, default=2000)
    parser.add_argument("--bootstrap-seed", type=int, default=20260926)
    parser.add_argument("--jobs", type=int, default=1,
                        help="parallel independent pairs; each pair runs sequentially")
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    result = run(stage=args.stage, games=args.games, seed_start=args.seed_start,
                 bootstrap_rounds=args.bootstrap_rounds,
                 bootstrap_seed=args.bootstrap_seed, jobs=args.jobs)
    encoded = json.dumps(result, ensure_ascii=False, indent=2)
    print(encoded)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(encoded + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
