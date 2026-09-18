"""Task 2.6 验收:聚合统计 = 逐局求和(fair_match 同口径)。"""

from mj.clientd.stats import arena_stats, per_group_stats


def _rec(roles, winner, draw, scores):
    return {
        "roles": roles,
        "result": {"winner": winner, "draw": draw,
                   "scores": scores,
                   "mult": (3 if (not draw and winner is not None) else None)},
    }


def _hand(records):
    n = 0
    wins = draws = 0
    score = opp = 0.0
    mults = {}
    for r in records:
        res = r["result"]
        if res["scores"] is None:
            continue
        n += 1
        ms = r["roles"].index(0)
        score += res["scores"][ms]
        opps = [res["scores"][s] for s in range(4) if s != ms]
        opp += sum(opps) / len(opps)
        if res["draw"]:
            draws += 1
        elif res["winner"] == ms:
            wins += 1
            m = res["mult"]
            mults[m] = mults.get(m, 0) + 1
    return {"games": n, "wins": wins, "win_rate": wins / n if n else 0,
            "draws": draws,
            "avg_score": score / n if n else 0,
            "opp_avg_score": opp / n if n else 0,
            "mults": dict(sorted(mults.items()))}


def test_stats_equals_per_game_sum(tmp_path):
    records = []
    for i in range(16):
        main_seat = i % 4
        roles = [0, 1, 2, 3]
        # 造 16 条:主位赢 4 次、流局 2 次,其余他家赢
        if i == 0:
            winner, draw = main_seat, False
            scores = [0, 0, 0, 0]
            scores[main_seat] = 16
        elif i == 1:
            winner, draw = None, True
            scores = [0, 0, 0, 0]
        else:
            winner = (i % 4 + 1) % 4
            draw = False
            scores = [1, 2, 3, 4]
        rec = _rec(roles, winner, draw, scores)
        rec["result"]["scores"] = scores
        records.append(rec)
    got = arena_stats(sorted(records, key=lambda r: 0))
    want = _hand(records)
    assert got["games"] == want["games"]
    assert got["wins"] == want["wins"]
    assert got["draws"] == want["draws"]
    assert abs(got["win_rate"] - want["win_rate"]) < 1e-9
    assert abs(got["avg_score"] - want["avg_score"]) < 1e-9
    assert abs(got["opp_avg_score"] - want["opp_avg_score"]) < 1e-9
    assert got["mults"] == want["mults"]


def test_per_group_stats(tmp_path):
    records = [
        _rec([0, 1, 2, 3], 0, False, [10, 1, 1, 1]),
        _rec([0, 1, 2, 3], 1, False, [1, 10, 1, 1]),
        _rec([0, 1, 2, 3], 0, True, [0, 0, 0, 0]),
    ]
    groups = per_group_stats(records, lambda r: "fixed")
    assert set(groups) == {"fixed"}
    assert groups["fixed"]["games"] == 3