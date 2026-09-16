# Specification: hard-state-regression

## ADDED Requirements

### Requirement: Hard states SHALL enter a permanent regression set

A state SHALL be added to the hard regression set when any declared trigger holds: extremely high reference regret, catastrophic action, special-state failure, an explicit policy mistake found in paired games, a real failure found online/simulated, or recurrence of a previously fixed regression. Entries SHALL persist unless the state, label or rules are proven buggy.

#### Scenario: paired-game mistake
- **WHEN** a paired game exposes a clear policy mistake
- **THEN** the state is persisted with its failure tag and source game

### Requirement: Hard set entries SHALL be deduplicated by failure mode

Deduplication SHALL use `state_id` plus feature similarity, game phase, action pattern and failure tag. The set optimizes failure-mode coverage, not row count.

#### Scenario: duplicate failure mode
- **WHEN** many near-identical states share one failure mode
- **THEN** only representative entries are retained under that mode

### Requirement: Every checkpoint SHALL be evaluated on the hard set

Hard-set evaluation SHALL report mean/p95/max regret, catastrophic count, fixed/regressed counts and per-special-tag metrics, and SHALL store each historical policy's action/probability/regret on the same states.

#### Scenario: regression detected
- **WHEN** a new checkpoint increases catastrophic count relative to the previous promoted policy
- **THEN** the run is flagged for early stop / rollback and the regression is recorded

### Requirement: Hard-set membership SHALL NOT leak into training splits

The hard regression set is evaluation data. Rows used for hard-state oversampling in training MUST be drawn only from training-split games.

#### Scenario: hard state from validation
- **WHEN** a hard state originates in a validation game
- **THEN** it is used for evaluation only and never for gradient updates
