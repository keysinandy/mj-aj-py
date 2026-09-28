"""本地竞技场批量执行器(task 2.3)。

- 并发 = multiprocessing spawn Pool,worker 跨局复用(initializer 传取消事件);
- 第 i 局 seed = seed0 + i,与调度无关 → 同配置(确定性策略)逐局一致;
- 座位/庄家轮转(fair_match 口径):主位坐 i%4,庄家 (i//4)%4,
  对手三位的相对顺序不变(相对下家喂牌关系固定);
- 进度事件流(完成数/速率)与局边界取消:停止后已启动对局跑完保留,
  未启动任务返回 skip 不被写入;统计只计写入局;
- 每局写 records.write_game_record。

耗时口径:Rust 内核缺失或策略较慢时按实际耗时推进,不做强断言。
"""

from __future__ import annotations

import json
import multiprocessing as mp
import os
import random as _random
import time

from .records import write_game_record, result_from_game, load_game_record
from .strategies import make_player
from .random_claim import make_random_claim_bot
from .stats import arena_stats
from .sessions import SessionManager
from .errors import ValidationError
from ..legacy_eval import DEFAULT_BOT_EVALUATOR

__all__ = ["run_arena", "build_player", "ArenaConfig", "resolve_seed0",
           "DEFAULT_ARENA_DIR", "make_arena_session_manager",
           "DEFAULT_SEATS"]

DEFAULT_ARENA_DIR = "local/arena"
MAX_STEPS_PER_GAME = 20000

# Web 控制台默认座位:主位 legacy bot,对手 random。
DEFAULT_SEATS = [
    {"strategy": "bot", "evaluator": DEFAULT_BOT_EVALUATOR},
    {"strategy": "random"},
    {"strategy": "random"},
    {"strategy": "random"},
]
_GLOBAL = {"stop": None}


def resolve_seed0(config):
    """确定批次种子:缺省随机生成并回填到 config。"""
    seed = config.get("seed0")
    if seed is None:
        seed = _random.SystemRandom().randint(0, 2 ** 31 - 1)
        config["seed0"] = seed
    return int(seed)


class ArenaConfig:
    """便捷解析 user 字典配置(保持引用,便于 seed0 回填到调用方)。"""

    def __init__(self, data):
        self.data = data if isinstance(data, dict) else {}
        self.n_games = int(self.data.get("n_games", 16))
        self.concurrency = int(self.data.get("concurrency", 4))
        self.seats = self.data.get("seats") or []  # 4 角色: 主/对1/对2/对3
        self.you_cai_bi_kao = bool(self.data.get("you_cai_bi_kao", False))
        self.base = int(self.data.get("base", 1))

    @property
    def seed0(self):
        return resolve_seed0(self.data)

    def validate(self):
        if self.n_games < 1:
            raise ValueError("n_games must be >= 1")
        if self.concurrency < 1:
            raise ValueError("concurrency must be >= 1")
        if len(self.seats) != 4:
            raise ValueError("seats must contain exactly 4 role configs "
                             "(main + 3 opponents)")
        return self


def build_player(config, seed, seat):
    """由角色配置构造玩家;random 策略以 (seed, seat) 派生 rng 保可复现。"""
    if not isinstance(config, dict):
        raise TypeError(f"seat config must be dict, got {config!r}")
    strategy = config.get("strategy")
    if strategy == "random":
        player = make_random_claim_bot(seed, seat)
    else:
        player = make_player(config)
    from ..strategy_runtime import snapshot_for_config
    snapshot = snapshot_for_config(config)
    try:
        player.strategy_snapshot = snapshot
        player.bot_strategy = strategy
        player.bot_evaluator = config.get("evaluator") or strategy
    except (AttributeError, TypeError):
        pass
    return player


def _init_worker(stop_event):
    _GLOBAL["stop"] = stop_event


def _stop_set():
    ev = _GLOBAL.get("stop")
    return ev is not None and ev.is_set()


