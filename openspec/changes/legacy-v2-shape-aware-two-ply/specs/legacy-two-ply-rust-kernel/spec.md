## MODIFIED Requirements

### Requirement: Native legacy two-ply evaluation is optional and semantically equivalent

When the shape-aware profile is enabled, Rust and Python reference evaluators MUST additionally agree on:

- child standing shape quality;
- best child discard after shape tie-break;
- future shape weighted sum/mean;
- final selected root.

The native shape helper MUST use the same versioned decomposition semantics as the Python reference.

#### Scenario: Native and Python shape-aware parity

- **WHEN** the same roots, visible counts, locked count, legal masks, profile and budget are evaluated by Python and Rust
- **THEN** selected action, future improvement, future ukeire, future types, future shape and best-child discard SHALL match exactly

#### Scenario: Native and Python reference agree
- **WHEN** the same roots, visible counts, locked count, legal sets, profile, and node budget are evaluated by Python and Rust
- **THEN** each root's completion state, future improvement weight, future ukeire, best-child discard mass, and final selected action are equal

#### Scenario: Hidden state cannot reach the kernel
- **WHEN** two games differ only in opponent concealed hands or real wall order
- **THEN** the native request payload and returned metrics are identical

## ADDED Requirements

### Requirement: Native response contract and version SHALL expose shape metrics safely

Enabling native future shape requires a kernel contract/version bump.

The adapter MUST validate the new metric count and draw-row shape exactly. An older kernel MUST be treated as version mismatch and SHALL fall back transactionally.

#### Scenario: Old wheel has no child shape column

- **WHEN** Python expects the shape-aware kernel contract
- **AND** the installed wheel returns the previous row/metric layout
- **THEN** the adapter MUST NOT infer or synthesize child shape
- **AND** SHALL return the baseline fallback action with an explicit kernel version mismatch reason

### Requirement: Native Stage B SHALL compare shape only after speed metrics tie

For each Stage B unit, Rust SHALL compare candidate child discards by:

1. minimum child shanten;
2. maximum child ukeire;
3. maximum child ukeire tile types;
4. maximum child standing shape quality;
5. stable discard tile.

#### Scenario: 45s child beats 12s child only on shape tie-break

- **GIVEN** two child discards produce equal shanten/ukeire/types
- **AND** one retains 45s while the other retains 12s
- **WHEN** native Stage B selects best child
- **THEN** it SHALL select the 45s child under the shape-aware profile

### Requirement: Shape-aware native work SHALL remain bounded by the existing online budget

The shape-aware native implementation MUST NOT increase the online hard deadline or frontier root cap.

Shape computation SHOULD be performed only for children that have survived the existing shanten/ukeire/type filters or through an equivalently bounded cached helper.

#### Scenario: Performance gate fails

- **WHEN** correctness/parity passes but ordinary discard p95/p99, fallback rate or 4-bot elapsed/game exceeds the change acceptance gate
- **THEN** shape-aware mode MUST remain non-default
- **AND** the 50ms online hard budget MUST NOT be raised merely to make the change pass
