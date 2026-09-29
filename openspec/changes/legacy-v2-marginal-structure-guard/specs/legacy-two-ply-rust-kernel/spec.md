## MODIFIED Requirements

### Requirement: Native winner certification SHALL match speed-band root semantics

If native code performs root winner certification or safe-partial bounds, it MUST use the same retained speed-band/Pareto frontier and comparator semantics as Python.

#### Scenario: 57 versus 48 remains unresolved after admission

- **GIVEN** both roots are retained in shanten-2 competitive band
- **AND** Stage A future-improvement bounds overlap
- **THEN** native SHALL NOT certify the 57 root solely from current ukeire
- **AND** SHALL continue Stage B or return unsafe/incomplete according to budget

### Requirement: Native and Python complete evaluations SHALL agree

They SHALL agree on retained roots, future metrics, marginal loss tier used by comparator, and final selected root.

#### Scenario: Native and Python reference agree

- **WHEN** the same retained roots, visible counts, legal sets, profile, and
  node budget are evaluated by Python and Rust
- **THEN** completion state, future improvement, future ukeire, child discard
  mass, and final selected root SHALL agree

### Requirement: Existing budget/fallback contract SHALL remain unchanged

Search horizon, frontier cap, and hard deadline MUST NOT increase. Incompatible version/comparator semantics SHALL trigger transactional fallback.

#### Scenario: Partial native frontier falls back

- **WHEN** one eligible root completes natively but another exceeds the node
  budget
- **THEN** no partial native metric SHALL affect ordering, the complete legacy
  action SHALL be returned, and the explanation SHALL record the fallback
