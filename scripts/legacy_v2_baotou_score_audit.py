"""Audit exact next-draw score among equally fast baotou-scope discards.

The audit never changes actions. It uses only the current hero hand and public
unseen tile mass, retaining the selected tier, White protection and winning
mass. Exact score differences identify candidates for a subsequent paired test;
they are not evidence of final settlement profit.
"""
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
        if phase == "discard" and HU not in legal and isinstance(info, dict) and info.get("reason") == "discard_baotou":
            tier = info["baotou_tier"]
            counts[f"tier_{tier}_decisions"] += 1
            rows = {row["tile"]: row for row in info.get("candidates", ())}
            base = rows.get(action)
            if base:
                eligible = [row for row in rows.values()
                            if row["baotou_tier"] == tier and
                            (row["tile"] == W) == (action == W) and
                            row["baotou_progress_score_x2"] == base["baotou_progress_score_x2"] and
                            row["current_selfdraw_hu_ukeire"] >= base["current_selfdraw_hu_ukeire"]]
                if len(eligible) > 1:
                    counts[f"tier_{tier}_ties"] += 1
                    visible = game.visible_counts(seat)
                    remaining = [max(0, 4 - value) for value in visible]
                    values = {}
                    for row in eligible:
                        standing = list(game.hands[seat])
                        standing[row["tile"]] -= 1
                        chain, piao = _post_discard_chain(game, seat, row["tile"], standing)
                        values[row["tile"]] = _expected_next_draw_reward(
                            game, seat, standing, len(game.melds[seat]), chain, piao,
                            False, remaining, draw_delay=4)
                    best = max(values, key=lambda tile: (values[tile]["value"], tile == action, -tile))
                    if values[best]["value"] > values[action]["value"] + 1e-9:
                        counts[f"tier_{tier}_score_divergences"] += 1
                        fixtures.append({"seed": seed, "seat": seat, "dealer": game.dealer,
                            "hand": list(game.hands[seat]), "melds": game.melds[seat],
                            "visible": visible, "wall_left": game.live_wall_left(),
                            "legal": list(legal), "baseline": action, "challenger": best,
                            "tier": tier, "rows": eligible,
                            "values": values, "chain": game.chain[seat], "chain_piao": game.chain_piao[seat]})
        if action not in legal:
            raise AssertionError("illegal baseline action")
        game.step(action)
    return {"counts": dict(counts), "fixtures": fixtures[:8]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--games", type=int, default=256)
    parser.add_argument("--seed-start", type=int, default=2800000)
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
    payload = {"schema": "baotou-score-tie-audit-v1", "games": args.games,
               "seed_start": args.seed_start, "counts": dict(counts),
               "fixtures": fixtures[:32], "elapsed_s": time.perf_counter()-started}
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in payload.items() if key != "fixtures"}))


if __name__ == "__main__":
    main()
