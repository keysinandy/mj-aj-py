"""Task 3.7 前置验收:clientd HTTP API(记录浏览/本地回放帧/种子库)。

起真实服务线程 + 临时 arena/seeds/games 根,写一条本地记录后:
- GET /api/records/local → 批次摘要含该局 game_path;
- POST /api/records/local/frames → 帧数组与本地回放一致、n_frames>0;
- 路径穿越恶意的 batch_id 被拒(400);
- GET/POST/DELETE /api/seeds CRUD。
"""

import json
import os
import urllib.request

from mj.clientd.api import api_router
from mj.clientd.records import write_game_record, result_from_game
from mj.clientd.service import Request, Service


def _get(port, path):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}") as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def _post(port, path, body):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def _delete(port, path):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}",
                                 method="DELETE")
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def _write_demo_game(arena_root):
    from mj.game import Game
    from mj.bot import choose_action
    g = Game(seed=7, dealer=0)
    actions = []
    while not g.done and len(actions) < 2000:
        seat = g.current_seat()
        a = choose_action(g, seat)
        actions.append(int(a))
        g.step(a)
    batch = os.path.join(arena_root, "b1")
    os.makedirs(batch, exist_ok=True)
    with open(os.path.join(batch, "batch.json"), "w", encoding="utf-8") as f:
        json.dump({"batch_id": "b1", "seed0": 7, "status": "finished",
                   "n_games": 1, "completed": 1, "stats": {}}, f)
    path = write_game_record(
        batch, 0, seed=7, dealer=0, base=1, you_cai_bi_kao=False,
        seats=[{"strategy": "bot", "evaluator": "legacy"}],
        roles=[0, 1, 2, 3], actions=actions, result=result_from_game(g))
    return os.path.basename(batch), path


def _start(tmp_path):
    arena = tmp_path / "arena"
    games = tmp_path / "games"
    seeds = tmp_path / "seeds"
    for d in (arena, games, seeds):
        d.mkdir(parents=True, exist_ok=True)
    router = api_router(arena_root=str(arena), games_root=str(games),
                        seed_root=str(seeds))
    svc = Service(discovery=str(tmp_path / "ports.json"), router=router)
    svc.start()
    return svc, arena, seeds


def test_records_local_index_and_frames(tmp_path):
    svc, arena, _ = _start(tmp_path)
    batch_id, path = _write_demo_game(str(arena))
    try:
        status, body = _get(svc.ports["http"], "/api/records/local")
        assert status == 200
        batches = body["batches"]
        assert any(os.path.basename(b["batch_dir"]) == batch_id for b in batches)
        b = next(b for b in batches
                 if os.path.basename(b["batch_dir"]) == batch_id)
        assert len(b["game_paths"]) >= 1

        from mj.clientd.replay import local_frames
        expected = local_frames(path)
        status, body = _post(svc.ports["http"],
                             "/api/records/local/frames",
                             {"batch_id": batch_id, "game": 0})
        assert status == 200
        assert body["n_frames"] > 1
        assert body["n_frames"] == len(expected)
        assert body["session"]["metadata"]["source"] == "local"
        assert len(body["session"]["steps"]) == body["n_frames"]
    finally:
        svc.stop()


def test_frames_resolves_dir_via_index(tmp_path):
    """"竞技场实际落盘形态:目录 batch_<id>,batch.json 里 batch_id 无前缀。
    回放帧接口必须以索引的 batch_id 定位实际目录,否则真实批次 404。"""
    svc, arena, _ = _start(tmp_path)
    batch_id, path = _write_demo_game(str(arena))
    # 模拟 run_arena:目录改名为 batch_<id>,batch.json 保持 <id>
    real_dir = os.path.join(str(arena), "batch_" + batch_id)
    old_dir = os.path.join(str(arena), batch_id)
    os.rename(old_dir, real_dir)
    with open(os.path.join(real_dir, "batch.json"), "w", encoding="utf-8") as f:
        json.dump({"batch_id": batch_id, "seed0": 7, "status": "finished",
                   "n_games": 1, "completed": 1, "stats": {}}, f)
    path = os.path.join(real_dir, os.path.basename(path))
    try:
        status, body = _post(svc.ports["http"],
                             "/api/records/local/frames",
                             {"batch_id": batch_id, "game": 0})
        assert status == 200 and body["n_frames"] > 1
    finally:
        svc.stop()


def test_frames_empty_roots(tmp_path):
    svc, arena, _ = _start(tmp_path)
    try:
        status, body = _get(svc.ports["http"], "/api/records/local")
        assert status == 200 and body["batches"] == []
        status, body = _get(svc.ports["http"], "/api/records/online")
        assert status == 200 and body["games"] == []
    finally:
        svc.stop()


def test_online_index_query_is_time_filtered_and_paged(tmp_path):
    import time

    games = tmp_path / "games"
    base = time.mktime(time.strptime("2026-09-19", "%Y-%m-%d"))
    day = time.strftime("%Y%m%d", time.localtime(base))
    day_dir = games / day
    day_dir.mkdir(parents=True)
    for gid, timestamp in (("first", base + 60), ("second", base + 120),
                           ("third", base + 180)):
        (day_dir / f"token_{gid}.jsonl").write_text(
            json.dumps({"ts": timestamp, "type": "meta"}) + "\n",
            encoding="utf-8")

    router = api_router(games_root=str(games), arena_root=str(tmp_path / "arena"),
                        seed_root=str(tmp_path / "seeds"))
    status, body = router.dispatch(Request(
        "GET", f"/api/records/online?start_ts={base + 90}&end_ts={base + 150}&limit=1"))
    assert status == 200
    assert [game["gid"] for game in body["games"]] == ["second"]
    assert body["has_more"] is False

    status, body = router.dispatch(Request(
        "GET", f"/api/records/online?start_ts={base}&end_ts={base + 300}&limit=1"))
    assert status == 200
    assert body["has_more"] is True
    assert body["next_offset"] == 1


