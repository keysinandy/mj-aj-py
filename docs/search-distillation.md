# Search Teacher Distillation BC

OpenSpec change: `openspec/changes/search-teacher-distillation-bc/`.

The pipeline replaces accuracy-selected one-hot BC with a regret-selected,
soft-target distillation loop: a high-budget information-set search teacher
labels student-visited states, quality gates judge datasets, and promotion is
decided by frozen reference regret plus paired round score.

Baseline freeze: `openspec/changes/search-teacher-distillation-bc/artifacts/baseline_freeze.json`
(HEAD `8a94fdeb`, rules `hangzhou-platform-guide-v34`, score units
`hero_round_score_points`).

## Reduced Gen0 scope (approved 2026-09-16)

Measured CPU-only throughput after the history-hash optimization is
33.7 sims/s/core (512 sims ≈ 15s, 2048 sims ≈ 61s, 8192 sims ≈ 4.1min per
state). The full 200k–500k state target is a multi-week run on the 6-core
host, so the first generation uses a frozen reduced profile:
`artifacts/teacher_budget_reduced_gen0.json` (tiers 512→1024),
`dataset0` target 10k–20k states (bounded by the frozen train split),
8000-sim reference and ≥1024 paired pairs. The full ladder, 8k/16k
reference, 4096-pair gate and online switch requirements are unchanged and
still required before any release; reduced Gen0 evidence is explicitly
labeled. See `design.md` §15.

**YCBK rule:** `you_cai_bi_kao` is treated as permanently disabled for this
change — generation, teacher search, reference sets and paired schedules
MUST use `you_cai_bi_kao=false` (`--ycbk off`). The engine/runtime still
honor the platform flag at inference, but YCBK-on is never training input
or release evidence. See `design.md` §16.

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
  --split train --ycbk off --workers 6 \
  --policy-source heuristic:shape-v2 \
  --disagreement-source heuristic:shape-v1 \
  --budget-json openspec/changes/search-teacher-distillation-bc/artifacts/teacher_budget_reduced_gen0.json \
  --manifest-out openspec/changes/search-teacher-distillation-bc/artifacts/dataset0.manifest.json

# 2. frozen reference set (validation split; forced states skipped)
PYTHONPATH=. python3 scripts/search_teacher_generate.py \
  --reference-out data/distill/reference_gen0.jsonl \
  --split validation --limit-specs 24 --ycbk off --workers 6 \
  --reference-simulations 8000

# 2b. freeze pi0 by reference regret (heuristics; paired CI optional)
PYTHONPATH=. python3 scripts/search_pi0_freeze.py \
  --reference data/distill/reference_gen0.jsonl \
  --out runs/search_bc/pi0_selection.json

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
  --games 1024 --ycbk off --out runs/search_bc/gen0/paired_report.json
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

## Regret-aware active distillation

OpenSpec change: `openspec/changes/regret-aware-active-distillation/`.

The second round keeps the deployment shape (teacher offline, PolicyNet
online) and optimizes the loop: active sampling, regret-aware loss, replay
and a permanent hard-state set, with staged gates.

| Module | Contract |
| --- | --- |
| `mj/training/active_sampling.py` | candidate pool + cheap scoring, `ActiveSamplingProfile` ratios/importance, deterministic quota sampling, `state_id` |
| `mj/training/teacher_cache.py` | cache keyed by state + teacher version + config hash; reuse only at <= cached budget |
| `mj/training/regret_training.py` | `RegretAwareLossProfile` (policy/ranking/catastrophic), pairwise logistic ranking with Q-gap weights, `teacher_confidence x policy_error x importance` weights (clipped) |
| `mj/training/replay_buffer.py` | recent/historical/hard/special buckets, historical reservoir, deterministic batch mix |
| `mj/training/hard_states.py` | permanent hard-state registry, failure-mode dedupe, per-checkpoint hard evaluation + fixed/regressed report |

Commands:

