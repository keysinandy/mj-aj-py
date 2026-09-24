#!/usr/bin/env python3
"""Paired local-game and same-machine performance evidence for BigHandIntent.

Performance mode plays four bots of each requested evaluator on interleaved,
matched seeds and records ordinary-discard latency/frontier/fallback facts.
Score mode compares one candidate hero against three frozen-baseline bots and
the same hero/baseline table on matched seed/seat/dealer cases.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
import math
import os
import platform
import random
import statistics
import sys
import time
from typing import Iterable

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from mj.bot import choose_action
from mj.game import Game
from mj.big_hand_intent import evaluate_big_hand_intent
from mj.legacy_eval import DEFAULT_BOT_EVALUATOR
from mj.shanten import kernel_runtime_diagnostic


def _percentile(values: Iterable[float], fraction: float):
    values = sorted(float(value) for value in values)
    if not values:
        return None
    index = (len(values) - 1) * float(fraction)
    low = int(math.floor(index))
    high = int(math.ceil(index))
    if low == high:
        return values[low]
    return values[low] + (values[high] - values[low]) * (index - low)


def _distribution(values: Iterable[float], *, unit: str):
    values = [float(value) for value in values]
    return {
        "n": len(values),
        "unit": unit,
        "mean": statistics.fmean(values) if values else None,
        "p50": _percentile(values, 0.50),
        "p95": _percentile(values, 0.95),
        "p99": _percentile(values, 0.99),
        "max": max(values) if values else None,
    }


def _bootstrap(values, *, rounds=5000, seed=20260923, alpha=0.05):
    values = [float(value) for value in values]
    if not values:
        return {"n": 0, "mean": None, "ci_low": None, "ci_high": None,
                "alpha": float(alpha),
                "method": "paired source-seed percentile bootstrap"}
    rng = random.Random(int(seed))
    n = len(values)
    means = [sum(values[rng.randrange(n)] for _ in range(n)) / n
             for _ in range(int(rounds))]
    means.sort()
    lower = min(len(means) - 1, max(0, int(alpha / 2 * len(means))))
    upper = min(len(means) - 1,
                max(0, int((1 - alpha / 2) * len(means)) - 1))
    return {
        "n": n,
        "sum": sum(values),
        "mean": statistics.fmean(values),
        "ci_low": means[lower],
        "ci_high": means[upper],
        "alpha": float(alpha),
        "method": "paired source-seed percentile bootstrap",
        "rounds": int(rounds),
        "seed": int(seed),
    }


def run_intent_microbench(*, iterations=10000, seed_start=810000):
    """Measure only the fixed O(34) public-feature computation."""
    if int(iterations) <= 0:
        raise ValueError("iterations must be positive")
    cases = []
    for offset in range(256):
        game = Game(seed=int(seed_start) + offset)
        seat = game.current_seat()
        hand = game.hands[seat]
        locked = len(game.melds[seat])
        visible = tuple(game.visible_counts(seat))
        try:
            live_wall = int(game.live_wall_left())
            opponent_melds = max(
                len(game.melds[other]) for other in range(4) if other != seat)
        except (AttributeError, TypeError, ValueError):
            live_wall = None
            opponent_melds = None
        for tile, count in enumerate(hand):
            if not count:
                continue
            standing = list(hand)
            standing[tile] -= 1
            cases.append((tuple(standing), locked, visible, live_wall,
                          opponent_melds))
    if not cases:
        raise RuntimeError("failed to build intent benchmark fixtures")
    durations = []
    kinds = Counter()
    start = time.perf_counter()
    for index in range(int(iterations)):
        hand, locked, visible, live_wall, opponent_melds = cases[index % len(cases)]
        before = time.perf_counter_ns()
        result = evaluate_big_hand_intent(
            hand, locked, visible, live_wall=live_wall,
            max_opponent_melds=opponent_melds)
        durations.append((time.perf_counter_ns() - before) / 1_000_000)
        kinds.update(result.kinds)
    elapsed_s = time.perf_counter() - start
    return {
        "mode": "intent_microbench",
        "iterations": int(iterations),
        "fixture_count": len(cases),
        "latency": _distribution(durations, unit="ms/call; public O(34) intent only"),
        "kind_counts": dict(kinds),
        "wall_time_s": elapsed_s,
        "kernel_runtime": kernel_runtime_diagnostic(),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
    }


def _evaluation_payload(result):
    if isinstance(result, tuple) and len(result) == 2:
        action, evaluation = result
    else:
        action, evaluation = result, None
    return action, evaluation if isinstance(evaluation, dict) else {}


def _ordinary_sample(game, seat, action, evaluation, elapsed_ms):
    if game.phase != "discard" or int(action) < 0:
        return None
    metrics = evaluation.get("search_metrics") or {}
    candidates = evaluation.get("candidates") or ()
    root_count = metrics.get("root_candidates")
    if root_count is None:
        root_count = len([row for row in candidates
                          if row.get("admitted_by") in
                          (None, "primary", "shape_guard", "big_hand_guard")])
    selected = evaluation.get("selected")
    selected_row = next((row for row in candidates
                         if row.get("tile") == selected), {})
    kinds = selected_row.get("intent_kinds", ())
    if isinstance(kinds, str):
        kinds = (kinds,)
    return {
        "elapsed_ms": float(elapsed_ms),
        "frontier_roots": int(root_count) if root_count is not None else None,
        "fallback": bool(evaluation.get("fallback_reason")),
        "fallback_reason": evaluation.get("fallback_reason"),
        "profile_fingerprint": evaluation.get("profile_fingerprint"),
        "intent_kinds": sorted(set(str(kind) for kind in kinds)),
        "challenger": evaluation.get("big_hand_challenger"),
        "override": bool(evaluation.get("big_hand_override")),
        "override_reason": evaluation.get("big_hand_override_reason"),
        "phase": evaluation.get("big_hand_phase", "disabled"),
        "speed_pool_tiles": evaluation.get("speed_pool_tiles", ()),
        "frontier_tiles": [row.get("tile") for row in candidates
                           if row.get("admitted_by") in
                           ("primary", "shape_guard", "big_hand_guard") and
                           row.get("future_nodes") is not None],
        "admitted_by": Counter(
            str(row.get("admitted_by", "none")) for row in candidates),
        "intent_kinds_seen": sorted({
            str(kind) for row in candidates
            for kind in (row.get("intent_kinds") or ())
        }),
        "intent_roots": sum(bool(row.get("intent_kinds"))
                             for row in candidates),
        "big_hand_gate_reasons": Counter(
            str(row.get("big_hand_gate_reason")) for row in candidates
            if row.get("big_hand_gate_reason")),
        "selected": selected,
        "speed_winner": evaluation.get("speed_winner"),
        "big_hand_challenger": evaluation.get("big_hand_challenger"),
        "big_hand_override": bool(evaluation.get("big_hand_override")),
        "roots": [{
            key: row.get(key) for key in (
                "tile", "shanten", "ukeire", "intent_kinds",
                "intent_strength", "chiitoi_shanten", "pair_units",
                "luxury_groups", "luxury_upgrade_tiles",
                "luxury_upgrade_live", "wild_count", "wild_live",
                "speed_eligible", "shanten_regression", "admitted_by",
                "big_hand_gate_reason", "missing",
            ) if key in row
        } for row in candidates],
        "live_wall": int(game.live_wall_left()),
        "opponent_melds": max(
            (len(game.melds[other]) for other in range(4) if other != seat),
            default=0,
        ),
    }


def _run_game(seed, dealer, evaluator_for_seat, *, ycbk=False,
              collect_decisions=False):
    game = Game(seed=int(seed), dealer=int(dealer),
                you_cai_bi_kao=bool(ycbk))
    decision_rows = []
    started = time.perf_counter()
    while not game.done:
        seat = game.current_seat()
        evaluator = evaluator_for_seat(seat)
        decision_started = time.perf_counter_ns()
        result = choose_action(game, seat, evaluator=evaluator,
                               return_evaluation=collect_decisions)
        elapsed_ms = (time.perf_counter_ns() - decision_started) / 1_000_000
        action, evaluation = _evaluation_payload(result)
        legal = tuple(game.legal_actions())
        if action not in legal:
            raise RuntimeError(
                f"{evaluator} produced illegal action={action}; seed={seed} "
                f"dealer={dealer} seat={seat} phase={game.phase} "
                f"legal={legal} drawn={game.drawn[seat]} "
                f"wall={game.live_wall_left()}"
            )
        if collect_decisions:
            sample = _ordinary_sample(
                game, seat, action, evaluation, elapsed_ms)
            if sample is not None:
                sample.update({"seat": seat, "action": int(action)})
                decision_rows.append(sample)
        game.step(action)
    elapsed_s = time.perf_counter() - started
    if sum(game.scores) != 0:
        raise AssertionError(f"score conservation failed: {game.scores}")
    return {
        "scores": [float(score) for score in game.scores],
        "winner": game.result[0] if game.result else None,
        "draw": game.result is None,
        "elapsed_s": elapsed_s,
        "decisions": decision_rows,
    }


def run_performance(*, evaluators, games=200, repeats=3, seed_start=310000,
                    ycbk=False, decision_sample_limit=256,
                    intent_sample_limit=100):
    evaluators = tuple(dict.fromkeys(str(value) for value in evaluators))
    if not evaluators:
        raise ValueError("at least one evaluator is required")
    if int(games) <= 0 or int(repeats) <= 0:
        raise ValueError("games and repeats must be positive")
    stats = {name: {"decision_ms": [], "frontier_roots": [],
                    "game_elapsed_ms": [], "fallbacks": 0,
                    "ordinary_decisions": 0, "game_scores": [[], [], [], []],
                    "profile_fingerprints": Counter(),
                    "intent_decisions": 0, "intent_roots": 0,
                    "intent_kinds": Counter(), "admitted_by": Counter(),
                    "big_hand_gate_reasons": Counter(),
                    "phase_counts": Counter(), "overrides": 0,
                    "decision_samples": [], "intent_samples": []}
             for name in evaluators}
    started = time.perf_counter()
    total_matches = int(games) * int(repeats) * len(evaluators)
    match_index = 0
    for repeat in range(int(repeats)):
        for offset in range(int(games)):
            seed = int(seed_start) + offset
            dealer = (repeat * int(games) + offset) % 4
            order = list(evaluators)
            if (repeat + offset) % 2:
                order.reverse()
            for evaluator in order:
                result = _run_game(
                    seed, dealer, lambda _seat, value=evaluator: value,
                    ycbk=ycbk, collect_decisions=True)
                target = stats[evaluator]
                target["game_elapsed_ms"].append(result["elapsed_s"] * 1000)
                for seat, score in enumerate(result["scores"]):
                    target["game_scores"][seat].append(score)
                for row in result["decisions"]:
                    row["seed"] = seed
                    row["dealer"] = dealer
                    target["ordinary_decisions"] += 1
                    target["decision_ms"].append(row["elapsed_ms"])
                    if row["frontier_roots"] is not None:
                        target["frontier_roots"].append(row["frontier_roots"])
                    target["fallbacks"] += int(row["fallback"])
                    target["intent_roots"] += int(row["intent_roots"])
                    target["intent_decisions"] += bool(row["intent_kinds_seen"])
                    target["intent_kinds"].update(row["intent_kinds_seen"])
                    target["admitted_by"].update(row["admitted_by"])
                    target["big_hand_gate_reasons"].update(
                        row["big_hand_gate_reasons"])
                    target["phase_counts"].update([row["phase"]])
                    target["overrides"] += int(row["override"])
                    if len(target["decision_samples"]) < int(decision_sample_limit):
                        target["decision_samples"].append(row)
                    if (row["intent_kinds_seen"] and
                            len(target["intent_samples"]) <
                            int(intent_sample_limit)):
                        target["intent_samples"].append(row)
                    if row["profile_fingerprint"]:
                        target["profile_fingerprints"][
                            row["profile_fingerprint"]] += 1
                match_index += 1
                if match_index % 100 == 0:
                    print(f"performance matches {match_index}/{total_matches}",
                          file=sys.stderr, flush=True)
    elapsed_s = time.perf_counter() - started
    summaries = {}
    for name, value in stats.items():
        n = value["ordinary_decisions"]
        summaries[name] = {
            "four_bot_games": len(value["game_elapsed_ms"]),
            "ordinary_decisions": n,
            "ordinary_discard_latency": _distribution(
                value["decision_ms"], unit="ms; choose_action(return_evaluation=True)"),
            "frontier_root_count": _distribution(
                value["frontier_roots"], unit="roots"),
            "frontier_root_count_max": max(value["frontier_roots"], default=None),
            "fallback_rate": (value["fallbacks"] / n if n else None),
            "fallback_count": value["fallbacks"],
            "four_bot_elapsed_per_game": _distribution(
                value["game_elapsed_ms"], unit="ms/game; instrumented full match"),
            "seat_score_mean": [statistics.fmean(scores) if scores else None
                                for scores in value["game_scores"]],
            "profile_fingerprints": dict(value["profile_fingerprints"]),
            "intent_decisions": value["intent_decisions"],
            "intent_roots": value["intent_roots"],
            "intent_kind_root_counts": dict(value["intent_kinds"]),
            "admitted_by_counts": dict(value["admitted_by"]),
            "big_hand_gate_reason_counts": dict(
                value["big_hand_gate_reasons"]),
            "phase_counts": dict(value["phase_counts"]),
            "big_hand_override_selections": value["overrides"],
            "decision_samples": value["decision_samples"],
            "intent_decision_samples": value["intent_samples"],
        }
    comparisons = {}
    if len(evaluators) == 2:
        base, candidate = evaluators
        b = summaries[base]
        c = summaries[candidate]
        b95 = b["ordinary_discard_latency"]["p95"]
        c95 = c["ordinary_discard_latency"]["p95"]
        b50 = b["four_bot_elapsed_per_game"]["p50"]
        c50 = c["four_bot_elapsed_per_game"]["p50"]
        comparisons = {
            "baseline_evaluator": base,
            "candidate_evaluator": candidate,
            "ordinary_discard_p95_regression_pct": (
                (c95 / b95 - 1) * 100 if b95 and c95 is not None else None),
            "four_bot_elapsed_p50_regression_pct": (
                (c50 / b50 - 1) * 100 if b50 and c50 is not None else None),
            "candidate_frontier_max_le_3": (
                c["frontier_root_count_max"] is not None and
                c["frontier_root_count_max"] <= 3),
            "sample_gate_pass": int(games) >= 200 and int(repeats) >= 3,
        }
    return {
        "mode": "performance",
        "seed_start": int(seed_start),
        "games_per_evaluator_per_repeat": int(games),
        "repeats": int(repeats),
        "ycbk": bool(ycbk),
        "seat_policy": "all four seats use the same evaluator",
        "interleaved": True,
        "instrumentation_note": (
            "Decision and whole-game timings include return_evaluation=True "
            "to collect per-decision diagnostics."),
        "evaluators": summaries,
        "comparison": comparisons,
        "runtime_s": elapsed_s,
        "total_matches": total_matches,
        "matches_per_second": total_matches / elapsed_s if elapsed_s else None,
        "kernel_runtime": kernel_runtime_diagnostic(),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "default_evaluator": DEFAULT_BOT_EVALUATOR,
    }


def _play_score_game(seed, seat, dealer, hero_evaluator, baseline_evaluator,
                     *, ycbk=False):
    game = Game(seed=int(seed), dealer=int(dealer),
                you_cai_bi_kao=bool(ycbk))
    ordinary_decisions = 0
    overrides = []
    intent_samples = []
    fingerprints = Counter()
    intent_kinds_seen = set()
    intent_kind_roots = Counter()
    admission_counts = Counter()
    phase_counts = Counter()
    started = time.perf_counter()
    while not game.done:
        current = game.current_seat()
        evaluator = (hero_evaluator if current == seat
                     else baseline_evaluator)
        result = choose_action(game, current, evaluator=evaluator,
                               return_evaluation=(current == seat))
        action, evaluation = _evaluation_payload(result)
        legal = tuple(game.legal_actions())
        if action not in legal:
            raise RuntimeError(
                f"{evaluator} produced illegal action={action}; seed={seed} "
                f"hero={seat} dealer={dealer} seat={current} "
                f"phase={game.phase} legal={legal}"
            )
        if current == seat and game.phase == "discard" and int(action) >= 0:
            ordinary_decisions += 1
            phase_counts.update([evaluation.get("big_hand_phase", "disabled")])
            for candidate in evaluation.get("candidates") or ():
                kinds = candidate.get("intent_kinds") or ()
                if isinstance(kinds, str):
                    kinds = (kinds,)
                intent_kinds_seen.update(map(str, kinds))
                intent_kind_roots.update(map(str, kinds))
                admission_counts.update([
                    str(candidate.get("admitted_by", "none"))])
            if any(candidate.get("intent_kinds")
                   for candidate in evaluation.get("candidates") or ()):
                intent_samples.append({
                    "seed": int(seed), "hero_seat": int(seat),
                    "dealer": int(dealer), "action": int(action),
                    "selected": evaluation.get("selected"),
                    "phase": evaluation.get("big_hand_phase", "disabled"),
                    "speed_pool_tiles": evaluation.get("speed_pool_tiles", ()),
                    "speed_winner": evaluation.get("speed_winner"),
                    "challenger": evaluation.get("big_hand_challenger"),
                    "override": bool(evaluation.get("big_hand_override")),
                    "override_reason": evaluation.get(
                        "big_hand_override_reason"),
                    "live_wall": int(game.live_wall_left()),
                    "opponent_melds": max(
                        len(game.melds[other]) for other in range(4)
                        if other != seat),
                    "roots": [{key: candidate.get(key) for key in (
                        "tile", "shanten", "ukeire", "intent_kinds",
                        "intent_strength", "chiitoi_shanten", "pair_units",
                        "luxury_groups", "luxury_upgrade_tiles",
                        "luxury_upgrade_live", "wild_count", "wild_live",
                        "shanten_regression", "admitted_by",
                        "big_hand_gate_reason", "missing",
                    ) if key in candidate}
                              for candidate in
                              evaluation.get("candidates") or ()],
                })
            if evaluation.get("profile_fingerprint"):
                fingerprints[evaluation["profile_fingerprint"]] += 1
            if evaluation.get("big_hand_challenger") is not None:
                challenger = evaluation.get("big_hand_challenger")
                challenger_tile = (challenger.get("tile")
                                   if isinstance(challenger, dict)
                                   else challenger)
                row = {
                    "seed": int(seed),
                    "hero_seat": int(seat),
                    "dealer": int(dealer),
                    "selected": int(action),
                    "speed_winner": evaluation.get("speed_winner"),
                    "challenger": challenger_tile,
                    "override": bool(evaluation.get("big_hand_override")),
                    "reason": evaluation.get("big_hand_override_reason"),
                    "intent_kinds": [],
                    "live_wall": int(game.live_wall_left()),
                    "opponent_melds": max(
                        (len(game.melds[other]) for other in range(4)
                         if other != seat), default=0),
                }
                candidates = evaluation.get("candidates") or ()
                candidate_row = next((candidate for candidate in candidates
                                      if candidate.get("tile") ==
                                      challenger_tile), {})
                kinds = candidate_row.get("intent_kinds", ())
                if isinstance(kinds, str):
                    kinds = (kinds,)
                row["intent_kinds"] = sorted(set(map(str, kinds)))
                overrides.append(row)
        game.step(action)
    if sum(game.scores) != 0:
        raise AssertionError(f"score conservation failed: {game.scores}")
    return {
        "score": float(game.scores[seat]),
        "win": bool(game.result and game.result[0] == seat),
        "draw": game.result is None,
        "elapsed_s": time.perf_counter() - started,
        "ordinary_decisions": ordinary_decisions,
        "override_events": overrides,
        "intent_samples": intent_samples,
        "profile_fingerprints": dict(fingerprints),
        "intent_kinds_seen": sorted(intent_kinds_seen),
        "intent_kind_roots": dict(intent_kind_roots),
        "admission_counts": dict(admission_counts),
        "phase_counts": dict(phase_counts),
    }


def _live_wall_bucket(wall):
    wall = int(wall)
    return "24+" if wall >= 24 else "12-23" if wall >= 12 else "0-11"


def _opponent_meld_bucket(melds):
    melds = int(melds)
    return "0" if melds == 0 else "1" if melds == 1 else "2+"


def _challenger_event_counts(events):
    counts = {
        "total": len(events),
        "overrides": sum(bool(event.get("override")) for event in events),
        "by_live_wall": Counter(),
        "by_opponent_melds": Counter(),
        "overrides_by_live_wall": Counter(),
        "overrides_by_opponent_melds": Counter(),
    }
    for event in events:
        wall = _live_wall_bucket(event["live_wall"])
        melds = _opponent_meld_bucket(event["opponent_melds"])
        counts["by_live_wall"][wall] += 1
        counts["by_opponent_melds"][melds] += 1
        if event.get("override"):
            counts["overrides_by_live_wall"][wall] += 1
            counts["overrides_by_opponent_melds"][melds] += 1
    return {
        key: dict(value) if isinstance(value, Counter) else value
        for key, value in counts.items()
    }


def _bucket_summary(rows, *, rounds, seed):
    grouped = defaultdict(list)
    for row in rows:
        events = row["candidate"].get("override_events", ())
        kinds = set(row["candidate"].get("intent_kinds_seen", ()))
        kinds.update(kind for event in events
                     for kind in event.get("intent_kinds", ()))
        for kind in kinds:
            grouped[f"intent:{kind}"].append(row["score_delta"])
        if events:
            grouped["game_with_challenger"].append(row["score_delta"])
            challenger_buckets = set()
            for event in events:
                challenger_buckets.add(
                    f"challenger_live_wall:{_live_wall_bucket(event['live_wall'])}")
                challenger_buckets.add(
                    "challenger_opponent_melds:"
                    f"{_opponent_meld_bucket(event['opponent_melds'])}")
            for bucket in challenger_buckets:
                grouped[bucket].append(row["score_delta"])
        override_events = [event for event in events
                           if event.get("override")]
        if override_events:
            grouped["game_with_override"].append(row["score_delta"])
            override_buckets = set()
            for event in override_events:
                override_buckets.add(
                    f"override_live_wall:{_live_wall_bucket(event['live_wall'])}")
                override_buckets.add(
                    "override_opponent_melds:"
                    f"{_opponent_meld_bucket(event['opponent_melds'])}")
            for bucket in override_buckets:
                grouped[bucket].append(row["score_delta"])
    return {key: _bootstrap(values, rounds=rounds, seed=seed + index)
            for index, (key, values) in enumerate(sorted(grouped.items()))}


def run_score(*, candidate_evaluator, baseline_evaluator, games=4096,
              seed_start=410000, ycbk=False, bootstrap_rounds=5000,
              bootstrap_seed=20260923, required_pairs=4096):
    if int(games) <= 0:
        raise ValueError("games must be positive")
    rows = []
    errors = []
    started = time.perf_counter()
    for index in range(int(games)):
        seed = int(seed_start) + index
        seat = index % 4
        dealer = (index // 4) % 4
        args = (seed, seat, dealer)
        try:
            baseline_args = (*args, baseline_evaluator, baseline_evaluator)
            candidate_args = (*args, candidate_evaluator, baseline_evaluator)
            if index % 2:
                candidate = _play_score_game(*candidate_args, ycbk=ycbk)
                baseline = _play_score_game(*baseline_args, ycbk=ycbk)
            else:
                baseline = _play_score_game(*baseline_args, ycbk=ycbk)
                candidate = _play_score_game(*candidate_args, ycbk=ycbk)
            rows.append({
                "source_group": f"seed:{seed}", "seed": seed,
                "index": index, "seat": seat, "dealer": dealer,
                "baseline": baseline, "candidate": candidate,
                "score_delta": candidate["score"] - baseline["score"],
            })
        except Exception as exc:
            errors.append({"source_group": f"seed:{seed}", "seed": seed,
                           "index": index,
                           "error": f"{type(exc).__name__}:{exc}"})
        if (index + 1) % 100 == 0:
            print(f"paired score games {index + 1}/{games}",
                  file=sys.stderr, flush=True)
    valid = [row for row in rows if row.get("score_delta") is not None]
    delta = [row["score_delta"] for row in valid]
    baseline_scores = [row["baseline"]["score"] for row in valid]
    candidate_scores = [row["candidate"]["score"] for row in valid]
    by_seat = {}
    for seat in range(4):
        values = [row["score_delta"] for row in valid if row["seat"] == seat]
        by_seat[str(seat)] = _bootstrap(
            values, rounds=bootstrap_rounds, seed=bootstrap_seed + seat)
    combinations = Counter((row["seat"], row["dealer"]) for row in valid)
    balanced = (len(combinations) == 16 and
                (not combinations or len(set(combinations.values())) == 1))
    outcome = _bootstrap(delta, rounds=bootstrap_rounds,
                         seed=bootstrap_seed)
    elapsed_s = time.perf_counter() - started
    override_events = [event for row in valid
                       for event in row["candidate"].get(
                           "override_events", ())]
    intent_samples = [sample for row in valid
                      for sample in row["candidate"].get(
                          "intent_samples", ())][:100]
    override_selections = sum(event["override"] for event in override_events)
    ordinary_decisions = sum(row["candidate"]["ordinary_decisions"]
                             for row in valid)
    intent_kind_roots = Counter()
    admission_counts = Counter()
    phase_counts = Counter()
    for row in valid:
        intent_kind_roots.update(
            row["candidate"].get("intent_kind_roots", {}))
        admission_counts.update(
            row["candidate"].get("admission_counts", {}))
        phase_counts.update(row["candidate"].get("phase_counts", {}))
    return {
        "mode": "score",
        "candidate_evaluator": candidate_evaluator,
        "baseline_evaluator": baseline_evaluator,
        "seed_start": int(seed_start),
        "games_requested": int(games),
        "valid_pairs": len(valid),
        "required_pairs": int(required_pairs),
        "sample_gate_pass": len(valid) >= int(required_pairs),
        "ycbk": bool(ycbk),
        "metric": "candidate hero round settlement points - baseline hero points",
        "opponents": "three frozen-baseline evaluators",
        "seat_dealer_combinations": {
            f"{seat}:{dealer}": count
            for (seat, dealer), count in sorted(combinations.items())
        },
        "balanced_16_way_schedule": balanced,
        "baseline_score": _bootstrap(
            baseline_scores, rounds=bootstrap_rounds,
            seed=bootstrap_seed + 11),
        "candidate_score": _bootstrap(
            candidate_scores, rounds=bootstrap_rounds,
            seed=bootstrap_seed + 12),
        "score_delta": outcome,
        "score_delta_by_hero_seat": by_seat,
        "baseline_wins": sum(row["baseline"]["win"] for row in valid),
        "candidate_wins": sum(row["candidate"]["win"] for row in valid),
        "baseline_draws": sum(row["baseline"]["draw"] for row in valid),
        "candidate_draws": sum(row["candidate"]["draw"] for row in valid),
        "candidate_ordinary_decisions": ordinary_decisions,
        "candidate_intent_kind_root_counts": dict(intent_kind_roots),
        "candidate_admitted_by_counts": dict(admission_counts),
        "candidate_phase_counts": dict(phase_counts),
        "intent_decision_sample_count": sum(
            len(row["candidate"].get("intent_samples", ()))
            for row in valid),
        "intent_decision_samples": intent_samples,
        "big_hand_challenger_events": len(override_events),
        "big_hand_override_selections": override_selections,
        "big_hand_override_rate_per_ordinary_discard": (
            override_selections / ordinary_decisions
            if ordinary_decisions else 0.0),
        "big_hand_challenger_event_counts": _challenger_event_counts(
            override_events),
        "big_hand_challenger_event_samples": override_events[:100],
        "score_delta_game_level_strata": _bucket_summary(
            valid, rounds=bootstrap_rounds, seed=bootstrap_seed + 100),
        "profile_fingerprints": {
            "baseline": dict(Counter(
                fingerprint
                for row in valid
                for fingerprint in row["baseline"][
                    "profile_fingerprints"])),
            "candidate": dict(Counter(
                fingerprint
                for row in valid
                for fingerprint in row["candidate"][
                    "profile_fingerprints"])),
        },
        "errors": errors,
        "runtime_s": elapsed_s,
        "kernel_runtime": kernel_runtime_diagnostic(),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "default_evaluator": DEFAULT_BOT_EVALUATOR,
        "interpretation": (
            "Intent/challenger/override subgroup rows stratify game outcomes; "
            "they are descriptive, not causal per-decision reward estimates. "
            "An empty game_with_override bucket means no overrides occurred "
            "and their per-event benefit cannot be estimated."),
    }


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("performance", "score", "intent"),
                        required=True)
    parser.add_argument("--evaluators", default="legacyV2",
                        help="comma-separated evaluators for 4-bot performance mode")
    parser.add_argument("--candidate-evaluator", default="legacy-v2-phase-a")
    parser.add_argument("--baseline-evaluator", default="legacy-v2-baseline")
    parser.add_argument("--games", type=int)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--seed-start", type=int)
    parser.add_argument("--you-cai-bi-kao", action="store_true")
    parser.add_argument("--bootstrap-rounds", type=int, default=5000)
    parser.add_argument("--bootstrap-seed", type=int, default=20260923)
    parser.add_argument("--required-pairs", type=int, default=4096)
    parser.add_argument("--iterations", type=int, default=10000)
    parser.add_argument("--decision-sample-limit", type=int, default=256)
    parser.add_argument("--intent-sample-limit", type=int, default=100)
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    if args.mode == "performance":
        value = run_performance(
            evaluators=[name.strip() for name in args.evaluators.split(",")
                        if name.strip()],
            games=args.games or 200, repeats=args.repeats,
            seed_start=args.seed_start or 310000,
            ycbk=args.you_cai_bi_kao,
            decision_sample_limit=args.decision_sample_limit,
            intent_sample_limit=args.intent_sample_limit)
    elif args.mode == "score":
        value = run_score(
            candidate_evaluator=args.candidate_evaluator,
            baseline_evaluator=args.baseline_evaluator,
            games=args.games or 4096,
            seed_start=args.seed_start or 410000,
            ycbk=args.you_cai_bi_kao,
            bootstrap_rounds=args.bootstrap_rounds,
            bootstrap_seed=args.bootstrap_seed,
            required_pairs=args.required_pairs)
    else:
        value = run_intent_microbench(
            iterations=args.iterations,
            seed_start=args.seed_start or 810000)
    encoded = json.dumps(value, ensure_ascii=False, indent=2)
    print(encoded)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(encoded + "\n")
    return 0 if not value.get("errors") else 2


if __name__ == "__main__":
    raise SystemExit(main())
