"""Observe production HU delays and their public reward margins."""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
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
    profile = LegacyTwoPlyProfile.weighted_online()
    counts, fixtures = Counter(), []
    while not game.done:
        seat, phase = game.current_seat(), game.phase
        legal = tuple(game.legal_actions())
        action, info = _decide(game, seat, profile)
        if phase == "discard" and HU in legal:
            counts["hu_windows"] += 1
            rows = [{key: value for key, value in row.items() if key != "progress"}
                    for row in info.get("hu_window_candidates", ())]
            immediate = next((row for row in rows if row["action"] == HU), None)
            selected = next((row for row in rows if row["action"] == action), None)
            if immediate and selected and selected["type"] in {"piao_discard", "baotou_next_draw"}:
                ratio = selected["value"]/immediate["value"]
                counts["discard_delays"] += 1
                counts["wall_below_12"] += int(game.live_wall_left() < 12)
                counts["ratio_below_1_25"] += int(ratio < 1.25)
                counts["ratio_below_1_5"] += int(ratio < 1.5)
                counts["ratio_below_2"] += int(ratio < 2)
                fixtures.append({"seed": seed, "seat": seat, "dealer": game.dealer,
                    "hand": game.hands[seat][:], "melds": game.melds[seat],
                    "wall_left": game.live_wall_left(), "legal": list(legal),
                    "baseline": action, "ratio": ratio,
                    "max_opp_melds": max(len(game.melds[s]) for s in range(4) if s != seat),
                    "complete": info.get("complete"), "rows": rows})
        if action not in legal:
            raise AssertionError("illegal baseline action")
        game.step(action)
    return {"counts": dict(counts), "fixtures": fixtures[:8]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--games", type=int, default=512)
    parser.add_argument("--seed-start", type=int, default=4200000)
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    started = time.perf_counter()
    counts, fixtures = Counter(), []
    with ProcessPoolExecutor(max_workers=args.jobs) as pool:
        for index, result in enumerate(pool.map(audit_match,
                [(i, args.seed_start+i) for i in range(args.games)], chunksize=4)):
            counts.update(result["counts"])
            fixtures.extend(result["fixtures"])
            if (index+1) % 64 == 0:
                print(f"audit {index+1}/{args.games}: {dict(counts)}", flush=True)
    payload = {"schema": "hu-discard-delay-audit-v1", "games": args.games,
               "seed_start": args.seed_start, "counts": dict(counts),
               "kernel": kernel_runtime_diagnostic(), "fixtures": fixtures,
               "elapsed_s": time.perf_counter()-started}
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in payload.items() if key != "fixtures"}))


if __name__ == "__main__":
    main()
