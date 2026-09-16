# Specification: regret-aware-policy-loss

## ADDED Requirements

### Requirement: Soft teacher targets SHALL remain the primary policy loss

The trainer SHALL keep a masked soft-target KL/cross-entropy against the teacher visit distribution. Near ties MUST NOT be collapsed into one-hot labels and illegal actions MUST be masked before normalization.

#### Scenario: near tie
- **WHEN** the teacher splits mass 49%/46%
- **THEN** both probabilities remain the training target

### Requirement: Ranking loss SHALL weight by Q difference

The trainer SHALL optionally add a pairwise logistic ranking loss over action pairs with `Q(i) > Q(j)`, weighted by `clip(|Q_i - Q_j| / q_scale, 0, max_weight)`. First version compares best-vs-others (or top-k vs bad) and uses `ranking_weight=0.25` with `policy_weight=1.0`.

#### Scenario: near-tie pair revisited
- **WHEN** two actions have nearly equal Q
- **THEN** the ranking term carries almost no weight for that pair

#### Scenario: expensive mistake
- **WHEN** the policy's chosen action has much lower Q than the best
- **THEN** the ranking term pushes the best action's logit above it

### Requirement: Sample weights SHALL be confidence x error x importance and clipped

`sample_weight = clip(teacher_confidence * policy_error_factor * importance_factor, min, max)` with declared ranges. Ambiguity SHALL NOT increase the forced-learning weight; confident teachers SHALL weigh more than near-tie teachers.

#### Scenario: volatile hard case
- **WHEN** a critical state has large raw score variance
- **THEN** its weight stays inside the declared clip and does not collapse to zero or explode the batch

### Requirement: Catastrophic margin loss SHALL be opt-in and versioned

A catastrophic margin term MAY be enabled with a declared threshold and weight; the first version SHALL default it to zero and enable it only after ranking is stable.

#### Scenario: default training
- **WHEN** no catastrophic config is declared
- **THEN** `catastrophic_weight=0` and training equals the policy (+optional ranking) objective

### Requirement: Loss configuration SHALL be fingerprinted

Every loss/weight constant SHALL enter the training profile fingerprint and be recorded in the checkpoint provenance.

#### Scenario: changed ranking weight
- **WHEN** `ranking_weight` changes
- **THEN** the training profile fingerprint changes
