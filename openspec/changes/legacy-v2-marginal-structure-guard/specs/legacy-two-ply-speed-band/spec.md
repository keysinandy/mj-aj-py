## ADDED Requirements

### Requirement: Same-shanten roots SHALL use a shanten-aware competitive speed band

For roots with the same minimum shanten:

~~~text
current_speed_ratio = current_ukeire / max_current_ukeire
~~~

Initial minimum ratios:

- shanten 0: 1.00
- shanten 1: 0.90
- shanten 2: 0.82
- shanten >=3: 0.78

A root meeting the threshold SHALL NOT be eliminated solely because another root has higher raw current ukeire.

#### Scenario: 57 versus 48 at shanten 2 remains competitive

- **GIVEN** best current ukeire=57
- **AND** challenger current ukeire=48
- **WHEN** speed band is evaluated at shanten 2
- **THEN** ratio SHALL be approximately 0.842
- **AND** challenger SHALL remain in the competitive band
- **AND** raw 57>48 alone MUST NOT eliminate it

#### Scenario: 57 versus 35 at shanten 2 is speed-dominated

- **GIVEN** best current ukeire=57
- **AND** challenger current ukeire=35
- **WHEN** speed band is evaluated
- **THEN** ratio SHALL be below 0.82
- **AND** speed dominance MAY eliminate it

### Requirement: Competitive roots SHALL be Pareto-pruned

Dimensions SHALL include at least:

- maximize current ukeire
- maximize current ukeire tile kinds
- minimize marginal structure loss tier
- maximize standing shape quality

A root is dominated only when another is no worse in every declared dimension and strictly better in at least one.

#### Scenario: Faster but structurally worse root does not dominate

- **GIVEN** A has higher current ukeire
- **AND** A has strictly worse marginal structure loss than B
- **THEN** A MUST NOT dominate B solely through current ukeire

#### Scenario: Strictly worse root is pruned

- **GIVEN** A is no worse than B in every declared dimension
- **AND** strictly better in at least one
- **THEN** B SHALL be Pareto-dominated by A

### Requirement: Pareto frontier SHALL remain bounded and deterministic

The baseline best-current-ukeire root SHALL always remain. Non-dominated roots SHALL be deterministically capped to max_frontier_candidates<=3.

#### Scenario: Input order changes

- **WHEN** the same candidates arrive in different iteration order
- **THEN** speed band, dominance, capped frontier, and order SHALL remain identical
