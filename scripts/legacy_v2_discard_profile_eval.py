"""Paired legacyV2 profile experiments against three frozen production opponents.

One hero changes its discard and/or ordinary reaction admission profile.
Opponents stay production; defaults and HU/KONG rules remain the control.
Hero/dealer rotate independently over 16 pairs. Multiprocess timings are
descriptive only; the performance mode runs interleaved in a single process.
"""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
import hashlib
import json
from pathlib import Path
import statistics
import sys
import time
import mj_kernels.mj_kernels as native_kernel

from mj.game import Game, PASS
from mj.legacy_eval import LegacyTwoPlyProfile
from mj.legacy_react import LegacyReactionProfile
from mj.shanten import kernel_runtime_diagnostic
from scripts.legacy_v2_big_hand_grid_scan import (
    _decide, bootstrap, run_profile_performance,
)

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_FIELDS = frozenset({
    "speed_band_enabled", "pareto_frontier_enabled",
    "speed_band_min_ratio_by_shanten", "marginal_structure_slack_by_shanten",
    "marginal_structure_guard_enabled",
    "baotou_progress_weight", "baotou_score_tiebreak_enabled",
})
REACTION_FIELDS = frozenset({"claim_min_gain_ratio", "pong_min_abs_gain", "chow_min_abs_gain",
                             "self_kong_progress_enabled", "hu_discard_delay_min_gain_ratio"})


def production_profile(**overrides):
    reaction_profile(**overrides)
    overrides.pop("reaction", None)
    unknown = set(overrides) - EXPERIMENT_FIELDS
    if unknown:
        raise ValueError(f"unsupported experiment fields: {sorted(unknown)}")
    normalized = {key: tuple(value) if isinstance(value, list) else value
                  for key, value in overrides.items()}
    return LegacyTwoPlyProfile.weighted_online(**normalized)


def reaction_profile(**overrides):
    values = overrides.get("reaction", {})
    unknown = set(values) - REACTION_FIELDS
    if unknown:
        raise ValueError(f"unsupported reaction fields: {sorted(unknown)}")
    return LegacyReactionProfile.v2_online(**values)