```bash
# 1. candidate pool from the current policy (policy-only cheap scoring)
PYTHONPATH=. python3 scripts/search_candidate_pool.py \
  --out data/distill/pool_gen0.jsonl --selected-out data/distill/pool_gen0.selected.jsonl \
  --manifest-out data/distill/pool_gen0.manifest.json \
  --policy-source checkpoint:runs/search_bc/gen0/best-by-regret.pt \
  --batch-size 20000 --generation 0 --split train --ycbk off

# 2. teacher-label only the selected states (cache is resumable)
PYTHONPATH=. python3 scripts/search_teacher_generate.py \
  --pool-in data/distill/pool_gen0.selected.jsonl \
  --out data/distill/dataset_dagger1.jsonl \
  --teacher-cache-dir data/distill/teacher_cache \
  --budget-json openspec/changes/search-teacher-distillation-bc/artifacts/teacher_budget_reduced_gen0.json

# 3. train with regret-aware loss / replay (one experiment variable at a time)
PYTHONPATH=. python3 scripts/search_bc_train.py \
  --data data/distill/dataset0.jsonl data/distill/dataset_dagger1.jsonl \
  --out runs/search_bc/gen1 --generation 1 --replay-size 20000 \
  --ranking-weight 0.25 --catastrophic-weight 0.0 --device cuda

# 4. staged gates: offline -> hard set -> fast paired (full paired unchanged)
PYTHONPATH=. bash scripts/search_distill_pipeline.sh \
  runs/search_bc/gen1/best-by-regret.pt data/distill/reference_gen0.jsonl \
  runs/search_bc/gen1/gates
```

Experiment order is one variable per run: E0 baseline → E1 active sampling
→ E2 ranking loss → E3 both → E4 replay → E5 hard set → E6 catastrophic /
weighting refinements. Top-1 accuracy is diagnostic; dashboards lead with
mean/p95 regret, catastrophic rate, special-state regret and teacher cost.

## Multi-machine generation

Games are independent and deterministic, so generation shards by seed window
and merges cleanly. Keep the same code revision, budget profile and
belief/search profiles on every machine, and use one `--out` +
`--teacher-cache-dir` per machine (no shared writes):

```bash
# machine A (train seeds 240000-240511)
PYTHONPATH=. python3 scripts/search_teacher_generate.py \
  --out data/distill/shard_a.jsonl --seed-start 240000 --games 512 \
  --ycbk off --workers "$(nproc)" \
  --policy-source heuristic:shape-v1 \
  --budget-json openspec/changes/search-teacher-distillation-bc/artifacts/teacher_budget_reduced_gen0.json \
  --teacher-cache-dir data/distill/cache_a

# machine B (train seeds 240512-241023)
PYTHONPATH=. python3 scripts/search_teacher_generate.py \
  --out data/distill/shard_b.jsonl --seed-start 240512 --games 512 \
  --ycbk off --workers "$(nproc)" \
  --policy-source heuristic:shape-v1 \
  --budget-json openspec/changes/search-teacher-distillation-bc/artifacts/teacher_budget_reduced_gen0.json \
  --teacher-cache-dir data/distill/cache_b

# merge (dedupes by work_id, rejects conflicting fingerprints)
PYTHONPATH=. python3 scripts/search_dataset_merge.py \
  --data "data/distill/shard_*.jsonl" --out data/distill/dataset0.jsonl \
  --manifest-out data/distill/dataset0.manifest.json
```

Source-group splits derive from the seed ranges, so windows inside
`240000..241023` stay in `train` and merging cannot leak validation/final
test. Reference generation shards the same way (`--reference-out` plus a
per-machine `--seed-start/--games` window, or distinct `--limit-specs`
windows); concatenate the reference JSONL files.

Training stays single-machine: 10k–50k rows is minutes on the GPU, while
teacher search is the CPU bottleneck by orders of magnitude. Distributed
training is not implemented and is not needed at this scale.

