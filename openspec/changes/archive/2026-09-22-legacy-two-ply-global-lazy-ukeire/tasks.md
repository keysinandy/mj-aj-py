# Tasks

## 1. Two-stage Rust weighted kernel

- [x] 1.1 Add Stage-A accumulator rows for child shanten, improvement weight,
  bounds, and best-shanten discard lists without calculating child ukeire.
- [x] 1.2 Add bounds-based early winner detection and skip Stage B when a unique
  improvement winner is proven, preserving row arity and explicit sentinel state.
- [x] 1.3 Reuse Stage-A best children for Stage B only when improvement remains
  tied; verify exact weighted action/metrics parity.

## 2. Deadline and adapter contract

- [x] 2.1 Propagate an internal hard deadline through shanten/ukeire inner loops
  and preserve work-budget accounting/reasons.
- [x] 2.2 Map Stage-A-only rows to null future ukeire fields and add top-level
  search phase/usage metadata without changing legacy fallback semantics.

## 3. Benchmark and regression

- [x] 3.1 Add tests for early Stage-A winner, improvement tie entering Stage B,
  sentinel/missing metrics, deadline abort, and Rust/Python exact parity.
- [x] 3.2 Report search-used rate and Stage-A/Stage-B counts in the 100-state
  benchmark; run formatting, OpenSpec strict validation, and focused tests.
