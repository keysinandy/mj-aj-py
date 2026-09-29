"""测试房 runner:4 线程各持一令牌对弈(跨轮复用打满 N 场)。

用法:
  python3 -m mj.platform.runner --config local/platform.json \
      --strategy policy --ckpt runs/bc0/best.pt --games 3 [--dump]

策略:policy(policy_player 加载 checkpoint)/ bot(启发式)/
random(随机合法,规则覆盖探针用)。
"""

import argparse
import json
import os
import random
import sys
import threading
import time

from .api import Api
from .bot_client import BotClient
from .config import load_config
from .recorder import Recorder
from .security import redact_exception, redact_value
from ..legacy_eval import (
    DEFAULT_BOT_EVALUATOR,
    LEGACY_V2_EVALUATORS,
    canonical_evaluator,
)


def make_decide(strategy, ckpt=None, evaluator=DEFAULT_BOT_EVALUATOR, model=None,
                policy_profile=None, marginal_structure_guard_enabled=None,
                speed_band_enabled=None, pareto_frontier_enabled=None,
                speed_band_min_ratio_by_shanten=None):
    from ..strategy_runtime import snapshot_for_config

    if strategy == "policy-v3" or (
            strategy == "bot" and evaluator in ("policy-v3", "policy_v3")):
        from mj.decision.policy_v3 import PolicyV3Runtime, load_policy_value_model
        if model is None and ckpt:
            try:
                if os.path.exists(ckpt):
                    model = load_policy_value_model(ckpt)
            except Exception as exc:
                model = None
                load_error = f"checkpoint_load:{type(exc).__name__}"
            else:
                load_error = None
        else:
            load_error = None
        runtime = PolicyV3Runtime(model, profile=policy_profile)
        if load_error is not None and runtime.model_error == "model_missing":
            runtime.model_error = load_error
        def play(g, seat):
            return runtime.choose(g, seat, return_evaluation=True)
        play.bot_evaluator = "policy-v3"
        play.bot_strategy = "policy-v3"
        play.policy_v3_runtime = runtime
        from ..strategy_runtime import strategy_snapshot
        model_path = ckpt or (model if isinstance(model, str) else None)
        play.strategy_snapshot = strategy_snapshot(
            "policy-v3", "policy-v3", profile=runtime.profile,
            model_name=os.path.basename(model_path) if model_path else None)
        return play
    if strategy == "policy":
        from mj.evaluate import policy_player
        if not ckpt or not os.path.exists(ckpt):
            raise SystemExit(f"--ckpt 不存在: {ckpt}")
        play = policy_player(ckpt)
        play.bot_strategy = "policy"
        play.bot_evaluator = "policy"
        play.strategy_snapshot = snapshot_for_config({
            "strategy": "policy", "evaluator": "policy",
            "model_name": os.path.basename(ckpt),
        })
        return play
    if strategy == "bot":
        from mj.bot import choose_action
        profile = canonical_evaluator(evaluator or DEFAULT_BOT_EVALUATOR)
        if profile in ("shape-v1", "shape_v1", "shape"):
            from mj.hand_eval import warmup
            warmup("shape-v1")
        def play(g, seat):
            if profile in ("shape-v1", "shape_v1", "shape",
                           "shape-v2", "shape_v2", "ev2",
                           "legacy-two-ply-v1", "legacy_v1", "legacy-v1",
                           *LEGACY_V2_EVALUATORS):
                requested = ("shape-v2" if profile in
                             ("shape-v2", "shape_v2", "ev2")
                             else ("legacy-two-ply-v1" if profile in
                                   ("legacy-two-ply-v1", "legacy_v1",
                                    "legacy-v1") else
                                   (DEFAULT_BOT_EVALUATOR if
                                    profile in LEGACY_V2_EVALUATORS else
                                    "shape-v1")))
                return choose_action(g, seat, evaluator=requested,
                                     marginal_structure_guard_enabled=(
                                         marginal_structure_guard_enabled),
                                     speed_band_enabled=speed_band_enabled,
                                     pareto_frontier_enabled=(
                                         pareto_frontier_enabled),
                                     speed_band_min_ratio_by_shanten=(
                                         speed_band_min_ratio_by_shanten),
                                     return_evaluation=True)
            action = choose_action(g, seat)
            return action, {"version": "legacy", "profile": "legacy",
                            "level": "legacy", "selected": action,
                            "reason": "legacy_evaluator", "candidates": []}
        # BotClient uses this immutable marker only for recorder metadata.
        play.bot_evaluator = profile
        play.bot_strategy = "bot"
        snapshot_config = {"strategy": "bot", "evaluator": profile}
        if profile in LEGACY_V2_EVALUATORS:
            snapshot_config["marginal_structure_guard_enabled"] = (
                True if marginal_structure_guard_enabled is None else
                bool(marginal_structure_guard_enabled))
            if speed_band_enabled is not None:
                snapshot_config["speed_band_enabled"] = bool(
                    speed_band_enabled)
            if pareto_frontier_enabled is not None:
                snapshot_config["pareto_frontier_enabled"] = bool(
                    pareto_frontier_enabled)
            if speed_band_min_ratio_by_shanten is not None:
                snapshot_config["speed_band_min_ratio_by_shanten"] = (
                    speed_band_min_ratio_by_shanten)
        play.strategy_snapshot = snapshot_for_config(snapshot_config)
        return play
    if strategy == "random":
        rng = random.Random()
        def play(g, seat):
            return rng.choice(g.legal_actions())
        play.bot_strategy = "random"
        play.bot_evaluator = "random"
        play.strategy_snapshot = snapshot_for_config({
            "strategy": "random", "evaluator": "random",
        })
        return play
    raise SystemExit(f"未知策略 {strategy}")


