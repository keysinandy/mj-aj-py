"""Summarize one recorded room without contacting the server or changing logs.

Usage: python3 scripts/window_acceptance.py ROOM [--out local/report.json]
Exit 0 means no detected replay/legal errors, not complete window acceptance.
"""

import argparse
from collections import Counter, defaultdict
import json
import math
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mj.log_replay import replay_game
from mj.platform.bot_client import BotClient
from mj.platform.mirror import MirrorInconsistent


def distribution(values):
    values = sorted(values)
    if not values:
        return {"n": 0, "p50": None, "p95": None, "max": None}

    def percentile(p):
        index = (len(values) - 1) * p
        lo, hi = math.floor(index), math.ceil(index)
        return round(values[lo] + (values[hi] - values[lo]) * (index - lo), 1)

    return {"n": len(values), "p50": percentile(.5),
            "p95": percentile(.95), "max": round(values[-1], 1)}


def summarize(paths):
    types, actions, misses, confirms, transport = (Counter() for _ in range(5))
    requests = []
    games = []
    audit_totals = Counter()
    boundary_recovered = 0
    for path in paths:
        with path.open(encoding="utf8") as stream:
            records = [json.loads(line) for line in stream if line.strip()]
        audit = Counter()
        bot = BotClient(None, "audit", None)
        mirror = None
        cursor = None
        decision_keys, successful_keys, boundary_keys = {}, set(), []
        rounds = set()
        for row in records:
            kind = row["type"]
            types[kind] += 1
            if kind == "meta":
                bot.base = row.get("base", 1)
                bot.you_cai_bi_kao = bool(row.get("you_cai_bi_kao"))
            elif kind == "snapshot":
                mirror = bot._mirror_from_snapshot(row["snap"])
                cursor = row.get("seq")
                rounds.add(mirror.round_no)
            elif kind == "events":
                cursor = row.get("seq_to")
                for event in row["events"]:
                    audit["events_recorded"] += 1
                    if event.get("type") == "round_ended":
                        audit["round_ended_recorded"] += 1
                    if mirror is not None:
                        try:
                            mirror.apply_event(event)
                        except MirrorInconsistent:
                            audit["mirror_errors"] += 1
                            mirror = None
            elif kind == "decision":
                audit["decisions_recorded"] += 1
                if mirror is None:
                    audit["decisions_without_mirror"] += 1
                    continue
                # Historical mirror discard counts may differ after reanchor.
                # Same authoritative seq plus round/pending is stronger evidence
                # for these immediate recoveries; do not merge later same tiles.
                decision_keys[row["id"]] = (
                    row["phase"], mirror.round_no, mirror.pending, row.get("seq"))
                try:
                    legal = sorted(mirror.build_game(row["phase"]).legal_actions())
                except MirrorInconsistent:
                    audit["decisions_unbuildable"] += 1
                else:
                    audit["decisions_checked"] += 1
                    if legal != row["legal"]:
                        audit["legal_mismatches"] += 1
            elif kind == "reset":
                mirror = None
            elif kind == "req":
                requests.append(row)
                for name in ("retry_429", "retry_gateway", "retry_network"):
                    transport[name] += row.get("transport", {}).get(name, 0)
                meta = row.get("transport", {})
                transport["physical_attempts"] += meta.get(
                    "state_physical_attempts", row.get("attempts") or 1)
            elif kind == "action":
                payload = row.get("payload", {}).get("action", "unknown")
                actions[f'{row.get("phase")}/{payload}/{"ok" if row.get("ok") else "failed"}'] += 1
                if row.get("ok") and row.get("decision") in decision_keys:
                    successful_keys.add(decision_keys[row["decision"]])
            elif kind == "claim_miss":
                misses[f'{row.get("phase")}/{row.get("reason")}'] += 1
                if row.get("reason") == "decision_boundary_resync" and mirror is not None:
                    boundary_keys.append((row["phase"], mirror.round_no,
                                          mirror.pending, row.get("seq", cursor)))
            elif kind == "window_confirm":
                confirms[row.get("outcome", row.get("stage", "unknown"))] += 1
        boundary_recovered += sum(key in successful_keys for key in boundary_keys)
        audit_totals.update(audit)
        replay = replay_game(records, want_samples=False)
        games.append({"file": str(path), "end": next(
            (r.get("reason") for r in reversed(records) if r["type"] == "end"), None),
            "rounds_in_snapshots": sorted(rounds), "coverage": dict(audit),
            "replay_illegal": len(replay["illegal"]),
            "replay_warnings": replay["warnings"],
            "replay_rounds_settled": replay["n_rounds"],
            "replay_clean": replay["clean"]})

    def request_group(rows):
        return {"requests": len(rows),
                "queue_ms": distribution([r.get("throttle", {}).get("queue_wait_ms", 0)
                                          for r in rows]),
                "latency_ms": distribution([r.get("latency_ms", 0) for r in rows]),
                "retry_429": sum(r.get("transport", {}).get("retry_429", 0) for r in rows),
                "deadline_missed": sum(bool(r.get("throttle", {}).get("deadline_missed"))
                                       for r in rows)}

    by_kind = defaultdict(list)
    physical = []
    for row in requests:
        by_kind[row.get("request_kind", "UNKNOWN_LEGACY")].append(row)
        physical.extend(row.get("transport", {}).get("state_attempts", []))
    starts = sorted(item["started_epoch"] for item in physical
                    if item.get("started_epoch") is not None)
    left, peak = 0, 0
    for right, stamp in enumerate(starts):
        while starts[left] <= stamp - 1.0:
            left += 1
        peak = max(peak, right - left + 1)
    return {
        "files": len(paths), "record_counts": dict(types), "actions": dict(actions),
        "claim_miss_records": dict(misses), "window_confirm_records": dict(confirms),
        "legacy_boundary_misses_later_succeeded": boundary_recovered,
        "transport": dict(transport),
        "physical_state_evidence": {
            "attempts_logged": len(physical),
            "status": dict(Counter(str(item.get("status")) for item in physical)),
            "max_starts_per_rolling_second": peak if starts else None,
            "note": "Client start times; not server arrival times. Legacy logs lack these fields.",
        },
        "request_status": dict(Counter(r.get("status") for r in requests)),
        "state_all": request_group(requests),
        "state_urgent": request_group([r for r in requests
                                        if r.get("throttle", {}).get("urgent")]),
        "state_by_kind": {k: request_group(v) for k, v in sorted(by_kind.items())},
        "coverage": dict(audit_totals), "games": games,
        "limitations": [
            "Only recorded decisions and observable windows can be verified.",
            "Snapshot gaps can hide opportunities; zero misses is not proof of zero loss.",
            "Settlement coverage is limited to recorded round_ended events.",
            "Different deals and network conditions prevent causal claims from one room.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("room")
    parser.add_argument("--root", type=Path, default=Path("local/games"))
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    paths = sorted(args.root.glob(f"**/*_{args.room}_r*_b*.jsonl"))
    if not paths:
        parser.error(f"No logs for room {args.room}")
    report = summarize(paths)
    result = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(result + "\n", encoding="utf8")
    else:
        print(result)
    return int(any(g["replay_illegal"] or g["coverage"].get("legal_mismatches")
                   for g in report["games"]))


if __name__ == "__main__":
    sys.exit(main())
