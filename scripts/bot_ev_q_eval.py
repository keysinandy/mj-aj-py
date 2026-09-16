#!/usr/bin/env python3
"""Build independent-world paired-Q/regret evidence for discard actions.

The source state is created from a local game only to obtain a reproducible
public decision point.  The Q values are then evaluated on a different,
explicitly sampled world set, so the source game is never reused as an
oracle.  The output is JSONL understood by :mod:`scripts.bot_ev_regret`.

This is an offline evidence producer.  It does not change the production
profile or the default legacy policy.
"""

from __future__ import annotations

import argparse
from collections import Counter
import copy
import json
import math
import os
from pathlib import Path
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from mj.bot import choose_action
from mj.decision.calibration import (
    FEATURE_NAMES, bucket_key, candidate_features, evidence_contract,
    freeze_split_manifest, split_for_seed,
)
from mj.decision.context import PublicDecisionContext
from mj.decision.fast_ev import evaluate_discard_context
from mj.decision.frontier import discard_frontier
from mj.decision.profile import ProfileSpec, profile_from_json
from mj.game import Game
from mj.hand_eval import EvalProfile, evaluate_discard_candidates
from mj.rollout.belief import BeliefSampler
from mj.rollout.evaluator import PairedTeacher
from mj.rollout.simulator import FixedContinuation, run_rollout


DEFAULT_FEATURE_NODE_BUDGET = 10_000_000
DEFAULT_FEATURE_TIME_BUDGET_MS = 30_000.0


def _load_profile(path):
    if not path:
        return ProfileSpec.shape_v2_discard()
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(value.get("profile"), dict):
        value = value["profile"]
    return profile_from_json(value)


def _complete_context(game, seat):
    """Project a source game while retaining only public/count information."""
    context = PublicDecisionContext.from_game(game, seat)
    # A local source game exposes the public chain event history.  Supplying
    # those counters is allowed for offline rollout; no opponent hand or wall
    # order is copied into the context.
    return context.replace(
        chain_counts=tuple(int(x) for x in game.chain),
        chain_piao_counts=tuple(int(x) for x in game.chain_piao),
        rollout_valid=True, missing_fields=(), unsupported=())


def _find_source_context(seed, seat, dealer, ycbk, *, min_shanten=None):
    """Advance a deterministic legacy source until an ordinary discard root.

    Calibration can request a lower-shanten root because the exact shape-v1
    improvement feature is intentionally expensive.  The source policy and
    seed remain unchanged; this only chooses a later, still source-owned
    decision point.  If no such point is reached, the first ordinary root is
    returned so the independent-Q evidence is not lost.
    """
    game = Game(seed=int(seed), dealer=int(dealer),
                you_cai_bi_kao=bool(ycbk))
    first = None
    while not game.done:
        current = game.current_seat()
        actions = tuple(game.legal_actions())
        if current == seat and len(actions) > 1 and all(action >= 0
                                                         for action in actions):
            context = _complete_context(game, seat)
            if first is None:
                first = (copy.deepcopy(game), context,
                         int(choose_action(game, seat, evaluator="legacy")))
            if (min_shanten is not None and
                    min(item.shanten for item in discard_frontier(
                        context, use_rust=True)) > int(min_shanten)):
                action = choose_action(game, seat, evaluator="legacy")
                if action not in actions:
                    raise RuntimeError("source legacy action is not legal")
                game.step(action)
                continue
            actual = choose_action(game, seat, evaluator="legacy")
            if actual not in actions:
                raise RuntimeError("source legacy action is not legal")
            return game, context, int(actual)
        action = choose_action(game, current, evaluator="legacy")
        if action not in actions:
            raise RuntimeError(
                f"source legacy action {action} is illegal; legal={actions}")
        game.step(action)
    return first if first is not None else (None, None, None)


def _as_json(value):
    if value is None:
        return None
    return value.as_json() if hasattr(value, "as_json") else value


