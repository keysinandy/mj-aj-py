#!/usr/bin/env python3
"""Grid scan for legacyV2 BigHandIntent profile parameters.

Historical acceptance showed that the default big-hand thresholds
(min_live=24, max_opponent_melds=1, ...) almost never admit a ``+1 shanten``
challenger (4096 matches -> 1 challenger event, 0 overrides).  This script
scans a compact grid of the gate parameters and, for every combination,
reports *both* the override/challenger trigger rate and the paired hero net
score against the frozen production legacyV2 baseline.  A grid point with
zero triggers cannot certify a score effect, so the trigger dimension is
mandatory evidence and is not collapsed into the score CI.

Only the ordinary-discard profile is varied.  HU / KONG / reaction continue to
use the production legacyV2 reaction profile so every decision point stays
legal and the freeze/piao/HU guards keep their frozen behaviour.  The
candidate profile equals the production weighted-online profile (shape quality
and marginal-structure guard on, the online defaults) with big-hand switched
on and the grid parameters applied; the baseline is exactly the production
weighted-online profile.  Source-seed paired scores use the same methodology as
``legacy_big_hand_intent_eval.run_score`` so the numbers remain comparable.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
import math
import hashlib
import os
from pathlib import Path
import platform
import random
import statistics
import sys
import time
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mj.bot import _choose_draw_action, _choose_react_evaluated
from mj.game import Game
from mj.game import HU
from mj.legacy_kong import kong_actions
from mj.legacy_eval import LegacyTwoPlyProfile
from mj.legacy_react import LegacyReactionProfile
from mj.shanten import kernel_runtime_diagnostic


BIG_HAND_FIELDS = (
    "big_hand_enabled",
    "big_hand_same_shanten_enabled",
    "big_hand_plus_one_enabled",
    "big_hand_min_live",
    "big_hand_max_opponent_melds",
    "big_hand_min_ukeire",
    "big_hand_max_ukeire_loss",
    "big_hand_min_pair_units",
    "big_hand_min_luxury_upgrade_live",
    "big_hand_plus_one_luxury_noninferior",
    "big_hand_plus_one_parallel",
    "big_hand_plus_one_min_strength",
)


def percentile(values: Iterable[float], fraction: float):
    values = sorted(float(value) for value in values)
    if not values:
        return None
    index = (len(values) - 1) * float(fraction)
    low = int(math.floor(index))
    high = int(math.ceil(index))
    if low == high:
        return values[low]
    return values[low] + (values[high] - values[low]) * (index - low)


def bootstrap(values, *, rounds=5000, seed=20261008, alpha=0.05):
    values = [float(value) for value in values]
    if not values:
        return {"n": 0, "mean": None, "ci_low": None, "ci_high": None,
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
        "n": n, "sum": sum(values), "mean": statistics.fmean(values),
        "ci_low": means[lower], "ci_high": means[upper],
        "alpha": float(alpha), "method": "paired source-seed percentile bootstrap",
        "rounds": int(rounds), "seed": int(seed),
    }


def production_profile(**big_hand_overrides):
    """Production legacyV2 weighted-online profile with optional big-hand flags."""
    return LegacyTwoPlyProfile.weighted_online(
        big_hand_enabled=big_hand_overrides.get("big_hand_enabled", False),
        big_hand_same_shanten_enabled=big_hand_overrides.get(
            "big_hand_same_shanten_enabled", True),
        big_hand_plus_one_enabled=big_hand_overrides.get(
            "big_hand_plus_one_enabled", False),
        big_hand_min_live=big_hand_overrides.get("big_hand_min_live", 24),
        big_hand_max_opponent_melds=big_hand_overrides.get(
            "big_hand_max_opponent_melds", 1),
        big_hand_min_ukeire=big_hand_overrides.get("big_hand_min_ukeire", 4),
        big_hand_max_ukeire_loss=big_hand_overrides.get(
            "big_hand_max_ukeire_loss", 4),
        big_hand_min_pair_units=big_hand_overrides.get(
            "big_hand_min_pair_units", 4),
        big_hand_min_luxury_upgrade_live=big_hand_overrides.get(
            "big_hand_min_luxury_upgrade_live", 1),
        big_hand_plus_one_luxury_noninferior=big_hand_overrides.get(
            "big_hand_plus_one_luxury_noninferior", False),
        big_hand_plus_one_parallel=big_hand_overrides.get(
            "big_hand_plus_one_parallel", False),
        big_hand_plus_one_min_strength=big_hand_overrides.get(
            "big_hand_plus_one_min_strength", "STRONG"),
    )


def _decide(game, seat, discard_profile, reaction_profile=None, *, reaction_diagnostics=False):
    acts = tuple(game.legal_actions())
    if len(acts) == 1:
        return acts[0], None
    reaction_profile = reaction_profile or LegacyReactionProfile.v2_online()
    if game.phase == "discard":
        return _choose_draw_action(
            game, seat, acts, discard_profile=discard_profile,
            reaction_profile=reaction_profile)
    return _choose_react_evaluated(
        game, seat, acts, return_evaluation=reaction_diagnostics,
        reaction_profile=reaction_profile)


def _big_hand_scope_stats(evaluation):
    """Extract override / challenger / phase facts from an evaluation dict."""
    if not isinstance(evaluation, dict):
        return {}
    guard = evaluation.get("big_hand_guard") or {}
    return {
        "phase": evaluation.get("big_hand_phase", "disabled"),
        "skipped_reason": guard.get("skipped_reason"),
        "challenger": evaluation.get("big_hand_challenger"),
        "override": bool(evaluation.get("big_hand_override")),
        "override_reason": evaluation.get("big_hand_override_reason"),
        "admitted_tiles": list(guard.get("admitted_tiles") or ()),
        "admission_count": len(guard.get("admitted_tiles") or ()),
        "gate_reasons": Counter(
            str(reason)
            for reason in (guard.get("candidate_gate_reasons") or {}).values()),
    }


def pair_assignment(index):
    """Eight-case cycle balances candidate seats independently of dealer."""
    return (index // 2) % 4, {index % 2, (index % 2) + 2}


def _play_pair(args):
    """One seed pair: hero candidate (2 seats) vs production legacyV2.

    Returns the paired per-seed hero score difference averaged over both seats
    (candidate team minus baseline team), plus challenge/override counts.
    """
    index, seed, candidate_overrides, baseline_overrides = args
    candidate = production_profile(**candidate_overrides)
    baseline = production_profile(**(baseline_overrides or {}))
    opponent = production_profile()
    dealer, candidate_seats = pair_assignment(index)
    counters = {
        "decisions": 0, "discard_decisions": 0,
        "challenger_events": 0, "override_events": 0,
        "admission_events": 0, "phases": Counter(),
        "skipped_reasons": Counter(), "gate_reasons": Counter(),
        "override_reasons": Counter(),
    }
    topup_rows = []
    baseline_discard_ms = []
    challenger_increment_ms = []

    # Per hero-hand wild-count buckets inside the candidate arm only.  The
    # bucket key is the hero hand's white-dragon count at a discard decision,
    # so we can attribute challenge/override/admission frequency and discard
    # decision latency by how rich in 财神 the standing hand is.
    wild_discard = Counter()          # wild -> number of hero discard decisions
    wild_override = Counter()         # wild -> overrides accepted at that bucket
    wild_challenger = Counter()       # wild -> challenger events
    wild_admission = Counter()        # wild -> admission events
    wild_gate = defaultdict(Counter)  # wild -> gate reason -> count
    wild_discard_ms = defaultdict(list)
    # Off-policy divergence: for each hero discard point inside the candidate
    # arm, also ask the baseline what it would play for the same public board.
    # This records whether big-hand actually changes the chosen action, by wild
    # bucket, without needing a second full match to detect divergence.
    wild_action_divergence = Counter()
    wild_divergence_ms = defaultdict(list)

    def hero_score(team_uses_candidate):
        game = Game(seed=seed, dealer=dealer)
        initial_max_wild = max(int(game.hands[s][33]) for s in candidate_seats)
        cand_max_wild = 0
        while not game.done:
            seat = game.current_seat()
            seat_is_hero = seat in candidate_seats
            if team_uses_candidate:
                if seat_is_hero:
                    wild = int(game.hands[seat][33])
                    if game.phase == "discard":
                        cand_max_wild = max(cand_max_wild, wild)
                    started = time.perf_counter()
                    action, evaluation = _decide(game, seat, candidate)
                    elapsed_ms = (time.perf_counter() - started) * 1000.0
                    if game.phase == "discard":
                        stats = _big_hand_scope_stats(evaluation)
                        if stats:
                            counters["decisions"] += 1
                            counters["discard_decisions"] += 1
                            counters["phases"][stats["phase"]] += 1
                            if stats["skipped_reason"]:
                                counters["skipped_reasons"][stats["skipped_reason"]] += 1
                            counters["gate_reasons"].update(stats["gate_reasons"])
                            counters["admission_events"] += stats["admission_count"]
                            if stats["challenger"] is not None:
                                counters["challenger_events"] += 1
                            if stats["override"]:
                                counters["override_events"] += 1
                            if stats["challenger"] is not None:
                                counters["override_reasons"][
                                    stats.get("override_reason") or
                                    ("override" if stats["override"]
                                     else "override_rejected")] += 1
                            wild_discard[wild] += 1
                            wild_discard_ms[wild].append(elapsed_ms)
                            wild_override[wild] += int(stats["override"])
                            wild_challenger[wild] += int(stats["challenger"] is not None)
                            wild_admission[wild] += stats["admission_count"]
                            wild_gate[wild].update(stats["gate_reasons"])
                            # off-policy comparison against the baseline on the
                            # same standing board (baseline call must not step).
                            started_base = time.perf_counter()
                            base_action, _ = _decide(game, seat, baseline)
                            base_ms = (time.perf_counter() - started_base) * 1000.0
                            baseline_discard_ms.append(base_ms)
                            if stats["challenger"] is not None:
                                challenger_increment_ms.append(elapsed_ms - base_ms)
                            topup = (evaluation.get("search_metrics") or {}).get(
                                "big_hand_challenger_topup")
                            if topup:
                                topup_rows.append(topup)
                            if base_action != action:
                                wild_action_divergence[wild] += 1
                                wild_divergence_ms[wild].append(elapsed_ms)
                else:
                    action, _ = _decide(game, seat, opponent)
            else:
                action, _ = _decide(game, seat, baseline if seat_is_hero else opponent)
            legal = tuple(game.legal_actions())
            if action not in legal:
                raise RuntimeError(
                    f"illegal action seed={seed} seat={seat} phase={game.phase} "
                    f"action={action} legal={legal}")
            game.step(action)
        if sum(game.scores) != 0:
            raise AssertionError("settlement is not zero sum")
        return (sum(game.scores[s] for s in candidate_seats) / 2,
                cand_max_wild if team_uses_candidate else None, initial_max_wild)

    # Paired arms: candidate team vs all-baseline team on the SAME seed/dealer.
    if index % 2 == 0:
        candidate_arm, cand_max_wild, initial_max_wild = hero_score(True)
        baseline_arm, _, _ = hero_score(False)
    else:
        baseline_arm, _, _ = hero_score(False)
        candidate_arm, cand_max_wild, initial_max_wild = hero_score(True)
    return {
        "seed": seed,
        "dealer": dealer,
        "hero_seats": sorted(candidate_seats),
        "initial_max_wild": initial_max_wild,
        "pair_score": candidate_arm - baseline_arm,
        "candidate_arm_score": candidate_arm,
        "baseline_arm_score": baseline_arm,
        "cand_max_wild": cand_max_wild,
        "games": 2,
        "wild_discard": dict(wild_discard),
        "wild_override": dict(wild_override),
        "wild_challenger": dict(wild_challenger),
        "wild_admission": dict(wild_admission),
        "wild_gate": {k: dict(v) for k, v in wild_gate.items()},
        "wild_discard_ms": {k: v for k, v in wild_discard_ms.items()},
        "wild_action_divergence": dict(wild_action_divergence),
        "wild_divergence_ms": {k: v for k, v in wild_divergence_ms.items()},
        "baseline_discard_ms": baseline_discard_ms,
        "challenger_increment_ms": challenger_increment_ms,
        "topup_rows": topup_rows,
        **{key: (dict(value) if isinstance(value, Counter) else value)
           for key, value in counters.items()},
    }


def run_grid(grid_points, *, games=512, seed_start=700000, jobs=2,
             bootstrap_rounds=5000, bootstrap_seed=20261008, baseline_overrides=None):
    """Run paired score + trigger for every grid point."""
    if games < 2 or games % 2:
        raise ValueError("games must be positive and even for paired seeds")
    results = []
    for point in grid_points:
        key = ",".join(f"{name}={point.get(name)}"
                       for name in BIG_HAND_FIELDS if name in point)
        work = [(i, seed_start + i, point, baseline_overrides) for i in range(games // 2)]
        rows = []
        if jobs == 1:
            for value in map(_play_pair, work):
                rows.append(value)
        else:
            with ProcessPoolExecutor(max_workers=jobs) as pool:
                for value in pool.map(_play_pair, work, chunksize=4):
                    rows.append(value)
                    if len(rows) % 64 == 0:
                        print(f"progress {len(rows)*2}/{games} games", flush=True)
        deltas = [row["pair_score"] for row in rows]
        ci = bootstrap(deltas, rounds=bootstrap_rounds, seed=bootstrap_seed)
        phases = Counter()
        skipped = Counter()
        gate = Counter()
        for row in rows:
            phases.update(row["phases"])
            skipped.update(row["skipped_reasons"])
            gate.update(row["gate_reasons"])
        counters = {
            "decisions": sum(row["decisions"] for row in rows),
            "discard_decisions": sum(row["discard_decisions"] for row in rows),
            "challenger_events": sum(row["challenger_events"] for row in rows),
            "override_events": sum(row["override_events"] for row in rows),
            "admission_events": sum(row["admission_events"] for row in rows),
            "phases": dict(phases),
            "skipped_reasons": dict(skipped),
            "gate_reasons": dict(gate),
        }
        override_reasons = Counter()
        for row in rows:
            override_reasons.update(row.get("override_reasons") or {})
        override_rate = (counters["override_events"] /
                         counters["discard_decisions"]
                         if counters["discard_decisions"] else 0.0)

        # Aggregate per hero-hand wild-count (0..4) and per match max-wild.
        by_wild_discard = Counter()
        by_wild_override = Counter()
        by_wild_challenger = Counter()
        by_wild_admission = Counter()
        by_wild_ms = defaultdict(list)
        by_wild_gate = Counter()
        by_wild_divergence = Counter()
        by_wild_divergence_ms = defaultdict(list)
        for row in rows:
            by_wild_discard.update(row.get("wild_discard") or {})
            by_wild_override.update(row.get("wild_override") or {})
            by_wild_challenger.update(row.get("wild_challenger") or {})
            by_wild_admission.update(row.get("wild_admission") or {})
            by_wild_divergence.update(row.get("wild_action_divergence") or {})
            for w, ms in (row.get("wild_discard_ms") or {}).items():
                by_wild_ms[w].extend(ms)
            for w, ms in (row.get("wild_divergence_ms") or {}).items():
                by_wild_divergence_ms[w].extend(ms)
            for w, reasons in (row.get("wild_gate") or {}).items():
                for reason, count in reasons.items():
                    by_wild_gate[(w, reason)] += count
        wild_buckets = {}
        for w in sorted(by_wild_discard):
            n = by_wild_discard[w]
            ms = by_wild_ms[w]
            dv = by_wild_divergence[w]
            div_ms = by_wild_divergence_ms[w]
            wild_buckets[str(w)] = {
                "discard_decisions": n,
                "override_events": by_wild_override[w],
                "challenger_events": by_wild_challenger[w],
                "admission_events": by_wild_admission[w],
                "override_rate_per_discard": by_wild_override[w] / n if n else 0.0,
                "action_divergence_from_baseline": dv,
                "divergence_rate_per_discard": dv / n if n else 0.0,
                "divergence_discard_latency_ms": {
                    "p50": percentile(div_ms, 0.50) if div_ms else None,
                    "p95": percentile(div_ms, 0.95) if div_ms else None,
                },
                "discard_latency_ms": {
                    "p50": percentile(ms, 0.50) if ms else None,
                    "p95": percentile(ms, 0.95) if ms else None,
                    "max": max(ms) if ms else None,
                },
                "gate_reasons": {
                    reason: count for (w_, reason), count in by_wild_gate.items()
                    if w_ == w
                },
            }

        max_wild_pairs = defaultdict(list)
        initial_wild_pairs = defaultdict(list)
        for row in rows:
            initial_wild_pairs[row["initial_max_wild"]].append(row["pair_score"])
            mw = row.get("cand_max_wild")
            if mw is not None:
                max_wild_pairs[mw].append(row["pair_score"])
        max_wild_buckets = {
            str(w): bootstrap(vals, rounds=bootstrap_rounds,
                              seed=bootstrap_seed + w)
            for w, vals in sorted(max_wild_pairs.items())
        }
        topups = [item for row in rows for item in row["topup_rows"]]
        def latency(values):
            return {"n": len(values), "p50": percentile(values, .50),
                    "p95": percentile(values, .95),
                    "p99": percentile(values, .99),
                    "max": max(values) if values else None,
                    "mean": statistics.fmean(values) if values else None}
        results.append({
            "grid_point": point,
            "profile_fingerprint": production_profile(**point).fingerprint,
            "baseline_fingerprint": production_profile(**(baseline_overrides or {})).fingerprint,
            "games": games,
            "valid_pairs": len(rows),
            "score_ci": ci,
            "trigger": {
                "admission_events": counters["admission_events"],
                "challenger_events": counters["challenger_events"],
                "override_events": counters["override_events"],
                "discard_decisions": counters["discard_decisions"],
                "override_rate_per_discard": override_rate,
                "phases": counters["phases"],
                "skipped_reasons": counters["skipped_reasons"],
                "gate_reasons": counters["gate_reasons"],
                "override_reasons": dict(override_reasons),
            },
            "by_hero_wild_count": wild_buckets,
            "by_match_max_wild": max_wild_buckets,
            "by_initial_hero_max_wild": {
                str(w): bootstrap(values, rounds=bootstrap_rounds, seed=bootstrap_seed+w)
                for w, values in sorted(initial_wild_pairs.items())},
            "bucket_score_note": "Match max wild is observed in the candidate arm; descriptive, not a causal white-count effect.",
            "python_challenger_topup": {
                "attempts": len(topups),
                "complete": sum(item["complete"] for item in topups),
                "reasons": dict(Counter(item["reason"] for item in topups
                                        if item["reason"])),
                "latency_ms": latency([item["elapsed_ms"] for item in topups]),
            },
            "candidate_discard_latency_ms": latency([
                ms for values in by_wild_ms.values() for ms in values]),
            "counterfactual_baseline_discard_latency_ms": latency([
                ms for row in rows for ms in row["baseline_discard_ms"]]),
            "challenger_increment_latency_ms": latency([
                ms for row in rows for ms in row["challenger_increment_ms"]]),
            "latency_note": "Multiprocess exploratory timing; baseline follows candidate on the same state and has warm caches. Use interleaved single-process 4-bot benchmark for acceptance.",
            "paired_scores": [{key: row[key] for key in (
                "seed", "dealer", "hero_seats", "initial_max_wild",
                "pair_score", "candidate_arm_score", "baseline_arm_score",
                "cand_max_wild", "override_events", "challenger_events")}
                for row in rows],
        })
        print(f"[{key}] {len(rows)*2}/{games} games score={ci['mean']:.4f} "
              f"override={counters['override_events']} "
              f"challenger={counters['challenger_events']}",
              flush=True)
    return results


def build_default_grid():
    """Compact grid over the parameters most likely to unlock overrides."""
    grid = []
    for min_live in (12, 24):
        for opp_melds in (1, 2):
            for min_pair in (3, 4):
                for max_loss in (4, 12):
                    grid.append({
                        "big_hand_enabled": True,
                        "big_hand_same_shanten_enabled": True,
                        "big_hand_plus_one_enabled": True,
                        "big_hand_min_live": min_live,
                        "big_hand_max_opponent_melds": opp_melds,
                        "big_hand_min_ukeire": 4,
                        "big_hand_max_ukeire_loss": max_loss,
                        "big_hand_min_pair_units": min_pair,
                        "big_hand_min_luxury_upgrade_live": 1,
                    })
    return grid


def run_profile_performance(point, *, games=200, repeats=3, seed_start=1500000,
                            profile_factory=production_profile,
                            reaction_profile_factory=None):
    """Interleaved, single-process four-bot benchmark with no audit calls."""
    profiles = {"baseline": profile_factory(),
                "candidate": profile_factory(**point)}
    reactions = ({"baseline": reaction_profile_factory(),
                  "candidate": reaction_profile_factory(**point)}
                 if reaction_profile_factory else {key: None for key in profiles})
    timing = {key: {"discard_ms": [], "game_ms": [], "fallbacks": 0,
                    "reaction_ms": [], "reaction_fallbacks": 0,
                    "challengers": 0, "overrides": 0, "max_roots": 0,
                    "topup_ms": [], "self_kong_ms": [],
                    "self_kong_progress_overrides": 0,
                    "self_kong_fallbacks": 0, "hu_ms": [],
                    "hu_fallbacks": 0, "hu_delay_guard_overrides": 0} for key in profiles}
    batches = []
    for repeat in range(repeats):
        batch = {key: [] for key in profiles}
        for index in range(games):
            seed = seed_start + repeat * games + index
            order = ("baseline", "candidate") if (index + repeat) % 2 == 0 else (
                "candidate", "baseline")
            for key in order:
                game = Game(seed=seed, dealer=index % 4)
                started = time.perf_counter()
                while not game.done:
                    phase = game.phase
                    reaction_window = phase != "discard" and len(game.legal_actions()) > 1
                    legal = tuple(game.legal_actions())
                    self_kong_window = phase == "discard" and HU not in legal and bool(kong_actions(legal))
                    tick = time.perf_counter()
                    action, info = _decide(game, game.current_seat(), profiles[key], reactions[key],
                                           reaction_diagnostics=reaction_profile_factory is not None)
                    elapsed_ms = (time.perf_counter() - tick) * 1000
                    if phase == "discard" and HU in legal:
                        timing[key]["hu_ms"].append(elapsed_ms)
                        if isinstance(info, dict):
                            timing[key]["hu_fallbacks"] += int(bool(info.get("fallback_reason")))
                            timing[key]["hu_delay_guard_overrides"] += int(bool(
                                (info.get("hu_discard_delay_guard") or {}).get("override")))
                    if self_kong_window:
                        timing[key]["self_kong_ms"].append(elapsed_ms)
                        if isinstance(info, dict):
                            timing[key]["self_kong_progress_overrides"] += int(bool(info.get("kong_progress_override")))
                            timing[key]["self_kong_fallbacks"] += int(bool(info.get("continuation_fallback_reason")))
                    if reaction_window:
                        timing[key]["reaction_ms"].append(elapsed_ms)
                        if isinstance(info, dict):
                            timing[key]["reaction_fallbacks"] += int(bool(info.get("u2_fallback_reason")))
                    if phase == "discard" and action >= 0:
                        timing[key]["discard_ms"].append(elapsed_ms)
                        if isinstance(info, dict):
                            timing[key]["fallbacks"] += int(bool(info.get("fallback_reason")))
                            timing[key]["challengers"] += int(info.get("big_hand_challenger") is not None)
                            timing[key]["overrides"] += int(bool(info.get("big_hand_override")))
                            metrics = info.get("search_metrics") or {}
                            # HU arbitration counts immediate HU/baotou/KONG
                            # alternatives here; those are not discard roots.
                            count = (int(metrics.get("root_candidates") or 0)
                                     if "big_hand_phase" in info else 0)
                            timing[key]["max_roots"] = max(timing[key]["max_roots"], count)
                            if count > profiles[key].max_frontier_candidates:
                                raise AssertionError("frontier cap exceeded")
                            topup = metrics.get("big_hand_challenger_topup")
                            if topup:
                                timing[key]["topup_ms"].append(topup["elapsed_ms"])
                            if info.get("fallback_reason") and info.get("big_hand_override"):
                                raise AssertionError("override on fallback")
                    if action not in game.legal_actions():
                        raise AssertionError(f"illegal action: {seed}, {key}")
                    game.step(action)
                if sum(game.scores) != 0:
                    raise AssertionError("score conservation")
                game_ms = (time.perf_counter() - started) * 1000
                timing[key]["game_ms"].append(game_ms)
                batch[key].append(game_ms)
            if (index + 1) % 50 == 0:
                print(f"performance repeat {repeat+1}/{repeats}: {index+1}/{games}", flush=True)
        batches.append({key: statistics.fmean(values) for key, values in batch.items()})

    def distribution(values):
        return {"n": len(values), "p50": percentile(values, .5),
                "p95": percentile(values, .95), "p99": percentile(values, .99),
                "mean": statistics.fmean(values) if values else None,
                "max": max(values) if values else None}
    report = {key: {
        "profile_fingerprint": profiles[key].fingerprint,
        "discard_latency_ms": distribution(values["discard_ms"]),
        "reaction_latency_ms": distribution(values["reaction_ms"]),
        "reaction_fallback_rate": (values["reaction_fallbacks"] / len(values["reaction_ms"])
                                   if values["reaction_ms"] else None),
        "reaction_fingerprint": (reactions[key] or LegacyReactionProfile.v2_online()).fingerprint,
        "game_elapsed_ms": distribution(values["game_ms"]),
        "python_topup_latency_ms": distribution(values["topup_ms"]),
        "self_kong_latency_ms": distribution(values["self_kong_ms"]),
        "self_kong_progress_overrides": values["self_kong_progress_overrides"],
        "hu_latency_ms": distribution(values["hu_ms"]),
        "hu_delay_guard_overrides": values["hu_delay_guard_overrides"],
        "hu_fallback_rate": (values["hu_fallbacks"] / len(values["hu_ms"])
                             if values["hu_ms"] else None),
        "self_kong_fallback_rate": (values["self_kong_fallbacks"] / len(values["self_kong_ms"])
                                    if values["self_kong_ms"] else None),
        "fallback_rate": values["fallbacks"] / len(values["discard_ms"]),
        "challengers": values["challengers"], "overrides": values["overrides"],
        "max_frontier_roots": values["max_roots"],
    } for key, values in timing.items()}
    p95_ratio = (report["candidate"]["discard_latency_ms"]["p95"] /
                 report["baseline"]["discard_latency_ms"]["p95"])
    batch_ratio = (statistics.median(item["candidate"] for item in batches) /
                   statistics.median(item["baseline"] for item in batches))
    return {"grid_point": point, "matches_per_profile": games * repeats,
            "profiles": report, "batch_elapsed_ms_per_game": batches,
            "discard_p95_regression_pct": (p95_ratio - 1) * 100,
            "batch_elapsed_per_game_regression_pct": (batch_ratio - 1) * 100,
            "gates": {"discard_p95": p95_ratio <= 1.10,
                      "elapsed_per_game": batch_ratio <= 1.10,
                      "sample_size": games >= 200 and repeats >= 3}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--grid-json", help="path to a JSON list of grid points")
    parser.add_argument("--baseline-json", help="optional hero baseline overrides; opponents remain production")
    parser.add_argument("--mode", choices=("score", "performance"), default="score")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--games", type=int, default=512)
    parser.add_argument("--seed-start", type=int, default=700000)
    parser.add_argument("--jobs", type=int, default=2)
    parser.add_argument("--bootstrap-rounds", type=int, default=5000)
    parser.add_argument("--bootstrap-seed", type=int, default=20261008)
    parser.add_argument("--output", default="runs/big_hand_grid_scan.json")
    args = parser.parse_args(argv)
    baseline_overrides = (json.loads(Path(args.baseline_json).read_text(encoding="utf-8"))
                          if args.baseline_json else {})
    source_hashes = {
        path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
        for path in ("mj/legacy_eval.py", "mj/big_hand_intent.py",
                     "scripts/legacy_v2_big_hand_grid_scan.py")}

    if args.grid_json:
        grid = json.loads(Path(args.grid_json).read_text(encoding="utf-8"))
    else:
        grid = build_default_grid()

    started = time.perf_counter()
    if args.mode == "performance":
        results = [run_profile_performance(point, games=args.games,
                   repeats=args.repeats, seed_start=args.seed_start) for point in grid]
    else:
        results = run_grid(
            grid, games=args.games, seed_start=args.seed_start, jobs=args.jobs,
            bootstrap_rounds=args.bootstrap_rounds,
            bootstrap_seed=args.bootstrap_seed, baseline_overrides=baseline_overrides)

    payload = {
        "schema": "legacy-v2-big-hand-grid-scan-v2",
        "pairing": "Same seed/dealer; two hero seats; dealer independent of hero parity in an eight-case cycle; arm order alternates; opponents frozen production.",
        "baseline_overrides": baseline_overrides,
        "mode": args.mode,
        "kernel": kernel_runtime_diagnostic(),
        "games_per_point": args.games,
        "seed_start": args.seed_start,
        "grid_size": len(grid),
        "results": results,
        "runtime_s": round(time.perf_counter() - started, 2),
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "source_sha256": source_hashes,
    }
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
