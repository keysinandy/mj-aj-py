# Specification: generation-replay

## ADDED Requirements

### Requirement: Training data SHALL be mixed from declared replay buckets

Aggregate training SHALL combine recent generation, historical representative, hard and special buckets using declared ratios. First-version mix: recent 0.50 / historical 0.25 / hard 0.15 / special 0.10, all configurable.

#### Scenario: new generation arrives
- **WHEN** generation k+1 data is added
- **THEN** the batch mixture still contains historical rows and MUST NOT be dominated by the newest generation

### Requirement: Generation provenance SHALL determine bucket membership

Every row SHALL retain `generation_id` and `state_source`; replay selection SHALL be a deterministic function of the declared replay profile, sample identities and seed.

#### Scenario: reproducibility
- **WHEN** the same replay profile, dataset aggregate and seed are used
- **THEN** the selected batch composition is identical

### Requirement: Historical rows SHALL be bounded

Historical storage SHALL use reservoir sampling or per-generation quotas so the aggregate does not grow without bound.

#### Scenario: long history
- **WHEN** many generations have accumulated
- **THEN** older generations keep a bounded quota and total historical rows stay within the declared cap

### Requirement: Replay SHALL NOT break episode-level splits

Replay selection SHALL only draw from datasets whose source groups belong to the requested split.

#### Scenario: validation leakage attempt
- **WHEN** the training pool requests replay data
- **THEN** validation/final-test source groups are never included
