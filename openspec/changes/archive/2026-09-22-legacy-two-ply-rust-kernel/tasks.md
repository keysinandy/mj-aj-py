# Tasks

## 1. Native kernel contract

- [x] 1.1 Add a fixed-array Rust implementation for one-draw/one-child-discard legacy frontier evaluation with deterministic node accounting.
- [x] 1.2 Expose `legacy_two_ply_frontier` through PyO3 with compact rows, native version metadata, and four-copy/shape validation.
- [x] 1.3 Add a Python `mj.shanten` capability wrapper and `MJ_KERNELS=python`/missing-extension behavior.

## 2. Python adapter and semantics

- [x] 2.1 Add a versioned kernel selector to `LegacyTwoPlyProfile` and its fingerprint/JSON representation.
- [x] 2.2 Adapt `evaluate_legacy_two_ply` to call the native batch kernel only for supported public states, validate all rows, and map native metrics into existing `FutureEvaluation` values.
- [x] 2.3 Preserve Python shape/feed tie-breaks, root legality filtering, explanation construction, and transactional fallback on malformed/partial/over-budget native results.

## 3. Tests and evidence

- [x] 3.1 Add fixed `seq=100` native/Python parity tests, including `3t` versus `9b` and the lower-current-ukeire `3b` candidate.
- [x] 3.2 Add randomized public-state parity tests for metrics, best-child discard mass, visible isolation, frozen legality, and node-budget fallback.
- [x] 3.3 Run focused tests, Rust/Python parity, compile/diff checks, and an interleaved 100-state 8/50/1000 ms benchmark; record evidence and release decision.
- [x] 3.4 Keep the native path opt-in/default legacy until performance and existing information-safety/paired-benefit gates pass; validate and report the OpenSpec change.
