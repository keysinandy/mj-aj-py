"""clientd HTTP API 路由(task 3.7 前置):记录浏览器 + 本地回放帧 + 种子库。

挂载于服务 Router,供前端/CLI 消费:
- GET  /api/records/local    本地批次两级浏览(batch → game)
- GET  /api/records/online   线上日志(日期 → gid,支持时间范围分页)
- POST /api/records/local/frames  按 batch_id+game 取统一 ReplaySession/帧
- GET  /api/seeds            种子库列表
- POST /api/seeds            命名保存种子
- DELETE /api/seeds/:name    删除种子

安全:记录路径仅由服务端由 (batch_id, game) 拼出并校验,不接受任意文件路径;
batch_id 必须为纯目录名(禁路径穿越),game 必须为整数。
"""

from __future__ import annotations

import base64
from datetime import datetime, timedelta
import math
import os
import time
from urllib.parse import parse_qs, unquote, urlsplit

from .errors import NotFoundError, ValidationError
from .index import (
    index_local_batches, index_online_games,
    DEFAULT_ARENA_ROOT, DEFAULT_GAMES_ROOT,
)
from .records import game_path, load_game_record
from .seeds import SeedLibrary
from .service import Router
from .settings import (DEFAULT_MODEL_DIR, DEFAULT_SETTINGS_PATH,
                       ModelStore, PlatformSettings,
                       probe_platform_connection)

__all__ = ["api_router", "session_router"]


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


def _query_value(query, *names):
    for name in names:
        values = query.get(name)
        if values:
            value = values[-1].strip()
            if value:
                return value
    return None


def _query_int(query, names, *, default, minimum=0, maximum=None):
    raw = _query_value(query, *names)
    if raw is None:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ValidationError(f"{names[0]} must be an integer, got {raw!r}")
    if value < minimum or (maximum is not None and value > maximum):
        bound = (f" between {minimum} and {maximum}"
                 if maximum is not None else f" >= {minimum}")
        raise ValidationError(f"{names[0]} must be{bound}, got {value}")
    return value


def _query_time(value, field, *, end=False):
    """解析 epoch 秒或本地 ISO 日期时间，供线上日志查询使用。"""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit() and len(text) == 8:
        try:
            parsed = datetime.strptime(text, "%Y%m%d")
        except ValueError:
            parsed = None
        if parsed is not None:
            if end:
                parsed += timedelta(days=1) - timedelta(milliseconds=1)
            return time.mktime(parsed.timetuple()) + parsed.microsecond / 1e6
    try:
        numeric = float(text)
        if math.isfinite(numeric):
            return numeric
    except (TypeError, ValueError):
        pass
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise ValidationError(
            f"{field} must be epoch seconds or ISO date-time, got {value!r}")
    if end and parsed.tzinfo is None and "T" not in text and " " not in text:
        parsed += timedelta(days=1) - timedelta(milliseconds=1)
    if parsed.tzinfo is not None:
        return parsed.timestamp()
    return time.mktime(parsed.timetuple()) + parsed.microsecond / 1e6


