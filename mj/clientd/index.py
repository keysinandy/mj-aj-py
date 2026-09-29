"""记录浏览器索引(task 3.6):本地批次 + 线上 gid 两级浏览。

- 本地批次:扫描 <arena_root>/*/batch.json → 批次摘要(seed0/局数/统计),
  展开到单局 game_*.json;
- 线上日志:扫描 <games_root>/<日期>/<token>_<gid>.jsonl → {date, gid, path,
  strategy, evaluator}。

未启用内容筛选时，线上索引只读取每个 JSONL 的第一条记录作为开始时间；
启用白板筛选时，索引会额外流式扫描日志中的快照和公开事件，仍不把正文
整体加载进内存。
调用方可传时间范围和 offset/limit，服务层据此只向客户端返回一页结果。

只读消费磁盘目录,不迁移、不改名既有文件,与 Recorder/竞技场现有布局兼容;
空目录 / 缺 meta / 半写文件均容错(跳过并不抛错)。
"""

from __future__ import annotations

import glob
import json
import os
import time

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
        # 竞技场记录按“角色”保存策略；主位角色可能因轮转落在不同物理
        # 座位，因此只读取第一局的 roles/seats，给记录列表标出我方策略。
        if games:
            first_game = _load_json_quiet(games[0], {})
            if isinstance(first_game, dict):
                roles = first_game.get("roles")
                seats = first_game.get("seats")
                viewer = 0
                if isinstance(roles, list):
                    try:
                        viewer = roles.index(0)
                    except ValueError:
                        viewer = 0
                if isinstance(seats, list) and 0 <= viewer < len(roles or []):
                    role_index = roles[viewer]
                    if isinstance(role_index, int) and 0 <= role_index < len(seats):
                        role = seats[role_index]
                        if isinstance(role, dict):
                            for key in ("strategy", "evaluator", "model_name"):
                                if role.get(key) is not None:
                                    entry[key] = role[key]
        out.append(entry)
    return out


def _first_record_timestamp(path):
    """读取一条 JSONL 的首条记录时间；失败时退回文件 mtime。"""
    record = _first_record(path)
    if isinstance(record, dict):
        value = record.get("ts")
        if isinstance(value, (int, float)):
            return float(value)
    try:
        return float(os.path.getmtime(path))
    except OSError:
        return None


def _first_record(path):
    """读取首条完整记录，供索引展示轻量元数据。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                value = json.loads(line)
                return value if isinstance(value, dict) else None
    except (OSError, UnicodeError, ValueError, TypeError):
        return None


def _day_in_range(day, start_ts, end_ts):
    """用目录日期提前排除不可能命中的日志目录。"""
    if not (len(day) == 8 and day.isdigit()):
        return True
    if start_ts is not None:
        start_day = time.strftime("%Y%m%d", time.localtime(start_ts))
        if day < start_day:
            return False
    if end_ts is not None:
        end_day = time.strftime("%Y%m%d", time.localtime(end_ts))
        if day > end_day:
            return False
    return True


def index_online_games(root=None, gid=None, *, record_id=None,
                       start_ts=None, end_ts=None, min_whiteboards=None,
                       offset=0, limit=None):
    """返回线上日志索引，可按 gid、record_id 或时间范围筛选分页。

    ``start_ts``/``end_ts`` 是 epoch 秒，时间范围为闭区间；结果先按
    ``started_at`` 倒序，再应用 ``offset``/``limit``，这样跨日期和跨文件
    分页仍然稳定。``limit`` 为 ``None`` 时返回全部匹配项。
    ``min_whiteboards`` 不为空时，仅保留某个 round 内我方可见白板数达到
    该阈值的对局，并返回命中的 round 元数据；筛选发生在排序和分页之前。
    未启用白板筛选时，索引阶段最多读取每个文件的第一条 JSON。
    """
    root = root or DEFAULT_GAMES_ROOT
    if not os.path.isdir(root):
        return []
    offset = max(0, int(offset or 0))
    if limit is not None:
        limit = max(0, int(limit))
        if limit == 0:
            return []
    if min_whiteboards is not None:
        min_whiteboards = int(min_whiteboards)
        if min_whiteboards < 1 or min_whiteboards > 4:
            raise ValueError("min_whiteboards must be between 1 and 4")
    matches = []
    for day in sorted(os.listdir(root), reverse=True):
        day_dir = os.path.join(root, day)
        if not os.path.isdir(day_dir) or not _day_in_range(
                day, start_ts, end_ts):
            continue
        pattern = os.path.join(day_dir, "*.jsonl")
        for path in sorted(glob.glob(pattern), reverse=True):
            base = os.path.basename(path)          # <token>_<gid>.jsonl
            stem = base[: -len(".jsonl")]
            if "_" in stem and os.sep not in stem:
                gid_part = stem.rsplit("_", 1)[1]
            else:
                gid_part = stem
            if gid is not None and gid_part != str(gid):
                continue
            if record_id is not None and f"{day}~{stem}" != str(record_id):
                continue
            first = _first_record(path)
            started_at = _first_record_timestamp(path)
            if start_ts is not None and (
                    started_at is None or started_at < start_ts):
                continue
            if end_ts is not None and (
                    started_at is None or started_at > end_ts):
                continue
            whiteboard_rounds = None
            if min_whiteboards is not None:
                from .replay import scan_online_whiteboard_rounds
                whiteboard_rounds = scan_online_whiteboard_rounds(
                    path, min_whiteboards)
                if not whiteboard_rounds:
                    continue
            entry = {"date": day, "gid": gid_part, "path": path,
                     "name": stem, "record_id": f"{day}~{stem}",
                     "started_at": started_at}
            if whiteboard_rounds is not None:
                entry["whiteboard_rounds"] = whiteboard_rounds
            if isinstance(first, dict):
                for key in ("strategy", "evaluator", "model_name"):
                    if first.get(key) is not None:
                        entry[key] = first[key]
            matches.append(entry)

    # Directory and filename order is not a reliable proxy for when a game
    # started.  Collect only the lightweight metadata above, sort the complete
    # filtered index, and paginate afterwards so page boundaries do not change
    # when files from different dates have interleaved timestamps.
    matches.sort(key=lambda entry: (
        entry["started_at"] is not None,
        entry["started_at"] if entry["started_at"] is not None
        else float("-inf"),
        entry["record_id"],
    ), reverse=True)
    if limit is None:
        return matches[offset:]
    return matches[offset:offset + limit]
