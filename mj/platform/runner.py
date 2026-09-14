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


def make_decide(strategy, ckpt=None, evaluator="legacy"):
    if strategy == "policy":
        from mj.evaluate import policy_player
        if not ckpt or not os.path.exists(ckpt):
            raise SystemExit(f"--ckpt 不存在: {ckpt}")
        return policy_player(ckpt)
    if strategy == "bot":
        from mj.bot import choose_action
        profile = evaluator or "legacy"
        if profile in ("shape-v1", "shape_v1", "shape"):
            from mj.hand_eval import warmup
            warmup("shape-v1")
        def play(g, seat):
            if profile in ("shape-v1", "shape_v1", "shape"):
                return choose_action(g, seat, evaluator="shape-v1",
                                     return_evaluation=True)
            action = choose_action(g, seat)
            return action, {"version": "legacy", "profile": "legacy",
                            "level": "legacy", "selected": action,
                            "reason": "legacy_evaluator", "candidates": []}
        # BotClient uses this immutable marker only for recorder metadata.
        play.bot_evaluator = profile
        return play
    if strategy == "random":
        rng = random.Random()
        return lambda g, seat: rng.choice(g.legal_actions())
    raise SystemExit(f"未知策略 {strategy}")


class DumpingApi(Api):
    """原始请求/响应 dump 到目录(首跑探针用)。"""

    def __init__(self, server, token, name, dump_dir, **api_kwargs):
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
                json.dump(payload, f, ensure_ascii=False, indent=1)

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
                                  "error": _dump_error(e)})
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
                                   "error": _dump_error(e)})
            raise
        self._dump("action", {"gid": gid, "payload": payload,
                               "deadline": deadline, "res": r})
        return r


def _dump_error(exc):
    """把异常转成稳定、可 JSON 序列化的传输诊断。"""
    return {
        "type": type(exc).__name__,
        "message": str(exc),
        "status": getattr(exc, "status", None),
        "code": getattr(exc, "code", ""),
        "uncertain": bool(getattr(exc, "uncertain", False)),
        "timed_out": bool(getattr(exc, "timed_out", False)),
        "deadline_exceeded": bool(getattr(exc, "deadline_exceeded", False)),
        "attempts": getattr(exc, "attempts", None),
    }


def run_room(cfg, strategy="policy", ckpt=None, games=1, dump=False,
             dump_dir="local/logs", record=True, state_rate=15.0,
             evaluator="legacy"):
    tokens = cfg["tokens"]
    stop = threading.Event()
    results = {}
    lock = threading.Lock()
    recorder = Recorder() if record else None

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
                    choices=("policy", "bot", "random"))
    ap.add_argument("--bot-evaluator", default="legacy",
                    choices=("legacy", "shape-v1"),
                    help="strategy=bot 时的评价器(默认 legacy)")
    ap.add_argument("--ckpt", default="runs/bc0/best.pt")
    ap.add_argument("--games", type=int, default=1, help="打满场数(跨轮复用)")
    ap.add_argument("--dump", action="store_true",
                    help="原始 state/action JSON dump 到 local/logs/")
    ap.add_argument("--no-recorder", action="store_true",
                    help="关闭结构化对局日志(默认写 local/games/,"
                         "正式赛数据不可再生,建议保持开启)")
    ap.add_argument("--state-rate", type=float, default=15.0,
                    help="每令牌 /state 主动限速(默认 15/s)")
    ap.add_argument("--no-state-throttle", action="store_true",
                    help="关闭 /state 主动限速(仅排障/回滚)")
    args = ap.parse_args(argv)
    cfg = load_config(args.config)
    results = run_room(cfg, strategy=args.strategy, ckpt=args.ckpt,
                       games=args.games, dump=args.dump,
                       record=not args.no_recorder,
                       state_rate=None if args.no_state_throttle
                       else args.state_rate,
                       evaluator=args.bot_evaluator)
    print("\n===== 汇总 =====")
    for name, st in results.items():
        print(f"{name}: {json.dumps(st, ensure_ascii=False)}")


if __name__ == "__main__":
    main()
