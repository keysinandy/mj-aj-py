## MODIFIED Requirements

### Requirement: Frontier construction SHALL use speed band plus bounded Pareto challengers

The evaluator SHALL retain the baseline best-current root. Competitive-band roots SHALL be Pareto-pruned, then deterministically capped to the existing frontier limit.

#### Scenario: User 2w versus 4s reaches weighted comparison

- **GIVEN** 2w has shanten2/ukeire57/types17 and breaks a taatsu
- **AND** 4s(log 4t) has shanten2/ukeire48/types15 and does not break pair/taatsu
- **THEN** 4s SHALL NOT be eliminated solely by 57>48
- **AND** 2w SHALL NOT Pareto-dominate 4s if its marginal loss is worse
- **AND** 4s SHALL reach weighted two-ply subject only to deterministic cap

### Requirement: Raw current ukeire SHALL not be the first root key inside a competitive band

Root ordering SHALL be:

1. legality/wildcard gates
2. future shanten improvement
3. future ukeire mean
4. future ukeire types mean
5. marginal structure loss tier
6. future shape quality
7. standing shape quality
8. raw current ukeire
9. feed risk
10. stable tile

#### Scenario: Structure can beat a modest current-ukeire lead

- **GIVEN** two roots are in the same speed band
- **AND** earlier future-speed metrics do not separate them
- **AND** lower-current root has strictly lower marginal loss
- **THEN** the structurally safer root MAY win before raw current ukeire is consulted

#### Scenario: Future speed still beats structure

- **WHEN** root A has a strictly better earlier future-speed metric
- **THEN** A SHALL win regardless of later structure metrics

### Requirement: Stage A shortcut SHALL respect speed-band ordering

Within a competitive band, Stage A SHALL accept a partial winner only when
future-improvement bounds strictly prove it; otherwise it MUST continue Stage B
or fall back transactionally.

#### Scenario: 57 current ukeire does not skip Stage B

- **GIVEN** roots 57 and 48 are in the same shanten-2 band
- **AND** Stage A improvement bounds overlap
- **THEN** evaluator SHALL continue Stage B subject to budget
- **AND** MUST NOT select 57 solely from current ukeire

### Requirement: Transactional fallback remains unchanged

Unsafe/incomplete weighted search SHALL fall back transactionally and MUST NOT synthesize future metrics.

#### Scenario: Critical root is incomplete

- **WHEN** the critical retained root is incomplete before a safe certificate
  is available
- **THEN** the evaluator SHALL return the complete legacy ordering and leave
  missing future metrics null or absent
