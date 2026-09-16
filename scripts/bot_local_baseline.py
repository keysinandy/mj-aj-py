#!/usr/bin/env python3
"""Local mixed-BOT score/performance baseline.

The host owns the complete :class:`mj.game.Game`; a policy receives only a
detached seat view containing its own hand and public table state.  This is a
benchmark harness, not a production policy switch.  It never repairs an
illegal action or chooses another policy after an evaluator reports a
fallback.  Native evaluator fallback/delegation is retained in the result
metadata so the measured score is not confused with a no-fallback claim.

The default workload is 500 mixed games: one rollout-teacher, shape-v2,
shape-v1 and legacy seat per game.  The assignment rotates seats and dealer
position in a 16-game cycle.  A separate old-version comparison can run this
same script from a parent worktree with the same seed manifest.
"""

from __future__ import annotations

from collections import Counter, defaultdict
import argparse
import json
import math
import os
import platform
import statistics
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from mj.bot import choose_action
from mj.decision.context import PublicDecisionContext
from mj.decision.profile import ProfileSpec
from mj.decision.root import evaluate_root_context
from mj.decision.score_value import (
    REWARD_ENVELOPE_VERSION,
    RewardCertificate,
    RewardEnvelope,
    reward_envelope,
    theoretical_reward_bound,
)
from mj.game import DEAD_WALL, Game, HU
from mj.hand_eval import EvalProfile, evaluate_discard_candidates, evaluate_reaction
from mj.rollout.evaluator import PairedTeacher
from mj.rollout.simulator import FixedContinuation


BOT_TYPES = ("rollout-teacher", "shape-v2", "shape-v1", "legacy")


class HiddenStateAccess(RuntimeError):
    """Raised when a policy asks for concealed data outside its own seat."""


class _HiddenRow:
    """A fixed-size hand row which cannot reveal any tile identity."""

    def __init__(self, label):
        self.label = label

    def __len__(self):
        return 34

    def __getitem__(self, _key):
        raise HiddenStateAccess(f"opponent hand access: {self.label}")

    def __iter__(self):
        raise HiddenStateAccess(f"opponent hand iteration: {self.label}")

    def __contains__(self, _value):
        raise HiddenStateAccess(f"opponent hand membership: {self.label}")


class SeatView:
    """Detached policy input with only one concealed hand populated."""

    def __init__(self, game, seat, legal_actions):
        self._seat = int(seat)
        self._live_wall = max(0, int(game.live_wall_left()))
        self._legal = tuple(int(action) for action in legal_actions)
        self.hands = tuple(
            list(game.hands[index]) if index == self._seat
            else _HiddenRow(f"seat:{index}")
            for index in range(4))
        # The following fields are public table state or the actor's private
        # state.  No reference to the host Game, wall or RNG is retained.
        self.melds = tuple(tuple(tuple(m) for m in row)
                          for row in game.melds)
        self.discards = tuple(tuple(row) for row in game.discards)
        self.drawn = tuple(
            game.drawn[index] if index == self._seat else None
            for index in range(4))
        self.chows = tuple(int(value) for value in game.chows)
        self.turn = int(game.turn)
        self.phase = str(game.phase)
        self.pending = (tuple(game.pending) if game.pending is not None
                        else None)
        self.freeze = int(game.freeze)
        self.freezer = game.freezer
        self.chain = tuple(int(value) for value in game.chain)
        self.chain_piao = tuple(int(value) for value in game.chain_piao)
        self.dealer = int(game.dealer)
        self.base = int(game.base)
        self.you_cai_bi_kao = bool(game.you_cai_bi_kao)
        self._kong_draw = bool(game._kong_draw)
        self.react_seq = tuple(getattr(game, "react_seq", ()))
        self.react_idx = getattr(game, "react_idx", 0)
        self._n_claim = getattr(game, "_n_claim", 0)
        self.done = False
        self.result = None
        self.scores = [0] * 4

    @property
    def wall(self):
        raise HiddenStateAccess("wall access")

    @property
    def rng(self):
        raise HiddenStateAccess("random state access")

    def current_seat(self):
        return self.turn

    def live_wall_left(self):
        return self._live_wall

    def in_freeze(self, seat):
        if int(seat) != self._seat:
            raise HiddenStateAccess("freeze query for another seat")
        return self.freeze > 0 and self._seat != self.freezer

    def react_mode(self):
        return "claim" if self.react_idx < self._n_claim else "chow"

    def legal_actions(self):
        return self._legal

    def visible_counts(self, seat):
        if int(seat) != self._seat:
            raise HiddenStateAccess("visible-count query for another seat")
        visible = list(self.hands[self._seat])
        for row in self.discards:
            for tile in row:
                visible[int(tile)] += 1
        for row in self.melds:
            for kind, tile in row:
                tile = int(tile)
                if kind == "chow":
                    for value in (tile, tile + 1, tile + 2):
                        visible[value] += 1
                elif str(kind).startswith("kong"):
                    visible[tile] += 4
                else:
                    visible[tile] += 3
        return visible


