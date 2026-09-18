"""Task 1.3 验收:jsonl 增量 tail 读取器。

覆盖:单文件增量/无重复、未完行等到换行才提交、多字节 UTF-8 跨块不坏、
新文件自动发现、文件删除清理、10 个并发追加文件无丢无重、乱序容错。
"""

import json
import os
import threading
import time

from mj.clientd.tail import JsonlTail, JsonlWatcher


def _write(path, text):
    with open(path, "a", encoding="utf-8") as f:
        f.write(text)


def test_single_file_incremental(tmp_path):
    p = os.path.join(tmp_path, "a.jsonl")
    _write(p, '{"i":0}\n{"i":1}\n')
    tail = JsonlTail(p, from_start=True)
    got = tail.read()
    assert got == ['{"i":0}', '{"i":1}']
    assert tail.read() == []  # 空增量
    _write(p, '{"i":2}\n')
    assert tail.read() == ['{"i":2}']
    assert tail.read() == []
    tail.close()


def test_eventual_line_commit(tmp_path):
    p = os.path.join(tmp_path, "b.jsonl")
    _write(p, '{"i":0}\n{"i":1')  # 第二行未完
    tail = JsonlTail(p, from_start=True)
    assert tail.read() == ['{"i":0}']  # 未完行不提交
    _write(p, '0}\n{"i":2}\n')  # 未完行补全为 {"i":10}
    assert tail.read() == ['{"i":10}', '{"i":2}']
    tail.close()


def test_utf8_multibyte_not_corrupted(tmp_path):
    p = os.path.join(tmp_path, "u.jsonl")
    tail = JsonlTail(p, from_start=True)
    _write(p, '{"msg":"名')   # 中文末字节不完整
    assert tail.read() == []
    _write(p, '字}\n第二轮\n')
    lines = tail.read()
    assert lines == ['{"msg":"名字}', '第二轮']
    tail.close()


def test_from_start_false_ignores_existing(tmp_path):
    p = os.path.join(tmp_path, "c.jsonl")
    _write(p, '{"old":1}\n')
    tail = JsonlTail(p, from_start=False)
    assert tail.read() == []
    _write(p, '{"new":2}\n')
    assert tail.read() == ['{"new":2}']
    tail.close()


def test_new_file_discovery_and_removal(tmp_path):
    w = JsonlWatcher(str(tmp_path), pattern="*.jsonl", from_start=True)
    f1 = os.path.join(tmp_path, "1.jsonl")
    _write(f1, '{"a":1}\n')
    assert w.poll() == [(os.path.normpath(f1), '{"a":1}')]
    f2 = os.path.join(tmp_path, "2.jsonl")
    _write(f2, '{"b":1}\n')
    poll = w.poll()
    paths = {p for p, _ in poll}
    assert os.path.normpath(f2) in paths
    # 删除 f1 → 清理后不再返回
    os.remove(f1)
    poll = w.poll()
    assert all(os.path.normpath(f1) not in p for p, _ in poll)
    w.close()


def test_ten_concurrent_files_no_loss_no_dup(tmp_path):
    n_files, per_file = 10, 40
    paths = [os.path.join(tmp_path, f"f{i}.jsonl") for i in range(n_files)]
    for p in paths:
        _write(p, "")

    def appender(p, idx):
        for i in range(per_file):
            _write(p, json.dumps({"f": idx, "i": i}, ensure_ascii=False) + "\n")
            time.sleep(0.001)

    threads = [threading.Thread(target=appender, args=(paths[k], k))
               for k in range(n_files)]
    for t in threads:
        t.start()

    w = JsonlWatcher(str(tmp_path), pattern="*.jsonl", from_start=True)
    collected = {}
    deadline = time.time() + 30
    started = False
    # 进度中轮询直到全部收齐,再额外空轮询两次断言无重复
    while time.time() < deadline:
        for p, line in w.poll():
            collected.setdefault(os.path.normpath(p), []).append(json.loads(line))
        done = all(len(collected.get(os.path.normpath(p), [])) == per_file
                   for p in paths)
        if done and not started:
            started = True
            break
        time.sleep(0.01)
    for t in threads:
        t.join()
    for p in paths:
        np = os.path.normpath(p)
        items = collected.get(np, [])
        assert len(items) == per_file, f"{p}: got {len(items)}"
        # 无重复 & 无丢:每文件恰好 0..per_file-1 各一次
        got = [it["i"] for it in items]
        assert sorted(got) == list(range(per_file)), f"{p}: {sorted(got)}"
    w.close()


def _to_records(events):
    return [(json.loads(ev)["type"], json.loads(ev).get("seq")) for _t, ev in events]


def test_unordered_completion_tolerance(tmp_path):
    """先见 events 后见其配套 req 之类乱序,不崩溃且内容可还原。"""
    w = JsonlWatcher(str(tmp_path), pattern="*.jsonl", from_start=True)
    f1 = os.path.join(tmp_path, "x.jsonl")
    _write(f1, '{"type":"events","seq_to":5,"events":[]}\n')
    got1 = w.poll()
    assert got1 and _to_records(got1) == [("events", None)]
    # 补充结构不匹配的碎片行与后续 req
    _write(f1, '{"type":"req","seq":4}\n{"type":"events","seq_to":6,"events":[]}\n')
    got2 = w.poll()
    assert _to_records(got2) == [("req", 4), ("events", None)]
    w.close()