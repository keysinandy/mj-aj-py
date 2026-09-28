## ADDED Requirements

### Requirement: LegacyV2 SHALL model marginal structure roles without hard-protecting pairs

The evaluator SHALL compute a versioned marginal role for each discard candidate using only hero/public information.

A pair label alone MUST NOT imply hard protection.

#### Scenario: 899s second 9 has non-redundant pair optionality

- **GIVEN** a suited fragment 899s
- **WHEN** one 9s is discarded
- **THEN** the role SHALL record loss of the 99 pair option
- **AND** SHALL record reduced alternative structural routes
- **AND** SHALL NOT mark the removed copy as completed-meld redundant

#### Scenario: 7899s second 9 can be redundant to a completed meld

- **GIVEN** a suited fragment 7899s
- **WHEN** one 9s is discarded
- **THEN** the remaining hand SHALL still expose a completed 789s
- **AND** completed_meld_redundancy SHALL be true
- **AND** lost pair optionality alone MUST NOT force critical_compound_break

### Requirement: Pair optionality SHALL use public unseen counts only

Same-tile unseen mass MAY be exposed as call optionality, but MUST NOT be treated as a probability that an opponent will discard that tile.

#### Scenario: Hidden state differs

- **WHEN** two otherwise identical public states differ only in opponent concealed tiles or real wall order
- **THEN** marginal role and guard admission SHALL be identical

### Requirement: Singleton connectivity SHALL be live-tile aware

Central isolated tiles SHALL NOT be treated as equivalent to terminal isolated tiles when live neighbors differ.

#### Scenario: 5w has more live connectivity than 1w

- **GIVEN** equal public availability for relevant neighbors
- **WHEN** isolated 5w and 1w are evaluated
- **THEN** 5w SHALL have strictly greater singleton live connectivity

#### Scenario: Neighbors are exhausted

- **WHEN** useful public neighbors have zero unseen count
- **THEN** they SHALL contribute zero live connectivity
