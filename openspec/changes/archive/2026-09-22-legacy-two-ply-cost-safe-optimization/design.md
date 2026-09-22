# Design

## Context

See `proposal.md`. The existing weighted adapter sends every retained root to
the native kernel, accepts partial rows using only per-root coverage, and
normalizes partial future ukeire by the full theoretical draw mass. The native
kernel exposes shanten/cache counters but still stops on `child_nodes`, even
though child ukeire can perform many additional uncached shanten evaluations.

## Goals / Non-Goals

**Goals:**

- Preserve the exact root frontier ordering while avoiding two-ply work when
  the one-ply frontier already determines the action.
- Make partial acceptance a sufficient decision certificate, not a percentage
  heuristic; retain transactional legacy fallback when the certificate is not
  available.
- Make the weighted native budget correlate with uncached shanten DFS work and
  retain all existing counters for diagnosis.
- Keep the public-information boundary, Rust row shape compatibility, and
  exact/offline behavior stable.

**Non-Goals:**

- No root batch FFI, compact `u128` cache key, cache replacement policy,
  bitmask candidate enumeration, future-shanten prefilter, or root racing in
  this change; those are follow-up performance changes.
- No change to current shanten/ukeire definitions or the LegacyV2 default.

## Decisions

### 1. Singleton short-circuit in Python

After `_root_features()` and the configured frontier cap, a single retained
root is already the unique minimum-shanten/current-ukeire choice. Return it
through an explicit one-ply explanation with `short_circuit=frontier_singleton`
and no future values rather than invoking Rust. This avoids changing any
tie-break ordering. The alternative—running two-ply and discarding its result—
would preserve semantics but waste the entire search budget.

### 2. Improvement interval is the partial certificate

For every committed root, define `I=observed improve weight` and
`R=total_weight-covered_weight`. The root's improvement interval is `[I,I+R]`.
Partial ranking is allowed only if one root's lower bound is strictly greater
than every other root's upper bound. This is sufficient regardless of future
ukeire/shape/feed values because improvement is ordered before those
tie-breakers. Coverage remains diagnostic and may still be used as a soft
stop signal, but it cannot authorize an overlapping interval.

The adapter reports partial future ukeire mean as
`weighted_child_ukeire / covered_weight`; complete rows use total weight.
The row keeps `covered_weight` and `total_weight`, so consumers can identify
the denominator and the unobserved mass.

### 3. Work budget charges uncached shanten

Keep the public native parameter named `node_budget` for compatibility, but
interpret it in the weighted kernel as the maximum uncached shanten work unit
budget. A cache hit remains observable and does not consume a work unit. The
kernel checks this limit before child expansion and before each expensive
ukeire child expansion, returning committed rows with
`work_budget_exceeded` when the limit is reached. Root validation is allowed
to finish so the adapter can return a legal transactional fallback.

### 4. Preserve row compatibility

Do not alter the PyO3 tuple arity in this phase. Improvement bounds are derived
from existing `covered`, `total`, and `improve` metrics in Python and are added
to explanation JSON only. The Rust counters continue to expose both
`child_nodes` and `shanten_cache_misses`; the latter is the budget metric.

## Risks / Trade-offs

- **[Risk]** A singleton short-circuit removes future diagnostic details. →
  Mark the reason explicitly and keep all one-ply candidate values.
- **[Risk]** A strict interval certificate may cause more legacy fallbacks. →
  Prefer a correct legacy action over an unresolved partial rank; report the
  interval overlap so later racing can reduce it.
- **[Risk]** Work-based limiting can stop earlier than historical node limits.
  → Keep the existing profile field and counters, and benchmark completion,
  partial acceptance, and fallback separately.
- **[Risk]** Partial mean semantics change for accepted rows. → Expose covered
  and total denominators and add parity tests for both complete and partial
  rows.

## Migration Plan

1. Add explanation fields and Python singleton/certificate logic behind the
   existing `legacyV2` weighted profile.
2. Change only the weighted Rust stop check to uncached shanten work and
   rebuild the native wheel used by the benchmark/test environment.
3. Run focused parity/regression tests, then the 100-state benchmark; compare
   fallback and p99 with the prior recorded baseline.
4. If the new work budget increases unsafe fallback unexpectedly, restore the
   previous profile budget via configuration while retaining the certificate
   and singleton changes.

## Open Questions

None for this phase.
