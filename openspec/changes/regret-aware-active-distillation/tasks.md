# Tasks: Regret-aware Active Distillation

## 1. P0 Baseline and Schema

- [ ] 1.1 Freeze `policy_v0`/`dataset_v0`/`teacher_config_v0`/`training_config_v0`/`evaluation_config_v0` and seeds; make every later run comparable.
- [x] 1.2 Extend the teacher-state schema with `state_id`, `episode_id`, `generation_id`, student policy fields (`policy_prob_by_action`, `policy_action`, `policy_entropy`), `policy_regret`, `state_source` and `sample_weight`, keeping raw values unnormalized.
- [x] 1.3 Keep episode/game-level splits; prove no train/validation/test leakage for sampled rows.
- [x] 1.4 Add run metadata (`run_id`, config hashes, seeds, gate metrics) to every generated artifact.

## 2. Candidate Pool and Active Sampling

- [x] 2.1 Add candidate-pool collection from `pi_k` rollouts with policy-only cheap scoring (entropy, top1-top2 gap, special tags, visit counts, historical-failure flag).
- [x] 2.2 Add a configurable, deterministic active sampler with normal/disagreement/hard/special/random ratios.
- [x] 2.3 Define hard candidate criteria (action mismatch, regret threshold, high-prob low-Q, historical failure) with regret-based priority.
- [x] 2.4 Add cheap-teacher disagreement signal when available.
- [x] 2.5 Add candidate-pool serialization + tests for determinism and ratio tolerance.

## 3. Teacher Cache and Adaptive Budget

- [x] 3.1 Add teacher cache keyed by `state_hash + teacher_version + teacher_config_hash`; lower/equal budget may reuse, higher budget re-searches.
- [x] 3.2 Prevent duplicate accumulation for the same (state, generation) on cache hits.
- [ ] 3.3 Extend adaptive budget evidence with visit concentration, search stability and policy/teacher conflict; special/high-risk escalates.
- [x] 3.4 Add cache/budget tests (hit, miss, upgraded teacher, mixed generations).

## 4. Regret-aware Policy Loss

- [x] 4.1 Keep masked soft-target KL/CE as the primary loss; no one-hot collapse of near ties.
- [x] 4.2 Add pairwise logistic ranking loss with Q-gap weights over best-vs-others; default `policy_weight=1.0, ranking_weight=0.25`.
- [x] 4.3 Add opt-in catastrophic margin loss with declared threshold and default weight zero.
- [x] 4.4 Add `sample_weight = clip(teacher_confidence * policy_error_factor * importance_factor, min, max)`; ambiguity lowers forced-learning weight.
- [x] 4.5 Fingerprint all loss/weight constants into the training profile and checkpoint provenance.
- [x] 4.6 Add loss/weight unit tests (near-tie, expensive mistake, clip bounds, disabled catastrophic).

## 5. Generation Replay

- [x] 5.1 Add replay buckets (recent/historical/hard/special) with declared mix ratios.
- [x] 5.2 Add per-generation quotas or reservoir sampling to bound historical storage.
- [x] 5.3 Enforce split safety: replay never draws validation/final-test groups.
- [x] 5.4 Add deterministic batch-mixture tests.

## 6. Hard-State Regression Set

- [x] 6.1 Add persistent hard-state registry with triggers (extreme regret, catastrophic action, special failure, paired-game mistake, online failure, regression recurrence).
- [x] 6.2 Deduplicate by state id + similarity/phase/action pattern/failure tag; optimize failure-mode coverage.
- [x] 6.3 Add per-checkpoint hard-set evaluation (mean/p95/max regret, catastrophic/fixed/regressed counts, special tags, historical policy comparisons).
- [ ] 6.4 Keep hard-set evaluation data out of gradient updates unless the source group is in the training split.
- [x] 6.5 Add registry/evaluation tests.

## 7. Staged Gates and Orchestration

- [x] 7.1 Wire Gate A offline promotion rules (mean not worse; p95/catastrophic/special not materially worse; at least one core metric improved).
- [x] 7.2 Wire Gate B fast paired (256–512 pairs, same seed/seat/dealer/opponent schedule) as a pre-filter.
- [x] 7.3 Keep Gate C full paired at the unchanged 4096-pair release standard.
- [x] 7.4 Keep the runtime gate measuring the network-only release path (p50/p95/p99, memory, throughput).
- [ ] 7.5 Add a single pipeline entry point: rollout -> pool -> sample -> teacher -> dataset -> replay -> train -> gates -> promote.

## 8. Experiments

- [ ] 8.1 E0 baseline reproduction (platform reproducibility check).
- [ ] 8.2 E1 active sampling only.
- [ ] 8.3 E2 ranking loss only.
- [ ] 8.4 E3 active sampling + ranking loss.
- [ ] 8.5 E4 replay.
- [ ] 8.6 E5 hard-set regression/oversampling.
- [ ] 8.7 E6 catastrophic loss / temperature / weighting refinements (only after E5).
