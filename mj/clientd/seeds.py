"""种子库(task 2.5):命名保存/列表/删除/选用,批次种子一键入库。

存储:local/seeds/<safe_name>.json,每种子一个文件 {name, seed, note,
created_at, source(batch_id?)}。重名处理:save 覆盖同名;选用返回种子值。
"""

from __future__ import annotations

import json
import os
import re
import time

from .errors import ConflictError, ValidationError

__all__ = ["SeedLibrary", "safe_name"]


def safe_name(name):
    s = str(name).strip()
    s = re.sub(r"[^\w\-一-\uffff]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    if not s:
        raise ValidationError("seed name must be non-empty after sanitize")
    return s


class SeedLibrary:
    def __init__(self, directory="local/seeds"):
        self.directory = directory

    def _path(self, name):
        return os.path.join(self.directory, safe_name(name) + ".json")

    def _read(self, name):
        path = self._path(name)
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            rec = json.load(f)
        return rec

    def save(self, name, seed, note=None, source=None, overwrite=True):
        rec = {
            "name": safe_name(name),
            "seed": int(seed),
            "note": note,
            "source": source,
            "created_at": time.time(),
        }
        path = self._path(rec["name"])
        if os.path.exists(path) and not overwrite:
            raise ConflictError(f"seed name {rec['name']!r} already exists")
        os.makedirs(self.directory, exist_ok=True)
        rec["created_at"] = rec.get("created_at") or time.time()
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(rec, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
        return rec

    def list(self):
        if not os.path.isdir(self.directory):
            return []
        out = []
        for fn in sorted(os.listdir(self.directory)):
            if fn.endswith(".json"):
                rec = self._read(fn[:-5])
                if rec is not None:
                    out.append(rec)
        return out

    def get(self, name):
        rec = self._read(name)
        if rec is None:
            return None
        return rec["seed"]

    def delete(self, name):
        path = self._path(name)
        if not os.path.exists(path):
            raise ValidationError(f"seed {name!r} not found")
        os.remove(path)
        return True

    def save_batch(self, seeds, source=None, names=None):
        """批量入库;seeds 为 [seed,...],names 可选(缺省 seed_<i>)。"""
        records = []
        for i, seed in enumerate(seeds):
            name = (names[i] if names and i < len(names) else f"seed_{i}")
            rec = self.save(name, seed, source=source)
            records.append(rec)
        return records