def _public_context(view, seat):
    """Build a complete public/count-only context from the seat view."""
    context = PublicDecisionContext.from_game(view, seat)
    return context.replace(
        chain_counts=tuple(view.chain),
        chain_piao_counts=tuple(view.chain_piao),
        chain_count=view.chain[seat],
        chain_piao=view.chain_piao[seat],
        rollout_valid=True,
        missing_fields=(),
        unsupported=())


def _json_evaluation(value):
    if isinstance(value, dict):
        return value
    method = getattr(value, "as_json", None)
    if callable(method):
        return method()
    return {}


def _evaluation_meta(value):
    data = _json_evaluation(value)
    return {
        "level": data.get("level"),
        "reason": data.get("reason"),
        "fallback_reason": data.get("fallback_reason"),
        "delegated_reason": data.get("delegated_reason"),
        "complete": data.get("complete"),
        "nodes": data.get("nodes"),
        "kernel_calls": data.get("kernel_calls"),
    }, data


def _scalar_envelope(action, bound):
    bound = float(bound)
    return RewardEnvelope(
        version=REWARD_ENVELOPE_VERSION, candidate=int(action),
        fast_upper=bound, rollout_lower=-bound, rollout_upper=bound,
        rollout_abs=bound, components={"scalar_bound": bound},
        certificate=RewardCertificate(
            mode="override", proof="local_teacher_special_action_bound",
            details={"scalar_bound": bound}))


def _teacher_envelopes(context):
    """Use derived envelopes for discards and explicit safe bounds for roots."""
    special_bound = theoretical_reward_bound(context.base or 1)
    output = {}
    for action in context.legal_actions:
        if 0 <= int(action) < 34:
            envelope = reward_envelope(context, int(action))
            if envelope.rollout_abs is None:
                raise RuntimeError(
                    f"rollout envelope unavailable for discard {action}")
        else:
            # RewardEnvelope deliberately models ordinary discard candidates;
            # special actions still receive a signed explicit bound so the
            # teacher compares every legal root without selecting a fallback.
            envelope = _scalar_envelope(action, special_bound)
        output[int(action)] = envelope
    return output


def _run_rollout_teacher(view, seat, config):
    context = _public_context(view, seat)
    envelopes = _teacher_envelopes(context)
    profile = ProfileSpec.shape_v2_all_root(
        calibrated=True, explanation=False,
        node_budget=config["teacher_node_budget"],
        time_budget_ms=config["teacher_time_budget_ms"])
    teacher = PairedTeacher(
        context, profile=profile,
        continuation=FixedContinuation("legacy"),
        envelopes=envelopes,
        n0=config["teacher_n0"], batch=config["teacher_batch"],
        nmax=config["teacher_nmax"], alpha=config["teacher_alpha"])
    result = teacher.evaluate(context.legal_actions)
    if result.best_action is None:
        raise RuntimeError(
            f"rollout teacher produced no action: {result.stop_reason}")
    return int(result.best_action), {
        "level": "ROLLOUT-TEACHER",
        "reason": result.stop_reason,
        "fallback_reason": None,
        "delegated_reason": None,
        "complete": True,
        "teacher_status": result.status,
        "teacher_ambiguous": bool(result.ambiguous),
        "teacher_attempted_samples": result.attempted_samples,
        "teacher_valid_samples": result.sample_count,
        "teacher_failed_samples": result.failed_samples,
        "teacher_best_action": result.best_action,
        "teacher_runner_up": result.runner_up,
        "teacher_pairwise_racing_version": result.pairwise_racing_version,
        "teacher_bound_version": result.bound_version,
        "teacher_bound_mode": result.bound_mode,
        "teacher_nodes": None,
        "teacher_kernel_calls": None,
    }