def pair_assignment(index):
    return index % 4, (index // 4) % 4  # hero, dealer


def play_pair(args):
    index, seed, overrides = args
    candidate, baseline = production_profile(**overrides), production_profile()
    candidate_reaction, baseline_reaction = reaction_profile(**overrides), reaction_profile()
    hero, dealer = pair_assignment(index)
    results = {}
    diagnostics = Counter()
    order = ("baseline", "candidate") if index % 2 == 0 else ("candidate", "baseline")
    for arm in order:
        game = Game(seed=seed, dealer=dealer)
        claims = 0
        started = time.perf_counter()
        while not game.done:
            seat, phase = game.current_seat(), game.phase
            profile = candidate if arm == "candidate" and seat == hero else baseline
            reactor = candidate_reaction if arm == "candidate" and seat == hero else baseline_reaction
            action, info = _decide(game, seat, profile, reactor, reaction_diagnostics=True)
            if action not in game.legal_actions():
                raise AssertionError(f"illegal action: {seed}, {seat}, {action}")
            if arm == "candidate" and seat == hero and phase == "discard" and isinstance(info, dict):
                diagnostics["self_kong_windows"] += int(bool(info.get("kong_candidates")))
                diagnostics["self_kong_progress_overrides"] += int(bool(info.get("kong_progress_override")))
                guard = info.get("hu_discard_delay_guard") or {}
                diagnostics["hu_delay_guard_windows"] += int(bool(guard))
                diagnostics["hu_delay_guard_overrides"] += int(bool(guard.get("override")))
            if seat == hero and phase != "discard":
                claims += int(action != PASS)
            if arm == "candidate" and seat == hero and phase != "discard":
                diagnostics["reaction_decisions"] += 1
                if len(game.legal_actions()) > 1:
                    diagnostics["reaction_windows"] += 1
                    if isinstance(info, dict):
                        diagnostics["reaction_v1_changes"] += int(info.get("v1_action", action) != action)
                        if info.get("admission_action") != info.get("v1_action"):
                            diagnostics["admission_changed_windows"] += int("admission_action" in info)
                            diagnostics["new_claims_selected"] += int(
                                "admission_action" in info and info.get("v1_action") == PASS and action != PASS)
                            if (info.get("v1_action") == PASS and action != PASS and
                                    not info.get("u2_complete_or_safe_partial")):
                                raise AssertionError("new ordinary claim without usable U2 future")
                        diagnostics["reaction_future_fallbacks"] += int(bool(info.get("u2_fallback_reason")))
                diagnostics["claims"] += int(action != PASS)
            if arm == "candidate" and seat == hero and phase == "discard" and action >= 0:
                diagnostics["discard_decisions"] += 1
                if isinstance(info, dict) and info.get("reason") == "discard_baotou":
                    diagnostics["baotou_decisions"] += 1
                    diagnostics["baotou_weight_changes"] += int(bool(info.get("baotou_weight_changed_winner")))
                    tie = info.get("baotou_score_tiebreak") or {}
                    diagnostics["baotou_score_attempts"] += int(bool(tie.get("attempted")))
                    diagnostics["baotou_score_overrides"] += int(bool(tie.get("override")))
                    diagnostics["baotou_score_fallbacks"] += int(bool(tie.get("fallback_reason")))
                if isinstance(info, dict) and "big_hand_phase" in info:
                    diagnostics["weighted_discard_decisions"] += 1
                    diagnostics["fallbacks"] += int(bool(info.get("fallback_reason")))
                    diagnostics["search_used"] += int(bool(info.get("search_used")))
                    diagnostics["band_discard_decisions"] += int(info.get("speed_band_version") is not None)
                    diagnostics["structural_admissions"] += len(
                        (info.get("marginal_structure_guard") or {}).get("admitted_tiles", []))
                    count = int((info.get("search_metrics") or {}).get("root_candidates") or 0)
                    if count > profile.max_frontier_candidates:
                        raise AssertionError("frontier cap exceeded")
                    if info.get("big_hand_override"):
                        raise AssertionError("BigHand must stay disabled")
            game.step(action)
        if sum(game.scores) != 0:
            raise AssertionError("score conservation")
        results[arm] = {"score": game.scores[hero], "win": bool(game.result and game.result[0] == hero),
                        "claims": claims,
                        "scores": list(game.scores),
                        "elapsed_ms": (time.perf_counter() - started) * 1000}
    return {"seed": seed, "hero": hero, "dealer": dealer, **results,
            "delta": results["candidate"]["score"] - results["baseline"]["score"],
            "diagnostics": dict(diagnostics)}


def run_score(point, pairs, seed_start, jobs, rounds, alpha):
    tasks = [(index, seed_start + index, point) for index in range(pairs)]
    rows = []
    with ProcessPoolExecutor(max_workers=jobs) as pool:
        for row in pool.map(play_pair, tasks, chunksize=4):
            rows.append(row)
            if len(rows) % 64 == 0:
                print(f"pairs {len(rows)}/{pairs}", flush=True)
    diagnostics = Counter()
    for row in rows:
        diagnostics.update(row["diagnostics"])
    return {"overrides": point, "pairs": pairs, "matches": pairs * 2,
            "profile_fingerprint": production_profile(**point).fingerprint,
            "reaction_fingerprint": reaction_profile(**point).fingerprint,
            "paired_score": bootstrap([row["delta"] for row in rows], rounds=rounds, alpha=alpha),
            "nonzero_pairs": sum(row["delta"] != 0 for row in rows),
            "hero_score_mean": {arm: statistics.fmean(row[arm]["score"] for row in rows)
                                for arm in ("baseline", "candidate")},
            "hero_win_rate": {arm: statistics.fmean(row[arm]["win"] for row in rows)
                              for arm in ("baseline", "candidate")},
            "hero_claims": {arm: sum(row[arm]["claims"] for row in rows)
                            for arm in ("baseline", "candidate")},
            "diagnostics": dict(diagnostics), "rows": rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--grid-json", required=True)
    parser.add_argument("--mode", choices=("score", "performance"), default="score")
    parser.add_argument("--pairs", type=int, default=256)
    parser.add_argument("--games", type=int, default=200)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--seed-start", type=int, required=True)
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--bootstrap-rounds", type=int, default=5000)
    parser.add_argument("--alpha", type=float, default=0.05)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    if args.pairs <= 0 or args.pairs % 16 or args.jobs <= 0:
        parser.error("pairs must be a positive multiple of 16; jobs must be positive")
    if not 0 < args.alpha < 1 or args.bootstrap_rounds <= 0:
        parser.error("alpha must be between 0 and 1; bootstrap rounds must be positive")
    kernel = kernel_runtime_diagnostic()
    if not kernel["weighted_kernel_compatible"]:
        parser.error("compatible Rust weighted kernel required; use repository .venv")
    grid = json.loads(Path(args.grid_json).read_text(encoding="utf-8"))
    for point in grid:
        production_profile(**point)
    hashes = {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in (
        "mj/legacy_eval.py", "mj/bot.py", "mj/legacy_react.py", "mj/legacy_kong.py", "mj/legacy_ready_score.py",
        "mj/shanten.py", "mj/hand_eval.py", "rust/src/lib.rs",
        "scripts/legacy_v2_discard_profile_eval.py", "scripts/legacy_v2_big_hand_grid_scan.py")}
    started = time.perf_counter()
    results = []
    for point in grid:
        print(f"Evaluating {point}", flush=True)
        result = (run_score(point, args.pairs, args.seed_start, args.jobs, args.bootstrap_rounds, args.alpha)
                  if args.mode == "score" else run_profile_performance(
                      point, games=args.games, repeats=args.repeats,
                      seed_start=args.seed_start, profile_factory=production_profile,
                      reaction_profile_factory=reaction_profile))
        if args.mode == "performance":
            control, challenger = result["profiles"]["baseline"], result["profiles"]["candidate"]
            p99_ratio = challenger["discard_latency_ms"]["p99"] / control["discard_latency_ms"]["p99"]
            fallback_delta = challenger["fallback_rate"] - control["fallback_rate"]
            result["discard_p99_regression_pct"] = (p99_ratio - 1) * 100
            result["fallback_rate_delta_pp"] = fallback_delta * 100
            result["gates"].update(discard_p99=p99_ratio <= 1.15,
                                   fallback=fallback_delta <= .01)
            if point.get("reaction"):
                for fraction, threshold in (("p95", 1.10), ("p99", 1.15)):
                    ratio = challenger["reaction_latency_ms"][fraction] / control["reaction_latency_ms"][fraction]
                    result[f"reaction_{fraction}_regression_pct"] = (ratio - 1) * 100
                    result["gates"][f"reaction_{fraction}"] = ratio <= threshold
                delta = challenger["reaction_fallback_rate"] - control["reaction_fallback_rate"]
                result["reaction_fallback_rate_delta_pp"] = delta * 100
                result["gates"]["reaction_fallback"] = delta <= .01
                if point["reaction"].get("hu_discard_delay_min_gain_ratio", 1.0) > 1.0:
                    control_hu, candidate_hu = control["hu_latency_ms"], challenger["hu_latency_ms"]
                    result["gates"]["hu_sample_size"] = control_hu["n"] >= 64 and candidate_hu["n"] >= 64
                    for fraction, threshold in (("p95", 1.10), ("p99", 1.15)):
                        ratio = candidate_hu[fraction]/control_hu[fraction]
                        result[f"hu_{fraction}_regression_pct"] = (ratio-1)*100
                        result["gates"][f"hu_{fraction}"] = ratio <= threshold
                    delta = challenger["hu_fallback_rate"]-control["hu_fallback_rate"]
                    result["hu_fallback_rate_delta_pp"] = delta*100
                    result["gates"]["hu_fallback"] = delta <= .01
                if point["reaction"].get("self_kong_progress_enabled"):
                    for fraction, threshold in (("p95", 1.10), ("p99", 1.15)):
                        ratio = challenger["self_kong_latency_ms"][fraction] / control["self_kong_latency_ms"][fraction]
                        result[f"self_kong_{fraction}_regression_pct"] = (ratio-1)*100
                        result["gates"][f"self_kong_{fraction}"] = ratio <= threshold
                    delta = challenger["self_kong_fallback_rate"]-control["self_kong_fallback_rate"]
                    result["self_kong_fallback_rate_delta_pp"] = delta*100
                    result["gates"]["self_kong_fallback"] = delta <= .01
        results.append(result)
        print(json.dumps({key: value for key, value in result.items() if key != "rows"}), flush=True)
        payload = {"schema": "legacy-v2-discard-profile-eval-v1", "mode": args.mode,
                   "pairing": "one hero vs three production opponents; independent 16-case hero/dealer cycle; alternating arm order",
                   "baseline_fingerprint": production_profile().fingerprint,
                   "reaction_fingerprint": LegacyReactionProfile.v2_online().fingerprint,
                   "kernel": kernel, "source_sha256": hashes, "seed_start": args.seed_start,
                   "native_kernel_sha256": hashlib.sha256(Path(native_kernel.__file__).read_bytes()).hexdigest(),
                   "results": results, "runtime_s": time.perf_counter() - started,
                   "python": sys.executable}
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
