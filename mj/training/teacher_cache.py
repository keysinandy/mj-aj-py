"""Teacher result cache keyed by state + teacher version + config hash.

A cached result may be reused only when the requested budget is not higher
than the cached run's completed budget; otherwise the state is re-searched.
The cache never creates a second dataset row for the same (state, generation)
because row identity is still the work id.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from ..decision.profile import fingerprint
from ..search.report import SearchResult

CACHE_SCHEMA = "teacher-result-cache-v1"


def cache_key(*, state_hash, teacher_version, teacher_config_hash):
    if not str(state_hash) or not str(teacher_version) or not str(teacher_config_hash):
        raise ValueError("teacher cache key requires all three identities")
    return fingerprint({
        "schema": "teacher-cache-key-v1",
        "state_hash": str(state_hash),
        "teacher_version": str(teacher_version),
        "teacher_config_hash": str(teacher_config_hash),
    }, 32)


def result_from_json(data: Mapping[str, Any]):
    """Rebuild a SearchResult from a serialized report."""
    value = dict(data)
    for key in ("schema", "confidence", "oracle", "real_wall_optimal",
                "model_error_free", "fingerprint"):
        value.pop(key, None)
    value["report_schema"] = str(data.get("schema", "search-report-v1"))
    value["legal_actions"] = tuple(int(item)
                                   for item in data.get("legal_actions", ()))
    value["visit_policy"] = {int(key): float(item)
                             for key, item in
                             (data.get("visit_policy") or {}).items()}
    value["q_by_action"] = {int(key): float(item)
                            for key, item in
                            (data.get("q_by_action") or {}).items()}
    value["visit_counts"] = {int(key): int(item)
                             for key, item in
                             (data.get("visit_counts") or {}).items()}
    value["variance_by_action"] = {int(key): float(item)
                                   for key, item in
                                   (data.get("variance_by_action") or {}).items()}
    value["failures"] = tuple(data.get("failures", ()))
    return SearchResult(**value)


class TeacherCache:
    """In-memory cache with optional append-only JSONL persistence."""

    def __init__(self, entries: Mapping[str, Any] | None = None, *,
                 path=None):
        self.entries: dict[str, dict] = dict(entries or {})
        self.path = Path(path) if path is not None else None
        self._pending: list[dict] = []

    def __len__(self):
        return len(self.entries)

    @classmethod
    def load(cls, path):
        path = Path(path)
        entries = {}
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                entries[row["key"]] = row
        return cls(entries, path=path)

    @classmethod
    def load_dir(cls, directory):
        """Merge every cache shard; the caller owns the new shard path."""
        directory = Path(directory)
        entries: dict[str, dict] = {}
        if directory.exists():
            for path in sorted(directory.glob("*.jsonl")):
                for line in path.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    row = json.loads(line)
                    entries[row["key"]] = row
        return cls(entries)

    def open_shard(self, directory, *, name=None):
        import os

        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / (str(name or os.getpid()) + ".jsonl")
        return self.path

    def lookup(self, *, state_hash, teacher_version, teacher_config_hash,
               requested_simulations):
        key = cache_key(state_hash=state_hash, teacher_version=teacher_version,
                        teacher_config_hash=teacher_config_hash)
        entry = self.entries.get(key)
        if entry is None:
            return None
        if int(entry.get("completed_simulations", 0)) < int(requested_simulations):
            return None
        return result_from_json(entry["result"])

    def store(self, *, state_hash, teacher_version, teacher_config_hash,
              requested_simulations, result, generation=None):
        key = cache_key(state_hash=state_hash, teacher_version=teacher_version,
                        teacher_config_hash=teacher_config_hash)
        entry = {
            "schema": CACHE_SCHEMA,
            "key": key,
            "state_hash": str(state_hash),
            "teacher_version": str(teacher_version),
            "teacher_config_hash": str(teacher_config_hash),
            "requested_simulations": int(requested_simulations),
            "completed_simulations": int(result.simulations),
            "generation": generation,
            "result": result.as_json(),
        }
        entry["fingerprint"] = fingerprint(entry, 24)
        self.entries[key] = entry
        self._pending.append(entry)
        return entry

    def flush(self):
        if self.path is None or not self._pending:
            return 0
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as stream:
            for entry in self._pending:
                stream.write(json.dumps(entry, ensure_ascii=False,
                                        sort_keys=True) + "\n")
        count = len(self._pending)
        self._pending = []
        return count

    def manifest(self):
        value = {
            "schema": "teacher-cache-manifest-v1",
            "entries": len(self.entries),
            "path": str(self.path) if self.path is not None else None,
            "oracle": False,
        }
        value["fingerprint"] = fingerprint(value, 24)
        return value
