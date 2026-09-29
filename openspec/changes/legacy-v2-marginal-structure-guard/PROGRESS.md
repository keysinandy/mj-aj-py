# Progress: legacy-v2-marginal-structure-guard

Updated 2026-09-29 after the native Rust kernel rebuild.

## Validation

- Native kernel: `rust-weighted-two-ply-v5`, compatible; rebuilt with
  `maturin develop --release` for the repository `.venv` CPython 3.11.
- Targeted regression/parity suite: **102 passed**.
- Strict OpenSpec validation: passed for this change.
- 899s native golden: complete Stage B at the 50 ms online budget, selected
  tile 4p (tile index 12), not 9s.

## Performance

Command: `scripts/legacy_two_ply_weighted_bench.py --interleaved --states
1000 --repeats 3`.

| evaluator | p50 | p95 | p99 | fallback | frontier p50 |
| --- | ---: | ---: | ---: | ---: | ---: |
| baseline | 35.70 ms | 66.10 ms | 80.37 ms | 114/3000 | 3 |
| speed-band/Pareto | 30.57 ms | 66.42 ms | 79.83 ms | 87/3000 | 2 |

Candidate deltas: p95 **+0.48%**, p99 **-0.67%**, fallback **-0.90 pp**.
All three ordinary-discard performance thresholds pass.

## Local score

Command: `scripts/legacy_v2_speed_band_score_bench.py --games 200
--repeats 3` with the same 600 seed/dealer pairs and alternating evaluator
order.

- Paired balanced-hero score delta: **+1.387 points/game**.
- Bootstrap 95% interval: **[-0.08, +2.87]**.
- Balanced-hero win rate: **30.83% vs 27.00%** (+3.83 pp).
- The interval crosses zero, so this is positive directional evidence but not
  conclusive release evidence.

Full JSON evidence is in `artifacts/local_perf_3x1000.json`,
`artifacts/local_score_3x200.json`, and `artifacts/local_validation.json`.

## Rollout state

`speed_band_enabled` and `pareto_frontier_enabled` remain explicit opt-in
flags. The default is unchanged because the required 4096-pair and independent
30720-pair score gates have not been run, and the completed 600-pair interval
still crosses zero.
