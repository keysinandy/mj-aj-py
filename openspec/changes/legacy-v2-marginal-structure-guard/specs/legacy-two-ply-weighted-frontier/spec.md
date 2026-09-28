## MODIFIED Requirements

### Requirement: Frontier construction SHALL allow bounded marginal-role challengers

The evaluator SHALL always retain the baseline speed winner.

When marginal-role guard blocks singleton short-circuit, only bounded admitted challengers SHALL be added. Total roots MUST remain within the existing frontier cap.

Marginal role decides whether a root is worth comparing; the existing weighted two-ply comparator remains authoritative after admission.

#### Scenario: User 899s golden enters real two-ply

- **GIVEN** open PON 1m
- **AND** concealed hand is 3m 7m 2p 3p 4p 4p 7p 4s 8s 9s 9s after drawing 4p
- **AND** discard 9s has shanten 3 and current ukeire 82
- **AND** discard 3m has shanten 3 and current ukeire 77
- **WHEN** marginal guard is enabled and weighted search completes safely
- **THEN** weighted_two_ply_entered SHALL be true
- **AND** search_used SHALL be true
- **AND** future_nodes SHALL be greater than zero
- **AND** final selected discard SHALL NOT be 9s

#### Scenario: Transactional fallback may restore baseline

- **WHEN** kernel unavailability, deadline, work budget, or unsafe partial prevents a safe result
- **THEN** evaluator SHALL fall back transactionally to complete baseline legacy
- **AND** SHALL record fallback reason
- **AND** MUST NOT claim completed future metrics

### Requirement: Marginal role SHALL not become a dominant root score

The root comparator MUST NOT insert a generic pair-protection score ahead of current/future speed metrics.

#### Scenario: 7899s versus 5w remains context-dependent

- **GIVEN** one root discards the redundant 9 from 7899s
- **AND** another root discards a high-connectivity isolated 5w
- **WHEN** both enter the weighted frontier
- **THEN** winner SHALL be determined by complete existing weighted metrics and standing/future shape
- **AND** neither root SHALL be forced solely because one action breaks a pair
