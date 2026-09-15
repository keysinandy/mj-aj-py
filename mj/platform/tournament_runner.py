"""CLI and token-worker orchestration for formal tournaments.

The module intentionally owns no game protocol.  A worker prepares a
token-scoped context and delegates all game execution to ``BotClient``.
"""

import argparse
import inspect
import json
import threading
import time

from .api import Api, ApiError
from .bot_client import BotClient
from .config import TournamentConfigError, load_tournament_config
from .recorder import Recorder
from .runner import DumpingApi, make_decide
from .security import redact_exception, redact_text, redact_value
from .tournament import (TournamentContext, TournamentResult, TournamentRules,
                         TERMINATION_REASONS)


PRODUCTION_API = Api
POLL_RETRY_INITIAL = 0.5
POLL_RETRY_MAX = 8.0


def _is_auth_error(exc):
    return isinstance(exc, ApiError) and exc.status in (401, 403)


def _is_transient_error(exc):
    if isinstance(exc, (TimeoutError, OSError, ConnectionError)):
        return True
    return (isinstance(exc, ApiError)
            and (exc.status in (0, 408, 425, 429)
                 or 500 <= exc.status < 600))


def _sleep_stop(seconds, stop):
    if stop is None:
        time.sleep(seconds)
        return False
    return stop.wait(max(0.0, seconds))


def _call_factory(factory, *args, **kwargs):
    """Call production classes and small test doubles through one surface."""
    try:
        parameters = inspect.signature(factory).parameters
    except (TypeError, ValueError):
        return factory(*args, **kwargs)
    if any(parameter.kind == inspect.Parameter.VAR_KEYWORD
           for parameter in parameters.values()):
        return factory(*args, **kwargs)
    supported = {
        key: value for key, value in kwargs.items()
        if key in parameters
        and parameters[key].kind != inspect.Parameter.POSITIONAL_ONLY
    }
    return factory(*args, **supported)


def _make_recorder(factory, *, root, label, replay_trace, trace_root,
                   secrets=()):
    return _call_factory(
        factory, root=root, default_name=label, replay_trace=replay_trace,
        trace_root=trace_root, redact_secrets=secrets)


def _make_api(factory, server, token, state_rate, *, dump, label, dump_dir,
              secrets):
    if dump and factory is PRODUCTION_API:
        return DumpingApi(server, token, label, dump_dir,
                          state_rate=state_rate,
                          redact_secrets=secrets)
    return _call_factory(factory, server, token, state_rate=state_rate)


def _make_bot(factory, api, label, decide, recorder, log):
    kwargs = {
        "log": log,
        "recorder": recorder,
        "mode": "tournament",
        "use_notify": True,
    }
    return _call_factory(factory, api, label, decide, **kwargs)


