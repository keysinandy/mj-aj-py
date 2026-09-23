#!/usr/bin/env python3
"""正式锦标赛一键启动器(macOS / Windows 通用)。

包装 ``python -m mj.platform.tournament_runner``,补上跨平台启动器该做的三件事:

1. 预检:配置/令牌/ckpth 硬校验 + 安全护栏(见 evaluate_guards),
   并打印锦标赛关键配置(StartAt/YCBK/赛制)供人工确认;
2. 日志:子进程输出实时 tee 到 local/tournament_<评价器>_<时间戳>.log
   (PYTHONUNBUFFERED=1,修掉 README §3 的 stdout 块缓冲滞后问题);
3. 守护:runner 意外退出(PROTOCOL_FATAL 等)后先探活房间
   (api.me() / api.tournament()),房间还在才退避重启。
   依据 2026-09-17「1024杭麻竞技二测」实弹:瞬时 404 曾两次错杀
   worker,重启无副作用(报名在服务端,新进程经 active_games 接管)。

开赛前的准备情况(registering/stage_open)由 runner 本体以 1s 轮询,
本脚本不重复造;Ctrl-C 转发给子进程触发 INTERRUPTED 优雅收尾。

用法:
    python3 scripts/tournament.py                        # bot + legacyV2(线上首选)
    python3 scripts/tournament.py --bot-evaluator shape-v1
    python3 scripts/tournament.py --strategy policy --ckpt runs/bc0/best.pt
    python3 scripts/tournament.py --dry-run              # 只预检打印配置,不起 runner
"""

from __future__ import annotations

import argparse
import datetime as _dt
import os
import signal
import subprocess
import sys
import threading
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from mj.platform.api import Api, ApiError  # noqa: E402
from mj.platform.config import (  # noqa: E402
    TournamentConfigError, load_tournament_config)
from mj.platform.tournament import TERMINAL_STATES  # noqa: E402
from mj.legacy_eval import DEFAULT_BOT_EVALUATOR  # noqa: E402

STRATEGIES = ("policy", "bot", "random", "policy-v3")
EVALUATORS = ("legacy", "legacy-v1", "legacy-two-ply-v1",
              "legacyV2", "legacy-v2",
              "weighted-two-ply-frontier-v1", "shape-v1", "shape-v2",
              "policy-v3")

# strategy=bot 时 shape-v2 需显式解锁:线上镜像材料守恒门禁未过
# (2026-09-17 实跑 ~60% 受控回退,未崩溃但混合决策,PROGRESS.md 有结论)。
SHAPE_V2_EVALUATORS = ("shape-v2",)


def evaluate_guards(*, strategy, evaluator, no_recorder,
                    accept_no_recorder, accept_shape_v2):
    """启动前的安全护栏,返回错误信息列表(空 = 放行)。

    把 docs/锦标赛README.md 的血泪教训做成硬约束:
    - 正式赛 Recorder 是唯一可复盘数据源,关闭必须显式解锁;
    - bot + shape-v2 线上门禁未过,必须显式解锁。
    """
    errors = []
    if no_recorder and not accept_no_recorder:
        errors.append(
            "正式赛对局日志是唯一可复盘数据源,拒绝 --no-recorder;"
            "确认放弃证据请再加 --accept-no-recorder")
    if (strategy == "bot" and evaluator in SHAPE_V2_EVALUATORS
            and not accept_shape_v2):
        errors.append(
            "bot + shape-v2 线上镜像材料守恒门禁未过(实跑 ~60% 回退);"
            "确认要用请再加 --accept-shape-v2")
    return errors


