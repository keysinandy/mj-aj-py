# Design

## Context

`mj/legacy_eval.py` is the Python reference for `legacy-two-ply-v1`. It first filters root candidates by legal action, minimum shanten,财神 protection, and current ukeire. For each remaining root it enumerates up to 34 public draw kinds, creates `visible_after_draw`, enumerates legal child discards, and calls the Rust-dispatched Python API for child shanten and ukeire. The individual Rust kernels are fast, but each Python call still allocates lists/tuples and crosses the FFI boundary. The current 8 ms profile therefore falls back on the random-hand benchmark.

The repository already exposes `best_future_discard`, `discard_frontier`, and `discard_frontier_batch` from `mj_kernels`. Those APIs are useful lower-level stepping stones, but they do not return the complete weighted root frontier or carry the V1 transactional budget contract.

## Goals

- Preserve byte-for-byte-equivalent V1 metrics and action selection for supported inputs.
- Make one native call per root decision, not one call per child state.
- Keep all public-information and legality validation in Python.
- Keep Python available through `MJ_KERNELS=python` and as an automatic safe fallback.
- Make node-budget behavior deterministic; wall-clock measurement remains an outer diagnostic and fallback guard.

## Non-goals

- Do not port `Game`, rule routing, HU/KONG/reaction logic, or JSON/logging to Rust.
- Do not invoke Python callbacks from Rust.
- Do not read hidden hands, wall order, or opponent policy.
- Do not change the legacy default or enable Rust automatically before parity and performance gates pass.

## Native API

Add a PyO3 function with a compact row-oriented contract:

```text
legacy_two_ply_frontier(
    roots: Vec<Vec<i32>>,
    root_shantens: Vec<i32>,
    locked: i32,
    visible: Vec<i32>,
    legal_masks: Vec<Vec<i64>>,
    frozen: bool,
    node_budget: i64,
    time_budget_ms: f64,
    include_best_discards: bool,
) -> Vec<LegacyTwoPlyRow>
```

`legal_masks[root][draw]` is a 34-bit child-discard mask prepared by Python from the current rule legality. Each row contains the root index, `complete`, `future_improve_weight`, `future_ukeire`, per-draw best-child efficiency ties, weighted best-child discard counts after Python tie-breaking, node count, and a machine-readable fallback reason. The public binding may expose rows as tuples/dicts to avoid introducing a Python class in the extension. The Rust implementation uses fixed `[i32; 34]` arrays internally and reuses one shanten cache per decision.

For each root and each `draw` with `max(0, 4-visible[draw]) > 0`, Rust:

1. adds the draw to the root hand and to a copied visible vector;
2. validates and enumerates the supplied legal child discard mask, intersecting it with only the drawn tile when `frozen` is true;
3. computes child shanten and child ukeire using the same `visible_after_draw`;
4. selects minimum child shanten, then maximum child ukeire, then stable tile order;
5. accumulates weighted improvement, weighted ukeire, and the selected discard mass.

The native kernel intentionally does not compute Python `shape_cost` or `feed_risk`. The adapter passes only legal child tile sets and returns the native efficiency metrics. Python resolves the existing policy tie-break for exact child metric ties and applies the unchanged root ordering. If a future policy callback would change the selected child, the Python reference path remains available.

## Python adapter and fallback

Extend `LegacyTwoPlyProfile` with a `kernel` value (`python`, `auto`, or `rust`) and include it in the fingerprint. `auto` uses the native kernel only when the extension advertises the matching version; `python` is forced by `MJ_KERNELS=python`; `rust` fails closed to legacy if unavailable. The adapter validates row count, root index/order, non-negative metrics, weighted totals, and completion before mapping rows into `FutureEvaluation`.

The native call receives a deterministic node budget plus a monotonic wall-clock guard so a small profile cannot block on a full frontier. Python measures elapsed wall time around the call as a second guard and treats an over-budget or malformed result as an incomplete frontier. It must never merge complete native rows with incomplete Python rows. On any error, unsupported frozen/legality state, or partial result, `evaluate_legacy_two_ply` returns the complete legacy selection and records `kernel_fallback_reason`.

The Python implementation remains the oracle. A test-only switch can run both implementations on the same input and compare all scalar metrics and selected discard mass. Production `auto` does not run both.

## Compatibility and packaging

The extension version is exposed as `rust-legacy-two-ply-v1`; the value is recorded in evaluation JSON alongside the profile fingerprint. Existing wheels that lack the function are treated as an unavailable capability, not as a semantic mismatch. `rust/pyproject.toml` remains the build entry point; no new dependency is introduced.

## Performance gates

The first evidence target is 100 random public 13-tile states under the existing 8 ms profile: at least 95 complete decisions, p95 below 8 ms outside normal measurement jitter, and zero Python/Rust action or metric differences. These are release targets, not assumptions. Until they pass together with existing information-safety and paired-benefit gates, `legacy` remains the default and the native path is opt-in.
