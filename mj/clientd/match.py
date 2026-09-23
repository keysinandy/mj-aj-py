"""线上自由匹配会话 runner。

Web 控制台只提交对局参数，服务器地址与匹配令牌始终从 clientd 的本机
设置读取。这里复用平台 ``BotClient.run_match`` 的自动入席/整房退出语义，
并把运行中的场数与房间数映射到 clientd 会话进度。
"""

from __future__ import annotations

import threading
import time

from .errors import ValidationError
from .settings import DEFAULT_SETTINGS_PATH, PlatformSettings
from ..legacy_eval import (
    DEFAULT_BOT_EVALUATOR,
    LEGACY_V2_EVALUATORS,
    canonical_evaluator,
)


DEFAULT_MATCH_CONFIG = {
    "max_games": 10,
    "strategy": "bot",
    "evaluator": DEFAULT_BOT_EVALUATOR,
    "state_rate": 16.0,
    "room_close_wait": 65.0,
    "record": True,
    "replay_trace": False,
}
MATCH_STRATEGIES = {"policy", "policy-v3", "bot", "random"}
MATCH_EVALUATORS = {
    "legacy", "legacy-two-ply-v1", "legacy_v1", "legacy-v1",
    *LEGACY_V2_EVALUATORS,
    "shape-v1", "shape-v2", "policy-v3",
}


class _StopSignal:
    """将 clientd 的 callable 停止协议适配成 BotClient 的 Event 协议。

    SessionManager 为了兼容本地 arena runner，传给 runner 的是
    ``lambda: session.stop_requested``；线上 BotClient 则约定接收带
    ``is_set()`` 的 ``threading.Event``。直接把前者传下去会在第一次
    轮询时触发 ``AttributeError: 'function' object has no attribute
    'is_set'``。
    """

    def __init__(self, stop):
        self._stop = stop

    def is_set(self):
        if callable(self._stop):
            return bool(self._stop())
        return bool(self._stop is not None and self._stop.is_set())


def normalize_match_config(config):
    """校验 Web 匹配参数并填充安全默认值。

    令牌、服务器和其它凭据不属于此配置，避免出现在会话列表响应中。
    ``max_games=None`` 表示持续匹配，控制台默认使用有限场数便于一次性
    试跑；停止会话仍可随时结束持续模式。
    """
    if config is None:
        config = {}
    if not isinstance(config, dict):
        raise ValidationError("match config must be an object")
    result = dict(DEFAULT_MATCH_CONFIG)
    result.update(config)

    max_games = result.get("max_games")
    if max_games is not None:
        if isinstance(max_games, bool):
            raise ValidationError("max_games must be an integer or null")
        try:
            max_games = int(max_games)
        except (TypeError, ValueError):
            raise ValidationError("max_games must be an integer or null")
        if max_games < 1 or max_games > 10000:
            raise ValidationError("max_games must be between 1 and 10000")
    result["max_games"] = max_games

    strategy = result.get("strategy")
    if strategy not in MATCH_STRATEGIES:
        raise ValidationError(
            f"unknown match strategy {strategy!r}; expected "
            f"{sorted(MATCH_STRATEGIES)}")
    result["strategy"] = strategy

    evaluator = result.get("evaluator") or DEFAULT_BOT_EVALUATOR
    if evaluator not in MATCH_EVALUATORS:
        raise ValidationError(
            f"unknown match evaluator {evaluator!r}; expected "
            f"{sorted(MATCH_EVALUATORS)}")
    result["evaluator"] = canonical_evaluator(evaluator)

    try:
        state_rate = float(result.get("state_rate", 16.0))
    except (TypeError, ValueError):
        raise ValidationError("state_rate must be a positive number")
    if state_rate <= 0:
        raise ValidationError("state_rate must be a positive number")
    result["state_rate"] = state_rate

    try:
        room_close_wait = float(result.get("room_close_wait", 65.0))
    except (TypeError, ValueError):
        raise ValidationError("room_close_wait must be between 0 and 300")
    if room_close_wait < 0 or room_close_wait > 300:
        raise ValidationError("room_close_wait must be between 0 and 300")
    result["room_close_wait"] = room_close_wait

    for key in ("record", "replay_trace"):
        if not isinstance(result[key], bool):
            raise ValidationError(f"{key} must be a boolean")
    return result


def _settings_credentials(settings_path):
    settings = PlatformSettings(settings_path or DEFAULT_SETTINGS_PATH).get()
    server = settings.get("server", "")
    token = (settings.get("tokens") or {}).get("match", "")
    if not server:
        raise ValidationError("请先在设置中配置服务器地址")
    if not token:
        raise ValidationError("请先在设置中配置匹配令牌")
    return server, token