def api_router(arena_root=None, games_root=None, seed_root=None,
               settings_path=None, model_root=None):
    arena_root = arena_root or DEFAULT_ARENA_ROOT
    games_root = games_root or DEFAULT_GAMES_ROOT
    seeds = SeedLibrary(str(seed_root) if seed_root else None)
    settings = PlatformSettings(settings_path or DEFAULT_SETTINGS_PATH)
    models = ModelStore(model_root or DEFAULT_MODEL_DIR, settings=settings)
    router = Router()

    @router.get("/api/settings")
    def _settings_get(request):
        return 200, settings.get()

    @router.put("/api/settings")
    def _settings_put(request):
        return 200, settings.update(request.body or {})

    @router.patch("/api/settings")
    def _settings_patch(request):
        return 200, settings.update(request.body or {})

    @router.post("/api/settings/test-connection")
    def _settings_test_connection(request):
        body = request.body or {}
        if not isinstance(body, dict):
            raise ValidationError("connection test body must be an object")
        current = settings.get()
        mode = body.get("mode")
        server = body.get("server", current["server"])
        token = body.get("token")
        if token is None:
            configured = current["tokens"].get(mode, "") if mode else ""
            if isinstance(configured, list):
                token = configured[0] if configured else ""
            else:
                token = configured
        return 200, probe_platform_connection(server, token, mode)

    @router.get("/api/models")
    def _models_list(request):
        return 200, {"models": models.list()}

    @router.post("/api/models/import")
    def _model_import(request):
        body = request.body or {}
        if not isinstance(body, dict):
            raise ValidationError("model import body must be an object")
        name = body.get("name")
        encoded = body.get("content_base64")
        if not isinstance(encoded, str) or not encoded:
            raise ValidationError("content_base64 must be a non-empty string")
        try:
            content = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError) as exc:
            raise ValidationError("模型内容不是合法的 base64") from exc
        return 200, models.import_bytes(name, content)

    @router.post("/api/models/:name/select")
    def _model_select(request):
        return 200, models.select(unquote(request.params["name"]))

    @router.delete("/api/models/:name")
    def _model_delete(request):
        return 200, models.delete(unquote(request.params["name"]))

    @router.get("/api/records/local")
    def _local_batches(request):
        return 200, {"batches": index_local_batches(arena_root)}

    @router.get("/api/records/online")
    def _online_games(request):
        query = parse_qs(urlsplit(request.path).query)
        start_ts = _query_time(
            _query_value(query, "start_ts", "start", "from"),
            "start")
        end_ts = _query_time(
            _query_value(query, "end_ts", "end", "to"),
            "end", end=True)
        if start_ts is not None and end_ts is not None and start_ts > end_ts:
            raise ValidationError("start must be before or equal to end")
        limit = _query_int(
            query, ("limit", "page_size"), default=20, minimum=1, maximum=100)
        offset_raw = _query_value(query, "offset")
        if offset_raw is not None:
            offset = _query_int(query, ("offset",), default=0, minimum=0)
        else:
            page = _query_int(query, ("page",), default=1, minimum=1)
            offset = (page - 1) * limit
        # 多取一条只用于判断是否还有下一页，响应本身始终受 limit 限制。
        rows = index_online_games(
            games_root, start_ts=start_ts, end_ts=end_ts,
            offset=offset, limit=limit + 1)
        has_more = len(rows) > limit
        games = rows[:limit]
        return 200, {
            "games": games,
            "offset": offset,
            "limit": limit,
            "has_more": has_more,
            "next_offset": offset + limit if has_more else None,
        }

    @router.post("/api/records/local/frames")
    def _local_frames(request):
        body = request.body or {}
        batch_id = body.get("batch_id")
        game = _game_index(body.get("game"))
        if not _is_plain_name(batch_id):
            raise ValidationError(f"invalid batch_id {batch_id!r}")
        # 以索引(读取 batch.json 的 batch_id)做键解析实际目录:竞技场落盘目录
        # 形如 batch_<id>,而 batch.json 里存的 batch_id 无该前缀;二者必须在
        # 同一处对齐,否则回放 404。这里统一走 index 解析,与浏览器两级浏览复用
        # 同一命名来源。
        entry = next(
            (e for e in index_local_batches(arena_root)
             if e["batch_id"] == batch_id),
            None)
        if entry is None:
            raise NotFoundError(f"batch not found: {batch_id}")
        path = game_path(os.path.join(entry["batch_dir"]), game)
        if not os.path.exists(path):
            raise NotFoundError(f"game record not found: {path}")
        from .replay import local_session
        session = local_session(str(path))
        frames = [step["state"] for step in session["steps"]]
        return 200, {"gid": None, "path": path, "frames": frames,
                     "n_frames": len(frames), "session": session}

    @router.get("/api/records/online/:gid/frames")
    def _online_frames(request):
        gid = request.params["gid"]
        hits = index_online_games(games_root, gid)
        if not hits:
            raise NotFoundError(f"online game not found: {gid}")
        from .. import logview
        from .replay import online_session
        path = hits[0]["path"]
        records = logview.load_records(str(path))
        session = online_session(records, session_id=gid, path=str(path))
        frames = [step["state"] for step in session["steps"]]
        verifications = session["metadata"].get("verifications", [])
        return 200, {"gid": gid, "path": str(path), "frames": frames,
                     "n_frames": len(frames),
                     "verifications": verifications, "session": session}

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


def _inject_selected_model(config, model):
    """给没有显式模型的后续会话注入当前选择，不改运行中会话。"""
    if not model or not isinstance(config, dict):
        return config
    selected_path = model.get("path")
    if not selected_path:
        return config
    result = dict(config)
    injected = False

    def inject(role):
        nonlocal injected
        if not isinstance(role, dict):
            return role
        item = dict(role)
        strategy = item.get("strategy")
        if strategy == "policy" and not item.get("ckpt"):
            item["ckpt"] = selected_path
            injected = True
        elif strategy == "policy-v3" and not item.get("model"):
            item["model"] = selected_path
            injected = True
        return item

    if "strategy" in result:
        result = inject(result)
    if isinstance(result.get("seats"), list):
        result["seats"] = [inject(role) for role in result["seats"]]
    if injected:
        result.setdefault("model_name", model.get("name"))
    return result


def session_router(manager, model_resolver=None):
    """竞技场/线上匹配会话控制面路由(供 Web 控制台开跑/查询/停止)。

    - POST   /api/sessions            {kind, config} → 会话
    - GET    /api/sessions            全部会话列表
    - GET    /api/sessions/:id        单会话(含 progress/result)
    - GET    /api/sessions/:id/logs   增量读取有界会话日志
    - POST   /api/sessions/:id/stop   请求停止(局边界生效)
    """
    router = Router()

    @router.post("/api/sessions")
    def _create(request):
        body = request.body or {}
        kind = body.get("kind") or "arena"
        config = body.get("config") or {}
        if not isinstance(config, dict):
            raise ValidationError("config must be an object")
        if model_resolver is not None:
            config = _inject_selected_model(config, model_resolver())
        session = manager.create(kind, config)
        return 200, session.as_dict()

    @router.get("/api/sessions")
    def _list(request):
        return 200, {"sessions": manager.list()}

    @router.get("/api/sessions/:id")
    def _get(request):
        return 200, manager.get(request.params["id"]).as_dict()

    @router.get("/api/sessions/:id/logs")
    def _logs(request):
        session = manager.get(request.params["id"])
        query = parse_qs(urlsplit(request.path).query)
        after = _query_int(query, ("after",), default=0, minimum=0)
        limit = _query_int(
            query, ("limit",), default=500, minimum=1, maximum=1000)
        return 200, {"session_id": session.id,
                     **session.logs_after(after, limit)}

    @router.post("/api/sessions/:id/stop")
    def _stop(request):
        return 200, manager.stop(request.params["id"]).as_dict()

    return router
