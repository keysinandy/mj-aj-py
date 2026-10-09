"""Observe immediate settlement EV differences in ordinary tenpai discards."""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
import json
from pathlib import Path
import time

from mj.bot import _expected_next_draw_reward, _post_discard_chain
from mj.game import Game, HU
from mj.legacy_eval import LegacyTwoPlyProfile
from mj.tiles import W
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
        if (phase == "discard" and action >= 0 and HU not in legal
                and not game.in_freeze(seat) and isinstance(info, dict)
                and info.get("reason") != "discard_baotou"):
            rows = [row for row in info.get("candidates", ()) if row.get("shanten") == 0
                    and row["tile"] in legal and (row["tile"] == W) == (action == W)]
            if any(row["tile"] == action for row in rows):
                counts["ordinary_tenpai_decisions"] += 1
                if len(rows) > 1:
                    counts["multiple_tenpai_candidates"] += 1
                    visible = game.visible_counts(seat)
                    remaining = [max(0, 4-value) for value in visible]
                    values = {}
                    for row in rows:
                        standing = list(game.hands[seat])
                        standing[row["tile"]] -= 1
                        chain, piao = _post_discard_chain(game, seat, row["tile"], standing)
                        values[row["tile"]] = _expected_next_draw_reward(game, seat, standing,
                            len(game.melds[seat]), chain, piao, False, remaining, draw_delay=4)
                    best = max(values, key=lambda tile: (values[tile]["value"], tile == action, -tile))
                    if values[best]["value"] > values[action]["value"]+1e-9:
                        counts["score_divergences"] += 1
                        counts[f"white_{game.hands[seat][W]}"] += 1
                        fixtures.append({"seed": seed, "seat": seat, "dealer": game.dealer,
                            "hand": list(game.hands[seat]), "melds": game.melds[seat],
                            "visible": visible, "baseline": action, "challenger": best,
                            "rows": rows, "values": values, "wall_left": game.live_wall_left()})
        if action not in legal:
            raise AssertionError("illegal baseline action")
        game.step(action)
    return {"counts": dict(counts), "fixtures": fixtures[:8]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--games", type=int, default=512)
    parser.add_argument("--seed-start", type=int, default=3700000)
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
    payload = {"schema": "ordinary-tenpai-score-audit-v1", "games": args.games,
               "seed_start": args.seed_start, "counts": dict(counts),
               "fixtures": fixtures[:32], "elapsed_s": time.perf_counter()-started}
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in payload.items() if key != "fixtures"}))


if __name__ == "__main__":
    main()