def _build_decide(config):
    # 延迟导入平台 runner，避免仅使用本地竞技场时加载网络对局依赖。
    from ..platform.runner import make_decide

    try:
        strategy = config["strategy"]
        model_path = config.get("ckpt") or config.get("model")
        # 设置页管理的是 ONNX 文件；clientd 本地策略工厂能校验并加载
        # ONNX，而平台 CLI 的 legacy make_decide(policy) 只接收 torch
        # checkpoint。线上 policy 两种格式都支持，避免把已选模型误当
        # 成 PyTorch 文件。
        if strategy in ("policy", "policy-v3") \
                and isinstance(model_path, str) \
                and model_path.lower().endswith(".onnx"):
            from .strategies import make_player
            decide = make_player(config)
        else:
            decide = make_decide(
                strategy,
                config.get("ckpt"),
                evaluator=config.get("evaluator", DEFAULT_BOT_EVALUATOR),
                model=config.get("model"),
                policy_profile=config.get("policy_profile"),
            )
        # Recorder/BotClient 会把这两个稳定标记写进每场 meta，回放页可
        # 在不暴露令牌的前提下说明本场究竟使用 bot 还是 policy。
        strategy = config["strategy"]
        setattr(decide, "bot_strategy", strategy)
        evaluator = getattr(decide, "bot_evaluator", None)
        if evaluator is None:
            evaluator = config.get("evaluator") if strategy == "bot" else strategy
        setattr(decide, "bot_evaluator", evaluator)
        if config.get("model_name") is not None:
            setattr(decide, "bot_model_name", config["model_name"])
        return decide
    except SystemExit as exc:
        # make_decide 的 CLI 兼容错误是 SystemExit；Web 会话必须转成
        # 可序列化的 400/会话 error，而不能让 daemon 线程悬挂。
        raise ValidationError(str(exc)) from exc


def make_match_runner(config, settings_path=None):
    """返回 ``SessionManager`` runner 协议的线上匹配函数。"""
    cfg = normalize_match_config(config)
    server, token = _settings_credentials(settings_path)
    decide = _build_decide(cfg)

    # 这些导入放在创建匹配会话时，clientd 启动与本地 arena 不受平台
    # runner 的可选依赖影响，也便于离线测试替换 Api/BotClient。
    from ..platform.api import Api
    from ..platform.bot_client import BotClient
    from ..platform.recorder import Recorder

    def runner(stop, session):
        # 网络身份预检放在会话线程中，POST /api/sessions 不会因平台暂时
        # 不可达而阻塞 clientd 控制面；真正的 /api/match 仍由 BotClient
        # 按既有瞬态/鉴权语义处理。
        state_rate = cfg["state_rate"]
        api = Api(server, token, state_rate=state_rate)
        name = "match"
        try:
            me = api.me()
            if isinstance(me, dict) and me.get("user_id"):
                name = str(me["user_id"])
        except Exception:
            # /api/match 会再次完成鉴权；身份预检失败不应泄露令牌，也不应
            # 阻止重试型平台暂时不可用场景。保留稳定的日志文件前缀。
            pass

        recorder = None
        if cfg["record"]:
            recorder = Recorder(replay_trace=cfg["replay_trace"],
                                trace_root=cfg.get("trace_root"))
        bot = BotClient(
            api,
            name,
            decide,
            log=lambda _message: None,
            recorder=recorder,
            mode="match",
            use_notify=True,
            long_poll=False,
        )
        started = time.monotonic()
        monitor_done = threading.Event()
        total = cfg["max_games"] or 0

        def update_progress():
            with bot._stats_lock:
                games = int(bot.stats.get("games", 0))
                rooms = int(bot.stats.get("rooms", 0))
            elapsed = max(time.monotonic() - started, 1e-9)
            session.progress = {
                "done": games,
                "total": total,
                "rate": round(games / elapsed, 2),
                "last_index": games - 1,
                "rooms": rooms,
            }

        def monitor():
            while not monitor_done.wait(0.5):
                update_progress()
            update_progress()

        monitor_thread = threading.Thread(
            target=monitor, name=f"clientd-match-progress-{session.id}",
            daemon=True)
        monitor_thread.start()
        try:
            stats = bot.run_match(
                max_games=cfg["max_games"],
                stop=_StopSignal(stop),
                room_close_wait=cfg["room_close_wait"],
            )
            return {
                "games": int(stats.get("games", 0)),
                "rooms": int(stats.get("rooms", 0)),
                "scores": stats.get("scores", []),
                "stats": stats,
                "strategy": cfg["strategy"],
                "evaluator": cfg["evaluator"],
            }
        finally:
            monitor_done.set()
            monitor_thread.join(timeout=2)

    return runner


__all__ = [
    "DEFAULT_MATCH_CONFIG",
    "MATCH_EVALUATORS",
    "MATCH_STRATEGIES",
    "make_match_runner",
    "normalize_match_config",
]