def summarize_rules(config):
    """把 /rules.config 原文整理成人读摘要行(纯函数,便于测试)。"""
    if not isinstance(config, dict):
        return ["rules.config 非对象(原文:%r)" % (config,)]

    def _num(key):
        v = config.get(key)
        return v if isinstance(v, (int, float)) and not isinstance(v, bool) \
            else None

    lines = []
    name = config.get("Name") or "(未命名)"
    lines.append(f"锦标赛:{name}")
    start_at = _num("StartAt")
    if start_at is not None:
        start = _dt.datetime.fromtimestamp(int(start_at), _dt.timezone.utc) \
            .astimezone()
        lines.append(f"开赛时刻:{start:%Y-%m-%d %H:%M}({start.tzname()})"
                     f";当前{_dt.datetime.now().astimezone():%H:%M}")
    deadline = _num("RegisterDeadlineAt")
    if deadline is not None:
        dl = _dt.datetime.fromtimestamp(int(deadline), _dt.timezone.utc) \
            .astimezone()
        lines.append(f"报名截止:{dl:%Y-%m-%d %H:%M}")
    m, rounds = _num("M"), _num("Rounds")
    if m is not None and rounds is not None:
        lines.append(f"赛制:每场 {int(rounds)} 局 × M={int(m)} 并发场次")
    if "YouCaiBiKao" in config:
        ycbk = "开" if config.get("YouCaiBiKao") else "关"
        lines.append(f"有财必拷响(YCBK):{ycbk}(以 API 为准)")
    for key in ("DiscardTimeoutSec", "PengTimeoutSec", "ChiTimeoutSec"):
        if key in config:
            lines.append(f"{key}={config.get(key)}s")
    return lines


def room_should_restart(status):
    """探活判定的纯函数:房间状态 → 是否值得重启 runner。

    终态(finished/closed/void)不再重启;其余(registering/stage_open/
    running/stage_done)说明比赛仍在进行,值得重启。
    """
    return status not in TERMINAL_STATES


class RoomProbe:
    """令牌探活:me() 取绑定,再 tournament(tid) 取状态。

    探测本身允许瞬时错误(404/超时/5xx)重试——2026-09-17 实测
    registering 期存在房间在但接口 404 的情况。探测网络层失败
    仍无法判定时返回 "unknown",调用方按"可重启"处理
    (重启无害:runner 会自己做权威预检,真死了会再退出并计入次数)。
    """

    def __init__(self, server, token, api_factory=None, attempts=3):
        self._server = server
        self._token = token
        self._api_factory = api_factory or Api
        self._attempts = attempts

    def _is_transient(self, exc):
        if isinstance(exc, (TimeoutError, OSError, ConnectionError)):
            return True
        if isinstance(exc, ApiError):
            if exc.status == 404:
                # 2026-09-17 实弹(bot_client.py:690 同口径):瞬时 404
                # TOURNAMENT_GONE 房间仍在,应重试;其余 404(如
                # TOURNAMENT_NOT_FOUND)是房间真注销,不应重启。
                return exc.code == "TOURNAMENT_GONE"
            return (exc.status in (0, 408, 425, 429)
                    or 500 <= exc.status < 600)
        return False

    def _is_auth_error(self, exc):
        return isinstance(exc, ApiError) and exc.status in (401, 403)

    def probe(self):
        """返回 (verdict, detail)。verdict ∈ restart / stop / unknown。"""
        api = self._api_factory(self._server, self._token,
                                state_rate=None, state_throttle=False)
        me = None
        for attempt in range(self._attempts):
            try:
                me = api.me()
                break
            except Exception as exc:
                if self._is_auth_error(exc):
                    return "stop", f"令牌鉴权失败({exc.status})"
                if not self._is_transient(exc):
                    return "stop", f"me() 拒绝:{exc}"
                if attempt == self._attempts - 1:
                    return "unknown", f"me() 探测失败:{exc}"
                time.sleep(1.5)
        tid = (me or {}).get("tournament_id") or ""
        if not tid:
            return "stop", "令牌未绑定锦标赛(TOKEN_NOT_BOUND)"
        for attempt in range(self._attempts):
            try:
                resp = api.tournament(tid)
                status = (resp or {}).get("status", "")
                if room_should_restart(status):
                    return "restart", f"房间 {tid} 状态={status}"
                return "stop", f"房间 {tid} 已终态({status})"
            except Exception as exc:
                if self._is_auth_error(exc):
                    return "stop", f"令牌鉴权失败({exc.status})"
                if not self._is_transient(exc):
                    return "stop", f"房间不可达:{exc}"
                if attempt == self._attempts - 1:
                    return "unknown", f"tournament() 探测失败:{exc}"
                time.sleep(1.5)
        return "unknown", "unreachable"


