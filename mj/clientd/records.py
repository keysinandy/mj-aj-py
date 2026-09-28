"""本地对局记录读写(task 2.4)与回放辅助(供 3.1 复用)。

单局记录(每局一个文件 local/arena/<batch_id>/game_<index>.json):
    {seed, dealer, you_cai_bi_kao, base, seats[], roles[], actions[], result}
- seats:   每个角色(0=主位, 1..3=对手)的策略配置,用于统计分组;
- roles:   4 个物理座位各自承担的角色编号(主位随局数轮转);
- actions: 按序动作序列;重放 = Game(seed) + 逐 action step(),
           不依赖重跑任何决策 → 跨机器确定性、无 torch 依赖;
- result:  {winner, mult, draw, scores[4]} 或 None(未赛完)。

record 不含服务端种子(线上没有种子);种子仅是本地引擎的随机源。
"""

from __future__ import annotations

import json
import os

__all__ = ["GAME_FILE_GLOB", "game_path", "write_game_record",
           "load_game_record", "replay_game_from_record", "result_from_game"]

GAME_FILE_GLOB = "game_*.json"

_DIRTY_WRITE_RACE = None  # placeholder for type checkers


def game_path(batch_dir, index):
    return os.path.join(batch_dir, f"game_{int(index):06d}.json")


def result_from_game(game):
    """从已结束的 Game 提取 result;未结束返回 None。"""
    if not game.done:
        return None
    if game.result is not None:
        winner, mult, _parts = game.result
        return {"winner": winner, "mult": mult, "draw": False,
                "scores": list(game.scores)}
    return {"winner": None, "mult": None, "draw": True,
            "scores": list(game.scores)}


def write_game_record(batch_dir, index, *, seed, dealer, base,
                      you_cai_bi_kao, seats, roles, actions, result,
                      decision_audits=None, strategy_snapshots=None):
    rec = {
        "seed": int(seed), "dealer": int(dealer),
        "you_cai_bi_kao": bool(you_cai_bi_kao), "base": int(base),
        "seats": seats, "roles": list(roles),
        "actions": [int(a) for a in actions], "result": result,
    }
    if decision_audits:
        rec["decision_audits"] = decision_audits
    if strategy_snapshots:
        rec["strategy_snapshots"] = strategy_snapshots
    path = game_path(batch_dir, index)
    os.makedirs(batch_dir, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)
    return path


def load_game_record(path):
    with open(path, "r", encoding="utf-8") as f:
        rec = json.load(f)
    # 容错:缺 key 给默认
    rec.setdefault("you_cai_bi_kao", False)
    rec.setdefault("base", 1)
    rec.setdefault("roles", [0, 1, 2, 3])
    rec.setdefault("result", None)
    rec.setdefault("seats", [])
    return rec


def replay_game_from_record(rec):
    """按记录动作序列重放,返回 Game(不重跑决策,不依赖 torch)。"""
    from ..game import Game
    g = Game(seed=rec["seed"], dealer=rec.get("dealer", 0),
             base=rec.get("base", 1),
             you_cai_bi_kao=rec.get("you_cai_bi_kao", False))
    for action in rec["actions"]:
        if action not in list(g.legal_actions()):
            raise ValueError(
                f"recorded action {action} illegal at step "
                f"len(replayed) 状态: legal={list(g.legal_actions())}")
        g.step(action)
    return g
