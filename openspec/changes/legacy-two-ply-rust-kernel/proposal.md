# Proposal

## Why

The legacy two-ply evaluator now has a correct Python reference implementation, but its nested future-draw/child-discard loop still misses the 8 ms decision budget on the benchmark profile. The existing Rust `mj_kernels` extension already accelerates individual shanten and ukeire calls; batching the complete public-information frontier in Rust removes the remaining Python orchestration and FFI overhead.

## What Changes

- Add an optional Rust batch kernel that evaluates every eligible legacy V1 root candidate in one native call.
- Preserve the Python evaluator as the semantic reference, policy adapter, explanation builder, and transactional fallback.
- Add a versioned kernel selector/profile field and an explicit `auto`/`python`/`rust` capability path without changing the legacy default.
- Return compact per-root metrics from Rust: weighted future improvement, weighted future ukeire, best child discards, node count, and completion status.
- Keep shape-cost and feed-risk policy callbacks in Python; use Rust for pure shanten/ukeire enumeration and let Python resolve policy tie-breaks when needed.
- Add Python/Rust parity tests, fixed-fixture tests, budget/fallback tests, and interleaved latency evidence.
- Keep Rust kernel failures, unsupported rules, malformed rows, and budget exhaustion transactional: discard all partial native metrics and use complete Python legacy output.

## Capabilities

### New Capabilities

- `legacy-two-ply-rust-kernel`: Optional native batch execution contract for the public-information legacy two-ply evaluator.

### Modified Capabilities

- `bot-hand-evaluation`: The existing legacy V1 evaluator may use a semantically equivalent Rust batch implementation, with Python reference fallback and unchanged public-information/ranking rules.
- `bot-decision-explanations`: Evaluation metadata records the selected kernel, kernel version, and native fallback reason when present.

## Impact

- Rust: `rust/src/lib.rs` and the maturin-built `mj_kernels` extension.
- Python: `mj/legacy_eval.py`, `mj/shanten.py`, and evaluator/profile metadata.
- Tests/evidence: new parity and performance tests plus an evidence manifest under this change.
- No rule changes, no hidden-state inputs, no default-policy switch, and no new runtime dependency beyond the existing Rust extension build.
