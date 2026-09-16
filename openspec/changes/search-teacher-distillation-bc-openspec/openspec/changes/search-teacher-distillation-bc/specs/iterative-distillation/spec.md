# Specification: iterative-distillation

## ADDED Requirements

### Requirement: Training SHALL use student-visited states

After generation 0, each generation SHALL collect trajectories from the currently promoted student policy and teacher-label the states that policy actually visits.

#### Scenario: student makes a novel mistake
- **WHEN** `pi_k` enters a state absent from earlier teacher trajectories
- **THEN** that state is eligible for teacher relabeling in `dataset_k` and subsequent training

### Requirement: Generation provenance SHALL be append-only

Every dataset generation SHALL record `policy_version_source`, generation number, continuation version, teacher fingerprint and source groups.

#### Scenario: later policy data
- **WHEN** `pi2` generates a state
- **THEN** the row MUST NOT be relabeled as generation 0 / pi0 evidence

### Requirement: Promotion SHALL require both local and game-level evidence

A student SHALL NOT be promoted only because training loss or top1 accuracy improves. Promotion SHALL require frozen reference-regret and paired-round-score gates.

#### Scenario: regret improves but population score regresses
- **WHEN** mean reference regret improves but frozen-population paired score fails
- **THEN** promotion fails and the last all-gates-passed policy remains active

### Requirement: Iteration SHALL stop on declared no-improvement criteria

The system SHALL use `PolicyIterationRunner` or equivalent deterministic stopping rules and MUST NOT search random seeds until an improvement appears.

#### Scenario: two generations do not improve
- **WHEN** configured consecutive generations fail regret+score improvement
- **THEN** iteration stops with explicit reason and rollback policy

### Requirement: Opponent splits SHALL remain distinct

Self-play, legacy/shape-v1 and frozen-population evidence SHALL be reported separately.

#### Scenario: self-play improves only
- **WHEN** a student improves in self-play but regresses against frozen population
- **THEN** it MUST NOT be promoted for general online use