class DumpingApi(Api):
    """原始请求/响应 dump 到目录(首跑探针用)。"""

    def __init__(self, server, token, name, dump_dir, **api_kwargs):
        self._redact_secrets = api_kwargs.pop("redact_secrets", (token,))
        super().__init__(server, token, **api_kwargs)
        self.name = name
        self.dump_dir = dump_dir
        self._n = 0
        self._dump_lock = threading.Lock()
        os.makedirs(dump_dir, exist_ok=True)

    def _dump(self, kind, payload):
        # match 模式的十个场次线程共享一个 DumpingApi；编号和写文件
        # 必须一并串行化，否则并发动作会覆盖 dump 或复用编号。
        with self._dump_lock:
            self._n += 1
            path = os.path.join(self.dump_dir,
                                f"{self.name}_{self._n:04d}_{kind}.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(redact_value(payload, self._redact_secrets), f,
                          ensure_ascii=False, indent=1)

    def game_state(self, gid, seq, deadline=None, request_timeout=None,
                   logical_request_id=None, reason=None, generation=None,
                   transport_request_id=None, state_ticket=None,
                   candidate_id=None, state_throttle=None,
                   cancel_check=None):
        try:
            r = super().game_state(gid, seq, deadline=deadline,
                                   request_timeout=request_timeout,
                                   logical_request_id=logical_request_id,
                                   reason=reason, generation=generation,
                                   transport_request_id=transport_request_id,
                                   state_ticket=state_ticket,
                                   candidate_id=candidate_id,
                                   state_throttle=state_throttle,
                                   cancel_check=cancel_check)
        except Exception as e:
            self._dump("state", {"gid": gid, "seq": seq,
                                  "deadline": deadline,
                                  "request_timeout": request_timeout,
                                  "logical_request_id": logical_request_id,
                                  "transport_request_id": transport_request_id,
                                  "error": _dump_error(
                                      e, self._redact_secrets)})
            raise
        self._dump("state", {"gid": gid, "seq": seq,
                              "deadline": deadline,
                              "request_timeout": request_timeout,
                              "logical_request_id": logical_request_id,
                              "transport_request_id": transport_request_id,
                              "res": r})
        return r

    def game_action(self, gid, payload, deadline=None):
        try:
            r = super().game_action(gid, payload, deadline=deadline)
        except Exception as e:
            # 传输异常也要落盘，尤其是 uncertain=True 的响应丢失；这
            # 能和客户端随后的 seq=0 重锚在日志中对应起来。
            self._dump("action", {"gid": gid, "payload": payload,
                                   "deadline": deadline,
                                   "error": _dump_error(
                                       e, self._redact_secrets)})
            raise
        self._dump("action", {"gid": gid, "payload": payload,
                               "deadline": deadline, "res": r})
        return r


