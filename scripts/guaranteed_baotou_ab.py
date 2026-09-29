#!/usr/bin/env python3
"""Paired counterfactual A/B for candidate-specific guaranteed HU delay.

The current evaluator supplies the HU-window roots.  The baseline arm keeps
the same roots and applies the pre-change shared X/Y/Z factor to every delayed
root.  This isolates the policy change without reimplementing the evaluator.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time

from mj import bot
from mj.game import Game, HU
from mj.scoring import hand_multiplier


DELAYED = {"piao_discard", "baotou_next_draw"}
RANK = {"immediate_hu": 0, "piao_discard": 1,
        "baotou_next_draw": 2, "self_kong": 3}


def _select_shared_delay(evaluation):
    """Select the old shared-delay result from a current root payload."""
    rows = [dict(row) for row in evaluation.get("hu_window_candidates", ())
            if isinstance(row, dict)]
    if not rows:
        return None, None
    observed = tuple(evaluation.get("observed_delay_reasons") or ())
    factor = 0.0 if observed else 1.0
    for row in rows:
        if row.get("type") in DELAYED or row.get("type") == "self_kong":
            row["value"] = float(row.get("raw_value", row.get("value", 0.0))) * factor
    selected = max(
        rows,
        key=lambda row: (float(row.get("value", 0.0)),
                         float(row.get("win_probability", 0.0)),
                         -RANK.get(row.get("type"), 99),
                         -int(row.get("action", 0))),
    )
    return selected.get("action"), selected


def _luxury_upgrade_count(game, seat, row):
    """Count winning draw types whose real score contains luxury chiitoi."""
    if row.get("type") not in DELAYED:
        return 0
    action = row.get("action")
    standing = list(game.hands[seat])
    standing[action] -= 1
    chain, chain_piao = bot._post_discard_chain(
        game, seat, action, standing)
    found = 0
    for tile in row.get("winning_tiles", ()):
        final = list(standing)
        final[tile] += 1
        _mult, parts = hand_multiplier(
            final, standing, len(game.melds[seat]), chain, chain_piao)
        found += int(any(str(part).startswith("豪华七对") for part in parts))
    return found


def _play(seed, *, shared_delay):
    game = Game(seed=int(seed), dealer=int(seed) % 4,
                you_cai_bi_kao=False)
    metrics = {
        "hu_windows": 0,
        "guaranteed_candidate_count": 0,
        "guaranteed_baotou_pass_hu_count": 0,
        "guaranteed_baotou_cashout_count": 0,
        "guaranteed_baotou_lost_before_draw": 0,
        "abandoned_immediate_hu_value": 0.0,
        "selected_guaranteed_raw_value": 0.0,
        "realized_guaranteed_raw_value": 0.0,
        "luxury_upgrade_count": 0,
    }
    pending = {}
    while not game.done:
        seat = game.current_seat()
        legal = game.legal_actions()
        pending_row = pending.pop(seat, None)
        if pending_row is not None:
            if HU in legal:
                metrics["guaranteed_baotou_cashout_count"] += 1
                metrics["realized_guaranteed_raw_value"] += float(
                    pending_row.get("raw_value", 0.0))
            else:
                metrics["guaranteed_baotou_lost_before_draw"] += 1

        action, evaluation = bot.choose_action(
            game, seat, evaluator="legacyV2", return_evaluation=True)
        if not isinstance(evaluation, dict):
            evaluation = {}
        roots = [row for row in evaluation.get("hu_window_candidates", ())
                 if isinstance(row, dict)]
        guaranteed = [row for row in roots
                      if row.get("guaranteed_next_draw_hu")]
        metrics["guaranteed_candidate_count"] += len(guaranteed)
        if evaluation.get("decision_scope") == "hu_window_arbitration":
            metrics["hu_windows"] += 1

        selected_row = next((row for row in roots
                             if row.get("action") == action), None)
        if shared_delay:
            old_action, old_row = _select_shared_delay(evaluation)
            if old_action is not None:
                action, selected_row = old_action, old_row
        if action not in legal:
            raise RuntimeError((seed, seat, action, legal))

        if selected_row and selected_row.get("guaranteed_next_draw_hu"):
            metrics["guaranteed_baotou_pass_hu_count"] += int(HU in legal and
                                                               action != HU)
            if action != HU:
                metrics["abandoned_immediate_hu_value"] += float(
                    next((row.get("value", 0.0) for row in roots
                          if row.get("type") == "immediate_hu"), 0.0))
                metrics["selected_guaranteed_raw_value"] += float(
                    selected_row.get("raw_value", 0.0))
                metrics["luxury_upgrade_count"] += _luxury_upgrade_count(
                    game, seat, selected_row)
                pending[seat] = selected_row
        game.step(action)

    metrics["guaranteed_baotou_lost_before_draw"] += len(pending)
    return {"score": list(game.scores),
            "mult": game.result[1] if game.result else None,
            "metrics": metrics}


def run(games=16, seed0=940000):
    rows = {"candidate": [], "shared_delay": []}
    started = time.time()
    for index in range(int(games)):
        seed = int(seed0) + index
        rows["candidate"].append(_play(seed, shared_delay=False))
        rows["shared_delay"].append(_play(seed, shared_delay=True))

    def summarize(items):
        metric_names = tuple(items[0]["metrics"]) if items else ()
        metrics = {name: sum(row["metrics"][name] for row in items)
                   for name in metric_names}
        scores = [row["score"][0] for row in items]
        mults = [row["mult"] for row in items if row["mult"] is not None]
        return {
            "games": len(items),
            "score_mean": statistics.fmean(scores) if scores else 0.0,
            "multiplier_mean": statistics.fmean(mults) if mults else 0.0,
            "metrics": metrics,
        }

    candidate = summarize(rows["candidate"])
    baseline = summarize(rows["shared_delay"])
    return {
        "schema": "guaranteed-next-draw-hu-counterfactual-ab-v1",
        "games_per_arm": int(games),
        "seed0": int(seed0),
        "candidate": candidate,
        "shared_delay_baseline": baseline,
        "paired_score_delta_mean": candidate["score_mean"] - baseline["score_mean"],
        "paired_multiplier_delta_mean": (
            candidate["multiplier_mean"] - baseline["multiplier_mean"]),
        "seconds": round(time.time() - started, 3),
        "note": "bounded local counterfactual smoke; no online promotion",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--games", type=int, default=16)
    parser.add_argument("--seed0", type=int, default=940000)
    parser.add_argument("--out", default="local/ab_guaranteed_baotou.json")
    args = parser.parse_args()
    result = run(args.games, args.seed0)
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