def _policy_actions(game, seat, context, profile):
    """Return policy actions plus serialised explanations at one root."""
    legacy = choose_action(game, seat, evaluator="legacy")
    v1_result = choose_action(game, seat, evaluator="shape-v1",
                              return_evaluation=True)
    v1, v1_eval = (v1_result if isinstance(v1_result, tuple)
                   else (v1_result, None))
    v2_result = evaluate_discard_context(
        context, profile, level="EV2", legacy_best=legacy)
    # ``evaluate_discard_context`` consumes the value context and therefore
    # does not read hidden source state.  Use the same frozen profile for the
    # policy action and for its published explanation.
    v2 = v2_result.selected
    if v2 is None:
        v2 = legacy
    return {
        "legacy": int(legacy),
        "shape-v1": int(v1),
        "shape-v2": int(v2),
        # The source action is intentionally kept separate from policy names.
        "actual": int(legacy),
    }, {
        "shape-v1": _as_json(v1_eval),
        "shape-v2": v2_result.as_json(),
    }


def _teacher_discovery(context, profile, seed, worlds):
    """Select a teacher action on a discovery world set."""
    n = max(1, int(worlds))
    teacher = PairedTeacher(
        context, profile_fingerprint=profile.fingerprint,
        seed=int(seed) + 0x13579, belief_version=profile.belief_version,
        continuation=FixedContinuation("shape-v1"), n0=n, batch=n, nmax=n)
    result = teacher.evaluate(context.legal_discards)
    return result


def _independent_q(context, actions, profile, seed, worlds):
    """Evaluate all named root actions on one independent common world set."""
    n = max(1, int(worlds))
    sampler = BeliefSampler(context, seed=int(seed) + 0x2468A,
                            belief_version=profile.belief_version)
    continuation = FixedContinuation("shape-v1")
    unique_actions = tuple(sorted(set(int(value) for value in actions.values())))
    outcomes = {action: [] for action in unique_actions}
    world_rows = []
    errors = []
    for world in sampler.iter_samples(0, n):
        row = {"sample_id": int(world.sample_id),
               "world_fingerprint": world.fingerprint, "rewards": {}}
        for action in unique_actions:
            outcome = run_rollout(context, world, action, continuation)
            encoded = outcome.as_json()
            row["rewards"][str(action)] = encoded
            if outcome.valid:
                outcomes[action].append(float(outcome.reward))
            else:
                errors.append({"sample_id": int(world.sample_id),
                               "action": int(action),
                               "error": outcome.error})
        world_rows.append(row)

    q_by_action = {}
    action_status = {}
    for action in unique_actions:
        values = outcomes[action]
        complete = len(values) == n
        q_by_action[action] = statistics.fmean(values) if complete else None
        action_status[str(action)] = {
            "worlds": n, "valid": len(values), "complete": complete,
        }
    q = {
        name: q_by_action.get(int(action))
        for name, action in actions.items()
    }
    return q, {
        "seed": int(seed) + 0x2468A,
        "worlds": n,
        "world_rows": world_rows,
        "action_status": action_status,
        "errors": errors,
        "independent_worlds": not errors,
        "world_fingerprints": [row["world_fingerprint"] for row in world_rows],
    }


