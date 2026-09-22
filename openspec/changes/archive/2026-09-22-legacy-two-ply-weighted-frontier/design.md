# Design

## Context

See `proposal.md` for the motivation and externally visible scope. The
existing `legacy_two_ply_frontier` Rust function already batches child
evaluation, but it is an all-or-nothing result: it uses a decision-local
shanten cache that skips ordinary no-wildcard states, counts only outer child
nodes, and the Python adapter discards every row when the native call is
incomplete. The established Python layer remains responsible for legal root
construction, public visible counts, freeze/grab rules, 财神 handling,
reaction/HU/KONG routing, explanation metadata, and legacy fallback.

## Goals / Non-Goals

**Goals:**

- Make the online evaluator a deterministic weighted frontier with 40ms soft
  and 50ms hard defaults, exposed as the canonical `legacyV2` profile while
  retaining explicit `legacy` rollback.
- Reduce native work with all-state shanten caching, lazy child ukeire, a
  bounded root frontier, and high-weight draw ordering.
- Return per-root committed rows and explicit coverage so a partial result can
  be accepted only under a documented safety gate.
- Preserve a complete exact mode and Python parity path for replay, regression,
  and teacher data.
- Expose enough counters to distinguish algorithmic work, cache behavior, and
  probability coverage.

**Non-Goals:**

- No full-game EV, wall-order simulation, opponent policy, or hidden-hand
  access.
- No full-game EV, wall-order simulation, opponent policy, or hidden-hand
  access. The rollout decision may enable `legacyV2` by default while keeping
  latency/coverage evidence and transactional legacy fallback observable.
- No Python callback from Rust and no migration of Game/rule authorization into
  the extension.
- No floating-point probability in the hot loop; integer remaining weights are
  the comparison currency and normalization is report-only.

## Decisions

### 1. Add a new weighted native entry point instead of changing V1 rows

Keep `legacy_two_ply_frontier` as the exact compatibility oracle and add
`weighted_two_ply_frontier` with a versioned row contract. The new row carries
`complete`, `partial_accepted`, `covered_weight`, `total_weight`, per-root
metrics, and counters. This avoids silently changing old replay artifacts and
lets parity tests compare the new complete result with the old exact path.

An alternative was to add flags to the old function. That would make old
wheels and serialized profile fingerprints ambiguous, so it is rejected.

### 2. Keep legality and policy boundaries in Python

Python builds legal root candidates and child masks, applies the wildcard,
freeze, rule-phase, shape, and feed gates, and maps native ties into the
existing policy ordering. Rust receives only count arrays, visible counts,
legal masks, profile budgets, and deterministic ordering knobs. Moving all
root generation to Rust was considered, but would duplicate Game legality and
make public-information review harder; root feature reuse will therefore be a
separate optimization with parity coverage.

### 3. Use integer weighted search with committed per-root rows

Rust calculates `remaining = max(0, 4-visible[tile])`, sorts draw kinds by
`(remaining desc, tile asc)`, and stops starting new low-weight branches after
the soft deadline. It keeps the current branch until the hard deadline, then
returns every completed root row plus the active incomplete row. A root row is
committed only after its child metrics are internally consistent; Python never
combines an uncommitted root with complete roots.

### 4. Lazy child ukeire and all-state shanten cache

For a draw, all legal child shanten values are computed first. Only children at
the minimum shanten enter ukeire evaluation; ties then carry weighted ukeire,
tile-kind count, and optional policy tie-break data. The shanten cache key is a
fixed count array plus locked count and is used for both wild and no-wild hands.
The first implementation keeps fixed arrays to minimize semantic risk; a
compact two-word hand key can be added after profiling demonstrates hash
allocation is material.

### 5. Partial acceptance is explicit and conservative

The online profile has `allow_partial`, `min_partial_coverage`, and
`max_frontier_candidates`. A partial result is eligible only if all retained
roots have committed rows and each reaches the threshold (default 0.90).
Otherwise the adapter returns the complete legacy action. Exact mode always
remains transactional. This is safer than accepting the best-so-far root when
an unresolved root could reverse the ranking.

### 6. Separate online and offline profiles

`legacyV2` uses the `weighted_two_ply_frontier` kernel, 50ms hard budget and
partial gate; `legacy-two-ply-v1` uses the old all-or-nothing semantics for
offline analysis. Profile fingerprints include mode, kernel version, frontier
limit, lazy flag, soft/hard budgets, and partial threshold. The older
`weighted-two-ply-frontier-v1`/`weighted_two_ply` spellings normalize to
`legacyV2`. `MJ_KERNELS=python` continues to select the Python reference for
supported exact/weighted parity tests.

## Risks / Trade-offs

- **[Risk]** Partial acceptance can select a result before low-weight branches
  are known. → Require every retained root to meet coverage and expose the
  exact denominator; default to legacy when any root misses the gate.
- **[Risk]** More metrics and draw rows increase PyO3 serialization. → Return
  compact tuples/integers, keep full child rows optional, and benchmark raw
  native versus end-to-end separately.
- **[Risk]** Caching all shanten states increases memory. → Keep the cache
  decision-local with a profile capacity and record hit/miss counts.
- **[Risk]** A 3-root cap could remove a semantically valid tie. → Apply the cap
  only after minimum shanten/current ukeire frontiering, use a deterministic
  cheap heuristic, and retain exact mode with no cap for offline analysis.
- **[Risk]** Existing callers expect incomplete V1 to fall back. → Keep V1
  unchanged, add a new profile name/version, and test both paths explicitly.

## Migration Plan

1. Add the new profile and Rust capability; make canonical `legacyV2` the
   product default while keeping explicit `legacy` and existing
   `legacy-two-ply-v1` behavior unchanged.
2. Run focused parity, public-information, freeze, coverage, and fixed replay
   regression tests, then collect 50ms benchmark evidence.
3. If results fail or native capability is unavailable, the adapter falls back
   to complete legacy without changing action legality.
4. Keep benchmark and fallback telemetry as a release guard. A later change
   may tune the budget/frontier without changing the public `legacyV2` name.

## Open Questions

None. The profile defaults, acceptance threshold, legality boundary, and
exact/online split are fixed by this design and can be changed only by a new
spec change.
