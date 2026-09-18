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

__all__ = ["run_arena", "build_player", "ArenaConfig", "resolve_seed0",
           "DEFAULT_ARENA_DIR"]

DEFAULT_ARENA_DIR = "local/arena"
MAX_STEPS_PER_GAME = 20000
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
        return make_random_claim_bot(seed, seat)
    return make_player(config)


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
    guard = 0
    while not game.done and guard < MAX_STEPS_PER_GAME:
        seat = game.current_seat()
        act = players[seat](game, seat)
        alegal = list(game.legal_actions())
        if act not in alegal:
            # 防御:策略异常时静默取合法首项,保证批不中断(记录仍完整)
            act = alegal[0] if alegal else None
        if act is None:
            break
        actions.append(int(act))
        game.step(act)
        guard += 1
    return {"index": index, "skip": not game.done,
            "actions": actions, "result": result_from_game(game)}


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
                    actions=res["actions"], result=res["result"])
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