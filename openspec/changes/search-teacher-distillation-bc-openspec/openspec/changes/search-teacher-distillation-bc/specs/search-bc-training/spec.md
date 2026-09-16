# Specification: search-bc-training

## ADDED Requirements

### Requirement: Policy target SHALL default to search visit distribution

The trainer SHALL normalize legal search visit counts into a soft target and optimize masked cross-entropy. Illegal actions MUST be masked before policy normalization.

#### Scenario: teacher is uncertain between two actions
- **WHEN** visits are split between two legal actions
- **THEN** the student receives both target probabilities instead of a one-hot best action

### Requirement: Q-soft supervision SHALL be optional and versioned

The trainer MAY derive a soft target from q_by_action using a declared temperature and combine it with visit supervision. Mixing weights and temperature MUST enter the training profile fingerprint.

#### Scenario: Q-soft disabled
- **WHEN** `lambda_q=0`
- **THEN** the training result is semantically visit-only regardless of q_by_action being present

### Requirement: Policy-only SHALL be the initial safe training mode

Until Value v2 target/output semantics pass their own contract, default strongest-BC training SHALL use `value_weight=0`.

#### Scenario: raw score exceeds tanh range
- **WHEN** a search root value maps outside the current value head target range
- **THEN** policy-only training remains valid and MUST NOT silently clip under an undeclared transform

### Requirement: Sample weights SHALL be unit-safe

Training weights SHALL NOT use raw unnormalized score variance in a way that changes meaning when score units change. Any variance weighting MUST declare a score scale.

#### Scenario: high-value volatile state
- **WHEN** a critical high-score state has large raw variance
- **THEN** it MUST NOT collapse to near-zero weight solely because variance is numerically large in score-squared units

### Requirement: Checkpoint selection SHALL be regret-first

Every candidate checkpoint SHALL be evaluated on a frozen reference context split. Primary selection is lowest mean reference regret subject to p95, catastrophic-regret and safety constraints.

#### Scenario: accuracy and regret disagree
- **WHEN** checkpoint B has higher top1 accuracy but worse mean/p95 reference regret than A
- **THEN** B MUST NOT replace A solely because of top1 accuracy

### Requirement: Model input SHALL match Teacher information state

Student features SHALL include the versioned public/history/belief summary required by the feature contract. Oracle planes MUST remain disabled.

#### Scenario: same hand, different public history
- **WHEN** Teacher decisions differ because public history changes the posterior
- **THEN** student input SHALL carry the history/belief representation needed to distinguish those states

### Requirement: Training artifacts SHALL be reproducible

Every checkpoint SHALL embed or reference model, feature, dataset, teacher/search, opponent population, source-generation, loss, augmentation and random-seed provenance.

#### Scenario: checkpoint copied without manifest
- **WHEN** a model file lacks required provenance
- **THEN** it MUST NOT be marked calibrated/promoted for the policy-v3 release path
