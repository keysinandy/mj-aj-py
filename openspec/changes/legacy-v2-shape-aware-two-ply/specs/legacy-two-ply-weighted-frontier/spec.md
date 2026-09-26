## MODIFIED Requirements

### Requirement: Child evaluation is lazy and ordered by efficiency

For each root and simulated draw, the evaluator SHALL compute child shanten for every legal child discard before computing child ukeire. It SHALL compute child ukeire only for children tied on minimum child shanten.

For children still tied after weighted ukeire and ukeire tile-kind count, a shape-aware profile SHALL compare post-discard child standing shape quality before the stable discard-tile tie-break.

The child order SHALL therefore be:

1. minimum child shanten;
2. maximum weighted child ukeire;
3. maximum child ukeire tile-kind count;
4. maximum child standing shape quality when the shape profile is enabled;
5. deterministic stable discard tie-break.

Draw branches SHALL continue to be visited in descending remaining weight, then stable tile order.

#### Scenario: Equal shanten and ukeire use future shape

- **WHEN** two legal child discards tie on shanten, weighted ukeire, and ukeire tile-kind count
- **AND** one child retains a ryanmen while the other retains a penchan
- **THEN** the shape-aware profile SHALL select the ryanmen child

#### Scenario: Worse-shanten child remains unexpanded

- **WHEN** two legal child discards have shanten 1 and 2
- **THEN** only the shanten-1 child is eligible to win regardless of shape quality

#### Scenario: Worse-shanten child is not expanded
- **WHEN** two legal child discards have shanten 1 and 2
- **THEN** only the shanten-1 child is eligible for child ukeire evaluation

#### Scenario: Equal-shanten children retain policy tie-breaks
- **WHEN** two child discards tie on shanten and weighted ukeire
- **THEN** the evaluator may use ukeire tile-kind count, shape, feed risk, and stable tile order without changing the public-information metrics

#### Scenario: Lazy expansion preserves exact result
- **WHEN** all branches finish without a budget boundary
- **THEN** lazy evaluation returns the same selected child and aggregate weighted metrics as evaluating ukeire for every child

## ADDED Requirements

### Requirement: Weighted root metrics SHALL include future standing shape

For every completed root, the weighted evaluator SHALL aggregate child standing shape over the same public unseen-tile mass used by future ukeire.

The evaluation SHALL expose future shape sum/mean and denominator, and MUST NOT substitute zero for missing/incomplete future shape.

#### Scenario: Future structure separates equal ukeire roots

- **WHEN** two roots have equal current ukeire, future improvement mass, future ukeire mean, and future ukeire type mean
- **AND** root A more often reaches better child standing shapes
- **THEN** the shape-aware root comparator SHALL prefer root A before applying legacy discard-shape/feed-risk tie-breaks

### Requirement: Shape-aware root ordering SHALL preserve speed priority

When enabled, root ordering SHALL place future/standing shape only after the existing speed metrics.

Conceptually:

- wildcard protection;
- current ukeire;
- future improvement weight;
- future ukeire mean;
- future ukeire types mean;
- future shape quality mean;
- current standing shape quality;
- legacy discard shape cost;
- feed risk;
- stable tile.

#### Scenario: Shape does not override future ukeire

- **WHEN** root A has strictly better future ukeire mean than root B
- **AND** root B has strictly better future shape
- **THEN** root A SHALL win under the shape-aware profile unless an earlier existing policy gate says otherwise

### Requirement: Frontier cap SHALL use post-discard standing shape semantics

When shape quality is enabled, frontier truncation among otherwise eligible roots SHALL use standing hand shape quality rather than treating discard-local shape loss as the standing shape proxy.

Feature flag off SHALL preserve the existing baseline candidate set exactly.

#### Scenario: Better standing shape is not removed before two-ply

- **GIVEN** more eligible roots exist than max_frontier_candidates
- **AND** two roots have equal shanten/current ukeire but one retains 24s and another retains 12s
- **WHEN** the shape-aware frontier cap must choose between them
- **THEN** the 24s root SHALL rank ahead on standing shape, all earlier keys being equal

### Requirement: Stage A shortcut SHALL remain sound with shape-aware Stage B

A Stage-A-only winner MAY still be accepted only when the existing future-shanten-improvement ordering can strictly prove the winner.

When Stage A cannot strictly separate roots, Stage B MUST run before future shape participates.

#### Scenario: Improvement tie reaches Stage B

- **WHEN** roots tie on future shanten improvement mass
- **AND** shape-aware evaluation is enabled
- **THEN** the evaluator SHALL NOT declare a shape result from missing values
- **AND** SHALL enter Stage B subject to the existing budget/coverage rules
