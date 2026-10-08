## ADDED Requirements

### Requirement: Decision-wide bounded search
A shared deadline SHALL cap search work across all candidate branches of a decision. Extra work SHALL target ambiguous candidate boundaries only. Incomplete or unproven rankings SHALL revert to baseline without mixing partial scoring evidence.

#### Scenario: Multiple expensive decision subbranches
- **WHEN** the combined work of HU, KONG and continuation reaches the configured decision deadline
- **THEN** the system SHALL return a legal fallback with diagnostic timing rather than exceeding its agreed deadline through independent nested budgets

### Requirement: Optional persistent hand route
The system MAY retain a soft ordinary/chiitoi/luxury route estimate across hero turns, but SHALL reset on round boundaries, never prevent a legal override, and switch routes only with a configured evidence margin.

#### Scenario: Small route-value oscillation
- **WHEN** the competing route's expected value changes by less than the hysteresis margin
- **THEN** the previous soft route MAY remain, without overriding legal priority or a clearly higher-value action

### Requirement: Reproducible gated rollout
New capability flags SHALL default to disabled, be included in configuration fingerprints and decision audits, and require independent paired-score, correctness and latency evidence before enabling by default.

#### Scenario: Experimental feature disabled
- **WHEN** all new flags are disabled
- **THEN** selected actions and fallback behavior SHALL match the frozen legacyV2 baseline
