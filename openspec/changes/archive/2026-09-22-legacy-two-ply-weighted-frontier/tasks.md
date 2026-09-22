# Tasks

## 1. Profile and public contract

- [x] 1.1 Add the canonical `legacyV2` profile (internal `weighted_two_ply_frontier`) with 40ms soft deadline, 50ms hard deadline, `max_frontier_candidates=3`, lazy child ukeire, partial acceptance, and `min_partial_coverage=0.90`; keep explicit legacy and exact profiles unchanged.
- [x] 1.2 Extend evaluation models/JSON with mode, complete/partial state, covered and total draw weight, coverage, current/future tile-kind counts, and search metrics while leaving missing fields null rather than zero.
- [x] 1.3 Add capability/version wrappers and evaluator routing in bot, offline runner, clientd strategy validation, and logview; make `legacyV2` the default and retain old weighted spellings as aliases.

## 2. Rust weighted frontier

- [x] 2.1 Add a versioned PyO3 `weighted_two_ply_frontier` entry point with integer remaining-weight ordering, legal masks, soft/hard budget checks, per-root committed rows, and explicit complete/partial reasons.
- [x] 2.2 Change the native shanten memo to cache both wildcard and ordinary states under a bounded decision-local key; report shanten cache hits/misses without reading hidden state.
- [x] 2.3 Implement lazy child ukeire: compute every child shanten, expand ukeire only for minimum-shanten ties, and return weighted ukeire plus ukeire tile-kind count.
- [x] 2.4 Track root/draw/child nodes, shanten/ukeire calls, elapsed microseconds, total/covered weight, and per-root coverage; ensure counters include inner shanten work.
- [x] 2.5 Preserve exact V1 behavior and expose a distinct native kernel version so old wheels are treated as unavailable for the new profile rather than silently changing semantics.

## 3. Python adapter and partial policy

- [x] 3.1 Build the current root frontier before native search, apply deterministic cheap pruning only when it exceeds the configured cap, and pass public visible counts plus legal masks.
- [x] 3.2 Map committed native rows into `FutureEvaluation` values and accept partial results only when every retained root meets the coverage threshold; otherwise fall back transactionally to complete legacy.
- [x] 3.3 Keep shape/feed/wildcard/freeze/reaction/HU/KONG policy gates in Python and ensure partial results cannot influence an incomplete root or out-of-scope action.
- [x] 3.4 Keep exact offline mode available without partial acceptance and add a Python reference implementation for weighted/lazy parity tests.

## 4. Tests and regression evidence

- [x] 4.1 Add fixed `3t` versus `9b` regression assertions for weighted improvement, expected child ukeire, tile-kind diversity, and selected action.
- [x] 4.2 Add randomized native/reference parity tests for complete weighted rows, draw weights, lazy child ties, visible isolation, frozen legality, and exact fallback.
- [x] 4.3 Add partial coverage tests for accepted partial, insufficient retained-root coverage, soft/hard deadline ordering, and legacy fallback metadata.
- [x] 4.4 Add Rust/Python cache and metrics tests, compile/format checks, and focused bot/logview/clientd routing tests.
- [x] 4.5 Run a 100-state benchmark separating raw native and end-to-end latency; report complete, accepted partial, fallback, p50/p95/p99, coverage, frontier size, shanten calls, ukeire calls, and cache hit rate.
- [x] 4.6 Validate the change strictly; publish the recorded 50ms evidence and apply the explicit release decision to make `legacyV2` the default, with transactional legacy fallback retained.
