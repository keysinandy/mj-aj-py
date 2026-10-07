#!/usr/bin/env python3
"""ppo-league ↔ legacyV2 本地对称对局报告。

两种阵容,随机种局 + 座位×庄家公平轮转(与 fair_match 同口径):
  A  1×ppo-league + 3×legacyV2(启发式 bot) —— 测 ppo-league 单独在场
  B  1×legacyV2 + 3×ppo-league              —— 测 ppo-league 占 3 席支配

每个阵容对"跟踪的单席"统计:均分 / 胜率 / 平局 / 对手均分,并给出吞吐。
legacyV2 = 当前线上默认启发式(choose_action 默认 evaluator)。
"""

from __future__ import annotations

import argparse
import json
import time

from mj.bot import choose_action as _heuristic_bot
from mj.evaluate import policy_player, _play_game

PPO_LEAGUE_CKPT = "runs/ppo_league/final.pt"
EVALUATOR = "legacyV2"


def _run_matchup(ppo, single, n, seed0):
    """single in ('ppo','legacy'):跟踪的单席策略。返回报告 dict。"""
    wins = 0
    draws = 0
    score = 0.0
    opp_accum = 0.0
    opp_den = 0.0
    t0 = time.time()
    for i in range(n):
        seat = i % 4
        dealer = (i // 4) % 4
        players = []
        for s in range(4):
            if single == "ppo":
                players.append(ppo if s == seat else True)
            else:
                players.append(True if s == seat else ppo)
        g = _play_game(players, seed0 + i, dealer=dealer, evaluator=EVALUATOR)
        if g.result:
            w, _mult, _ = g.result
            if w == seat:
                wins += 1
        else:
            draws += 1
        score += g.scores[seat]
        opp_accum += sum(g.scores[s] for s in range(4) if s != seat)
        opp_den += 3
    elapsed = time.time() - t0
    return {
        "single_role": single,
        "games": n,
        "wins": wins,
        "win_rate": wins / n,
        "draws": draws,
        "avg_score": score / n,
        "opp_avg_score": opp_accum / max(opp_den, 1),
        "seconds": elapsed,
        "fps": n / elapsed if elapsed else 0.0,
        "seed0": seed0,
        "evaluator": EVALUATOR,
        "ppo_checkpoint": PPO_LEAGUE_CKPT,
    }


def _run_2v2(ppo, n, seed0):
    """2×ppo-league + 2×legacyV2 平衡对局;6 种座位配对 + 庄家轮转。"""
    pairs = [(0, 1), (0, 2), (0, 3), (1, 2), (1, 3), (2, 3)]
    ppo_wins = legacy_wins = 0
    draws = 0
    ppo_accum = legacy_accum = 0.0
    t0 = time.time()
    for i in range(n):
        ppo_seats = set(pairs[i % 6])
        dealer = (i // 6) % 4
        players = [ppo if s in ppo_seats else True for s in range(4)]
        g = _play_game(players, seed0 + i, dealer=dealer, evaluator=EVALUATOR)
        if g.result:
            w, _mult, _ = g.result
            if w in ppo_seats:
                ppo_wins += 1
            else:
                legacy_wins += 1
        else:
            draws += 1
        for s in range(4):
            if s in ppo_seats:
                ppo_accum += g.scores[s]
            else:
                legacy_accum += g.scores[s]
    elapsed = time.time() - t0
    games = n * 2  # 2 座每策略 × n 局
    return {
        "games": n,
        "seat_games": games,
        "ppo_wins": ppo_wins, "ppo_win_rate": ppo_wins / n,
        "legacy_wins": legacy_wins, "legacy_win_rate": legacy_wins / n,
        "draws": draws,
        "ppo_avg_score": ppo_accum / max(games, 1),
        "legacy_avg_score": legacy_accum / max(games, 1),
        "seconds": elapsed,
        "fps": n / elapsed if elapsed else 0.0,
        "seed0": seed0, "evaluator": EVALUATOR,
        "ppo_checkpoint": PPO_LEAGUE_CKPT,
    }


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=5000)
    ap.add_argument("--seed0", type=int, default=6000)
    ap.add_argument("--ckpt", default=PPO_LEAGUE_CKPT)
    ap.add_argument("--matchup", choices=("A", "B", "C", "both"), default="both")
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)

    ppo = policy_player(args.ckpt)
    report = {"schema": "ppo-league-vs-legacyv2-local-v1",
              "games_per_matchup": args.games, "seed0": args.seed0,
              "ckpt": args.ckpt, "matchups": {}}
    if args.matchup in ("A", "both"):
        report["matchups"]["A_ppo3legacy"] = _run_matchup(
            ppo, "ppo", args.games, args.seed0)
    if args.matchup in ("B", "both"):
        report["matchups"]["B_legacy3ppo"] = _run_matchup(
            ppo, "legacy", args.games, args.seed0)
    if args.matchup in ("C", "both"):
        report["matchups"]["C_2v2"] = _run_2v2(ppo, args.games, args.seed0)

    for name, row in report["matchups"].items():
        if name == "C_2v2":
            diff = row["ppo_avg_score"] - row["legacy_avg_score"]
            print(f"[{name}] 2v2 n={row['games']} "
                  f"ppo胜={row['ppo_win_rate']:.4f} legacy胜={row['legacy_win_rate']:.4f} "
                  f"ppo均分={row['ppo_avg_score']:+.3f} legacy均分={row['legacy_avg_score']:+.3f} "
                  f"(差{diff:+.3f}) 平局={row['draws']} ({row['fps']:.1f}局/s, {row['seconds']:.0f}s)")
        else:
            role = "ppo-league" if row["single_role"] == "ppo" else "legacyV2"
            print(f"[{name}] 跟踪单席={role:10s} n={row['games']} "
                  f"胜率={row['win_rate']:.4f} 均分={row['avg_score']:+.3f} "
                  f"对手均分={row['opp_avg_score']:+.3f} 平局={row['draws']} "
                  f"({row['fps']:.1f} 局/s, {row['seconds']:.0f}s)")
    if args.out:
        import os
        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())