# Weighted two-ply frontier evidence

The benchmark separates the raw Rust call from the end-to-end
`choose_action` path. The canonical `legacyV2` profile uses a 40ms soft
deadline, 50ms hard deadline, three-root cap, lazy child ukeire, and a 0.90
partial-coverage gate. The old weighted profile spelling remains an alias.

```bash
PYTHONPATH=/tmp/mj-kernel-weighted-test:. \
  python3 scripts/legacy_two_ply_weighted_bench.py --states 100 --seed 0
```

The current evidence is diagnostic: the weighted layer is faster than the
previous exact frontier and exposes accepted partial rows, but the end-to-end
50ms p95 and 95% complete/accepted gate are not yet met. The explicit release
decision nevertheless enables `legacyV2` by default; every incomplete/unsafe
result still falls back transactionally to `legacy` and the latency/coverage
metrics remain recorded for follow-up tuning.
