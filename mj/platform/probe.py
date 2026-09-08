"""首跑探针:真实平台协议字段发现(手动跑,dump 到 local/logs/)。

按实施计划第 6 步:guide 版本核对 → 4 令牌 /api/me(断言同房)→
锦标赛 config → 打一局 random-legal(全量 dump /state 与 /action 原始
JSON)→ 用免认证数据端点拉事件流跑 replay 校验。

  python3 -m mj.platform.probe [--config local/platform.json]
"""

import argparse
import json
import os
import sys

from .api import Api, ApiError, room_games, room_events
from .config import load_config
import mj.replay as replay_mod

SERVER_DEFAULT = "https://10.240.169.190:18080"


def probe(args):
    cfg = load_config(args.config)
    server = cfg["server"]
    print(f"== 1) 指南版本 ==")
    import urllib.request, ssl, json as _json
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with urllib.request.urlopen(server + "/portal/api/guide/version",
                                timeout=10, context=ctx) as r:
        meta = _json.load(r)
    print(f"version={meta['version']} updated_at={meta['updated_at']}"
          f"(本 bot 按 v22 开发)")
    if meta["version"] > 22:
        breaking = [c for c in meta["changes"]
                    if c["type"] == "breaking" and c["version"] > 22]
        print(f"⚠️ 有 BREAKING 变更: {[b['summary'] for b in breaking]}")

    print("\n== 2) 4 令牌 /api/me ==")
    tids = set()
    apis = {}
    for name, token in cfg["tokens"].items():
        api = Api(server, token)
        me = api.me()
        tids.add(me.get("tournament_id"))
        apis[name] = api
        print(f"{name}: user_id={me.get('user_id')} "
              f"tid={me.get('tournament_id')} "
              f"active={me.get('active_games')}")
    assert len(tids) == 1, f"令牌不同房: {tids}"
    tid = tids.pop()
    print(f"房间: {tid}")

    print("\n== 3) 锦标赛详情/config ==")
    t = apis["青龙"].tournament(tid)
    print(json.dumps({k: t.get(k) for k in
                      ("status", "config", "ready_users", "round_no")},
                     ensure_ascii=False, indent=1))

    if args.skip_game:
        print("\n(--skip-game:跳过对局探针)")
        return

    print("\n== 4) random-legal 对局探针(dump 到 local/logs/)==")
    from .runner import run_room
    results = run_room(cfg, strategy="random", games=1, dump=True,
                       dump_dir="local/logs")
    print(json.dumps(results, ensure_ascii=False, indent=1))

    print("\n== 5) 免认证数据端点 + replay 对账 ==")
    games = room_games(server, tid)
    print(f"局列表: {games}")
    for g in games:
        doc = room_events(server, tid, g["batch"])
        os.makedirs("local/logs", exist_ok=True)
        with open(f"local/logs/room_events_{g['batch']}.json", "w",
                  encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=1)
        print(f"batch {g['batch']} 顶层键: {list(doc.keys())}")
        reps = replay_mod.replay_doc(
            doc, base=(t.get("config") or {}).get("BaseScore", 1),
            you_cai_bi_kao=bool((t.get("config") or {}).get("YouCaiBiKao")))
        for r in reps:
            status = "OK" if not r["illegal"] else f"{len(r['illegal'])} ILLEGAL"
            print(f"  round {r['round_no']}: {status} "
                  f"({r['actions_checked']} 动作, auto_pass {r['auto_pass']})")
            for ill in r["illegal"][:10]:
                print(f"    [seq {ill['seq']}] {ill['msg']}")
            for w in r["warnings"][:5]:
                print(f"    [warn] {w}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="首跑探针")
    ap.add_argument("--config", default="local/platform.json")
    ap.add_argument("--skip-game", action="store_true")
    args = ap.parse_args(argv)
    probe(args)


if __name__ == "__main__":
    main()
