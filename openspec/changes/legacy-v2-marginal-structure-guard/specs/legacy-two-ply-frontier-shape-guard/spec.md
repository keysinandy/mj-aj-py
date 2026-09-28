## MODIFIED Requirements

### Requirement: 形状护栏前沿准入

shape-aware + marginal-role profile 下，unique maximum-current-ukeire root MAY short-circuit only when no meaningful role-preserving challenger exists inside the configured shanten-aware ukeire slack.

Initial slack:

- shanten 0: 0
- shanten 1: 2
- shanten 2: 4
- shanten >=3: 6

The frontier MUST remain bounded by max_frontier_candidates <= 3.

#### Scenario: 899s winner cannot singleton-short-circuit

- **GIVEN** the user 899s golden
- **AND** discard 9s has current ukeire 82
- **AND** a non-critical challenger has current ukeire at least 76
- **WHEN** the marginal-role guard runs at shanten 3
- **THEN** discard 9s SHALL NOT return through frontier_singleton
- **AND** at least one role-preserving challenger SHALL enter the weighted frontier

#### Scenario: Gap exceeds role slack

- **WHEN** every role-preserving challenger is worse than the speed winner by more than configured slack
- **THEN** the unique speed winner MAY retain one-ply singleton short-circuit

#### Scenario: 7899 pair is not hard-protected

- **GIVEN** a candidate discards one 9 from 7899s
- **AND** remaining 789s is complete
- **THEN** lost 99 alone SHALL NOT force preservation of 9s

#### Scenario: Guard truncation is deterministic

- **WHEN** more eligible challengers exist than free frontier slots
- **THEN** stable ordering SHALL be used
- **AND** input iteration order SHALL NOT change retained roots

#### Scenario: 拆面子候选进入比较

- **WHEN** the unique maximum-current-ukeire candidate breaks a completed
  meld and another candidate is one tile worse with materially lower
  structural loss
- **THEN** both candidates SHALL enter the same weighted comparison and the
  decision SHALL retain admission diagnostics

#### Scenario: 进张差距超出护栏

- **WHEN** a challenger exceeds the configured role-aware slack
- **THEN** it SHALL remain outside the marginal frontier and the action SHALL
  match the guard-disabled singleton result

#### Scenario: 护栏截断可复现

- **WHEN** more role-preserving challengers qualify than the frontier has free
  slots
- **THEN** stable ordering SHALL retain the same roots regardless of input
  iteration order

### Requirement: 护栏审计字段

Diagnostics SHALL distinguish singleton proven safe from singleton blocked by marginal structure.

#### Scenario: Singleton is blocked

- **THEN** diagnostics SHALL include slack, ukeire gap, marginal loss tier, challenger list, and admitted_by=marginal_structure_guard

#### Scenario: 决策可离线复核

- **WHEN** a decision changes its comparison set because of the marginal
  guard
- **THEN** the explanation SHALL include admission/truncation details and the
  completed weighted metrics needed to reproduce the selection
