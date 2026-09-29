"""Browser-driven formal tournament session using the existing BotClient."""

from __future__ import annotations

import math
import threading
import time

from .errors import ValidationError
from .settings import DEFAULT_SETTINGS_PATH, PlatformSettings
from ..legacy_eval import (
    DEFAULT_BOT_EVALUATOR,
    LEGACY_V2_EVALUATORS,
    canonical_evaluator,
)


TOURNAMENT_STRATEGIES = {"policy", "policy-v3", "bot", "random"}
TOURNAMENT_EVALUATORS = {
    "legacy", "legacy-two-ply-v1", "legacy_v1", "legacy-v1",
    *LEGACY_V2_EVALUATORS,
    "shape-v1", "shape-v2", "policy-v3",
}
TOURNAMENT_POLL_INTERVAL = 1.0


def normalize_tournament_config(config):
    """Validate public session options; credentials remain in PlatformSettings."""
    if not isinstance(config, dict):
        raise ValidationError("tournament config must be an object")
    strategy = config.get("strategy")
    if not isinstance(strategy, str) or strategy not in TOURNAMENT_STRATEGIES:
        raise ValidationError(
            "请显式选择锦标赛策略: policy / policy-v3 / bot / random")

    evaluator = config.get("evaluator") or DEFAULT_BOT_EVALUATOR
    if not isinstance(evaluator, str) or evaluator not in TOURNAMENT_EVALUATORS:
        raise ValidationError(
            f"unknown tournament evaluator {evaluator!r}; expected "
            f"{sorted(TOURNAMENT_EVALUATORS)}")
    evaluator = canonical_evaluator(evaluator)

    try:
        state_rate = float(config.get("state_rate", 16.0))
    except (TypeError, ValueError):
        raise ValidationError("state_rate must be a positive number")
    if not math.isfinite(state_rate) or state_rate <= 0:
        raise ValidationError("state_rate must be a positive number")

    result = {
        "strategy": strategy,
        "evaluator": evaluator,
        "state_rate": state_rate,
        "record": config.get("record", True),
        "replay_trace": config.get("replay_trace", False),
        "marginal_structure_guard_enabled": config.get(
            "marginal_structure_guard_enabled", True),
        "speed_band_enabled": config.get("speed_band_enabled", False),
        "pareto_frontier_enabled": config.get(
            "pareto_frontier_enabled", False),
    }
    for key in ("record", "replay_trace"):
        if not isinstance(result[key], bool):
            raise ValidationError(f"{key} must be a boolean")
    marginal = result["marginal_structure_guard_enabled"]
    if isinstance(marginal, str):
        marginal = marginal.strip().lower() in {
            "1", "true", "yes", "on", "enabled",
        }
    elif not isinstance(marginal, bool):
        raise ValidationError(
            "marginal_structure_guard_enabled must be a boolean")
    result["marginal_structure_guard_enabled"] = marginal
    for key in ("speed_band_enabled", "pareto_frontier_enabled"):
        value = result[key]
        if isinstance(value, str):
            value = value.strip().lower() in {
                "1", "true", "yes", "on", "enabled",
            }
        elif not isinstance(value, bool):
            raise ValidationError(f"{key} must be a boolean")
        result[key] = value
    for key in ("ckpt", "model", "model_name"):
        value = config.get(key)
        if value is not None:
            if not isinstance(value, str) or not value.strip():
                raise ValidationError(f"{key} must be a non-empty string")
            result[key] = value
    return result


def _settings_credentials(settings_path):
    settings = PlatformSettings(
        settings_path or DEFAULT_SETTINGS_PATH).get()
    server = settings.get("server", "")
    token = (settings.get("tokens") or {}).get("tournament", "")
    if not server:
        raise ValidationError("请先在设置中配置服务器地址")
    if not token:
        raise ValidationError("请先配置锦标赛 Key")
    return server, token