def _feature_rows(source_game, context, actions, q, profile, seed,
                  source_group, split, explanations):
    """Return complete feature rows for actions present in both frontiers."""
    frontier = {item.tile: item for item in discard_frontier(
        context, use_rust=True)}
    shape_profile = EvalProfile.shape_v1(
        discard_node_budget=DEFAULT_FEATURE_NODE_BUDGET,
        discard_time_budget_ms=DEFAULT_FEATURE_TIME_BUDGET_MS,
        explanation=False)
    shape_result = evaluate_discard_candidates(
        source_game, context.hero_seat, shape_profile)
    shape_json = _as_json(shape_result[1])
    shape_candidates = {
        int(item["tile"]): item for item in shape_json.get("candidates", ())
        if (item.get("complete", True) and item.get("I") is not None and
            item.get("B") is not None and item.get("C") is not None)
    }
    fast_profile = ProfileSpec.shape_v2_discard(
        node_budget=DEFAULT_FEATURE_NODE_BUDGET,
        time_budget_ms=DEFAULT_FEATURE_TIME_BUDGET_MS,
        explanation=False)
    fast = evaluate_discard_context(
        context, fast_profile, level="EV2", legacy_best=actions["legacy"])
    fast_candidates = {int(item["tile"]): item
                       for item in fast.as_json().get("candidates", ())
                       if item.get("EV2") is not None}
    rows = []
    for strategy, action in actions.items():
        action = int(action)
        target = q.get(strategy)
        if target is None or action not in frontier or action not in shape_candidates:
            continue
        if action not in fast_candidates:
            continue
        merged = dict(frontier[action].as_json())
        merged.update(fast_candidates[action])
        # Fast-EV serialisation intentionally leaves shape-v1-only I missing;
        # apply the shape-v1 fields last so an explicit missing value from the
        # other evaluator cannot erase a complete offline feature.
        merged.update(shape_candidates[action])
        merged["risk"] = max(0, int(frontier[action].visible_unknown) -
                              int(frontier[action].u1))
        values, missing = candidate_features(merged, context)
        if missing:
            continue
        feature_vector = {name: float(values[name]) for name in FEATURE_NAMES}
        row = {
            "features": feature_vector, "target": float(target),
            "source_group": source_group, "seed": int(seed),
            "split": split, "strategy": strategy, "action": action,
            "bucket": list(bucket_key(context, merged)),
            "hard_gate_pass": (int(frontier[action].shanten) ==
                                min(item.shanten for item in frontier.values())),
            "action_threshold_pass": action == int(actions["shape-v2"]),
            "target_kind": "independent_world_mean_Q",
            "independent_worlds": True,
            "oracle": False, "counterfactual_evaluation": True,
            "profile_fingerprint": profile.fingerprint,
            "rule_version": profile.rules_version,
            "kernel_version": profile.kernel_version,
            "scope": profile.scope,
            "continuation_version": profile.continuation_version,
            "teacher_profile_fingerprint": profile.fingerprint,
            "feature_profile_fingerprints": {
                "shape-v1": shape_profile.fingerprint,
                "shape-v2-offline": fast_profile.fingerprint,
            },
            "feature_provenance": values.get("feature_provenance", {}),
            "explanation_levels": {
                "shape-v1": (explanations.get("shape-v1") or {}).get("level"),
                "shape-v2": (explanations.get("shape-v2") or {}).get("level"),
            },
        }
        rows.append(row)
    return rows


def _schedule(manifest, split, count):
    spec = manifest["splits"][split]
    for index in range(min(int(count), int(spec["games"]))):
        yield int(spec["seed_start"]) + index, index


