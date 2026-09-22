# Tasks

## 1. One-ply short-circuit and explanation contract

- [x] 1.1 Add an explicit singleton-frontier short-circuit after weighted root pruning, return the unique legal tile without native two-ply, and verify the explanation records the short-circuit reason with missing future values.
- [x] 1.2 Add improvement observed/lower/upper bound fields and partial-mean denominator fields to candidate JSON, and verify complete rows keep equal bounds while partial rows use covered weight.

## 2. Decision-safe partial acceptance

- [x] 2.1 Replace coverage-only partial acceptance with committed-root interval checks; verify overlapping intervals fall back to complete legacy even above the coverage threshold.
- [x] 2.2 Verify a strict lower-bound winner is accepted as partial before the coverage threshold and cannot be overtaken by unvisited draw mass.

## 3. Rust weighted work budget

- [x] 3.1 Charge weighted budget by uncached shanten work, stop before expensive child/ukeire expansion when exhausted, and preserve committed rows/reason `work_budget_exceeded`.
- [x] 3.2 Keep cache hits, child nodes, ukeire calls, and shanten misses separately observable; verify the work budget does not change exact/offline V1 behavior.

## 4. Validation and evidence

- [x] 4.1 Add fixed and synthetic tests for singleton short-circuit, partial means, interval overlap, interval separation, and work-budget exhaustion.
- [x] 4.2 Run Rust/Python parity, focused bot tests, `git diff --check`, and the 100-state weighted benchmark; record complete/partial/fallback and p50/p95/p99 deltas against the prior baseline.