def _pump(stream, sink, echo=True):
    for line in stream:
        sink.write(line)
        sink.flush()
        if echo:
            sys.stdout.write(line)
            sys.stdout.flush()


def _game_log_progress(root, state):
    """统计今日对局日志数;变化时打印一行(对局真在进行的权威信号)。"""
    while not state["stop"].is_set():
        today = _dt.datetime.now().strftime("%Y%m%d")
        day_dir = os.path.join(root, today)
        try:
            names = sorted(f for f in os.listdir(day_dir)
                           if f.endswith(".jsonl"))
        except OSError:
            names = []
        if len(names) != state["count"]:
            state["count"] = len(names)
            latest = names[-1] if names else "-"
            print(f"[进度] {today} 对局日志 {len(names)} 个"
                  f"(最新:{latest})", flush=True)
        state["stop"].wait(10.0)


def _forward_interrupt(proc):
    """跨平台把 Ctrl-C 转发给子进程(触发 runner 的 INTERRUPTED 收尾)。"""
    try:
        if os.name == "nt":
            os.kill(proc.pid, signal.CTRL_C_EVENT)
        else:
            proc.send_signal(signal.SIGINT)
    except (OSError, ValueError):
        pass


def build_runner_command(args):
    cmd = [sys.executable, "-m", "mj.platform.tournament_runner",
           "--config", args.config,
           "--strategy", args.strategy,
           "--bot-evaluator", args.bot_evaluator,
           "--state-rate", str(args.state_rate)]
    if args.strategy in ("policy", "policy-v3"):
        cmd += ["--ckpt", args.ckpt]
    if args.dump:
        cmd.append("--dump")
    if args.no_recorder:
        cmd.append("--no-recorder")
    if args.replay_trace:
        cmd.append("--replay-trace")
    if args.trace_root:
        cmd += ["--trace-root", args.trace_root]
    if args.max_games_debug is not None:
        cmd += ["--max-games-debug", str(args.max_games_debug)]
    return cmd


