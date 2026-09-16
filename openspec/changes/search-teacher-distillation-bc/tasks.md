# Tasks: Search Teacher Distillation BC

## 1. P0 Baseline and Contracts

- [x] 1.1 Freeze `HEAD=8a94fdeb1a7801289f2bd707b24b271d99bb8961`, rules, belief/search/model/runtime fingerprints and current final-test splits.
- [x] 1.2 Add `TeacherBudgetProfile`, `SearchDistillationProfile`, `OpponentPopulationProfile`, and immutable fingerprints.
- [x] 1.3 Freeze `pi0` selection procedure: strongest low-latency policy is chosen by reference regret + paired score, not evaluator name.
- [x] 1.4 Freeze forced-state policy, special-state tags, catastrophic-regret threshold and score units.

## 2. P1 Teacher Dataset Generator

- [x] 2.1 Add `scripts/search_teacher_generate.py`.
- [x] 2.2 Generate trajectories from declared `policy_version_source` and frozen opponent population.
- [x] 2.3 Collect multi-action states with `InformationHistory`, `PublicDecisionContext`, `BeliefState`, planes/scalars and legal mask.
- [x] 2.4 Skip high-budget search for forced actions; retain deterministic 1–5% sanity subset.
- [x] 2.5 Implement adaptive teacher budget 512 -> 2048 -> 8192 -> 16000 with deterministic tier promotion.
- [x] 2.6 Store `SearchSample` plus teacher tier/status/Q-gap/special tags without hidden-world identities.
- [x] 2.7 Support deterministic resume by source_group/context_hash/search_seed without duplicate accumulation.
- [x] 2.8 Add hidden-information invariance and worker-count/reordering reproducibility tests.

## 3. P2 Dataset Split and Quality

- [x] 3.1 Keep complete source groups inside one split; reject train/validation/final-test leakage.
- [x] 3.2 Report counts by generation, legal-action count, phase, YCBK, dealer, shanten/live-wall bucket and special-state tag.
- [x] 3.3 Report simulations, ambiguity, failed simulations, belief resets, Q-gap and variance distributions.
- [x] 3.4 Add duplicate/context collision detection and feature fingerprint verification.
- [x] 3.5 Define minimum critical-bucket coverage before release evaluation.

## 4. P3 Search BC Trainer

- [x] 4.1 Add `scripts/search_bc_train.py` using `SearchDataset` / `PolicyValueNet`.
- [x] 4.2 Implement visit-distribution masked CE as default target.
- [x] 4.3 Implement optional Q-soft target with versioned temperature.
- [x] 4.4 Implement unit-safe weighting based on evidence/ambiguity/reset/Q-gap.
- [x] 4.5 Phase-1 default is policy-only (`value_weight=0`).
- [x] 4.6 Preserve suit-permutation augmentation only if belief/history planes transform consistently; add parity tests.
- [x] 4.7 Save every epoch checkpoint with full provenance.

## 5. P4 Regret-based Model Selection

- [x] 5.1 Add frozen reference context set outside training groups.
- [x] 5.2 Add `scripts/search_bc_eval.py` to run network-only actions against 8k/16k search reference.
- [x] 5.3 Report mean/p50/p95 regret, catastrophic regret rate, KL, top1 agreement and per-bucket regret.
- [x] 5.4 Select `best-by-regret.pt`; top1 is diagnostic only.
- [x] 5.5 Reject checkpoints that improve mean regret but materially regress p95/special-state gates.
- [x] 5.6 Add batch=1 CPU benchmark including feature extraction + legal mask.

## 6. P5 Value v2 (Optional before first policy release)

- [x] 6.1 Freeze score/value transform, output activation and inverse-transform contract.
- [x] 6.2 Remove incompatibility between raw root score and tanh output.
- [x] 6.3 Train/evaluate MAE/RMSE/ranking/bucket calibration.
- [x] 6.4 Enable ValueNet search leaf only after calibration gate passes.
- [ ] 6.5 Re-run teacher/reference evidence if leaf semantics change.

## 7. P6 Generation 0

- [ ] 7.1 Evaluate current candidates and freeze strongest fast `pi0`.
- [ ] 7.2 Generate `dataset0` with ~200k–500k useful multi-action states.
- [x] 7.3 Default teacher 2048 sims; hard/disagreement 8192; 16000 reference subset.
- [ ] 7.4 Train `pi1` under visit-only and at least one Q-soft/hybrid ablation.
- [ ] 7.5 Promote only if frozen reference regret improves and runtime legality/latency gates pass.

## 8. P7 DAgger / Policy Iteration

- [ ] 8.1 Run `pi1` trajectories to collect student-visited states and teacher-label `dataset1`.
- [ ] 8.2 Train `pi2` on versioned aggregate `D0 + D1` with generation-aware sampling.
- [ ] 8.3 Repeat at least once more if `pi2` passes promotion, producing `pi3` or explicit stop evidence.
- [ ] 8.4 Track each generation: mean/p95 regret, paired score, population score, teacher cost and batch1 latency.
- [ ] 8.5 Stop/rollback on population regression or consecutive no-improvement via `PolicyIterationRunner`.

## 9. P8 Paired Score and Robustness

- [ ] 9.1 Run at least 4096 paired games against previous promoted policy.
- [ ] 9.2 Run BC vs shape-v2 and BC vs shape-v1 with identical seed/seat/dealer/YCBK/opponent schedules.
- [x] 9.3 Use source-game clustered bootstrap for `hero_round_score_points`.
- [x] 9.4 Report self-play, legacy/shape-v1 and frozen-population matrices separately.
- [x] 9.5 For superiority require `CI95_lower(DeltaScore) > 0`; otherwise label non-regression/ambiguous.
- [x] 9.6 Report win rate/multiplier/draw only as secondary diagnostics.

## 10. P9 Runtime and Release

- [x] 10.1 Wire promoted checkpoint into `policy-v3`/BC runtime with explicit manifest.
- [ ] 10.2 After offline gates pass, evaluate `confidence_threshold=0` policy-only runtime.
- [x] 10.3 Retain fallback on model/manifest/feature/non-finite/runtime failures and record every fallback.
- [ ] 10.4 Verify illegal=0, NaN/non-finite=0, emergency fallback=0 in release workload.
- [ ] 10.5 Run ten-game concurrent scheduling and at least three new platform rooms; check p50/p95/p99/max and window loss.
- [ ] 10.6 Default switch only when regret, paired score, population, special-state, performance and online gates all pass.
- [x] 10.7 Preserve shape-v2/legacy kill switch and rollback checkpoint.

## 11. P10 Evidence and Docs

- [ ] 11.1 Save teacher/dataset/training/checkpoint/evaluation manifests under OpenSpec artifacts.
- [x] 11.2 Update replay/logview with student action, teacher/reference regret (offline), confidence and fallback diagnostics.
- [x] 11.3 Update docs for generating the next policy generation and reproducing promotion evidence.
- [ ] 11.4 Archive only after a promoted distilled policy completes all required gates.