def _dump_error(exc, secrets=()):
    """把异常转成稳定、可 JSON 序列化的传输诊断。"""
    return redact_exception(exc, secrets)


def run_room(cfg, strategy="policy", ckpt=None, games=1, dump=False,
             dump_dir="local/logs", record=True, state_rate=16.0,
             evaluator=DEFAULT_BOT_EVALUATOR, replay_trace=False,
             trace_root=None):
    tokens = cfg["tokens"]
    stop = threading.Event()
    results = {}
    lock = threading.Lock()
    recorder = (Recorder(replay_trace=replay_trace, trace_root=trace_root)
                if record else None)

    def worker(name, token):
        decide = (make_decide(strategy, ckpt) if evaluator == "legacy"
                  else make_decide(strategy, ckpt, evaluator=evaluator))
        api = DumpingApi(cfg["server"], token, name, dump_dir,
                         state_rate=state_rate) if dump \
            else Api(cfg["server"], token, state_rate=state_rate)
        bot = BotClient(
            api, name, decide,
            log=lambda m: (print(f"[{name}] {m}", flush=True)),
            recorder=recorder)
        try:
            stats = bot.run(max_games=games, stop=stop)
        except Exception as e:
            print(f"[{name}] 异常退出: {type(e).__name__}: {e}", flush=True)
            stats = {"error": f"{type(e).__name__}: {e}",
                     **bot.stats}
        with lock:
            results[name] = stats

    threads = [threading.Thread(target=worker, args=(n, t), name=n)
               for n, t in tokens.items()]
    for t in threads:
        t.start()
    try:
        for t in threads:
            t.join()
    except KeyboardInterrupt:
        print("\n收到中断,停止各线程…")
        stop.set()
        for t in threads:
            t.join()
    return results


def main(argv=None):
    ap = argparse.ArgumentParser(description="测试房 4 令牌 runner")
    ap.add_argument("--config", default="local/platform.json")
    ap.add_argument("--strategy", default="policy",
                    choices=("policy", "bot", "random", "policy-v3"))
    ap.add_argument("--bot-evaluator", default=DEFAULT_BOT_EVALUATOR,
                    choices=("legacy", "legacy-two-ply-v1", "legacy-v1",
                             "legacyV2", "legacy-v2",
                             "weighted-two-ply-frontier-v1", "shape-v1",
                             "shape-v2", "policy-v3"),
                    help="strategy=bot 时的评价器(默认 legacyV2)")
    ap.add_argument("--ckpt", default="runs/bc0/best.pt")
    ap.add_argument("--games", type=int, default=1, help="打满场数(跨轮复用)")
    ap.add_argument("--dump", action="store_true",
                    help="原始 state/action JSON dump 到 local/logs/")
    ap.add_argument("--no-recorder", action="store_true",
                    help="关闭结构化对局日志(默认写 local/games/,"
                         "正式赛数据不可再生,建议保持开启)")
    ap.add_argument("--state-rate", type=float, default=16.0,
                    help="每令牌 /state 主动限速(默认 16/s)")
    ap.add_argument("--no-state-throttle", action="store_true",
                    help="关闭 /state 主动限速(仅排障/回滚)")
    ap.add_argument("--replay-trace", action="store_true",
                    help="启用本地 replay trace 侧车(默认关闭，不改变对局行为)")
    ap.add_argument("--trace-root", default=None,
                    help="trace 侧车目录(默认跟随 local/games)")
    args = ap.parse_args(argv)
    cfg = load_config(args.config)
    # 启动诊断:缺兼容版本的 native weighted 内核时 v2 会事务性回退 v1；
    # 启动时显式报告实际 ABI,避免把旧 wheel 显示成可用。
    from ..shanten import format_kernel_diagnostic

    print(format_kernel_diagnostic(), flush=True)
    results = run_room(cfg, strategy=args.strategy, ckpt=args.ckpt,
                       games=args.games, dump=args.dump,
                       record=not args.no_recorder,
                       state_rate=None if args.no_state_throttle
                       else args.state_rate,
                       evaluator=args.bot_evaluator,
                       replay_trace=args.replay_trace,
                       trace_root=args.trace_root)
    print("\n===== 汇总 =====")
    for name, st in results.items():
        print(f"{name}: {json.dumps(st, ensure_ascii=False)}")


if __name__ == "__main__":
    main()