class TournamentWorker:
    """Run one scoped registration token until its tournament outcome."""

    def __init__(self, label, server, token, strategy="policy", ckpt=None,
                 evaluator="legacy", state_rate=15.0, dump=False,
                 dump_dir="local/logs", recorder=True,
                 replay_trace=False, trace_root=None, stop=None,
                 recorder_root="local/games", api_factory=None,
                 bot_factory=None, recorder_factory=None,
                 sleep=_sleep_stop):
        self.label = label
        self.server = server
        self.token = token
        self.strategy = strategy
        self.ckpt = ckpt
        self.evaluator = evaluator
        self.state_rate = state_rate
        self.dump = dump
        self.dump_dir = dump_dir
        self.record = recorder
        self.replay_trace = replay_trace
        self.trace_root = trace_root
        self.stop = stop
        self.recorder_root = recorder_root
        # Resolve defaults at construction time so test harnesses and future
        # adapters can replace the module-level production classes cleanly.
        self.api_factory = Api if api_factory is None else api_factory
        self.bot_factory = BotClient if bot_factory is None else bot_factory
        self.recorder_factory = (Recorder if recorder_factory is None
                                 else recorder_factory)
        self.sleep = sleep
        self.api = None
        self.bot = None
        self.context = None
        self.rules = None
        self._recorder = None

    def _log(self, message):
        print(f"[{self.label}] {redact_text(message, [self.token])}",
              flush=True)

    def _failure(self, reason, exc=None):
        error = None
        if exc is not None:
            safe = redact_exception(exc, [self.token])
            error = safe.get("message") or safe.get("type")
            self._log(f"{safe.get('type')}: {safe.get('message')}")
        return TournamentResult(
            token_label=self.label,
            tournament_id=(self.context.tournament_id
                           if self.context is not None else None),
            user_id=(self.context.user_id
                     if self.context is not None else None),
            termination_reason=reason,
            error=redact_text(error, [self.token]) if error else None,
        )

    def _close_recorder(self):
        if self._recorder is None:
            return
        try:
            self._recorder.close_all()
        except Exception:
            pass

    def _fetch_rules(self):
        delay = POLL_RETRY_INITIAL
        while True:
            if self.stop is not None and self.stop.is_set():
                raise RuntimeError("INTERRUPTED")
            try:
                return TournamentRules.from_response(self.api.rules())
            except Exception as exc:
                if _is_auth_error(exc) or not _is_transient_error(exc):
                    raise
                self._log(f"rules 暂时失败, {delay:.1f}s 后重试")
                if self.sleep(delay, self.stop):
                    raise RuntimeError("INTERRUPTED")
                delay = min(POLL_RETRY_MAX, delay * 2.0)

    def _fetch_me(self):
        delay = POLL_RETRY_INITIAL
        while True:
            if self.stop is not None and self.stop.is_set():
                raise RuntimeError("INTERRUPTED")
            try:
                return self.api.me()
            except Exception as exc:
                if _is_auth_error(exc) or not _is_transient_error(exc):
                    raise
                self._log(f"me 暂时失败, {delay:.1f}s 后重试")
                if self.sleep(delay, self.stop):
                    raise RuntimeError("INTERRUPTED")
                delay = min(POLL_RETRY_MAX, delay * 2.0)

    def _preflight(self):
        me = self._fetch_me()
        self.context = TournamentContext.from_me(
            token_label=self.label, server=self.server, response=me)
        if not self.context.tournament_id:
            raise RuntimeError("TOKEN_NOT_BOUND")
        self.rules = self._fetch_rules()
        self.context = TournamentContext(
            token_label=self.context.token_label,
            server=self.context.server,
            tournament_id=self.context.tournament_id,
            user_id=self.context.user_id,
            active_games=self.context.active_games,
            rules=self.rules,
        )

    def _result_from_bot(self, stats):
        stats = stats if isinstance(stats, dict) else {}
        stats = redact_value(stats, [self.token])
        reason = stats.get("termination_reason")
        if reason not in TERMINATION_REASONS:
            reason = "PROTOCOL_FATAL"
        result = TournamentResult(
            token_label=self.label,
            tournament_id=stats.get("tournament_id")
                            or (self.context.tournament_id
                                if self.context else None),
            user_id=stats.get("user_id")
                    or (self.context.user_id if self.context else None),
            termination_reason=reason,
            final_status=stats.get("final_status"),
            final_stage=stats.get("final_stage"),
            qualified=stats.get("qualified"),
            games=stats.get("games", 0),
            actions=stats.get("actions", 0),
            hu=stats.get("hu", 0),
            response_409=stats.get("response_409",
                                   stats.get("err409", 0)),
            post_uncertain=stats.get("post_uncertain", 0),
            stage_transitions=stats.get("stage_transitions", []),
            diagnostics={
                "tournament_warnings": stats.get("tournament_warnings", []),
                "tournament_diagnostic": stats.get(
                    "tournament_diagnostic", {}),
            },
            error=(str(stats["error"]) if stats.get("error") is not None
                   else None),
        )
        return result

    def run(self, max_games=None):
        secrets = (self.token,)
        try:
            self.api = _make_api(
                self.api_factory, self.server, self.token, self.state_rate,
                dump=self.dump, label=self.label, dump_dir=self.dump_dir,
                secrets=secrets)
            if self.record:
                self._recorder = _make_recorder(
                    self.recorder_factory, root=self.recorder_root,
                    label=self.label, replay_trace=self.replay_trace,
                    trace_root=self.trace_root, secrets=secrets)
            self._preflight()
            # Preflight is deliberately inside the same resource boundary as
            # game execution: a token that is unbound or unauthorized still
            # closes a recorder created for the attempted run.
        except RuntimeError as exc:
            reason = str(exc)
            if reason not in ("TOKEN_NOT_BOUND", "INTERRUPTED"):
                reason = "PROTOCOL_FATAL"
            result = self._failure(reason, None)
            self._close_recorder()
            return result
        except ApiError as exc:
            result = self._failure("AUTH_FAILED" if _is_auth_error(exc)
                                   else "PROTOCOL_FATAL", exc)
            self._close_recorder()
            return result
        except SystemExit as exc:
            result = self._failure("PROTOCOL_FATAL", exc)
            self._close_recorder()
            return result
        except Exception as exc:
            result = self._failure("PROTOCOL_FATAL", exc)
            self._close_recorder()
            return result
        try:
            decide = make_decide(self.strategy, self.ckpt,
                                 evaluator=self.evaluator)
            self.bot = _make_bot(
                self.bot_factory, self.api, self.label, decide,
                self._recorder, self._log)
            configure = getattr(self.bot, "configure_tournament", None)
            if configure is not None:
                configure(self.context)
            else:
                # Minimal fakes may not implement formal context injection;
                # keep their observable attributes useful without changing
                # the production path.
                self.bot.tournament_context = self.context
                self.bot.tournament_rules = self.rules
            stats = self.bot.run(max_games=max_games, stop=self.stop)
            return self._result_from_bot(stats)
        except KeyboardInterrupt:
            if self.stop is not None:
                self.stop.set()
            return self._failure("INTERRUPTED")
        except ApiError as exc:
            return self._failure("AUTH_FAILED" if _is_auth_error(exc)
                                 else "PROTOCOL_FATAL", exc)
        except SystemExit as exc:
            return self._failure("PROTOCOL_FATAL", exc)
        except Exception as exc:
            return self._failure("PROTOCOL_FATAL", exc)
        finally:
            # BotClient normally closes its recorder.  This is idempotent and
            # also covers preflight failures and minimal test doubles.
            self._close_recorder()


