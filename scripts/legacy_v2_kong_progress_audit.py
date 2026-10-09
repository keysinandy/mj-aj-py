"""Observe production self-KONG rejections without changing played actions."""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
import hashlib
import json
from pathlib import Path
import time

from mj.game import Game, HU
from mj.legacy_eval import LegacyTwoPlyProfile
from mj.shanten import kernel_runtime_diagnostic
from scripts.legacy_v2_big_hand_grid_scan import _decide


def audit_match(task):
    index, seed = task
    game = Game(seed=seed, dealer=index % 4)
    counts, fixtures = Counter(), []
    profile = LegacyTwoPlyProfile.weighted_online()
    while not game.done:
        seat, phase = game.current_seat(), game.phase
        legal = tuple(game.legal_actions())
        action, info = _decide(game, seat, profile)
        if phase == "discard" and HU not in legal and isinstance(info, dict):
            for row in info.get("kong_candidates", ()):
                reason = row.get("rejection_reason") or "passed"
                counts[f"{row['kind']}:{reason}"] += 1
                # Closed KONG consumes the same triplet as the frozen discard
                # only when that discard was the redundant fourth tile.
                eligible = (row["kind"] in {"closed", "add"} and row["tile"] == action
                            and row.get("structure_safe")
                            and row.get("shape_preserved")
                            and reason == "post_kong_not_tenpai"
                            and game.live_wall_left() >= 16)
                if eligible:
                    counts[f"redundant_{row['kind']}_candidates"] += 1
                    counts[f"shanten_{row['post_kong_progress']['shanten']}"] += 1
                    fixtures.append({"seed": seed, "seat": seat, "dealer": game.dealer,
                                     "hand": list(game.hands[seat]), "melds": game.melds[seat],
                                     "visible": game.visible_counts(seat),
                                     "wall_left": game.live_wall_left(), "baseline": action,
                                     "legal": list(legal), "candidate": row})
        if action not in legal:
            raise AssertionError("illegal baseline action")
        game.step(action)
    return {"counts": dict(counts), "fixtures": fixtures[:8]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--games", type=int, default=256)
    parser.add_argument("--seed-start", type=int, default=3200000)
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    if args.games <= 0 or args.jobs <= 0:
        parser.error("games and jobs must be positive")
    started = time.perf_counter()
    counts, fixtures = Counter(), []
    with ProcessPoolExecutor(max_workers=args.jobs) as pool:
        for index, result in enumerate(pool.map(audit_match,
                [(i, args.seed_start+i) for i in range(args.games)], chunksize=4)):
            counts.update(result["counts"])
            fixtures.extend(result["fixtures"])
            if (index+1) % 64 == 0:
                print(f"audit {index+1}/{args.games}: {dict(counts)}", flush=True)
    root = Path(__file__).resolve().parents[1]
    payload = {"schema": "self-kong-progress-audit-v1", "games": args.games,
               "seed_start": args.seed_start, "counts": dict(counts),
               "fixtures": fixtures[:32], "elapsed_s": time.perf_counter()-started,
               "kernel": kernel_runtime_diagnostic(),
               "source_sha256": {path: hashlib.sha256((root/path).read_bytes()).hexdigest()
                                 for path in ("mj/bot.py", "mj/legacy_kong.py",
                                              "scripts/legacy_v2_kong_progress_audit.py")}}
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in payload.items() if key != "fixtures"}))


if __name__ == "__main__":
    main()