def _run_shape_v2(view, seat, config):
    context = _public_context(view, seat)
    profile = ProfileSpec.shape_v2_all_root(
        calibrated=True, explanation=False,
        # The all-root transaction must retain a comparable value for every
        # ordinary discard before it can rank HU/KONG/reaction actions.  The
        # production discard evaluator may safely prune by a derived upper
        # bound, but a pruned candidate has no value for this mixed root.  An
        # unknown-bound profile disables that optimization without selecting
        # a substitute action or invoking a policy fallback.
        bound_mode="unknown",
        node_budget=config["shape_v2_node_budget"],
        time_budget_ms=config["shape_v2_time_budget_ms"])
    result = evaluate_root_context(context, profile)
    data = result.as_json()
    if result.selected is None:
        raise RuntimeError(
            "shape-v2 produced no action: "
            f"phase={view.phase}; legal={view.legal_actions()}; "
            f"reason={result.reason}; delegated={result.delegated_reason}")
    meta = {
        "level": data.get("level"),
        "reason": data.get("reason"),
        "fallback_reason": None,
        "delegated_reason": data.get("delegated_reason"),
        "complete": data.get("complete"),
        "nodes": data.get("nodes"),
        "kernel_calls": data.get("kernel_calls"),
        "bound_version": data.get("bound_version"),
        "bound_mode": data.get("bound_mode"),
    }
    return int(result.selected), meta


def _run_shape_v1(view, seat, config):
    profile = EvalProfile.shape_v1(
        discard_node_budget=config["shape_v1_node_budget"],
        react_node_budget=config["shape_v1_react_node_budget"],
        discard_time_budget_ms=config["shape_v1_time_budget_ms"],
        react_time_budget_ms=config["shape_v1_react_time_budget_ms"],
        explanation=False)
    actions = tuple(view.legal_actions())
    if len(actions) == 1:
        return actions[0], {
            "level": "forced",
            "reason": "only_legal_action",
            "fallback_reason": None,
            "delegated_reason": None,
            "complete": True,
            "nodes": 0,
            "kernel_calls": 0,
        }
    if view.phase == "discard":
        # Shape-v1's HU/KONG branch is an explicit part of that evaluator's
        # current policy; it is not repaired by this harness.
        if HU in actions or any(action <= -7 for action in actions):
            action, evaluation = choose_action(
                view, seat, evaluator="shape-v1", return_evaluation=True)
        else:
            action, evaluation = evaluate_discard_candidates(
                view, seat, profile=profile)
    else:
        action, evaluation = evaluate_reaction(view, seat, profile=profile)
    meta, data = _evaluation_meta(evaluation)
    meta["profile"] = data.get("profile", data.get("version", "shape-v1"))
    return int(action), meta


def _run_one_policy(kind, view, seat, config):
    if kind == "legacy":
        return int(choose_action(view, seat, evaluator="legacy")), {
            "level": "legacy", "reason": "legacy_evaluator",
            "fallback_reason": None, "delegated_reason": None,
            "complete": True, "nodes": 0, "kernel_calls": 0,
        }
    if kind == "shape-v1":
        return _run_shape_v1(view, seat, config)
    if kind == "shape-v2":
        return _run_shape_v2(view, seat, config)
    if kind == "rollout-teacher":
        return _run_rollout_teacher(view, seat, config)
    raise ValueError(f"unknown bot type: {kind}")


def _percentile(values, p):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * p))]


def _git_revision():
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True).strip()
    except Exception:
        return None