def run_tournament(cfg, *, strategy="policy", ckpt=None, evaluator="legacy",
                   state_rate=15.0, dump=False, no_recorder=False,
                   replay_trace=False, trace_root=None,
                   max_games_debug=None, stop=None, **worker_kwargs):
    """Run configured token workers concurrently and return safe results."""
    shared_stop = stop or threading.Event()
    results = {}
    lock = threading.Lock()
    workers = []

    def run_one(label, token):
        worker = TournamentWorker(
            label, cfg["server"], token, strategy=strategy, ckpt=ckpt,
            evaluator=evaluator, state_rate=state_rate, dump=dump,
            recorder=not no_recorder, replay_trace=replay_trace,
            trace_root=trace_root, stop=shared_stop, **worker_kwargs)
        result = worker.run(max_games=max_games_debug)
        with lock:
            results[label] = result.as_dict()

    try:
        for label, token in cfg["tokens"].items():
            thread = threading.Thread(target=run_one, args=(label, token),
                                      name=f"tournament:{label}")
            thread.start()
            workers.append(thread)
        for thread in workers:
            thread.join()
    except KeyboardInterrupt:
        shared_stop.set()
        for thread in workers:
            thread.join(timeout=5)
        for label in cfg["tokens"]:
            results.setdefault(label, TournamentResult(
                token_label=label,
                termination_reason="INTERRUPTED").as_dict())

    return redact_value(results, [token for token in cfg["tokens"].values()])


def build_parser():
    parser = argparse.ArgumentParser(
        description="正式锦标赛 runner（由报名令牌和平台权威状态驱动）")
    parser.add_argument("--config", default="local/platform.json")
    parser.add_argument("--strategy", default="policy",
                        choices=("policy", "bot", "random"))
    parser.add_argument("--ckpt", default="runs/bc0/best.pt",
                        help="policy 策略 checkpoint")
    parser.add_argument("--bot-evaluator", default="legacy",
                        choices=("legacy", "shape-v1"),
                        help="strategy=bot 时的评价器")
    parser.add_argument("--state-rate", type=float, default=15.0,
                        help="每 token /state 主动限速(默认 15/s)")
    parser.add_argument("--dump", action="store_true",
                        help="安全的 state/action 原始摘要写入 local/logs/")
    parser.add_argument("--no-recorder", action="store_true",
                        help="关闭正式赛证据记录（不安全，会丢失不可再生证据）")
    parser.add_argument("--replay-trace", action="store_true",
                        help="启用本地 replay trace 侧车")
    parser.add_argument("--trace-root", default=None,
                        help="trace 侧车目录")
    parser.add_argument("--max-games-debug", type=int, default=None,
                        help="仅调试；正式锦标赛不要使用，会导致提前离赛")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        cfg = load_tournament_config(args.config)
    except TournamentConfigError as exc:
        raise SystemExit(str(exc)) from None
    if args.no_recorder:
        print("警告：已关闭正式锦标赛 Recorder，证据将被丢弃。", flush=True)
    if args.max_games_debug is not None:
        print("警告：--max-games-debug 仅调试；正式锦标赛不要使用，会导致提前离赛",
              flush=True)
    stop = threading.Event()
    try:
        results = run_tournament(
            cfg, strategy=args.strategy, ckpt=args.ckpt,
            evaluator=args.bot_evaluator, state_rate=args.state_rate,
            dump=args.dump, no_recorder=args.no_recorder,
            replay_trace=args.replay_trace, trace_root=args.trace_root,
            max_games_debug=args.max_games_debug, stop=stop)
    except KeyboardInterrupt:
        stop.set()
        results = {label: TournamentResult(
            token_label=label, termination_reason="INTERRUPTED").as_dict()
            for label in cfg["tokens"]}
    print("\n===== 正式锦标赛汇总 =====")
    print(json.dumps(results, ensure_ascii=False, sort_keys=True))
    fatal = any(result.get("termination_reason") in {
        "AUTH_FAILED", "PROTOCOL_FATAL",
    } for result in results.values())
    return 1 if fatal else 0


if __name__ == "__main__":
    raise SystemExit(main())
