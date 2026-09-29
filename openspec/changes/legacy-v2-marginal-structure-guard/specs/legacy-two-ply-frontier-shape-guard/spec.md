## MODIFIED Requirements

### Requirement: 形状护栏前沿准入

Under shape-aware + marginal-role + speed-band profile, a unique maximum-current-ukeire root MAY short-circuit only after other roots are safely eliminated by legality, speed dominance, Pareto dominance, or deterministic cap.

A challenger inside the competitive speed band MUST NOT be removed solely by an absolute current-ukeire gap.

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
