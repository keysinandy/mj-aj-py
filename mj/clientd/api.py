"""clientd HTTP API 路由(task 3.7 前置):记录浏览器 + 本地回放帧 + 种子库。

挂载于服务 Router,供前端/CLI 消费:
- GET  /api/records/local    本地批次两级浏览(batch → game)
- GET  /api/records/online   线上日志(日期 → gid)
- POST /api/records/local/frames  按 batch_id+game 取预计算本地回放帧
- GET  /api/seeds            种子库列表
- POST /api/seeds            命名保存种子
- DELETE /api/seeds/:name    删除种子

安全:记录路径仅由服务端由 (batch_id, game) 拼出并校验,不接受任意文件路径;
batch_id 必须为纯目录名(禁路径穿越),game 必须为整数。
"""

from __future__ import annotations

import os

from .errors import NotFoundError, ValidationError
from .index import (
    index_local_batches, index_online_games,
    DEFAULT_ARENA_ROOT, DEFAULT_GAMES_ROOT,
)
from .records import game_path, load_game_record
from .seeds import SeedLibrary
from .service import Router

__all__ = ["api_router"]


def _is_plain_name(name):
    return bool(name) and os.path.basename(name) == name and ".." not in name


def _game_index(value):
    try:
        idx = int(value)
    except (TypeError, ValueError):
        raise ValidationError(f"game must be an integer, got {value!r}")
    if idx < 0:
        raise ValidationError(f"game index out of range: {idx}")
    return idx


def api_router(arena_root=None, games_root=None, seed_root=None):
    arena_root = arena_root or DEFAULT_ARENA_ROOT
    games_root = games_root or DEFAULT_GAMES_ROOT
    seeds = SeedLibrary(str(seed_root) if seed_root else None)
    router = Router()

    @router.get("/api/records/local")
    def _local_batches(request):
        return 200, {"batches": index_local_batches(arena_root)}

    @router.get("/api/records/online")
    def _online_games(request):
        return 200, {"games": index_online_games(games_root)}

    @router.post("/api/records/local/frames")
    def _local_frames(request):
        body = request.body or {}
        batch_id = body.get("batch_id")
        game = _game_index(body.get("game"))
        if not _is_plain_name(batch_id):
            raise ValidationError(f"invalid batch_id {batch_id!r}")
        path = game_path(os.path.join(arena_root, str(batch_id)), game)
        if not os.path.exists(path):
            raise NotFoundError(f"game record not found: {path}")
        from .replay import local_frames
        frames = local_frames(str(path))
        return 200, {"gid": None, "path": path, "frames": frames,
                     "n_frames": len(frames)}

    @router.get("/api/seeds")
    def _seed_list(request):
        return 200, {"seeds": seeds.list()}

    @router.post("/api/seeds")
    def _seed_save(request):
        body = request.body or {}
        name = body.get("name")
        seed_list = body.get("seeds")
        if not _is_plain_name(name):
            raise ValidationError(f"invalid seed name {name!r}")
        if not isinstance(seed_list, (list, tuple)):
            raise ValidationError("seeds must be a list")
        records = seeds.save_batch([int(s) for s in seed_list],
                                   source=name)
        return 200, {"name": name, "n": len(records),
                     "seeds": records}

    @router.delete("/api/seeds/:name")
    def _seed_delete(request):
        name = request.params["name"]
        if not _is_plain_name(name):
            raise ValidationError(f"invalid seed name {name!r}")
        try:
            ok = seeds.delete(name)
        except ValidationError:
            raise NotFoundError(f"seed not found: {name}")
        return 200, {"name": name, "deleted": ok}

    return router