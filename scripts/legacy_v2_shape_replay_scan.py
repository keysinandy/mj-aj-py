#!/usr/bin/env python3
"""Scan a deterministic sample of local replay decisions with public state only.

The recorder's Mirror reconstructs only the observer's concealed hand, public
melds/discards, visible material, and live-wall size. Opponent concealed hands
are zero-filled and the wall order is unknown. Each discard is evaluated on
fresh baseline/candidate Game copies because evaluators may mutate their input.
"""

from __future__ import annotations

import argparse
from collections import Counter
import copy
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mj.bot import choose_action
from mj.log_replay import replay_game
from mj.logview import load_records
from mj.platform.proto import tname
from scripts.legacy_v2_shape_score_eval import _evaluation_payload, _signature_delta


BASELINE = "legacy-v2-baseline"
CANDIDATE = "legacy-v2-shape-phase-b"


def _sample_paths(root: Path, limit: int) -> tuple[list[Path], int]:
    paths = sorted(root.rglob("*.jsonl"))
    if limit <= 0 or len(paths) <= limit:
        return paths, len(paths)
    if limit == 1:
        return [paths[len(paths) // 2]], len(paths)
    indices = sorted({round(i * (len(paths) - 1) / (limit - 1))
                      for i in range(limit)})
    return [paths[i] for i in indices], len(paths)


def _tile_name(action):
    try:
        return tname(int(action))
    except (TypeError, ValueError):
        return str(action)


def scan(paths: list[Path], *, root: Path) -> dict:
    result = {
        "files_sampled": len(paths),
        "files_with_public_discard_states": 0,
        "files_with_replay_warnings": 0,
        "files_with_illegal_replay": 0,
        "files_with_scan_exceptions": 0,
        "discard_decision_states_seen": 0,
        "paired_discard_evaluations": 0,
        "decision_evaluation_exceptions": Counter(),
        "baseline_candidate_divergences": 0,
        "shape_changed_winner_decisions": 0,
        "shape_flag_action_mismatches": 0,
        "shape_flag_action_mismatch_examples": [],
        "scope": Counter(),
        "changed_by_scope": Counter(),
        "shape_stage": Counter(),
        "changed_by_shape_stage": Counter(),
        "taatsu_upgrade": Counter(),
        "shanten": Counter(),
        "wall_left": Counter(),
        "open_meld_count": Counter(),
        "action_pairs": Counter(),
        "fallback_reasons": Counter(),
        "replay_errors": Counter(),
    }

    for path_index, path in enumerate(paths, start=1):
        hook_seen = [0]
        try:
            records = load_records(str(path))

            def decision_hook(game, seat, record):
                # Mirror.build_game maps protocol phase "draw" to engine phase
                # "discard". Reaction windows are deliberately not included.
                if game.phase != "discard":
                    return
                hook_seen[0] += 1
                result["discard_decision_states_seen"] += 1
                baseline_game = copy.deepcopy(game)
                candidate_game = copy.deepcopy(game)
                for other in range(4):
                    if other != seat:
                        baseline_game.hands[other] = [0] * 34
                        candidate_game.hands[other] = [0] * 34
                try:
                    baseline_action, _ = _evaluation_payload(choose_action(
                        baseline_game, seat, evaluator=BASELINE,
                        return_evaluation=True))
                    candidate_action, evaluation = _evaluation_payload(choose_action(
                        candidate_game, seat, evaluator=CANDIDATE,
                        return_evaluation=True))
                except Exception as exc:
                    result["decision_evaluation_exceptions"][
                        type(exc).__name__] += 1
                    return
                result["paired_discard_evaluations"] += 1
                scope = str(evaluation.get("decision_scope", "legacy"))
                shape_stage = str(evaluation.get(
                    "shape_quality_stage") or "none")
                result["scope"][scope] += 1
                result["shape_stage"][shape_stage] += 1
                if evaluation.get("fallback_reason"):
                    result["fallback_reasons"][str(
                        evaluation["fallback_reason"])] += 1
                changed = baseline_action != candidate_action
                if bool(evaluation.get("shape_changed_winner")):
                    result["shape_changed_winner_decisions"] += 1
                if changed:
                    result["baseline_candidate_divergences"] += 1
                    result["changed_by_scope"][scope] += 1
                    result["changed_by_shape_stage"][shape_stage] += 1
                    if not evaluation.get("shape_changed_winner"):
                        result["shape_flag_action_mismatches"] += 1
                    result["action_pairs"][
                        f"{_tile_name(baseline_action)}->{_tile_name(candidate_action)}"] += 1
                    if (not evaluation.get("shape_changed_winner") and
                            len(result["shape_flag_action_mismatch_examples"]) < 12):
                        result["shape_flag_action_mismatch_examples"].append({
                            "source_path": str(path.relative_to(root)),
                            "seq": record.get("seq"),
                            "baseline_action": _tile_name(baseline_action),
                            "candidate_action": _tile_name(candidate_action),
                            "decision_scope": scope,
                            "shape_quality_stage": shape_stage,
                            "shape_quality_used": evaluation.get(
                                "shape_quality_used"),
                            "selected": evaluation.get("selected"),
                            "legacy_best": evaluation.get("legacy_best"),
                            "speed_winner": evaluation.get("speed_winner"),
                            "shape_baseline_selected": evaluation.get(
                                "shape_baseline_selected"),
                            "stage_b_entered": evaluation.get(
                                "stage_b_entered"),
                            "fallback_reason": evaluation.get(
                                "fallback_reason"),
                            "frontier_guard": evaluation.get(
                                "frontier_guard"),
                        })
                    result["taatsu_upgrade"][_signature_delta(evaluation)] += 1
                    candidates = evaluation.get("candidates") or ()
                    selected = evaluation.get("selected")
                    row = next((item for item in candidates
                                if item.get("tile") == selected), {})
                    if row.get("shanten") is not None:
                        result["shanten"][str(row["shanten"])] += 1
                    result["open_meld_count"][str(
                        len(game.melds[seat]))] += 1
                    try:
                        wall = int(game.live_wall_left())
                        bucket = ("0-10" if wall <= 10 else
                                  "11-20" if wall <= 20 else
                                  "21-40" if wall <= 40 else "41+")
                        result["wall_left"][bucket] += 1
                    except (AttributeError, TypeError, ValueError):
                        result["wall_left"]["unknown"] += 1

            replay = replay_game(records, want_samples=False,
                                 decision_hook=decision_hook)
            if hook_seen[0]:
                result["files_with_public_discard_states"] += 1
            if replay["illegal"]:
                result["files_with_illegal_replay"] += 1
                for issue in replay["illegal"]:
                    result["replay_errors"]["illegal"] += 1
            if replay["warnings"]:
                result["files_with_replay_warnings"] += 1
                for issue in replay["warnings"]:
                    result["replay_errors"]["warning"] += 1
        except Exception as exc:  # retain a usable scan if one old log is malformed
            result["files_with_scan_exceptions"] += 1
            result["replay_errors"][type(exc).__name__] += 1
        if path_index % 25 == 0 or path_index == len(paths):
            print(f"replay files {path_index}/{len(paths)}", file=sys.stderr,
                  flush=True)

    decisions = result["paired_discard_evaluations"]
    result["shape_changed_action_rate"] = (
        result["baseline_candidate_divergences"] / decisions
        if decisions else None)
    for name in ("scope", "changed_by_scope", "shape_stage",
                 "changed_by_shape_stage", "taatsu_upgrade", "shanten",
                 "wall_left", "open_meld_count", "action_pairs",
                 "fallback_reasons", "replay_errors",
                 "decision_evaluation_exceptions"):
        result[name] = dict(sorted(result[name].items()))
    result["action_pairs_top20"] = dict(Counter(result["action_pairs"]).most_common(20))
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("local/games"))
    parser.add_argument("--max-files", type=int, default=200,
                        help="deterministic evenly spaced sample; <=0 scans all")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    paths, total = _sample_paths(args.root, args.max_files)
    details = scan(paths, root=args.root)
    payload = {
        "schema": "legacy-v2-shape-aware-two-ply/historical-replay-scan-v1",
        "source_root": str(args.root),
        "source_files_available": total,
        "sampling": "sorted paths with evenly spaced deterministic indices",
        "sampled_paths": [str(path) for path in paths],
        "baseline_evaluator": BASELINE,
        "candidate_evaluator": CANDIDATE,
        "public_state_only": True,
        "hidden_opponent_hands_used": False,
        "true_wall_order_used": False,
        "divergence_definition": "candidate action differs from baseline action at a replayed public discard decision",
        "result": details,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({key: details[key] for key in (
        "files_sampled", "files_with_public_discard_states",
        "files_with_replay_warnings", "files_with_illegal_replay",
        "files_with_scan_exceptions",
        "discard_decision_states_seen", "paired_discard_evaluations",
        "baseline_candidate_divergences", "shape_changed_action_rate",
        "shape_changed_winner_decisions", "scope", "changed_by_scope",
        "shape_stage", "changed_by_shape_stage",
        "taatsu_upgrade", "replay_errors")}, ensure_ascii=False, indent=2))
    print(f"artifact: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
