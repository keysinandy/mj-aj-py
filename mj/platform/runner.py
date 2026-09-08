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


def make_decide(strategy, ckpt=None):
    if strategy == "policy":
        from mj.evaluate import policy_player
        if not ckpt or not os.path.exists(ckpt):
            raise SystemExit(f"--ckpt 不存在: {ckpt}")
        return policy_player(ckpt)
    if strategy == "bot":
        from mj.bot import choose_action
        return choose_action
    if strategy == "random":
        rng = random.Random()
        return lambda g, seat: rng.choice(g.legal_actions())
    raise SystemExit(f"未知策略 {strategy}")


class DumpingApi(Api):
    """原始请求/响应 dump 到目录(首跑探针用)。"""

    def __init__(self, server, token, name, dump_dir):
        super().__init__(server, token)
        self.name = name
        self.dump_dir = dump_dir
        self._n = 0
        os.makedirs(dump_dir, exist_ok=True)

    def _dump(self, kind, payload):
        self._n += 1
        path = os.path.join(self.dump_dir,
                            f"{self.name}_{self._n:04d}_{kind}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)

    def game_state(self, gid, seq):
        r = super().game_state(gid, seq)
        self._dump("state", {"gid": gid, "seq": seq, "res": r})
        return r

    def game_action(self, gid, payload):
        r = super().game_action(gid, payload)
        self._dump("action", {"gid": gid, "payload": payload, "res": r})
        return r


def run_room(cfg, strategy="policy", ckpt=None, games=1, dump=False,
             dump_dir="local/logs", record=True):
    tokens = cfg["tokens"]
    stop = threading.Event()
    results = {}
    lock = threading.Lock()
    recorder = Recorder() if record else None

    def worker(name, token):
        decide = make_decide(strategy, ckpt)
        api = DumpingApi(cfg["server"], token, name, dump_dir) if dump \
            else Api(cfg["server"], token)
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
    ap.add_argument("--ckpt", default="runs/bc0/best.pt")
    ap.add_argument("--games", type=int, default=1, help="打满场数(跨轮复用)")
    ap.add_argument("--dump", action="store_true",
                    help="原始 state/action JSON dump 到 local/logs/")
    ap.add_argument("--no-recorder", action="store_true",
                    help="关闭结构化对局日志(默认写 local/games/,"
                         "正式赛数据不可再生,建议保持开启)")
    args = ap.parse_args(argv)
    cfg = load_config(args.config)
    results = run_room(cfg, strategy=args.strategy, ckpt=args.ckpt,
                       games=args.games, dump=args.dump,
                       record=not args.no_recorder)
    print("\n===== 汇总 =====")
    for name, st in results.items():
        print(f"{name}: {json.dumps(st, ensure_ascii=False)}")


if __name__ == "__main__":
    main()
