#!/usr/bin/env python3
"""Audit replay reaction decisions across legacy v1/v2 and shape-v2 teacher."""

from __future__ import annotations

import argparse
from collections import Counter
import glob
import json
import os
from pathlib import Path
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from mj import bot
from mj.game import CHOW_HIGH, CHOW_LOW, CHOW_MID, KONG_OPEN, PONG
from mj.legacy_react import LegacyReactionProfile
from mj.log_replay import replay_game
from mj.logview import load_records


_ACTION_KIND = {
    CHOW_LOW: "CHOW",
    CHOW_MID: "CHOW",
    CHOW_HIGH: "CHOW",
    PONG: "PONG",
    KONG_OPEN: "KONG_OPEN",
}


def _candidate_metrics(evaluation):
    rows = []
    for candidate in evaluation.get("candidates") or []:
        future = candidate.get("future") or {}
        progress = candidate.get("progress") or {}
        rows.append({
            "action": candidate.get("action"),
            "accepted": candidate.get("accepted"),
            "v2_accepted": candidate.get("v2_accepted"),
            "reason": candidate.get("reason"),
            "v2_reason": candidate.get("v2_reason"),
            "shanten": progress.get("shanten"),
            "ukeire_live": progress.get("ukeire_live"),
            "future_improve_weight": future.get("future_improve_weight"),
            "future_ukeire_mean": future.get("future_ukeire_mean"),
            "special": {
                "baotou_ready": progress.get("baotou_ready"),
                "baotou_ukeire_live": progress.get("baotou_ukeire_live"),
                "piao_draw_live": progress.get("piao_draw_live"),
            },
        })
    return rows


def _public_reaction_context(game, seat):
    """Return only public inputs needed to freeze a reaction decision.

    In particular, do not serialize other seats' concealed hands or the wall
    order from the replay Game. ``visible`` is the hero-visible count vector;
    wall_left is only the public live-wall count.
    """
    owner, tile = game.pending
    return {
        "seat": int(seat),
        "pending_owner": int(owner),
        "pending_tile": int(tile),
        "hand": [int(value) for value in game.hands[seat]],
        "visible": [int(value) for value in game.visible_counts(seat)],
        "melds": [
            [[str(kind), int(tile)] for kind, tile in melds]
            for melds in game.melds
        ],
        "discards": [[int(tile) for tile in river]
                     for river in game.discards],
        "wall_left": max(0, int(game.live_wall_left())),
        "dealer": int(getattr(game, "dealer", 0)),
        "base": int(getattr(game, "base", 1)),
        "you_cai_bi_kao": bool(
            getattr(game, "you_cai_bi_kao", False)),
        "chain": [int(value) for value in getattr(game, "chain", [0] * 4)],
        "chain_piao": [
            int(value) for value in getattr(game, "chain_piao", [0] * 4)],
        "freeze": int(getattr(game, "freeze", 0)),
        "freezer": getattr(game, "freezer", None),
        "chows": [int(value) for value in getattr(game, "chows", [0] * 4)],
        "react_idx": int(getattr(game, "react_idx", 0)),
        "n_claim": int(getattr(game, "_n_claim", 1)),
        "legal_actions": [int(action) for action in game.legal_actions()],
    }


def _regression_categories(row):
    """Categorize observable v1/v2 divergences from audit-row diagnostics."""
    categories = set()
    v1_action = row.get("legacy_v1_action")
    v2_action = row.get("legacy_v2_action")
    if v1_action != v2_action and v1_action != -1 and v2_action == -1:
        categories.add("u1_good_u2_bad")
    if ((row.get("tempo_cost") or 0) >= 2
            and v1_action != -1 and v2_action == -1
            and any(candidate.get("v2_reason")
                    == "tempo_no_strict_future_gain"
                    for candidate in row.get("candidates") or [])):
        categories.add("high_tempo_no_gain")
    if {v1_action, v2_action} == {PONG, KONG_OPEN}:
        categories.add("pong_kong_flip")
    return categories


def _percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    return ordered[min(len(ordered) - 1,
                       int((len(ordered) - 1) * fraction))]


def _latency_summary(values):
    return {
        "n": len(values),
        "p50_ms": _percentile(values, 0.50),
        "p95_ms": _percentile(values, 0.95),
        "p99_ms": _percentile(values, 0.99),
        "mean_ms": statistics.fmean(values) if values else None,
        "max_ms": max(values) if values else None,
    }


