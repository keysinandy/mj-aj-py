# Search Teacher Distillation BC

OpenSpec change: `openspec/changes/search-teacher-distillation-bc/`.

The pipeline replaces accuracy-selected one-hot BC with a regret-selected,
soft-target distillation loop: a high-budget information-set search teacher
labels student-visited states, quality gates judge datasets, and promotion is
decided by frozen reference regret plus paired round score.

Baseline freeze: `openspec/changes/search-teacher-distillation-bc/artifacts/baseline_freeze.json`
(HEAD `8a94fdeb`, rules `hangzhou-platform-guide-v34`, score units
`hero_round_score_points`).

## Contracts

| Module | Contract |
| --- | --- |
| `mj/training/teacher_budget.py` | `TeacherBudgetProfile`: tiers 512/2048/8192/16000, deterministic promotion, forced sanity ratio, critical tags |
| `mj/training/distillation_profile.py` | `SearchDistillationProfile` (target mode/temperature/weights/catastrophic threshold/coverage minimums), `OpponentPopulationProfile`, special-state tags, `select_strongest_fast_policy` (pi0) |
| `mj/training/search_data.py` | `SearchSample` with generation/forced/teacher tier/status/Q-gap/feature fingerprint + `work_id` resume identity |
| `mj/training/dataset_report.py` | split integrity, quality report, duplicate/collision detection, coverage gate, feature-fingerprint verification |
| `mj/training/search_bc_train.py` | visit/Q-soft/hybrid targets, unit-safe weights, suit augmentation, per-epoch provenance checkpoints |
| `mj/training/regret_selection.py` | frozen reference rows, network-only regret/KL/agreement/bucket report, regret-first selection, batch=1 benchmark |
| `mj/training/paired_eval.py` | paired candidate/baseline schedule, per-matrix report, source-game clustered bootstrap, superiority labels |
| `mj/training/value_contract.py` | Value v2 transform contract + calibration gate (transform lives in `mj/models/policy_value.py`) |
| `mj/decision/release.py` | release manifest (checkpoint hash + profile), kill switch/rollback, illegal/NaN/emergency gate |
| `mj/decision/policy_v3.py` | `calibration_policy=policy-only-v1` for uncalibrated policy-only checkpoints, fallback/receipt counters |

Every profile/contract exposes a 24-char `fingerprint` over its canonical
payload; a changed threshold changes the fingerprint recorded in manifests.

## Commands

```bash
# 0. baseline freeze (static artifact)
python3 -c "from mj.training import write_baseline_freeze; \
  write_baseline_freeze('openspec/changes/search-teacher-distillation-bc/artifacts/baseline_freeze.json')"

# 1. teacher dataset (resume is the default: an existing --out is deduplicated by work_id)
PYTHONPATH=. python3 scripts/search_teacher_generate.py \
  --out data/distill/dataset0.jsonl \
  --split train --ycbk both --workers 8 \
  --policy-source heuristic:shape-v2 \
  --disagreement-source heuristic:shape-v1 \
  --manifest-out openspec/changes/search-teacher-distillation-bc/artifacts/dataset0.manifest.json

# 2. frozen high-budget reference set (validation/final-test groups only)
PYTHONPATH=. python3 scripts/search_teacher_generate.py \
  --reference-out data/distill/reference.jsonl --split validation --ycbk both

# 3. BC training (visit-only policy-first is the default; --augment suit is parity-tested)
PYTHONPATH=. python3 scripts/search_bc_train.py \
  --data data/distill/dataset0.jsonl --out runs/search_bc/gen0 \
  --generation 0 --blocks 2 --width 64 --epochs 4 --bs 256 --lr 3e-4 \
  --augment suit \
  --provenance openspec/changes/search-teacher-distillation-bc/artifacts/dataset0.manifest.json

# 4. regret-first selection against the frozen reference
PYTHONPATH=. python3 scripts/search_bc_eval.py \
  --reference data/distill/reference.jsonl \
  --checkpoint runs/search_bc/gen0/epoch_*.pt \
  --out runs/search_bc/gen0/selection_report.json \
  --benchmark-samples 64 --benchmark-repeats 3 \
  --latency-p95-ms 36
```

`best-by-regret.pt` is the only checkpoint allowed to continue to the
paired/population gates; top-1 agreement is printed as a diagnostic.

```bash
# 5. paired games vs shape-v2/shape-v1/frozen population (same seed/seat/dealer/YCBK)
PYTHONPATH=. python3 scripts/search_bc_paired.py \
  --candidate checkpoint:runs/search_bc/gen0/best-by-regret.pt \
  --baseline heuristic:shape-v2 \
  --matrices self_play,legacy_shape_v1,frozen_population \
  --games 2048 --ycbk both --out runs/search_bc/gen0/paired_report.json
```

`paired_score_report` clusters by source-game seed, uses
`cluster_bootstrap` (never resampling individual games), requires
`ci95_lower > 0` for a `superior` verdict, and keeps win/multiplier/draw rate
as secondary diagnostics only.

## DAgger generations

Generation `k+1` reuses the same commands with the checkpoint selected at
generation `k` as `--policy-source checkpoint:runs/search_bc/genk/best-by-regret.pt`,
a new `--generation k+1`, and `--provenance` pointing at the generation
manifest (the policy version is recorded automatically from `--policy-source`).
Aggregate `D0 + D1` by passing multiple `--data` paths;
each sample keeps its generation/policy source and every source group stays
inside exactly one split (`merge_split_assignments` rejects conflicts).

## Promotion gates

A candidate is promotable only when all of the following hold:

1. reference regret: mean does not increase and p95/catastrophic gates pass;
2. paired games: `CI95_lower(hero_round_score_points) > 0` on the
   previous-promoted / shape-v2 / shape-v1 matrices with identical
   seed/seat/dealer/YCBK schedules;
3. population: self-play, legacy/shape-v1 and frozen population reported
   separately, no population regression;
4. runtime: `release_gate_report` shows illegal=0, non-finite=0,
   emergency=0 and batch=1 p95 inside the decision window;
5. online: ten-game concurrent scheduling and platform rooms satisfy p95/p99
   and window losses.

`PolicyIterationRunner`/`should_stop_iteration` provide deterministic
stopping and rollback (`last-all-gates-passed`).

## Release and rollback

```python
from mj.decision.release import write_release_manifest, load_release_runtime
manifest = write_release_manifest("release.json", checkpoint="best-by-regret.pt",
                                  model=model, value_mode="policy-only",
                                  rollback_checkpoint="current_default.pt")
runtime, active = load_release_runtime("release.json")
```

An inactive manifest produces a model-less runtime so the `shape-v2 ->
legacy` kill switch stays available; every fallback is counted in
`runtime.stats`. Value-mode releases additionally require a calibrated model
manifest and a calibration fingerprint.

## Operational blockers

The code path above is covered by `tests/test_distillation_contracts.py`,
`tests/test_teacher_generate.py`, `tests/test_search_bc_train.py`,
`tests/test_regret_selection.py`, `tests/test_value_contract.py` and
`tests/test_release.py`. Producing the real `dataset0` (200k–500k states at
2048–16000 simulations), the 4096-game paired matrices and the platform
workload still requires the declared compute and room access.