def _assignment(index):
    """Return seat -> BOT mapping and dealer for a balanced 16-game cycle."""
    shift = int(index) % 4
    mapping = {
        (type_index + shift) % 4: bot_type
        for type_index, bot_type in enumerate(BOT_TYPES)
    }
    dealer = (int(index) // 4) % 4
    return mapping, dealer


def _new_bot_stats():
    return {
        "games": 0, "valid_games": 0, "wins": 0, "draws": 0,
        "scores": [], "decision_ms": [], "decisions": 0,
        "nodes": [], "kernel_calls": [],
        "fallbacks": Counter(), "delegations": Counter(),
        "native_legacy_levels": Counter(),
        "levels": Counter(), "errors": Counter(),
        "hidden_access": 0,
    }


def _finish_stats(stats):
    scores = stats["scores"]
    timings = stats["decision_ms"]
    return {
        "games": stats["games"],
        "valid_games": stats["valid_games"],
        "wins": stats["wins"],
        "win_rate": stats["wins"] / stats["valid_games"]
        if stats["valid_games"] else None,
        "draws": stats["draws"],
        "score_total": sum(scores) if scores else 0,
        "score_mean": statistics.fmean(scores) if scores else None,
        "score_median": statistics.median(scores) if scores else None,
        "score_p05": _percentile(scores, 0.05),
        "score_p95": _percentile(scores, 0.95),
        "decisions": stats["decisions"],
        "decision_ms_total": sum(timings) if timings else 0.0,
        "decision_ms_mean": statistics.fmean(timings) if timings else None,
        "decision_ms_p50": _percentile(timings, 0.50),
        "decision_ms_p95": _percentile(timings, 0.95),
        "decision_ms_p99": _percentile(timings, 0.99),
        "decision_ms_max": max(timings) if timings else None,
        "nodes_mean": statistics.fmean(stats["nodes"])
        if stats["nodes"] else None,
        "kernel_calls_total": sum(stats["kernel_calls"]),
        "fallbacks": dict(sorted(stats["fallbacks"].items())),
        "delegations": dict(sorted(stats["delegations"].items())),
        "native_legacy_levels": dict(sorted(
            stats["native_legacy_levels"].items())),
        "levels": dict(sorted(stats["levels"].items())),
        "errors": dict(sorted(stats["errors"].items())),
        "hidden_access": stats["hidden_access"],
    }


def run(games=500, seed_start=2026091600, *, you_cai_bi_kao=False,
        output=None, config=None, stop_on_error=False):
    config = dict(config or {})
    defaults = {
        # The teacher is deliberately fixed and visible in the report.  Its
        # continuation is explicitly legacy, not a fallback chosen by this
        # harness, and its root action is still selected by PairedTeacher.
        "teacher_n0": 1, "teacher_batch": 1, "teacher_nmax": 1,
        "teacher_alpha": 0.05,
        "teacher_node_budget": 100000, "teacher_time_budget_ms": 5000.0,
        # These budgets are only used by the benchmark adapter.  The ordinary
        # production CLI continues to use its own profile defaults.
        "shape_v1_node_budget": 200000,
        "shape_v1_react_node_budget": 100000,
        "shape_v1_time_budget_ms": 2000.0,
        "shape_v1_react_time_budget_ms": 1000.0,
        "shape_v2_node_budget": 200000,
        "shape_v2_time_budget_ms": 2000.0,
    }
    defaults.update(config)
    stats = {kind: _new_bot_stats() for kind in BOT_TYPES}
    rows = []
    started = time.perf_counter()
    for index in range(int(games)):
        mapping, dealer = _assignment(index)
        seed = int(seed_start) + index
        game = Game(seed=seed, dealer=dealer,
                    you_cai_bi_kao=bool(you_cai_bi_kao))
        per_game = {
            "index": index, "seed": seed, "dealer": dealer,
            "seat_bots": {str(seat): mapping[seat] for seat in range(4)},
            "status": "ok", "decisions": {kind: 0 for kind in BOT_TYPES},
            "decision_ms": {kind: 0.0 for kind in BOT_TYPES},
            "fallbacks": {kind: [] for kind in BOT_TYPES},
            "delegations": {kind: [] for kind in BOT_TYPES},
            "native_legacy_levels": {kind: [] for kind in BOT_TYPES},
            "hidden_access": {kind: 0 for kind in BOT_TYPES},
        }
        try:
            while not game.done:
                seat = int(game.current_seat())
                kind = mapping[seat]
                legal = tuple(game.legal_actions())
                view = SeatView(game, seat, legal)
                started_decision = time.perf_counter()
                try:
                    action, meta = _run_one_policy(kind, view, seat, defaults)
                except HiddenStateAccess:
                    stats[kind]["hidden_access"] += 1
                    per_game["hidden_access"][kind] += 1
                    raise
                elapsed_ms = (time.perf_counter() - started_decision) * 1000.0
                if action not in legal:
                    raise RuntimeError(
                        f"illegal action {action}; legal={legal}; "
                        f"kind={kind}; seed={seed}; seat={seat}")
                game.step(action)
                timing = stats[kind]
                timing["decisions"] += 1
                timing["decision_ms"].append(elapsed_ms)
                timing["levels"][str(meta.get("level"))] += 1
                per_game["decisions"][kind] += 1
                per_game["decision_ms"][kind] += elapsed_ms
                fallback = meta.get("fallback_reason")
                delegated = meta.get("delegated_reason")
                if fallback:
                    timing["fallbacks"][str(fallback)] += 1
                    per_game["fallbacks"][kind].append(str(fallback))
                if delegated:
                    timing["delegations"][str(delegated)] += 1
                    per_game["delegations"][kind].append(str(delegated))
                if kind != "legacy" and meta.get("level") == "legacy":
                    native_reason = str(meta.get("reason") or "legacy_level")
                    timing["native_legacy_levels"][native_reason] += 1
                    per_game["native_legacy_levels"][kind].append(
                        native_reason)
                if meta.get("nodes") is not None:
                    try:
                        timing["nodes"].append(int(meta["nodes"]))
                    except (TypeError, ValueError):
                        pass
                if meta.get("kernel_calls") is not None:
                    try:
                        timing["kernel_calls"].append(int(meta["kernel_calls"]))
                    except (TypeError, ValueError):
                        pass
        except Exception as exc:
            per_game["status"] = "invalid"
            per_game["error"] = f"{type(exc).__name__}: {exc}"
            kind = mapping.get(int(game.current_seat()), "unknown")
            stats[kind]["errors"][f"{type(exc).__name__}: {exc}"] += 1
            if stop_on_error:
                raise
        if per_game["status"] == "ok":
            for seat, kind in mapping.items():
                stats[kind]["games"] += 1
                stats[kind]["valid_games"] += 1
                stats[kind]["scores"].append(float(game.scores[seat]))
            if game.result is None:
                for kind in mapping.values():
                    stats[kind]["draws"] += 1
            else:
                winner = int(game.result[0])
                stats[mapping[winner]]["wins"] += 1
            per_game["scores"] = {
                kind: float(game.scores[seat])
                for seat, kind in mapping.items()
            }
            per_game["result"] = {
                "winner": (mapping[int(game.result[0])]
                           if game.result is not None else None),
                "multiplier": (game.result[1]
                                if game.result is not None else None),
                "draw": game.result is None,
            }
        else:
            # An invalid game is retained in raw output but contributes no
            # score.  No seat receives a synthetic zero reward.
            for kind in mapping.values():
                stats[kind]["games"] += 1
        rows.append(per_game)
    result = {
        "schema": "bot-local-mixed-score-performance-v1",
        "workload": {
            "games_requested": int(games), "seed_start": int(seed_start),
            "you_cai_bi_kao": bool(you_cai_bi_kao),
            "bot_types": list(BOT_TYPES),
            "mixed_seats_per_game": True,
            "no_harness_fallback": True,
        },
        "runtime": {
            "git_revision": _git_revision(), "python": sys.version,
            "platform": platform.platform(), "machine": platform.machine(),
            "reward_envelope_version": REWARD_ENVELOPE_VERSION,
        },
        "config": defaults,
        "elapsed_s": time.perf_counter() - started,
        "valid_games": sum(1 for row in rows if row["status"] == "ok"),
        "invalid_games": sum(1 for row in rows if row["status"] != "ok"),
        "summary": {kind: _finish_stats(stats[kind]) for kind in BOT_TYPES},
        "rows": rows,
    }
    if output:
        output = os.path.abspath(output)
        output_dir = os.path.dirname(output)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
        with open(output, "w", encoding="utf-8") as handle:
            json.dump(result, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="本地四类 BOT 对局基线")
    parser.add_argument("--games", type=int, default=500)
    parser.add_argument("--seed-start", type=int, default=2026091600)
    parser.add_argument("--you-cai-bi-kao", action="store_true")
    parser.add_argument(
        "--output",
        default="openspec/changes/bot-ev-tight-bound-racing/artifacts/"
                "bot_local_mixed_20260916.json")
    parser.add_argument("--stop-on-error", action="store_true")
    args = parser.parse_args(argv)
    value = run(args.games, args.seed_start,
                you_cai_bi_kao=args.you_cai_bi_kao,
                output=args.output, stop_on_error=args.stop_on_error)
    print(json.dumps({
        "schema": value["schema"],
        "git_revision": value["runtime"]["git_revision"],
        "games_requested": value["workload"]["games_requested"],
        "valid_games": value["valid_games"],
        "invalid_games": value["invalid_games"],
        "elapsed_s": value["elapsed_s"],
        "summary": value["summary"],
        "output": os.path.abspath(args.output),
    }, ensure_ascii=False, indent=2))
    return 0 if value["invalid_games"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
