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


def main(argv=None):
    ap = argparse.ArgumentParser(description="自由对战(/api/match)挂机 runner")
    ap.add_argument("--config", default="local/platform.json")
    ap.add_argument("--strategy", default="policy",
                    choices=("policy", "bot", "random"))
    ap.add_argument("--ckpt", default="runs/ppo4/ckpt_350000.pt",
                    help="policy 策略 checkpoint(BC best.pt 或 PPO ckpt)")
    ap.add_argument("--games", type=int, default=10,
                    help="打满场数(以整房为退出粒度,1 房 = 10 场)")
    ap.add_argument("--dump", action="store_true",
                    help="原始 state/action JSON dump 到 local/logs/")
    args = ap.parse_args(argv)

    cfg = load_match_config(args.config)
    decide = make_decide(args.strategy, args.ckpt)
    api = Api(cfg["server"], cfg["match_token"])
    name = api.me().get("user_id") or "match"
    if args.dump:
        api = DumpingApi(cfg["server"], cfg["match_token"], name,
                         "local/logs")
    recorder = Recorder()
    bot = BotClient(api, name, decide,
                    log=lambda m: (print(f"[{name}] {m}", flush=True)),
                    recorder=recorder, mode="match")
    stop = threading.Event()
    try:
        stats = bot.run_match(max_games=args.games, stop=stop)
    except KeyboardInterrupt:
        print("\n收到中断,停止对弈线程…")
        stop.set()
        stats = bot.stats
    except ApiError as e:
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
