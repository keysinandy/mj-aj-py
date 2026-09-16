#!/usr/bin/env python3
"""Merge multi-machine search-distillation dataset shards without duplicates."""

from __future__ import annotations

import argparse
import glob
import json
from pathlib import Path

from mj.training.search_data import (
    merge_datasets,
    read_search_dataset,
    write_search_dataset,
)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", nargs="+", required=True,
                        help="shard JSONL paths or globs")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--manifest-out", type=Path, default=None)
    args = parser.parse_args(argv)

    datasets = []
    paths = []
    for pattern in args.data:
        for path in sorted(glob.glob(pattern)):
            datasets.append(read_search_dataset(path))
            paths.append(path)
    if not datasets:
        raise ValueError("no dataset shards matched")
    merged = merge_datasets(datasets)
    write_search_dataset(args.out, merged)
    report = {
        "schema": "search-distillation-merge-v1",
        "inputs": paths,
        "input_rows": sum(len(dataset.samples) for dataset in datasets),
        "merged_rows": len(merged.samples),
        "duplicates_removed": (sum(len(dataset.samples)
                                   for dataset in datasets) -
                               len(merged.samples)),
        "source_groups": sorted({sample.source_group
                                 for sample in merged.samples}),
        "generations": sorted({int(sample.generation)
                               for sample in merged.samples}),
        "dataset_fingerprint": merged.fingerprint,
        "oracle": False,
    }
    from mj.decision.profile import fingerprint
    report["fingerprint"] = fingerprint(report, 24)
    if args.manifest_out:
        args.manifest_out.write_text(
            json.dumps(report, ensure_ascii=False, indent=2,
                       sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in (
        "input_rows", "merged_rows", "duplicates_removed",
        "dataset_fingerprint")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