def test_online_index_query_filters_min_whiteboards(tmp_path):
    games = tmp_path / "games"
    day = games / "20260919"
    day.mkdir(parents=True)
    base = {
        "seat": 0,
        "dealer": 0,
        "phase": "draw",
        "turn": 0,
        "discards": [[], [], [], []],
        "melds": [[], [], [], []],
    }
    records = [
        {"type": "meta", "gid": "white", "ts": 100},
        {"type": "snapshot", "seq": 0, "snap": {
            **base, "round_no": 3, "my_hand": ["白", "白"],
        }},
    ]
    (day / "token_white.jsonl").write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in records)
        + "\n", encoding="utf-8")
    router = api_router(games_root=str(games), arena_root=str(tmp_path / "arena"),
                        seed_root=str(tmp_path / "seeds"))
    status, body = router.dispatch(Request(
        "GET", "/api/records/online?min_whiteboards=2&limit=1"))
    assert status == 200
    assert [game["gid"] for game in body["games"]] == ["white"]
    assert body["games"][0]["whiteboard_rounds"][0]["round_no"] == 3


def test_frames_rejects_traversal_and_bad_game(tmp_path):
    svc, arena, _ = _start(tmp_path)
    batch_id, _ = _write_demo_game(str(arena))
    try:
        status, body = _post(svc.ports["http"], "/api/records/local/frames",
                             {"batch_id": "../outside", "game": 0})
        assert status == 400 and body["error"] == "VALIDATION"
        status, body = _post(svc.ports["http"], "/api/records/local/frames",
                             {"batch_id": batch_id, "game": 99})
        assert status == 404 and body["error"] == "NOT_FOUND"
        status, body = _post(svc.ports["http"], "/api/records/local/frames",
                             {"batch_id": batch_id, "game": "x"})
        assert status == 400 and body["error"] == "VALIDATION"
    finally:
        svc.stop()


def _write_demo_online_game(games_root, gid="game1"):
    from mj.replay_debugger.fixtures import jsonl
    hands = [
        ["1w", "1w", "5b", "5b", "5b", "2t", "3t", "4t", "7w", "8w", "9w", "东", "南", "中"],
        ["2w"] * 13, ["3w"] * 13, ["4w"] * 13,
    ]
    records = [
        {"type": "meta", "gid": gid, "name": "demo", "seat": 0},
        {"type": "snapshot", "seq": 179, "snap": {
            "gid": gid, "seat": 0, "round_no": 1, "my_hand": hands[0],
            "discards": [[], [], [], []], "melds": [[], [], [], []],
            "hand_counts": [14, 13, 13, 13]}},
        {"type": "events", "events": [
            {"seq": 180, "type": "tile_drawn", "seat": 0, "tile": "9w"},
            {"seq": 181, "type": "tile_discarded", "seat": 0, "tile": "1w"}]},
        {"type": "end", "scores": [100, 200, -150, -150]},
    ]
    day = os.path.join(str(games_root), "20260919")
    os.makedirs(day, exist_ok=True)
    path = os.path.join(day, f"u_token_{gid}.jsonl")
    with open(path, "w", encoding="utf-8") as f:
        f.write(jsonl(records))
    return gid, path


def test_online_frames_endpoint(tmp_path):
    svc, _, _ = _start(tmp_path)
    games = tmp_path / "games"
    gid, _ = _write_demo_online_game(str(games))
    try:
        status, body = _get(svc.ports["http"],
                            f"/api/records/online/{gid}/frames")
        assert status == 200
        assert body["n_frames"] >= 1
        assert all(f["info_kind"] == "online" for f in body["frames"])
        assert body["frames"][0]["hands"] is None
        assert body["frames"][0]["my_hand"] is not None
        assert body["session"]["metadata"]["source"] == "online"
        assert len(body["session"]["steps"]) == body["n_frames"]
        assert isinstance(body["verifications"], list)
        # 未知 gid → 404
        status, body = _get(svc.ports["http"], "/api/records/online/nope/frames")
        assert status == 404 and body["error"] == "NOT_FOUND"
    finally:
        svc.stop()


def test_seeds_crud(tmp_path):
    svc, _, _ = _start(tmp_path)
    try:
        status, body = _get(svc.ports["http"], "/api/seeds")
        assert status == 200 and body["seeds"] == []
        status, body = _post(svc.ports["http"], "/api/seeds",
                             {"name": "note1", "seeds": [1, 2, 3, 4]})
        assert status == 200 and body["n"] == 4
        status, body = _get(svc.ports["http"], "/api/seeds")
        assert status == 200
        names = {r["name"] for r in body["seeds"]}
        assert {"seed_0", "seed_1", "seed_2", "seed_3"} <= names
        status, body = _delete(svc.ports["http"], "/api/seeds/seed_1")
        assert status == 200 and body["deleted"] is True
        status, body = _delete(svc.ports["http"], "/api/seeds/seed_1")
        assert status == 404
        status, body = _post(svc.ports["http"], "/api/seeds",
                             {"name": "../evil", "seeds": [1]})
        assert status == 400
    finally:
        svc.stop()