def _play_task(task):
    index, seed, dealer, base, ycbk, player_configs = task
    if _stop_set():
        return {"index": index, "skip": True, "actions": [], "result": None}
    from ..game import Game
    players = [build_player(cfg, seed, seat)
               for seat, cfg in enumerate(player_configs)]
    game = Game(seed=seed, dealer=dealer, base=base, you_cai_bi_kao=ycbk)
    actions = []
    decision_audits = []
    strategy_snapshots = []
    for physical_seat, player in enumerate(players):
        snapshot = getattr(player, "strategy_snapshot", None)
        if snapshot is not None:
            strategy_snapshots.append({
                "seat": physical_seat,
                "snapshot": (snapshot.as_json()
                             if hasattr(snapshot, "as_json") else snapshot),
            })
    guard = 0
    while not game.done and guard < MAX_STEPS_PER_GAME:
        seat = game.current_seat()
        player = players[seat]
        started = time.perf_counter()
        if hasattr(player, "decide_with_evaluation"):
            act, evaluation = player.decide_with_evaluation(game, seat)
        else:
            act, evaluation = player(game, seat), None
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        alegal = list(game.legal_actions())
        if act not in alegal:
            # 防御:策略异常时静默取合法首项,保证批不中断(记录仍完整)
            act = alegal[0] if alegal else None
        if act is None:
            break
        try:
            from ..strategy_runtime import decision_audit
            snapshot = getattr(player, "strategy_snapshot", None)
            decision_audits.append({
                "step": len(actions) + 1,
                "decision_audit": decision_audit(
                    evaluation, act, elapsed_ms,
                    phase=("draw" if game.phase == "discard"
                           else "response_react"),
                    snapshot=snapshot,
                    decision_id=len(actions) + 1),
            })
        except Exception:
            # Diagnostics are additive; a serialization issue cannot alter
            # the action or stop an arena game that was already decided.
            pass
        actions.append(int(act))
        game.step(act)
        guard += 1
    return {"index": index, "skip": not game.done,
            "actions": actions, "result": result_from_game(game),
            "decision_audits": decision_audits,
            "strategy_snapshots": strategy_snapshots}


def _task_list(cfg):
    """按轮转规则生成全部任务 (index, seed, dealer, base, ycbk, configs)。"""
    seed0 = cfg.seed0
    seats = list(cfg.seats)
    tasks = []
    roles_by_index = {}
    for i in range(cfg.n_games):
        main_seat = i % 4
        dealer = (i // 4) % 4
        others = [s for s in range(4) if s != main_seat]
        player_configs = [None] * 4
        roles = [None] * 4
        player_configs[main_seat] = seats[0]
        roles[main_seat] = 0
        for k, phys in enumerate(others):
            player_configs[phys] = seats[k + 1]
            roles[phys] = k + 1
        tasks.append((i, seed0 + i, dealer, cfg.base, cfg.you_cai_bi_kao,
                      player_configs))
        roles_by_index[i] = roles
    return tasks, roles_by_index


def run_arena(config, out_dir=None, is_stop=lambda: False,
              progress=None, batch_id=None):
    """执行批次,落盘到 out_dir/<batch_id>/;返回 {paths, stats, cancelled}。"""
    cfg = ArenaConfig(config).validate()
    out_dir = out_dir or DEFAULT_ARENA_DIR
    if batch_id is None:
        batch_id = time.strftime("%Y%m%d-%H%M%S")
    batch_dir = os.path.join(out_dir, f"batch_{batch_id}")

    seed0 = cfg.seed0
    tasks, roles_by_index = _task_list(cfg)
    stop_ev = mp.Event()

    def _check_stop():
        if is_stop():
            stop_ev.set()

    ctx = mp.get_context("spawn")
    completed = []
    skipped_indexes = []
    t0 = time.time()
    with ctx.Pool(cfg.concurrency, initializer=_init_worker,
                  initargs=(stop_ev,)) as pool:
        for res in pool.imap_unordered(_play_task, tasks, chunksize=1):
            _check_stop()
            if res.get("skip"):
                skipped_indexes.append(res["index"])
            else:
                roles = roles_by_index[res["index"]]
                path = write_game_record(
                    batch_dir, res["index"], seed=seed0 + res["index"],
                    dealer=(res["index"] // 4) % 4, base=cfg.base,
                    you_cai_bi_kao=cfg.you_cai_bi_kao,
                    seats=cfg.seats, roles=roles,
                    actions=res["actions"], result=res["result"],
                    decision_audits=res.get("decision_audits"),
                    strategy_snapshots=res.get("strategy_snapshots"))
                completed.append({"index": res["index"], "path": path,
                                  "seed": seed0 + res["index"]})
            if progress is not None:
                progress(len(completed), cfg.n_games,
                         (time.time() - t0) or 1e-9, res["index"])
        pool.close()
        pool.join()

    cancelled = stop_ev.is_set() or bool(is_stop())
    stats = None
    if completed:
        stats = arena_stats([load_game_record(c["path"]) for c in completed])
    summary = {
        "batch_id": batch_id, "batch_dir": batch_dir, "seed0": seed0,
        "n_games": cfg.n_games, "concurrency": cfg.concurrency,
        "seats": cfg.seats, "you_cai_bi_kao": cfg.you_cai_bi_kao,
        "base": cfg.base,
        "status": "cancelled" if cancelled else "complete",
        "completed": len(completed), "skipped": len(skipped_indexes),
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t0)),
        "elapsed": round(time.time() - t0, 3),
        "indexes": [c["index"] for c in completed],
        "stats": stats,
    }
    _write_json(batch_dir, "batch.json", summary)
    return {
        "batch_id": batch_id, "batch_dir": batch_dir, "seed0": seed0,
        "n_games": cfg.n_games, "concurrency": cfg.concurrency,
        "completed": len(completed), "skipped": len(skipped_indexes),
        "cancelled": cancelled, "elapsed": time.time() - t0,
        "game_paths": [c["path"] for c in completed],
        "indexes": [c["index"] for c in completed],
        "stats": stats,
    }