def run(*, profile=None, split_manifest=None, splits=("train", "validation",
                                                     "final_test"),
        contexts_per_split=1, worlds=16, teacher_worlds=8):
    profile = profile or ProfileSpec.shape_v2_discard()
    manifest = split_manifest or freeze_split_manifest(
        profile_fingerprint=profile.fingerprint,
        rule_version=profile.rules_version,
        kernel_version=profile.kernel_version,
        scope=profile.scope,
        continuation_version=profile.continuation_version,
        strategy="shape-v2")
    records = []
    calibration_rows = []
    errors = []
    for split in splits:
        if split not in manifest["splits"]:
            raise ValueError(f"unknown split: {split}")
        for seed, index in _schedule(manifest, split, contexts_per_split):
            seat = index % 4
            dealer = (index // 4) % 4
            ycbk = bool(index % 2)
            try:
                game, context, actual = _find_source_context(
                    seed, seat, dealer, ycbk, min_shanten=1)
                if context is None:
                    errors.append({"seed": seed, "split": split,
                                   "error": "no_ordinary_discard_context"})
                    continue
                actions, explanations = _policy_actions(
                    game, seat, context, profile)
                teacher_result = _teacher_discovery(
                    context, profile, seed, teacher_worlds)
                teacher_action = teacher_result.best_action
                if teacher_action is None:
                    errors.append({"seed": seed, "split": split,
                                   "error": "teacher_no_action"})
                    continue
                actions["teacher"] = int(teacher_action)
                q, q_meta = _independent_q(
                    context, actions, profile, seed, worlds)
                source_group = f"seed:{seed}"
                record = {
                    "source_group": source_group, "seed": seed,
                    "index": index, "split": split, "seat": seat,
                    "dealer": dealer, "you_cai_bi_kao": ycbk,
                    "actions": actions, "q": q,
                    "q_meta": q_meta,
                    "teacher": {
                        "action": int(teacher_action),
                        "ambiguous": bool(teacher_result.ambiguous),
                        "status": teacher_result.status,
                        "sample_count": teacher_result.sample_count,
                        "discovery_seed": seed + 0x13579,
                        "discovery_fingerprint": teacher_result.fingerprint,
                    },
                    "actual_action": int(actual),
                    "independent_worlds": bool(q_meta["independent_worlds"]),
                    "counterfactual_evaluation": True,
                    "oracle": False,
                    "profile": profile.as_json(),
                    "contract": evidence_contract(
                        profile, manifest=manifest, strategy="shape-v2",
                        scope=profile.scope),
                    "context": context.as_json(),
                    "source_policy": "legacy",
                    "explanations": explanations,
                }
                records.append(record)
                calibration_rows.extend(_feature_rows(
                    game, context, actions, q, profile, seed, source_group,
                    split, explanations))
                print(json.dumps({"split": split, "seed": seed,
                                  "seat": seat, "dealer": dealer,
                                  "teacher_action": teacher_action,
                                  "teacher_ambiguous": teacher_result.ambiguous,
                                  "q_complete": all(value is not None
                                                    for value in q.values()),
                                  "calibration_rows": len(calibration_rows)},
                                 ensure_ascii=False), file=sys.stderr,
                      flush=True)
            except Exception as exc:
                errors.append({"seed": seed, "split": split,
                               "error": f"{type(exc).__name__}:{exc}"})
    return {"records": records, "calibration_rows": calibration_rows,
            "errors": errors, "profile": profile, "manifest": manifest}


def _write_jsonl(path, rows):
    if not path:
        return
    with open(path, "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False,
                                    separators=(",", ":")) + "\n")


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile-json")
    parser.add_argument("--split-manifest")
    parser.add_argument("--split-manifest-output")
    parser.add_argument("--splits", default="train,validation,final_test")
    parser.add_argument("--contexts-per-split", type=int, default=1)
    parser.add_argument("--worlds", type=int, default=16)
    parser.add_argument("--teacher-worlds", type=int, default=8)
    parser.add_argument("--output", required=True,
                        help="paired Q/regret JSONL output")
    parser.add_argument("--calibration-output",
                        help="calibration rows JSONL output")
    parser.add_argument("--error-output")
    args = parser.parse_args(argv)
    profile = _load_profile(args.profile_json)
    manifest = None
    if args.split_manifest:
        manifest = json.loads(Path(args.split_manifest).read_text(encoding="utf-8"))
    value = run(
        profile=profile, split_manifest=manifest,
        splits=tuple(x.strip() for x in args.splits.split(",") if x.strip()),
        contexts_per_split=args.contexts_per_split, worlds=args.worlds,
        teacher_worlds=args.teacher_worlds)
    _write_jsonl(args.output, value["records"])
    _write_jsonl(args.calibration_output, value["calibration_rows"])
    _write_jsonl(args.error_output, value["errors"])
    if args.split_manifest_output:
        Path(args.split_manifest_output).write_text(
            json.dumps(value["manifest"], ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
    print(json.dumps({
        "schema": "bot-ev-discard/independent-q-source-v1",
        "records": len(value["records"]),
        "calibration_rows": len(value["calibration_rows"]),
        "errors": len(value["errors"]),
        "profile_fingerprint": profile.fingerprint,
        "split_manifest_fingerprint": value["manifest"]["fingerprint"],
        "independent_worlds": all(
            row.get("independent_worlds") is True for row in value["records"]),
        "oracle": False,
    }, ensure_ascii=False, indent=2))
    return 0 if not value["errors"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