def build_parser():
    ap = argparse.ArgumentParser(
        description="正式锦标赛一键启动器(跨平台,包装 tournament_runner)")
    ap.add_argument("--config", default="local/platform.json")
    ap.add_argument("--strategy", default="bot", choices=STRATEGIES,
                    help="默认 bot(线上首选;runner 原默认 policy,此处已改)")
    ap.add_argument("--bot-evaluator", default=DEFAULT_BOT_EVALUATOR,
                    choices=EVALUATORS,
                    help="默认 legacyV2;可显式回退 legacy-v1")
    ap.add_argument("--ckpt", default="runs/bc0/best.pt")
    ap.add_argument("--state-rate", type=float, default=16.0)
    ap.add_argument("--dump", action="store_true")
    ap.add_argument("--no-recorder", action="store_true",
                    help="会被护栏拒绝,除非 --accept-no-recorder")
    ap.add_argument("--accept-no-recorder", action="store_true")
    ap.add_argument("--accept-shape-v2", action="store_true")
    ap.add_argument("--replay-trace", action="store_true")
    ap.add_argument("--trace-root", default=None)
    ap.add_argument("--max-games-debug", type=int, default=None)
    ap.add_argument("--max-restarts", type=int, default=5,
                    help="守护模式重启上限(意外退出且房间仍存活时)")
    ap.add_argument("--restart-delay", type=float, default=10.0,
                    help="重启退避基数(秒),按尝试次数翻倍)")
    ap.add_argument("--dry-run", action="store_true",
                    help="只预检并打印锦标赛配置,不启动 runner")
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)

    # ---- 1) 预检:配置文件 + 护栏 + checkpoint ----
    try:
        cfg = load_tournament_config(args.config)
    except TournamentConfigError as exc:
        print(f"配置错误:{exc}", file=sys.stderr)
        return 2
    errors = evaluate_guards(
        strategy=args.strategy, evaluator=args.bot_evaluator,
        no_recorder=args.no_recorder,
        accept_no_recorder=args.accept_no_recorder,
        accept_shape_v2=args.accept_shape_v2)
    if errors:
        for err in errors:
            print(f"护栏拒绝:{err}", file=sys.stderr)
        return 2
    if args.strategy in ("policy", "policy-v3") \
            and not os.path.isfile(args.ckpt):
        print(f"护栏拒绝:checkpoint 不存在:{args.ckpt}", file=sys.stderr)
        return 2

    token = next(iter(cfg["tokens"].values()))
    server = cfg["server"]

    # ---- 2) 预检:探活 + 打印锦标赛关键配置(best-effort) ----
    print(f"server={server}")
    try:
        verdict, detail = RoomProbe(server, token).probe()
    except Exception as exc:  # noqa: BLE001
        verdict, detail = "unknown", f"预检探活异常:{exc}"
    print(f"探活:{verdict} — {detail}")
    if verdict == "stop":
        print("房间已终态或令牌失效,无需启动。", file=sys.stderr)
        return 0
    try:
        rules = Api(server, token, state_rate=None,
                    state_throttle=False).rules()
        for line in summarize_rules(rules.get("config", rules) or {}):
            print(line)
    except Exception as exc:  # noqa: BLE001
        print(f"(预检拉取 rules 失败,runner 会自行重试:{exc})")

    if args.dry_run:
        print("--dry-run:预检完成,未启动 runner。")
        return 0

    # ---- 3) 守护式启动 ----
    os.makedirs("local", exist_ok=True)
    stamp = _dt.datetime.now().strftime("%m%d_%H%M%S")
    log_path = os.path.join(
        "local", f"tournament_{args.bot_evaluator}_{stamp}.log")
    cmd = build_runner_command(args)
    print(f"日志:{os.path.abspath(log_path)}")
    print(f"启动:{' '.join(cmd)}\n", flush=True)

    restarts = 0
    exit_code = None
    while True:
        with open(log_path, "a", encoding="utf-8", buffering=1) as log:
            if restarts:
                log.write(f"\n===== 守护重启 #{restarts} "
                          f"{_dt.datetime.now():%H:%M:%S} =====\n")
            log.write(f"# {' '.join(cmd)}\n")
            env = dict(os.environ, PYTHONUNBUFFERED="1")
            proc = subprocess.Popen(
                cmd, cwd=_ROOT, env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1)
            pump = threading.Thread(
                target=_pump, args=(proc.stdout, log), daemon=True)
            pump.start()
            state = {"stop": threading.Event(), "count": -1}
            progress = threading.Thread(
                target=_game_log_progress, args=("local/games", state),
                daemon=True)
            progress.start()
            try:
                exit_code = proc.wait()
            except KeyboardInterrupt:
                print("\n收到 Ctrl-C:转发给 runner(INTERRUPTED 收尾)…")
                _forward_interrupt(proc)
                try:
                    exit_code = proc.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    proc.terminate()
                    exit_code = proc.wait()
                return exit_code if exit_code is not None else 0
            finally:
                state["stop"].set()

        if exit_code == 0:
            print("runner 正常结束(汇总见上方 JSON 与日志)。")
            break
        restarts += 1
        if restarts > args.max_restarts:
            print(f"已达重启上限({args.max_restarts}),退出。"
                  f"最后退出码 {exit_code},日志:{log_path}",
                  file=sys.stderr)
            return exit_code or 1
        verdict, detail = RoomProbe(server, token).probe()
        print(f"runner 意外退出(码 {exit_code});探活:{verdict} — {detail}")
        if verdict == "stop":
            print("房间已终态或令牌失效,不再重启。")
            return exit_code or 1
        delay = args.restart_delay * (2 ** (restarts - 1))
        print(f"{delay:.0f}s 后重启(第 {restarts}/"
              f"{args.max_restarts} 次;重启无副作用,报名在服务端)。")
        time.sleep(delay)

    # ---- 4) 赛后提示 ----
    print("\n赛后复盘三件套:")
    print("  python3 -m mj.logview <gid> --windows")
    print("  python3 -m mj.log_replay <gid>")
    print("  python3 scripts/window_acceptance.py <roomId>")
    print(f"对局日志:local/games/{_dt.datetime.now():%Y%m%d}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
