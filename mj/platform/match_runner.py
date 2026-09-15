"""自由对战 runner:单全局令牌 /api/match 挂机攒对局数据。

用法:
  python3 -m mj.platform.match_runner --games 20
  python3 -m mj.platform.match_runner --strategy policy \
      --ckpt runs/ppo4/ckpt_350000.pt --games 100 [--dump] [--no-recorder]

语义(指南 v13/v15/v24,详见 PROGRESS.md P4):
- POST /api/match 入席 auto 房(满 4 人开 M=10 场 × Rounds=8 局,座次
  逐场重洗),打完整房 ~60s 宽限关停后自动 re-match,直至打满
  --games 场(以整房为退出粒度,不中途弃房)或 Ctrl+C;
- 令牌须为门户「我的 AI 身份」签发的绑定全局令牌(local/platform.json
  的 match_token 字段;匿名/测试房令牌会被永久 403 / 400);
- 对局日志与测试房同构(local/games/<日期>/<user_id>_<gid>.jsonl),
  meta.mode=match,log2data --mode match 可单独取数;
- 409 MATCH_BUSY / MATCH_LIMIT_REACHED / 网络抖动自动退避重试;
- auto 房整场打完会计入门户排行榜(积分榜/胡大牌榜/单场得分榜)。
"""

import argparse
import json
import threading

from .api import Api, ApiError
from .bot_client import BotClient
from .config import load_match_config
from .recorder import Recorder
from .runner import DumpingApi, make_decide


def bot_transport_options(no_long_poll=False, no_notify=False):
    """将 CLI 对照开关映射为 BotClient 的传输模式。

    默认使用 SSE 通知 + ``/state?seq=N`` 拉取增量。``--no-notify``
    关闭 SSE 并退回普通主动轮询；``--no-long-poll`` 保留为兼容旧命令，
    不再改变默认模式。SSE 帧只作唤醒信号，不能直接推进本地游标。
    """
    return {
        "use_notify": not no_notify,
        "long_poll": False,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="自由对战(/api/match)挂机 runner")
    ap.add_argument("--config", default="local/platform.json")
    ap.add_argument("--strategy", default="policy",
                    choices=("policy", "bot", "random"))
    ap.add_argument("--bot-evaluator", default="legacy",
                    choices=("legacy", "shape-v1"),
                    help="strategy=bot 时的评价器(默认 legacy)")
    ap.add_argument("--ckpt", default="runs/ppo4/ckpt_350000.pt",
                    help="policy 策略 checkpoint(BC best.pt 或 PPO ckpt)")
    ap.add_argument("--games", type=int, default=10,
                    help="打满场数(以整房为退出粒度,1 房 = 10 场)")
    ap.add_argument("--state-rate", type=float, default=15.0,
                    help="每令牌 /state 主动限速(默认 15/s)")
    ap.add_argument("--no-state-throttle", action="store_true",
                    help="关闭 /state 主动限速(仅排障/回滚)")
    ap.add_argument("--no-notify", action="store_true",
                    help="关闭 /notify SSE 事件驱动,退回纯轮询(排障/对照)")
    ap.add_argument("--no-long-poll", action="store_true",
                    help="兼容旧参数；当前默认已使用 SSE + /state 增量")
    ap.add_argument("--dump", action="store_true",
                    help="原始 state/action JSON dump 到 local/logs/")
    ap.add_argument("--replay-trace", action="store_true",
                    help="启用本地 replay trace 侧车(默认关闭，不改变对局行为)")
    ap.add_argument("--trace-root", default=None,
                    help="trace 侧车目录(默认跟随 local/games)")
    args = ap.parse_args(argv)

    cfg = load_match_config(args.config)
    decide = (make_decide(args.strategy, args.ckpt)
              if args.bot_evaluator == "legacy"
              else make_decide(args.strategy, args.ckpt,
                               evaluator=args.bot_evaluator))
    state_rate = None if args.no_state_throttle else args.state_rate
    api = Api(cfg["server"], cfg["match_token"], state_rate=state_rate)
    name = api.me().get("user_id") or "match"
    if args.dump:
        api = DumpingApi(cfg["server"], cfg["match_token"], name,
                         "local/logs", state_rate=state_rate)
    transport = bot_transport_options(no_long_poll=args.no_long_poll,
                                      no_notify=args.no_notify)
    recorder = Recorder(replay_trace=args.replay_trace,
                        trace_root=args.trace_root)
    bot = BotClient(api, name, decide,
                    log=lambda m: (print(f"[{name}] {m}", flush=True)),
                    recorder=recorder, mode="match",
                    **transport)
    stop = threading.Event()
    try:
        stats = bot.run_match(max_games=args.games, stop=stop)
    except KeyboardInterrupt:
        print("\n收到中断,停止对弈线程…")
        stop.set()
        stats = bot.stats
    except ApiError as e:
        if e.status == 403 and e.code == "FEATURE_DISABLED":
            raise SystemExit(
                "match 功能当前未启用(HTTP 403 FEATURE_DISABLED):"
                f"{e.message}") from e
        if e.status in (401, 403) or e.code == "TOKEN_NOT_SCOPED":
            raise SystemExit(
                f"match 永久被拒(HTTP {e.status} {e.code}):{e.message}\n"
                "  match_token 须为门户『我的 AI 身份』签发的绑定全局令牌,"
                "匿名/测试房令牌不可用(v24)。") from e
        raise
    print("\n===== 汇总 =====")
    print(json.dumps({k: v for k, v in stats.items() if k != "scores"},
                     ensure_ascii=False))
    for sc in stats.get("scores", []):
        print(f"  终局积分: {sc}")


if __name__ == "__main__":
    main()
