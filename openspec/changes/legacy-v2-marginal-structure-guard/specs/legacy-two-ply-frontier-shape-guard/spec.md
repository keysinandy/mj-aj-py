## MODIFIED Requirements

### Requirement: 形状护栏前沿准入

Under shape-aware + marginal-role + speed-band profile, a unique maximum-current-ukeire root MAY short-circuit only after other roots are safely eliminated by legality, speed dominance, Pareto dominance, or deterministic cap.

A challenger inside the competitive speed band MUST NOT be removed solely by an absolute current-ukeire gap.

The existing shape-aware admission and stable truncation rules SHALL remain
valid when the speed-band frontier is enabled.

#### Scenario: 拆面子候选进入比较

- **WHEN** the unique maximum-current-ukeire candidate breaks a completed
  shape and another candidate is one tile behind with materially lower
  structure loss
- **THEN** both candidates SHALL enter the same weighted comparison and the
  explanation SHALL record the admission details

#### Scenario: 进张差距超出护栏

- **WHEN** a challenger exceeds the configured shape guard slack and is below
  the speed-band threshold
- **THEN** the challenger SHALL not enter the frontier and the choice SHALL
  remain the same as with the guard disabled

#### Scenario: 护栏截断可复现

- **WHEN** more admitted candidates exist than `max_frontier_candidates`
- **THEN** stable truncation SHALL preserve the declared ordering and changing
  input iteration order SHALL not change the selected action

#### Scenario: 899s does not raw-speed singleton

- **GIVEN** 9s has 82 at shanten 3
- **AND** a challenger has 77
- **THEN** 77/82 SHALL be above the shanten-3 threshold
- **AND** raw-speed frontier_singleton MUST NOT fire

#### Scenario: 57 versus 48 ignores old absolute shape slack

- **GIVEN** shanten=2, best=57, challenger=48
- **THEN** 48/57 SHALL qualify the challenger
- **AND** an old one-tile/small fixed slack MUST NOT reject it

#### Scenario: Clearly slower challenger may be pruned

- **WHEN** speed ratio is below threshold
- **THEN** speed dominance MAY prune it
- **AND** diagnostics SHALL name the threshold and dominating root

### Requirement: 护栏审计字段

Diagnostics SHALL expose speed ratio, band threshold, marginal loss, Pareto status, retained challengers, and singleton reason.

#### Scenario: 决策可离线复核

- **WHEN** the guard changes the comparison set
- **THEN** the decision record SHALL include admission, truncation, and final
  weighted metrics sufficient to reproduce the selected action offline