def audit_paths(paths, *, limit=1000, include_teacher=True,
                only_pong_kong=False):
    rows = []
    skipped = Counter()
    active_profile = LegacyReactionProfile.v2_online(enabled=True)

    def inspect(game, seat, record, source_path=None):
        if len(rows) >= limit or game.phase != "react":
            return
        legal = game.legal_actions()
        if (only_pong_kong
                and not (PONG in legal and KONG_OPEN in legal)):
            return
        if not any(action in legal for action in _ACTION_KIND):
            return
        v1_action, v1 = bot._choose_react_evaluated(
            game, seat, legal, return_evaluation=True)
        try:
            v2_action, v2 = bot._choose_react_evaluated(
                game, seat, legal, return_evaluation=True,
                reaction_profile=active_profile)
        except Exception as exc:  # audit records failure; production does not.
            v2_action, v2 = None, {"u2_fallback_reason": type(exc).__name__}
        teacher_action = None
        teacher = None
        if include_teacher:
            try:
                teacher_action, teacher = bot.choose_shape_v2_action(game, seat)
            except Exception as exc:
                teacher = {"complete": False,
                           "fallback_reason": type(exc).__name__}
        kind = _ACTION_KIND.get(v1_action) or _ACTION_KIND.get(v2_action) or "PASS"
        slow = v2.get("pong_kong_slow_path") or {}
        kong = v2.get("kong") or {}
        rows.append({
            "source": record.get("id"),
            "seq": record.get("seq"),
            "kind": kind,
            "legacy_v1_action": v1_action,
            "legacy_v2_action": v2_action,
            "teacher_action": teacher_action,
            "teacher_complete": bool((teacher or {}).get("complete", False)),
            "teacher_fallback_reason": (teacher or {}).get("fallback_reason"),
            "v1_reason": v1.get("reason"),
            "v2_reason": v2.get("reason"),
            "u2_fallback_reason": v2.get("u2_fallback_reason"),
            "u2_eligible": bool(v2.get("u2_eligible")),
            "u2_complete_or_safe_partial": bool(
                v2.get("u2_complete_or_safe_partial")),
            "u2_extra_elapsed_ms": v2.get("u2_extra_elapsed_ms"),
            "u2_coverage": v2.get("u2_coverage"),
            "u2_partial_accepted": bool(v2.get("u2_partial_accepted")),
            "u2_attempt_fallback_reasons": list(
                v2.get("u2_attempt_fallback_reasons") or ()),
            "pass_draw_index": v2.get("pass_draw_index"),
            "claim_draw_index": v2.get("claim_draw_index"),
            "tempo_cost": v2.get("tempo_cost"),
            "q_pong": slow.get("q_pong"),
            "q_kong": slow.get("q_kong"),
            "q_delta": slow.get("delta"),
            "kong_continuation_elapsed_ms": kong.get(
                "continuation_elapsed_ms"),
            "kong_continuation_complete": kong.get(
                "continuation_complete"),
            "kong_continuation_fallback_reason": kong.get(
                "continuation_fallback_reason"),
            "pong_kong_slow_path_elapsed_ms": slow.get("elapsed_ms"),
            "candidates": _candidate_metrics(v2),
            "_fixture_context": _public_reaction_context(game, seat),
            "_source_file": Path(source_path).name if source_path else None,
        })

    for path in paths:
        if len(rows) >= limit:
            break
        try:
            replay_game(
                load_records(path), want_samples=False,
                decision_hook=lambda game, seat, record, source_path=path:
                    inspect(game, seat, record, source_path))
        except (OSError, ValueError, KeyError) as exc:
            skipped[type(exc).__name__] += 1

    buckets = Counter()
    disagreements = Counter()
    teacher_eligible = Counter()
    regressions = {"u1_good_u2_bad": [], "high_tempo_no_gain": [],
                   "pong_kong_flip": []}
    for index, row in enumerate(rows):
        key = (row["kind"], row["tempo_cost"], row["v1_reason"])
        buckets[str(key)] += 1
        if row["teacher_complete"]:
            teacher_eligible[str(key)] += 1
        if row["legacy_v1_action"] != row["legacy_v2_action"]:
            disagreements[f"v1_v2:{key}"] += 1
        if (row["legacy_v2_action"] != row["teacher_action"]
                and row["teacher_complete"]):
            disagreements[f"v2_teacher:{key}"] += 1
        for category in _regression_categories(row):
            regressions[category].append(index)

    rates = {}
    for key, count in buckets.items():
        teacher_count = teacher_eligible[key]
        rates[key] = {
            "sample_count": count,
            "v1_v2_disagreement_rate": (
                disagreements[f"v1_v2:{key}"] / count if count else None),
            "teacher_complete_count": teacher_count,
            "v2_teacher_disagreement_rate": (
                disagreements[f"v2_teacher:{key}"] / teacher_count
                if teacher_count else None),
        }
    eligible = [row for row in rows if row["u2_eligible"]]
    covered = [row for row in eligible
               if row["u2_complete_or_safe_partial"]]
    u2_fallbacks = Counter(
        row["u2_fallback_reason"] or "none" for row in eligible)
    u2_attempt_fallbacks = Counter(
        reason for row in eligible
        for reason in row["u2_attempt_fallback_reasons"])
    kong_rows = [row for row in rows
                 if row["kong_continuation_elapsed_ms"] is not None]
    slow_rows = [row for row in rows
                 if row["pong_kong_slow_path_elapsed_ms"] is not None]
    regression_fixtures = {}
    for category, indices in regressions.items():
        regression_fixtures[category] = [
            {
                "source_file": rows[index]["_source_file"],
                "decision_id": rows[index]["source"],
                "seq": rows[index]["seq"],
                "kind": rows[index]["kind"],
                "legacy_v1_action": rows[index]["legacy_v1_action"],
                "legacy_v2_action": rows[index]["legacy_v2_action"],
                "v1_reason": rows[index]["v1_reason"],
                "v2_reason": rows[index]["v2_reason"],
                "tempo_cost": rows[index]["tempo_cost"],
                "q_pong": rows[index]["q_pong"],
                "q_kong": rows[index]["q_kong"],
                "candidates": rows[index]["candidates"],
                "public_context": rows[index]["_fixture_context"],
            }
            for index in indices[:20]
        ]
    output_rows = [
        {key: value for key, value in row.items()
         if not key.startswith("_")}
        for row in rows
    ]
    return {
        "schema": "legacy-reaction-audit-v1",
        "sample_count": len(rows),
        "requested_limit": limit,
        "window_filter": ("pong_and_kong_open" if only_pong_kong else "all"),
        "available_sample_shortfall": max(0, limit - len(rows)),
        "teacher_incomplete": sum(not row["teacher_complete"] for row in rows),
        "buckets": dict(buckets),
        "disagreements": dict(disagreements),
        "disagreement_rates": rates,
        "regression_indices": {key: value[:20]
                               for key, value in regressions.items()},
        "regression_fixtures": regression_fixtures,
        "performance": {
            "reaction_u2": {
                **_latency_summary([
                    row["u2_extra_elapsed_ms"] for row in eligible
                    if row["u2_extra_elapsed_ms"] is not None]),
                "eligible_count": len(eligible),
                "complete_or_safe_partial_count": len(covered),
                "complete_or_safe_partial_coverage": (
                    len(covered) / len(eligible) if eligible else None),
                "partial_accepted_count": sum(
                    row["u2_partial_accepted"] for row in eligible),
                "fallback_reasons": dict(u2_fallbacks),
                "attempt_fallback_reasons": dict(u2_attempt_fallbacks),
            },
            "kong_continuation": {
                **_latency_summary([
                    row["kong_continuation_elapsed_ms"]
                    for row in kong_rows]),
                "complete_count": sum(
                    row["kong_continuation_complete"] is True
                    for row in kong_rows),
                "fallback_reasons": dict(Counter(
                    row["kong_continuation_fallback_reason"] or "none"
                    for row in kong_rows)),
            },
            "pong_kong_slow_path": _latency_summary([
                row["pong_kong_slow_path_elapsed_ms"]
                for row in slow_rows]),
        },
        "skipped": dict(skipped),
        "rows": output_rows,
    }


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default="local/games")
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--output", required=True)
    parser.add_argument("--no-teacher", action="store_true")
    parser.add_argument(
        "--only-pong-kong", action="store_true",
        help="audit only response windows where both PONG and KONG_OPEN are legal")
    args = parser.parse_args(argv)
    paths = sorted(glob.glob(str(Path(args.root) / "**" / "*.jsonl"),
                             recursive=True))
    report = audit_paths(
        paths, limit=args.limit, include_teacher=not args.no_teacher,
        only_pong_kong=args.only_pong_kong)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(
        report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8")
    print(json.dumps({key: report[key] for key in (
        "sample_count", "requested_limit", "available_sample_shortfall",
        "teacher_incomplete")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
