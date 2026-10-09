#!/usr/bin/env python3
"""Public calibration and seed-paired 2v2 score/latency acceptance.

Games means actual complete matches: 5000 matches = 2500 seed pairs, with
candidate seats exchanged in the second match. No assumption is made about
wall order after the first different action. All rollout gates stay off.
"""
from __future__ import annotations

import argparse
import copy
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
import hashlib
import json
import math
from pathlib import Path
import statistics
import subprocess
import time

from mj.bot import choose_action
from mj.game import Game, HU, PASS, PONG, KONG_OPEN, CHOW_LOW, CHOW_MID, CHOW_HIGH
from mj.legacy_belief import LegacyDecisionFeatures, OpponentBelief
from mj.legacy_calibration import fit, VerifiedCalibration
from mj.legacy_hand_plan import HandPlan
from mj.legacy_quality_profile import LegacyQualityProfile
from mj.legacy_joint_reaction import QualityDecisionState
from mj.legacy_tail import continuation_bucket
from mj.decision.score_value import ScoreValue
from mj.legacy_eval import LegacyTwoPlyProfile
from mj.legacy_react import LegacyReactionProfile
from mj.shanten import shanten, kernel_runtime_diagnostic
from mj.tiles import W


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def source_digest():
    digest = hashlib.sha256()
    for folder in (Path("mj"), Path("rust/src")):
        for path in sorted(folder.rglob("*.py" if folder.name == "mj" else "*.rs")):
            digest.update(str(path).encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def freeze_baseline(output):
    sha = subprocess.check_output(["git", "-c", "safe.directory=D:/codespace/mj-aj-py", "rev-parse", "HEAD"], text=True).strip()
    value = {"sha": sha, "working_source_digest": source_digest(),
             "discard_profile": LegacyTwoPlyProfile.weighted_online().as_json(),
             "reaction_profile": LegacyReactionProfile.v2_online().as_json(),
             "scoring": {"version": "hangzhou-platform-guide-v34", "base": 1, "you_cai_bi_kao": False,
                         "win_rule": "self_draw_only", "multi_winner": False},
             "opponents": {"evaluator": "legacyV2", "sha": sha}, "kernel": kernel_runtime_diagnostic()}
    write_json(output, value)
    return value


def golden(output):
    from dataclasses import fields
    from mj.decision.context import PublicDecisionContext
    from mj.legacy_danger import DangerEstimator
    states = []
    for seed in (11, 20, 44, 51):
        game = Game(seed=seed)
        f = LegacyDecisionFeatures.from_game(game, 0)
        context = {field.name: getattr(f.context, field.name) for field in fields(PublicDecisionContext) if field.init}
        states.append({"seed": seed, "context": context, "cache_key": f.cache_key(),
                       "baseline_selected": choose_action(game, 0),
                       "danger": {str(t): r.as_json() for t, r in DangerEstimator().evaluate(f, f.context.legal_discards, OpponentBelief()).items()}})
    write_json(output, {"schema": "legacy-quality-public-golden-v1", "states": states})


def collect_game(seed):
    """Hidden labels are isolated here; only public features enter inference."""
    game = Game(seed=seed, dealer=seed % 4)
    rows, waiting = [], defaultdict(list)
    response = []
    tail_waiting, tail_reached = defaultdict(list), []
    while not game.done:
        seat = game.current_seat()
        action = choose_action(game, seat)
        if game.phase == "discard" and game.drawn[seat] is not None:
            # Exact next-draw HU events belong to the win component, not tail.
            reached = tail_waiting.pop(seat, [])
            if HU not in game.legal_actions():
                tail_reached.extend(reached)
            for row in waiting.pop(seat, []):
                rows.append(dict(row, label=int(action == HU), multiplier=1))
        if game.phase == "discard" and 0 <= action < 34:
            if response:
                rows.extend(row for row in response if row.get("opportunity"))
                response = []
            features = LegacyDecisionFeatures.from_game(game, seat)
            standing, chain, piao, _ = ScoreValue(game.dealer, game.base,
                game.you_cai_bi_kao, seat).discard(game.hands[seat], action,
                    len(game.melds[seat]), game.chain[seat], game.chain_piao[seat])
            tail_waiting[seat].append({"seed": seed, "seat": seat,
                "target": "continuation", "bucket": continuation_bucket(features, standing,
                    len(game.melds[seat]), chain=chain, chain_piao=piao)})
            model = OpponentBelief()
            for other in range(4):
                if other == seat:
                    continue
                for target in ("tenpai", "next_win", "pong", "chi", "kong"):
                    tile = action if target in ("pong", "chi", "kong") else None
                    estimate = model.estimate(features, other, target, tile)
                    row = {"seed": seed, "target": target, "bucket": features.bucket(other, tile),
                           "prediction": estimate.probability, "seat": other, "label": 0}
                    if target == "tenpai":
                        row["label"] = int(shanten(game.hands[other], len(game.melds[other])) == 0)
                        rows.append(row)
                    elif target == "next_win":
                        waiting[other].append(row)
                    elif target in ("pong", "kong") or other == (seat + 1) % 4:
                        response.append(row)
        elif game.phase == "react":
            if action != PASS:
                # The post-claim discard gets its own conditional state.
                tail_waiting.pop(seat, None)
            target = "pong" if action == PONG else "kong" if action == KONG_OPEN else "chi" if action in (CHOW_LOW, CHOW_MID, CHOW_HIGH) else None
            for row in response:
                allowed = row["target"] in ("pong", "kong") if game.react_mode() == "claim" else row["target"] == "chi"
                if row["seat"] == seat and allowed:
                    row["opportunity"] = True
                if row["seat"] == seat and row["target"] == target:
                    row["label"] = 1
        # Exact terminal multiplier belongs only to the training label.
        if action == HU:
            from mj.scoring import hand_multiplier
            standing = list(game.hands[seat])
            standing[game.drawn[seat]] -= 1
            mult, _ = hand_multiplier(game.hands[seat], standing, len(game.melds[seat]), game.chain[seat], game.chain_piao[seat])
            for row in rows:
                if row["target"] == "next_win" and row["seat"] == seat and row["label"]:
                    row["multiplier"] = mult
        game.step(action)
    rows.extend(row for row in response if row.get("opportunity"))
    rows.extend(dict(row, label=game.scores[row["seat"]]/game.base) for row in tail_reached)
    return rows


def calibrate(output, train_games=256, validation_games=128, jobs=1):
    train = list(range(720000, 720000 + train_games))
    validation = list(range(740000, 740000 + validation_games))
    rows = []
    if jobs == 1:
        results = map(collect_game, train + validation)
        for value in results:
            rows.extend(value)
    else:
        with ProcessPoolExecutor(max_workers=jobs) as pool:
            for value in pool.map(collect_game, train + validation, chunksize=4):
                rows.extend(value)
    artifact = fit(rows, train, validation)
    artifact["training_source_digest"] = source_digest()
    artifact["runner_digest"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    from mj.decision.profile import fingerprint
    artifact["calibration_id"] = fingerprint({k: v for k, v in artifact.items() if k != "calibration_id"})
    write_json(output, artifact)
    print(f"calibration: {len(rows)} labels / {len(artifact['buckets'])} buckets", flush=True)
    return artifact


def quantiles(values):
    if not values:
        return {"samples": 0, "p50": None, "p95": None, "p99": None, "max": None}
    ordered = sorted(values)
    return {"samples": len(values), **{key: ordered[min(len(ordered)-1, int((len(ordered)-1)*fraction))]
            for key, fraction in (("p50", .50), ("p95", .95), ("p99", .99))}, "max": max(ordered)}


def interval(values):
    n = len(values)
    mean = statistics.fmean(values) if n else None
    std = statistics.stdev(values) if n > 1 else None
    error = 1.96 * std / math.sqrt(n) if std is not None else None
    return {"samples": n, "mean": mean, "low": mean-error if error is not None else None,
            "high": mean+error if error is not None else None,
            "standard_deviation": std, "standardized_effect": mean/std if std else None,
            "method": "95% normal interval over independent seed pairs"}


def play_pair(args):
    index, seed, phase, artifact = args[:4]
    overrides = args[4] if len(args) > 4 else {}
    profile = LegacyQualityProfile.phase(phase, calibration_id=artifact["calibration_id"], **overrides)
    model = VerifiedCalibration(artifact)
    scores, outcomes = [], []
    timings = {"candidate": defaultdict(list), "baseline": defaultdict(list)}
    counts = {"candidate": Counter(), "baseline": Counter()}
    phase_buckets = Counter()
    regrets, hu_timings = [], []
    # Alternating execution order balances warm caches. Each match swaps all
    # seats while retaining seed and dealer; candidate groups have two seats.
    for arm in ((0, 1) if index % 2 == 0 else (1, 0)):
        game = Game(seed=seed, dealer=index % 4)
        game.gid, game.round_no = str(seed), 0
        candidate_seats = {arm, arm+2}
        plans = {s: HandPlan() for s in candidate_seats}
        states = {s: QualityDecisionState() for s in candidate_seats}
        count = 0
        game_regrets = 0
        begin = time.perf_counter()
        while not game.done:
            seat = game.current_seat()
            group = "candidate" if seat in candidate_seats else "baseline"
            started = time.perf_counter()
            if group == "candidate":
                action, evaluation = choose_action(game, seat, return_evaluation=True,
                    quality_profile=profile, quality_calibration=model, quality_hand_plan=plans[seat],
                    quality_state=states[seat])
                quality = evaluation["quality"]
                scope = quality["decision_scope"]
                counts[group]["override"] += int(quality["new_selected"] != quality["old_selected"])
                counts[group][scope + ":override"] += int(quality["new_selected"] != quality["old_selected"])
                counts[group][scope + ":deadline_exceeded"] += int(quality["deadline_exceeded"])
                counts[group]["committed_discards"] += int(bool(quality.get("committed_discard")))
                reason = quality.get("fallback_reason")
                if reason:
                    counts[group]["quality_reason:" + reason] += 1
                    if reason not in ("only_legal_action", "quality_disabled", "quality_window_inactive", "baseline_best"):
                        counts[group]["quality_abstentions"] += 1
                phase_buckets[scope + (":early" if game.live_wall_left() > 48 else ":middle" if game.live_wall_left() > 24 else ":late")] += 1
                if scope == "hu":
                    hu_timings.append({"seed": seed, "decision": count, "hero": seat,
                                       "selected": action, "immediate_hu": action == HU,
                                       "public_wall_left": game.live_wall_left(), "elapsed_ms": quality["elapsed_ms"]})
            else:
                action, evaluation = choose_action(game, seat, return_evaluation=True)
                actions = game.legal_actions()
                from mj.legacy_kong import kong_actions
                scope = "reaction" if game.phase != "discard" else "hu" if HU in actions else "kong" if kong_actions(actions) else "discard"
            elapsed = (time.perf_counter()-started)*1000
            timings[group][scope].append(elapsed)
            counts[group]["decisions"] += 1
            counts[group]["fallback"] += int(bool(evaluation.get("fallback_reason") or evaluation.get("u2_fallback_reason")) and evaluation.get("fallback_reason") != "only_legal_action")
            counts[group][scope + ":fallback"] += int(bool(evaluation.get("fallback_reason") or evaluation.get("u2_fallback_reason")) and evaluation.get("fallback_reason") != "only_legal_action")
            if action not in game.legal_actions():
                raise AssertionError(f"illegal action seed={seed} phase={game.phase} seat={seat}")
            if group == "candidate" and phase in ("C", "combined") and quality["new_selected"] != quality["old_selected"] and game_regrets < 2:
                old_reward = counterfactual_reward(game, seat, quality["old_selected"])
                child = quality.get("children", {}).get(action, {}).get("discard")
                new_reward = counterfactual_reward(game, seat, action, followup_discard=child)
                regrets.append({"public_input_hash": quality.get("public_input_hash"), "scope": scope,
                                "old_reward": old_reward, "new_reward": new_reward,
                                "regret": old_reward-new_reward, "committed_discard": child,
                                "continuation_policy": "committed joint child, then frozen legacyV2 all seats"})
                game_regrets += 1
            game.step(action)
            count += 1
            if count > 2000:
                raise RuntimeError("nonterminating game")
        if sum(game.scores) != 0:
            raise AssertionError("settlement is not zero sum")
        scores.append(sum(game.scores[s] for s in candidate_seats)/2)
        outcomes.append({"candidate_score": scores[-1], "scores": game.scores,
                         "candidate_seats": sorted(candidate_seats), "draw": game.result is None,
                         "candidate_win": bool(game.result and game.result[0] in candidate_seats),
                         "game_ms": (time.perf_counter()-begin)*1000})
    return {"seed": seed, "pair_score": statistics.fmean(scores), "games": outcomes,
            "timings": {g: dict(v) for g, v in timings.items()},
            "counts": {g: dict(v) for g, v in counts.items()}, "stage_buckets": dict(phase_buckets),
            "sampled_regret": regrets, "hu_timings": hu_timings}


def counterfactual_reward(game, hero, action, *, followup_discard=None):
    """Offline outcome label, isolated from all online value estimation."""
    from mj.bot import _push_rounds
    world = copy.deepcopy(game)
    if game in _push_rounds:
        _push_rounds[world] = dict(_push_rounds[game])
    world.step(action)
    if followup_discard is not None:
        if world.done or world.current_seat() != hero or world.drawn[hero] is not None or followup_discard not in world.legal_actions():
            raise ValueError("invalid counterfactual joint discard")
        world.step(followup_discard)
    steps = 0
    while not world.done:
        world.step(choose_action(world, world.current_seat()))
        steps += 1
        if steps > 2000:
            raise RuntimeError("counterfactual did not terminate")
    return world.scores[hero]


def summarize(rows, games, phase, artifact, protocol):
    timings = {g: defaultdict(list) for g in ("candidate", "baseline")}
    counts = {g: Counter() for g in timings}
    buckets = Counter()
    for row in rows:
        for group in timings:
            counts[group].update(row["counts"][group])
            for scope, values in row["timings"][group].items():
                timings[group][scope].extend(values)
        buckets.update(row["stage_buckets"])
    latency = {g: {s: quantiles(v) for s, v in groups.items()} for g, groups in timings.items()}
    ci = interval([r["pair_score"] for r in rows])
    clock = protocol.get("timing_clock", {})
    timing_valid = clock.get("name") == "perf_counter" and 0 < clock.get("resolution_s", 1) <= .0001
    gates = {}
    for scope in sorted(set(timings["candidate"]) | set(timings["baseline"])):
        b, c = latency["baseline"].get(scope), latency["candidate"].get(scope)
        gates[scope] = bool(b and c and b["samples"] >= 64 and c["samples"] >= 64 and
                            c["p95"] <= b["p95"] * 1.10 and c["p99"] <= b["p99"] * 1.15)
    fallback_rates = {g: counts[g]["fallback"]/counts[g]["decisions"] if counts[g]["decisions"] else 0 for g in counts}
    score_pass = ci["low"] is not None and ci["low"] >= -.10 and ci["mean"] >= 0
    full = len(rows)*2 == games
    active = counts["candidate"]["override"] > 0 if phase in ("A", "B", "C", "combined") else True
    matches = [match for row in rows for match in row["games"]]
    regrets = [value for row in rows for value in row.get("sampled_regret", [])]
    regret_pair_means = [statistics.fmean(v["regret"] for v in row["sampled_regret"])
                         for row in rows if row.get("sampled_regret")]
    return {"schema": "legacy-quality-2v2-acceptance-v1", "phase": phase,
            "games_requested": games, "games_completed": len(matches), "seed_pairs": len(rows),
            "protocol": protocol, "candidate_profile": LegacyQualityProfile.phase(phase,
                calibration_id=artifact["calibration_id"], **protocol.get("profile_overrides", {})).as_json(),
            "score_ci": ci, "latency_ms": latency,
            "end_to_end_ms": quantiles([m["game_ms"] for m in matches]),
            "counts": {g: dict(c) for g, c in counts.items()}, "fallback_rates": fallback_rates,
            "quality_abstention_rates": {g: counts[g]["quality_abstentions"]/counts[g]["decisions"] if counts[g]["decisions"] else 0 for g in counts},
            "stage_buckets": dict(buckets),
            "sampled_reaction_regret": {"max_samples_per_match": 2, "samples": len(regrets),
                                        "seed_pairs_with_samples": len(regret_pair_means),
                                        "ci": interval(regret_pair_means),
                                        "offline_hidden_world_labels": True, "online_hidden_information_used": False},
            "hu_timing": {"windows": sum(len(r.get("hu_timings", [])) for r in rows),
                          "immediate_hu": sum(h["immediate_hu"] for r in rows for h in r.get("hu_timings", []))},
            "outcomes": {"draws": sum(m["draw"] for m in matches), "candidate_wins": sum(m["candidate_win"] for m in matches),
                         "mean_positive_score": statistics.fmean([m["candidate_score"] for m in matches if m["candidate_score"] > 0]) if any(m["candidate_score"] > 0 for m in matches) else None,
                         "mean_negative_score": statistics.fmean([m["candidate_score"] for m in matches if m["candidate_score"] < 0]) if any(m["candidate_score"] < 0 for m in matches) else None},
            "gate": {"score": score_pass, "latency_by_scope": gates, "timing_valid": timing_valid,
                     "fallback": fallback_rates["candidate"] <= fallback_rates["baseline"]+.01,
                     "active_overrides": active, "complete": full,
                     "passed": full and score_pass and active and timing_valid and all(gates.values()) and fallback_rates["candidate"] <= fallback_rates["baseline"]+.01,
                     "default_enabled": False}, "kernel": kernel_runtime_diagnostic()}


def evaluate(output, phase, games, seed_start, artifact, jobs, *, profile_overrides=None):
    if games < 2 or games % 2:
        raise ValueError("games must be positive and even for paired 2v2")
    seeds = set(range(seed_start, seed_start+games//2))
    if seeds & (set(artifact["train_seeds"]) | set(artifact["validation_seeds"])):
        raise ValueError("acceptance seeds overlap calibration")
    protocol = {"games": games, "seed_start": seed_start, "candidate_seats": "0,2 then 1,3 per seed",
                "profile_overrides": profile_overrides or {},
                "dealer": "pair_index % 4", "opponents": "frozen legacyV2",
                "score_margin": -.10, "p95_ratio": 1.10, "p99_ratio": 1.15,
                "fallback_margin": .01, "minimum_latency_samples": 64,
                "calibration_id": artifact["calibration_id"], "source_digest": source_digest(),
                "runner_digest": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "same_initial_conditions": True, "same_post_divergence_draw_order_assumed": False,
                "timing_clock": {"name": "perf_counter", "implementation": time.get_clock_info("perf_counter").implementation,
                                 "resolution_s": time.get_clock_info("perf_counter").resolution}}
    write_json(str(output)+".protocol.json", protocol)
    work = [(i, seed_start+i, phase, artifact, profile_overrides or {}) for i in range(games//2)]
    rows = []
    output = Path(output)
    with ProcessPoolExecutor(max_workers=jobs) as pool, output.with_suffix(".rows.jsonl").open("w", encoding="utf-8") as handle:
        for row in pool.map(play_pair, work, chunksize=1):
            # Latency arrays are retained in raw evidence, never in the public
            # inference projection. Checkpoints permit inspection during runs.
            handle.write(json.dumps(row, ensure_ascii=False)+"\n")
            handle.flush()
            rows.append(row)
            if len(rows) % 25 == 0 or len(rows)*2 == games:
                report = summarize(rows, games, phase, artifact, protocol)
                write_json(output, report)
                print(f"{phase}: {len(rows)*2}/{games} games; score={report['score_ci']['mean']:.4f}", flush=True)
    return summarize(rows, games, phase, artifact, protocol)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default="runs/legacy_v2_quality")
    parser.add_argument("--games", type=int, default=5000)
    parser.add_argument("--jobs", type=int, default=2)
    parser.add_argument("--phase", choices=("off", "shadow", "A", "B", "C", "D", "route", "combined", "campaign"), default="campaign")
    parser.add_argument("--phase-games", type=int, default=256)
    parser.add_argument("--calibration")
    parser.add_argument("--freeze", action="store_true")
    parser.add_argument("--golden")
    parser.add_argument("--train-games", type=int, default=256)
    parser.add_argument("--validation-games", type=int, default=128)
    parser.add_argument("--seed-start", type=int, default=800000)
    parser.add_argument("--disable-continuation", action="store_true", help="Ablate the calibrated terminal tail")
    args = parser.parse_args()
    folder = Path(args.output_dir)
    folder.mkdir(parents=True, exist_ok=True)
    if args.golden:
        golden(args.golden)
        return
    if args.freeze:
        freeze_baseline(folder/"baseline.json")
        return
    artifact = json.loads(Path(args.calibration).read_text(encoding="utf-8")) if args.calibration else calibrate(folder/"calibration.json", args.train_games, args.validation_games, args.jobs)
    phases = ["shadow", "A", "B", "C", "D", "route", "combined"] if args.phase == "campaign" else [args.phase]
    results = {}
    overrides = {"continuation_ev_enabled": False} if args.disable_continuation else {}
    # Shadow first, then each capability independently. A failed gate never
    # promotes any phase; all gates are reported and defaults remain disabled.
    for index, phase in enumerate(phases):
        if phase not in ("shadow", "combined") and args.phase == "campaign":
            canary = evaluate(folder/f"{phase}_canary.json", phase, 64, 900000+index*1000, artifact, args.jobs, profile_overrides=overrides)
            write_json(folder/f"{phase}_canary.json", canary)
        games = args.phase_games if phase != "combined" and args.phase == "campaign" else args.games
        result = evaluate(folder/f"{phase}.json", phase, games, args.seed_start + index*10000, artifact, args.jobs, profile_overrides=overrides)
        write_json(folder/f"{phase}.json", result)
        results[phase] = result["gate"]
        write_json(folder/"rollout.json", {"phases": results, "default_enabled": False,
                   "rollback": {p: "LegacyQualityProfile.phase('off') or disable the independent phase flags" for p in phases},
                   "status": "running" if index+1 < len(phases) else "complete"})
    print(json.dumps(results, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