def _progress_snapshot(worker, session):
    bot = worker.bot
    server_connected = worker.server_connected
    server_status = worker.server_status
    server_message = worker.server_message
    last_server_check_at = worker.server_checked_at
    if bot is None:
        waiting_for_binding = bool(
            worker.context is not None and not worker.context.tournament_id)
        session.update_progress({
            "phase": ("waiting_binding" if waiting_for_binding
                      else "preflight"),
            "message": ("Key 暂未绑定赛事，每秒轮询等待"
                        if waiting_for_binding else
                        "正在校验锦标赛 Key 并读取赛事规则"),
            "poll_interval_sec": TOURNAMENT_POLL_INTERVAL,
            "server_connected": server_connected,
            "server_status": server_status,
            "server_message": server_message,
            "last_server_check_at": last_server_check_at,
            "games": 0,
            "actions": 0,
        })
        return

    status = getattr(bot, "tournament_status", None)
    phases = {
        "registering": ("waiting_registration", "报名/准备中，持续轮询等待开赛"),
        "stage_open": ("waiting_games", "本轮已开放，持续轮询并等待可加入对局"),
        "running": ("running", "赛事进行中，持续查找并加入本人活跃对局"),
        "stage_done": ("waiting_next_stage", "本轮结束，持续轮询等待下一阶段"),
    }
    phase, message = phases.get(
        status,
        ("waiting_tournament", "等待赛事状态，持续轮询中"),
    )
    stats_lock = getattr(bot, "_stats_lock", None)
    if stats_lock is None:
        stats = getattr(bot, "stats", {})
        games = int(stats.get("games", 0))
        actions = int(stats.get("actions", 0))
    else:
        with stats_lock:
            games = int(bot.stats.get("games", 0))
            actions = int(bot.stats.get("actions", 0))
    server_connected = getattr(
        bot, "tournament_server_connected", server_connected)
    server_status = getattr(
        bot, "tournament_server_status", server_status)
    server_message = getattr(
        bot, "tournament_server_message", server_message)
    last_server_check_at = getattr(
        bot, "tournament_server_checked_at", last_server_check_at)
    session.update_progress({
        "phase": phase,
        "message": message,
        "tournament_status": status,
        "stage": getattr(bot, "tournament_stage", None),
        "poll_interval_sec": TOURNAMENT_POLL_INTERVAL,
        "server_connected": server_connected,
        "server_status": server_status,
        "server_message": server_message,
        "last_server_check_at": last_server_check_at,
        "games": games,
        "actions": actions,
    })


