"""本地竞技场聚合统计(task 2.6):主位视角。

口径与 evaluate.fair_match 一致(座位×庄家均衡下主位视角):
win_rate / draws / avg_score / opp_avg_score / mults 分布。
统计必须可由逐局记录精确重算(对账)。
"""

from __future__ import annotations

__all__ = ["arena_stats", "per_group_stats"]


def arena_stats(records):
    """records: 逐局 dict(含 roles/result/scores)。返回主位视角统计。

    主位角色编号约定 0;每局主位坐 roles.index(0)。
    """
    n = 0
    wins = 0
    draws = 0
    score = 0.0
    opp_score = 0.0
    mults = {}
    by_seat = [0] * 4
    for rec in records:
        result = rec.get("result") or {}
        scores = result.get("scores")
        if scores is None:
            continue
        n += 1
        roles = rec.get("roles") or list(range(4))
        main_seat = _main_seat(rec)
        score += scores[main_seat]
        opp = [scores[s] for s in range(4) if s != main_seat]
        opp_score += (sum(opp) / len(opp)) if opp else 0.0
        for s in range(4):
            if result.get("winner") == s and not result.get("draw"):
                by_seat[s] += 1
        if result.get("draw"):
            draws += 1
        elif result.get("winner") == main_seat:
            wins += 1
            mult = result.get("mult")
            mults[mult] = mults.get(mult, 0) + 1
    return {
        "games": n,
        "wins": wins,
        "win_rate": (wins / n) if n else 0.0,
        "draws": draws,
        "avg_score": (score / n) if n else 0.0,
        "opp_avg_score": (opp_score / n) if n else 0.0,
        "wins_by_seat": by_seat,
        "mults": dict(sorted(mults.items())),
    }


def _main_seat(rec):
    roles = rec.get("roles")
    if roles and 0 in roles:
        return roles.index(0)
    return 0  # 缺 roles 时退化为固定坐 0


def per_group_stats(records, group_key):
    """按 group_key(每局配置指纹)分组统计,便于按对手配置对比。"""
    groups = {}
    for rec in records:
        key = group_key(rec)
        groups.setdefault(key, []).append(rec)
    return {key: arena_stats(list(items))
            for key, items in sorted(groups.items(), key=lambda kv: str(kv[0]))}