def _write_json(batch_dir, name, payload):
    path = os.path.join(batch_dir, name)
    os.makedirs(batch_dir, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _normalize_seats(config):
    """注入默认座位与默认规模;seats 若给出必须恰为 4 个。"""
    cfg = dict(config or {})
    seats = cfg.get("seats")
    if seats is None:
        cfg["seats"] = DEFAULT_SEATS
    elif list(seats) != seats or len(seats) != 4:
        raise ValidationError(
            "seats must be a list of exactly 4 role configs")
    cfg.setdefault("n_games", 16)
    cfg.setdefault("concurrency", 4)
    cfg.setdefault("base", 1)
    cfg.setdefault("you_cai_bi_kao", False)
    return cfg


def make_arena_session_manager(arena_root=None, max_concurrent=8,
                               settings_path=None, games_root=None):
    """构造面向 Web 的本地、匹配和锦标赛会话管理器。

    arena 会话运行本地批量对局；match 会话复用平台 ``/api/match`` 自动
    入席循环；tournament 会话复用正式赛阶段轮询。线上凭据由 runner
    从本机设置读取，不进入 Web 请求配置。
    """
    root = arena_root or DEFAULT_ARENA_DIR
    online_games_root = games_root or "local/games"

    def _build_match_runner(config):
        from .match import make_match_runner
        return make_match_runner(config, settings_path=settings_path)

    def _build_tournament_runner(config):
        from .tournament import make_tournament_runner
        return make_tournament_runner(
            config, settings_path=settings_path,
            games_root=online_games_root)

    def _factory(kind, config):
        if kind == "match":
            return _build_match_runner(config)
        if kind == "tournament":
            return _build_tournament_runner(config)
        if kind != "arena":
            raise ValidationError(
                f"clientd web 会话仅支持 arena 或 match;got {kind!r}")
        cfg = _normalize_seats(config)
        from ..strategy_runtime import snapshot_for_config
        strategy_snapshots = [
            {"seat": index,
             "snapshot": snapshot_for_config(seat_config).as_json()}
            for index, seat_config in enumerate(cfg["seats"])
        ]

        def runner(stop, session):
            session.update_progress({
                "strategy_status": "configured",
                "strategy_loaded": None,
                "strategy_name": "arena",
                "strategy_snapshots": strategy_snapshots,
                "strategy_snapshot": (strategy_snapshots[0]["snapshot"]
                                      if strategy_snapshots else None),
                "message": "座位策略配置已解析；本地 worker 将按局初始化策略",
            })
            def progress(done, total, rate, index):
                session.update_progress({
                    "done": done, "total": total,
                    "rate": round(rate, 2), "last_index": index,
                })
            return run_arena(cfg, out_dir=root, is_stop=stop,
                             progress=progress)
        return runner

    return SessionManager(max_concurrent=max_concurrent,
                          runner_factory=_factory)
