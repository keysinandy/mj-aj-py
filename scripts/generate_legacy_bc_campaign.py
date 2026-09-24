#!/usr/bin/env python3
"""Freeze and generate a resumable legacy BC data campaign.

The semantic campaign identity is independent of worker count and completion
order.  Each seed owns exactly one match, each match contains eight hands by
default, and each shard is published only after its temporary file is
complete.  The resulting ``manifest.json`` is compatible with the streaming
BC trainer and carries both the frozen campaign fingerprint and shard hashes.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import hashlib
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mj.bc_data import (  # noqa: E402
    DEFAULT_CONSECUTIVE_DEALS,
    DEFAULT_MATCH_ROUNDS,
    SHADOW_CONTRACT,
    SHADOW_KEYS,
    TRAINING_BOT_EVALUATOR,
    TRAINING_TEACHER_VERSION,
    _write_shard,
    training_teacher_fingerprint,
)
from mj.decision.profile import fingerprint  # noqa: E402
from mj.training.minisuphx_manifest import (  # noqa: E402
    ACTION_SCOPE_DISCARD,
    FEATURE_PUBLIC,
    VALUE_CONTRACT,
    git_head,
)


CAMPAIGN_SCHEMA = "legacy-bc-campaign-v1"
DATA_SCHEMA = "minisuphx-bc-data-v1"
VALIDATION_SEED_RANGE = (1_000_000, 1_001_999)
FINAL_TEST_SEED_RANGE = (2_000_000, 2_003_999)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, sort_keys=True, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _validate_positive(name: str, value: int) -> int:
    if isinstance(value, bool) or int(value) < 1:
        raise ValueError(f"{name} must be a positive integer")
    return int(value)


def build_campaign_config(*, campaign_id: str,
                          seed0: int = 0,
                          games: int = 30_000,
                          per_shard: int = 25,
                          rounds: int = DEFAULT_MATCH_ROUNDS,
                          default_consecutive_deals: int =
                          DEFAULT_CONSECUTIVE_DEALS,
                          evaluator: str = TRAINING_BOT_EVALUATOR,
                          scope: str = "all-root",
                          you_cai_bi_kao: bool = False,
                          allow_search_fallback: bool = False,
                          git_commit: str | None = None) -> dict:
    """Return the immutable semantic part of a BC campaign manifest."""
    if not campaign_id:
        raise ValueError("campaign_id must not be empty")
    seed0 = int(seed0)
    if seed0 < 0:
        raise ValueError("seed0 must be non-negative")
    games = _validate_positive("games", games)
    per_shard = _validate_positive("per_shard", per_shard)
    rounds = _validate_positive("rounds", rounds)
    default_consecutive_deals = _validate_positive(
        "default_consecutive_deals", default_consecutive_deals)
    if not evaluator:
        raise ValueError("evaluator must not be empty")
    if not scope:
        raise ValueError("scope must not be empty")

    teacher_fingerprint = ""
    teacher_version = ""
    if evaluator == TRAINING_BOT_EVALUATOR:
        teacher_fingerprint = training_teacher_fingerprint()
        teacher_version = TRAINING_TEACHER_VERSION

    n_shards = (games + per_shard - 1) // per_shard
    train_hi = seed0 + games - 1
    match_rules = {
        "schema": "legacy-match-v1",
        "rounds": rounds,
        "default_consecutive_deals": default_consecutive_deals,
        "dealer_continues_on": ["dealer_win", "draw"],
        "winner_becomes_dealer": True,
        "settlement": "dealer-x8",
    }
    return {
        "schema": CAMPAIGN_SCHEMA,
        "campaign_id": str(campaign_id),
        "git_commit": str(git_commit or git_head(str(ROOT))),
        "feature_contract": FEATURE_PUBLIC,
        "value_contract": VALUE_CONTRACT,
        "action_scope": ACTION_SCOPE_DISCARD,
        "evaluator": str(evaluator),
        "teacher_version": teacher_version,
        "teacher_fingerprint": teacher_fingerprint,
        "scope": str(scope),
        "you_cai_bi_kao": bool(you_cai_bi_kao),
        "allow_search_fallback": bool(allow_search_fallback),
        "match_rules": match_rules,
        "seed_domain": {
            "train_seed_lo": seed0,
            "train_seed_hi": train_hi,
            "games": games,
        },
        "seed_domains": {
            "train": [seed0, train_hi],
            "validation": list(VALIDATION_SEED_RANGE),
            "final_test": list(FINAL_TEST_SEED_RANGE),
        },
        "games": games,
        "per_shard": per_shard,
        "n_shards": n_shards,
        "include_metadata": True,
        "big_hand_shadow_contract": SHADOW_CONTRACT,
        "big_hand_shadow_keys": list(SHADOW_KEYS),
        "data_schema": DATA_SCHEMA,
    }


def campaign_fingerprint(config: dict) -> str:
    """Return the immutable campaign identity, excluding progress/output."""
    return fingerprint(config, 24)


def _dataset_fingerprint_payload(manifest: dict, teacher: str) -> dict:
    keys = (
        "schema", "feature_contract", "big_hand_shadow_contract", "evaluator",
        "teacher_version", "teacher_fingerprint", "scope", "you_cai_bi_kao",
        "seed_domain", "games", "n_shards", "git_commit", "per_shard",
        "match_rules", "campaign_id", "campaign_fingerprint", "shards",
    )
    payload = {key: manifest[key] for key in keys if key in manifest}
    payload["teacher"] = teacher
    return payload


def _inspect_shard(path: Path, expected_games: int | None = None) -> int:
    import numpy as np

    required = ("planes", "scalars", "mask", "action", "seat", "score")
    with np.load(path, allow_pickle=False) as shard:
        if any(key not in shard for key in required):
            raise ValueError(f"missing BC array in shard: {path}")
        samples = int(len(shard["action"]))
        if samples <= 0:
            raise ValueError(f"empty BC shard: {path}")
        for key in required:
            if len(shard[key]) != samples:
                raise ValueError(f"length mismatch for {key} in {path}")
    del expected_games  # match count is semantic metadata, not row count
    return samples


def _state_path(out: Path) -> Path:
    return out / "campaign.json"


def _load_or_freeze(out: Path, config: dict) -> tuple[dict, str]:
    expected = campaign_fingerprint(config)
    path = _state_path(out)
    if path.exists():
        with path.open("r", encoding="utf-8") as stream:
            state = json.load(stream)
        if state.get("schema") != CAMPAIGN_SCHEMA:
            raise ValueError(f"unexpected campaign schema in {path}")
        if state.get("campaign_fingerprint") != expected:
            raise ValueError(
                "campaign fingerprint mismatch; refusing to mix frozen configs")
        if state.get("config") != config:
            raise ValueError(
                "campaign config mismatch; refusing to resume incompatible data")
        state.setdefault("progress", {"shards": {}})
        state["progress"].setdefault("shards", {})
        return state, expected

    state = {
        "schema": CAMPAIGN_SCHEMA,
        "campaign_id": config["campaign_id"],
        "campaign_fingerprint": expected,
        "config": config,
        "status": "frozen",
        "progress": {"shards": {}},
    }
    _write_json(path, state)
    return state, expected


def _expected_shards(out: Path, config: dict):
    seed0 = int(config["seed_domain"]["train_seed_lo"])
    games = int(config["games"])
    per_shard = int(config["per_shard"])
    n_shards = int(config["n_shards"])
    for index in range(n_shards):
        count = min(per_shard, games - index * per_shard)
        final = out / f"shard_{index:05d}.npz"
        part = out / f".shard_{index:05d}.part.npz"
        yield index, seed0 + index * per_shard, count, final, part


def _entry(index: int, seed_start: int, games: int, path: Path,
           samples: int) -> dict:
    return {
        "index": int(index),
        "path": path.name,
        "seed_start": int(seed_start),
        "games": int(games),
        "samples": int(samples),
        "bytes": int(path.stat().st_size),
        "sha256": sha256_file(path),
    }


def _write_jobs(out: Path, config: dict, missing: list[tuple]) -> dict:
    jobs = {}
    for index, seed_start, games, _final, part in missing:
        jobs[index] = [
            seed_start, games, str(part), config["you_cai_bi_kao"], True,
            config["evaluator"], config["scope"],
            config["allow_search_fallback"],
            config["match_rules"]["rounds"],
            config["match_rules"]["default_consecutive_deals"],
        ]
    return jobs


def _write_final_manifest(out: Path, state: dict, config: dict,
                          campaign_fp: str) -> dict:
    progress = state["progress"]["shards"]
    shards = [progress[name] for name in sorted(progress)]
    samples = sum(int(row["samples"]) for row in shards)
    manifest = {
        "schema": DATA_SCHEMA,
        "feature_contract": config["feature_contract"],
        "big_hand_shadow_contract": config["big_hand_shadow_contract"],
        "big_hand_shadow_keys": config["big_hand_shadow_keys"],
        "evaluator": config["evaluator"],
        "teacher_version": config["teacher_version"],
        "teacher_fingerprint": config["teacher_fingerprint"],
        "scope": config["scope"],
        "you_cai_bi_kao": config["you_cai_bi_kao"],
        "allow_search_fallback": config["allow_search_fallback"],
        "match_rules": config["match_rules"],
        "seed_domain": config["seed_domain"],
        "seed_domains": config["seed_domains"],
        "campaign_id": config["campaign_id"],
        "campaign_fingerprint": campaign_fp,
        "games": config["games"],
        "n_shards": config["n_shards"],
        "per_shard": config["per_shard"],
        "samples": samples,
        "git_commit": config["git_commit"],
        "shards": shards,
    }
    manifest["dataset_fingerprint"] = fingerprint(
        _dataset_fingerprint_payload(manifest, config["evaluator"]), 24)
    manifest["fingerprint"] = fingerprint(manifest, 24)
    _write_json(out / "manifest.json", manifest)
    return manifest


def generate(args) -> dict:
    config = build_campaign_config(
        campaign_id=args.campaign_id, seed0=args.seed0, games=args.games,
        per_shard=args.per_shard, rounds=args.rounds,
        default_consecutive_deals=args.default_consecutive_deals,
        evaluator=args.evaluator, scope=args.scope,
        you_cai_bi_kao=args.you_cai_bi_kao,
        allow_search_fallback=args.allow_search_fallback,
    )
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    state, campaign_fp = _load_or_freeze(out, config)
    if args.freeze_only:
        print(json.dumps({
            "campaign_id": config["campaign_id"],
            "campaign_fingerprint": campaign_fp,
            "out": str(out),
            "status": state["status"],
        }, ensure_ascii=False, indent=2))
        return state

    expected = list(_expected_shards(out, config))
    progress = state["progress"]["shards"]
    for index, seed_start, games, final, _part in expected:
        name = final.name
        if final.exists():
            samples = _inspect_shard(final, games)
            previous = progress.get(name)
            current = _entry(index, seed_start, games, final, samples)
            if previous and previous.get("sha256") != current["sha256"]:
                raise ValueError(f"shard changed after publication: {final}")
            progress[name] = current

    missing = [row for row in expected if not row[3].exists()]
    state["status"] = "running" if missing else "complete"
    _write_json(_state_path(out), state)
    if missing:
        jobs = _write_jobs(out, config, missing)
        started = time.perf_counter()
        completed = 0
        with ProcessPoolExecutor(max_workers=args.workers) as executor:
            futures = {
                executor.submit(_write_shard, jobs[index]): index
                for index in jobs
            }
            by_index = {row[0]: row for row in missing}
            for future in as_completed(futures):
                index = futures[future]
                returned_path, returned_samples = future.result()
                row = by_index[index]
                final, part = row[3], row[4]
                if Path(returned_path) != part or not part.exists():
                    raise RuntimeError(
                        f"worker returned unexpected shard path: {returned_path}")
                os.replace(part, final)
                samples = _inspect_shard(final, row[2])
                if int(returned_samples) != samples:
                    raise RuntimeError(f"worker sample count mismatch: {final}")
                progress[final.name] = _entry(
                    index, row[1], row[2], final, samples)
                completed += 1
                state["status"] = "running"
                _write_json(_state_path(out), state)
                elapsed = max(time.perf_counter() - started, 1e-6)
                print(
                    f"shard {completed}/{len(missing)} index={index} "
                    f"samples={samples} elapsed={elapsed:.1f}s",
                    flush=True,
                )

    manifest = _write_final_manifest(out, state, config, campaign_fp)
    state.update({
        "status": "complete",
        "dataset_fingerprint": manifest["dataset_fingerprint"],
        "manifest_fingerprint": manifest["fingerprint"],
        "samples": manifest["samples"],
    })
    _write_json(_state_path(out), state)
    print(json.dumps({
        "campaign_id": config["campaign_id"],
        "campaign_fingerprint": campaign_fp,
        "dataset_fingerprint": manifest["dataset_fingerprint"],
        "manifest_fingerprint": manifest["fingerprint"],
        "games": manifest["games"],
        "samples": manifest["samples"],
        "shards": manifest["n_shards"],
    }, ensure_ascii=False, indent=2))
    return state


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="freeze/resume a deterministic legacy BC campaign")
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--games", type=int, default=30_000)
    parser.add_argument("--seed0", type=int, default=0)
    parser.add_argument("--per-shard", type=int, default=25)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--rounds", type=int, default=DEFAULT_MATCH_ROUNDS)
    parser.add_argument("--default-consecutive-deals", type=int,
                        default=DEFAULT_CONSECUTIVE_DEALS)
    parser.add_argument("--evaluator", default=TRAINING_BOT_EVALUATOR)
    parser.add_argument("--scope", default="all-root")
    parser.add_argument("--you-cai-bi-kao", action="store_true")
    parser.add_argument("--allow-search-fallback", action="store_true")
    parser.add_argument("--freeze-only", action="store_true")
    args = parser.parse_args(argv)
    args.workers = _validate_positive("workers", args.workers)
    return generate(args)


if __name__ == "__main__":
    main()
