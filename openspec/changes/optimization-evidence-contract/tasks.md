# Tasks: Optimization Evidence Contract

This change records future implementation; checkboxes below are not claims that the existing code is fixed.

## 1. Training promotion evidence

- [ ] 1.1 Normalize `offline_report.json` / `selection_report.json`, `fast_paired.json`, nested `paired_report.json` and historical flat `paired_<matrix>.json` in `mj/training/run_summary.py`; retain stage identity and distinguish absent, rejected and pending.
- [ ] 1.2 Make the pipeline check offline eligibility, and compare to a declared previous report before claiming inter-generation improvement; fail closed when a required report or provenance field is missing.
- [ ] 1.3 Extract testable per-matrix fast-paired decision logic; reject regression and insufficient pairs, route ambiguous evidence to full evaluation without calling it promoted, and leave full-paired/runtime standards unchanged.
- [ ] 1.4 Add fixture-based tests of early exit, nested/flat formats, false-pass regression and actual summary rendering; update `docs/search-distillation.md` with artifact names, provenance and gate semantics.

## 2. Teacher resume and v5 provenance

- [ ] 2.1 Reproduce the intermittent `tests/test_teacher_generate.py::TestGeneration::test_resume_matches_uninterrupted_run` failure under a recorded v5 runtime and test order; if it does not recur, record it as unresolved rather than invent a cause.
- [ ] 2.2 On a failing trace, distinguish changed source trajectories from changed skip/persisted work identities; prove the mismatch before editing generator or sample code.
- [ ] 2.3 Add identity and resumed-vs-uninterrupted fingerprint assertions for forced/unsupported/search-result paths; implement a fix only if its cause is diagnosed (otherwise keep the issue open), and reject conflicting duplicate rows.
- [ ] 2.4 Verify actual kernel/commit/teacher/search/belief/population manifest provenance and keep v3-degraded shards separate from v5 shards. Do not regenerate formal data until evidence passes.

## 3. Pending Shape Guard deltas

- [ ] 3.1 Reconcile both pending MODIFIED requirements with the canonical strict-lexicographic admission rule, equality negative scenario, `admitted_by`, fallback and standing-shape audit fields; retain speed-band extensions.
- [ ] 3.2 Run strict validation of both pending changes and inspect the archive projection for semantic rollback and missing headers; leave unresolvable archive conflicts explicitly open. Do not broaden the guard or freeze a new baseline in this task.

## 4. Online X-round follow-up (separate behavioral release)

- [ ] 4.1 Design and test a per-round, per-seat mirror-backed counter surviving repeated `Mirror.build_game()` calls and resetting on authoritative round transition; preserve offline Game accounting.
- [ ] 4.2 Before activation, run v5-clean mirror-path legality tests plus paired score and latency/window-loss gates; update `PROGRESS.md` and tests with the result. Keep X activation and Piao pass-cap enablement separate.
