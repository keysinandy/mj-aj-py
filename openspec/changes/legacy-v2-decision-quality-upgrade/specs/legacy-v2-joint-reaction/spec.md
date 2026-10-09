## ADDED Requirements

### Requirement: Comparable legal reaction roots
The system SHALL evaluate PASS, CHI, PONG, KONG and their legal resulting discards on a common net settlement horizon where the configured rules allow them. It SHALL respect frozen hands, response priority, action legality and the existing KONG safety gates.

#### Scenario: V1 gate rejects positive challenger
- **WHEN** the V1 significant-progress gate rejects a legal claim but bounded pre-screening shows a potential score improvement
- **THEN** an explicitly enabled rescue path MAY admit the candidate to joint evaluation without modifying the V1 baseline

#### Scenario: Overridden claim selects a child discard
- **WHEN** joint evaluation overrides the baseline with CHI/PONG and selects a legal child discard
- **THEN** the immediately following matching public discard state SHALL execute that child once, requiring matching round, public state, profile and legal action; stale plans SHALL be discarded

### Requirement: Atomic incompleteness fallback
The system SHALL not compare some candidates' complete futures against incompatible partial futures or silently use an incomplete KONG continuation.

#### Scenario: Joint search times out
- **WHEN** the common value comparison cannot complete or prove a safe winner inside budget
- **THEN** the existing reactionV2 result SHALL be returned transactionally with a fallback reason
- **AND** a discarded claim override SHALL NOT leave an armed child-discard plan
