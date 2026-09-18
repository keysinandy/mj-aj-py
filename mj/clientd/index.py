"""记录浏览器索引(task 3.6):本地批次 + 线上 gid 两级浏览。

- 本地批次:扫描 <arena_root>/*/batch.json → 批次摘要(seed0/局数/统计),
  展开到单局 game_*.json;
- 线上日志:扫描 <games_root>/<日期>/<token>_<gid>.jsonl → {date, gid, path}。

只读消费磁盘目录,不迁移、不改名既有文件,与 Recorder/竞技场现有布局兼容;
空目录 / 缺 meta / 半写文件均容错(跳过并不抛错)。
"""

from __future__ import annotations

import glob
import json
import os

from ..logview import find_logs as _find_online_logs

__all__ = ["index_local_batches", "index_online_games",
           "DEFAULT_ARENA_ROOT", "DEFAULT_GAMES_ROOT"]

DEFAULT_ARENA_ROOT = "local/arena"
DEFAULT_GAMES_ROOT = "local/games"


def _load_json_quiet(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def index_local_batches(root=None):
    """返回 [{batch_id, batch_dir, seed0, status, n_games, completed,
         skipped, stats, started_at, elapsed, game_paths}]。"""
    root = root or DEFAULT_ARENA_ROOT
    out = []
    if not os.path.isdir(root):
        return out
    for batch_file in sorted(glob.glob(os.path.join(root, "*", "batch.json"))):
        summary = _load_json_quiet(batch_file, {})
        batch_dir = os.path.dirname(batch_file)
        games = sorted(glob.glob(os.path.join(batch_dir, "game_*.json")))
        entry = {
            "batch_id": summary.get("batch_id") or os.path.basename(batch_dir),
            "batch_dir": batch_dir,
            "seed0": summary.get("seed0"),
            "status": summary.get("status", "unknown"),
            "n_games": summary.get("n_games"),
            "completed": summary.get("completed", len(games)),
            "skipped": summary.get("skipped", 0),
            "stats": summary.get("stats"),
            "started_at": summary.get("started_at"),
            "elapsed": summary.get("elapsed"),
            "game_paths": games,
        }
        out.append(entry)
    return out


def index_online_games(root=None, gid=None):
    """返回 [{date, gid, path}]。gid 过滤可选。games_root 可为空目录。"""
    root = root or DEFAULT_GAMES_ROOT
    if not os.path.isdir(root):
        return []
    out = []
    for day in sorted(os.listdir(root)):
        day_dir = os.path.join(root, day)
        if not os.path.isdir(day_dir):
            continue
        pattern = os.path.join(day_dir, "*.jsonl")
        for path in sorted(glob.glob(pattern)):
            base = os.path.basename(path)          # <token>_<gid>.jsonl
            stem = base[: -len(".jsonl")]
            if "_" in stem and os.sep not in stem:
                gid_part = stem.rsplit("_", 1)[1]
            else:
                gid_part = stem
            if gid is not None and gid_part != str(gid):
                continue
            out.append({"date": day, "gid": gid_part, "path": path,
                        "name": stem})
    return out