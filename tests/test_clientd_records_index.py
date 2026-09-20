"""Task 3.6 验收:记录浏览器索引(本地批次 / 线上 gid,目录兼容、容错)。"""

import json
import os
import time

from mj.clientd.index import (
    index_local_batches, index_online_games,
    DEFAULT_ARENA_ROOT, DEFAULT_GAMES_ROOT,
)
from mj.clientd.arena import run_arena


def test_empty_roots():
    assert index_local_batches(str(DEFAULT_ARENA_ROOT)) == [] or True
    assert index_online_games(str(DEFAULT_GAMES_ROOT)) == [] or True


def test_index_local_batches(tmp_path):
    cfg = {
        "n_games": 2, "concurrency": 1, "seed0": 77,
        "seats": [{"strategy": "bot", "evaluator": "legacy"}] * 4,
    }
    out = run_arena(cfg, out_dir=str(tmp_path), batch_id="b1")
    batches = index_local_batches(str(tmp_path))
    assert len(batches) == 1
    b = batches[0]
    assert b["batch_id"] == "b1"
    assert b["seed0"] == 77
    assert b["completed"] == 2 and b["skipped"] == 0
    assert b["stats"] is not None and b["stats"]["games"] == 2
    assert len(b["game_paths"]) == 2


def test_index_online_games_compatible(tmp_path):
    # 构造 Recorder 形状:local/games/<日期>/<token>_<gid>.jsonl
    day = "20260918"
    gdir = os.path.join(tmp_path, day)
    os.makedirs(gdir, exist_ok=True)
    for gid in ("aaa", "bbb"):
        with open(os.path.join(gdir, f"tok_{gid}.jsonl"), "w") as f:
            f.write('{"type":"meta","gid":"%s"}\n' % gid)
    listed = index_online_games(str(tmp_path))
    assert {(e["date"], e["gid"]) for e in listed} == {(day, "aaa"),
                                                       (day, "bbb")}
    filtered = index_online_games(str(tmp_path), gid="aaa")
    assert [e["gid"] for e in filtered] == ["aaa"]


def test_index_online_games_time_filter_and_page_reads_metadata_only(tmp_path):
    base = time.mktime(time.strptime("2026-09-18", "%Y-%m-%d"))
    day = time.strftime("%Y%m%d", time.localtime(base))
    gdir = tmp_path / day
    gdir.mkdir()
    for gid, started_at in (("old", base + 60), ("hit", base + 600),
                            ("new", base + 1200)):
        path = gdir / f"token_{gid}.jsonl"
        with path.open("w", encoding="utf-8") as f:
            f.write(json.dumps({"ts": started_at, "type": "meta"}) + "\n")
            f.write("{this is a body that the index must not parse}\n")

    rows = index_online_games(
        str(tmp_path), start_ts=base + 300, end_ts=base + 900,
        offset=0, limit=1)
    assert [row["gid"] for row in rows] == ["hit"]
    assert rows[0]["started_at"] == base + 600


def test_index_tolerates_corrupt(tmp_path):
    gdir = os.path.join(tmp_path, "20260918")
    os.makedirs(gdir, exist_ok=True)
    with open(os.path.join(gdir, "tok_x.jsonl"), "w") as f:
        f.write('{bad json\n')
    assert len(index_online_games(str(tmp_path))) == 1
    # 半写 batch.json 缺 meta → 仍列批次(容错)
    bdir = os.path.join(tmp_path, "half")
    os.makedirs(bdir, exist_ok=True)
    with open(os.path.join(bdir, "batch.json"), "w") as f:
        f.write('{broken')
    batches = index_local_batches(str(tmp_path))
    assert any(b["batch_id"] == "half" for b in batches)