def make_tournament_runner(config, settings_path=None,
                           games_root="local/games"):
    """Build a long-running web session; key is read locally, never serialized."""
    cfg = normalize_tournament_config(config)
    server, token = _settings_credentials(settings_path)

    # Match and tournament sessions share ONNX-aware strategy construction.
    from .match import _build_decide
    from ..platform.tournament_runner import TournamentWorker

    def runner(_stop, session):
        session.update_progress({
            "phase": "loading_strategy",
            "message": "正在加载参赛策略",
            "strategy_loaded": None,
            "strategy_status": "loading",
            "strategy_name": cfg["strategy"],
            "evaluator": cfg["evaluator"],
            "model_name": cfg.get("model_name"),
            "server_connected": None,
            "server_status": "checking",
            "server_message": "等待检测目标服务器",
        })
        session.append_log("正在加载参赛策略", source="strategy")
        try:
            decide = _build_decide(cfg)
        except Exception as exc:
            session.update_progress({
                "strategy_loaded": False,
                "strategy_status": "error",
                "message": f"策略加载失败：{exc}",
            })
            session.append_log(f"策略加载失败：{exc}", level="error",
                               source="strategy")
            raise
        session.update_progress({
            "strategy_loaded": True,
            "strategy_status": "loaded",
            "strategy_snapshot": (
                decide.strategy_snapshot.as_json()
                if hasattr(getattr(decide, "strategy_snapshot", None),
                           "as_json") else
                getattr(decide, "strategy_snapshot", None)),
            "message": "策略已加载，正在连接目标服务器",
        })
        strategy_label = cfg["strategy"]
        if cfg["strategy"] == "bot":
            strategy_label += f" · {cfg['evaluator']}"
        elif cfg.get("model_name"):
            strategy_label += f" · {cfg['model_name']}"
        session.append_log(f"策略加载成功：{strategy_label}",
                           source="strategy")

        last_worker_log = {}

        def log_sink(level, text):
            now = time.monotonic()
            previous = last_worker_log.get((level, text), 0.0)
            if level != "error" and now - previous < 5.0:
                return
            last_worker_log[(level, text)] = now
            session.append_log(text, level=level, source="tournament")

        last_server_log = [None]

        def server_status_sink(connected, status, message):
            session.update_progress({
                "server_connected": connected,
                "server_status": status,
                "server_message": message,
                "last_server_check_at": time.time(),
            })
            marker = (connected, status)
            if marker != last_server_log[0]:
                last_server_log[0] = marker
                level = "error" if status in (
                    "auth_failed", "unreachable", "error",
                    "reachable_error") else "info"
                session.append_log(message, level=level, source="connection")

        worker = TournamentWorker(
            "tournament", server, token,
            strategy=cfg["strategy"],
            ckpt=cfg.get("ckpt"),
            evaluator=cfg["evaluator"],
            state_rate=cfg["state_rate"],
            recorder=cfg["record"],
            replay_trace=cfg["replay_trace"],
            stop=session.stop_event,
            recorder_root=games_root,
            decide=decide,
            wait_for_binding=True,
            log_sink=log_sink,
            server_status_sink=server_status_sink,
        )
        monitor_stop = threading.Event()
        last_poll_log = [0.0]
        last_poll_marker = [None]

        def monitor():
            while not monitor_stop.wait(0.5):
                _progress_snapshot(worker, session)
                progress = session.progress or {}
                marker = (progress.get("phase"),
                          progress.get("tournament_status"),
                          str(progress.get("stage")))
                now = time.monotonic()
                if marker != last_poll_marker[0]:
                    last_poll_marker[0] = marker
                    last_poll_log[0] = now
                    description = progress.get("message") or "赛事状态更新"
                    session.append_log(f"轮询状态：{description}",
                                       source="poll")
                elif now - last_poll_log[0] >= 5.0:
                    last_poll_log[0] = now
                    description = progress.get("message") or "持续轮询中"
                    session.append_log(f"轮询中：{description}",
                                       source="poll")

        monitor_thread = threading.Thread(
            target=monitor,
            name=f"clientd-tournament-progress-{session.id}",
            daemon=True,
        )
        _progress_snapshot(worker, session)
        monitor_thread.start()
        final_result = None
        try:
            result = worker.run()
            final_result = result.as_dict()
            return final_result
        finally:
            monitor_stop.set()
            monitor_thread.join(timeout=2)
            _progress_snapshot(worker, session)
            if final_result is not None:
                reason = final_result.get("termination_reason")
                terminal_messages = {
                    "FINISHED": "锦标赛已完成",
                    "ELIMINATED": "本轮未晋级，赛事会话结束",
                    "CLOSED": "赛事已关闭",
                    "VOID": "赛事已作废",
                    "AUTH_FAILED": "锦标赛 Key 认证失败",
                    "TOKEN_NOT_BOUND": "锦标赛 Key 尚未绑定赛事",
                    "PROTOCOL_FATAL": "赛事 runner 遇到协议错误",
                    "INTERRUPTED": "锦标赛会话已停止",
                }
                session.update_progress({
                    "phase": "finished",
                    "message": terminal_messages.get(reason, "锦标赛会话已结束"),
                })
                level = ("error" if reason in (
                    "AUTH_FAILED", "PROTOCOL_FATAL") else
                    "warning" if reason not in (
                        "FINISHED", "CLOSED", "VOID") else "info")
                session.append_log(
                    terminal_messages.get(reason, "锦标赛会话已结束"),
                    level=level, source="tournament")
                if final_result.get("error"):
                    session.append_log(final_result["error"], level="error",
                                       source="error")
            else:
                session.update_progress({
                    "phase": "error",
                    "message": "锦标赛会话异常结束",
                })
                session.append_log("锦标赛会话异常结束", level="error",
                                   source="error")

    return runner


__all__ = [
    "TOURNAMENT_POLL_INTERVAL",
    "TOURNAMENT_STRATEGIES",
    "make_tournament_runner",
    "normalize_tournament_config",
]
