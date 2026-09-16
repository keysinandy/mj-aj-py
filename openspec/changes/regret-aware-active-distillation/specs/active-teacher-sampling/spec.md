# Specification: active-teacher-sampling

## ADDED Requirements

### Requirement: Candidate states SHALL be pooled before expensive teacher search

The pipeline SHALL collect on-policy candidate states and score them cheaply with policy-only inference before any expensive teacher search. Expensive search SHALL only run on actively selected states.

#### Scenario: easy state not selected
- **WHEN** a candidate state has low policy entropy, a large top1-top2 gap and no disagreement, hard-state history or special tag
- **THEN** it MAY be skipped or sampled at the low normal/random rate and MUST NOT consume a high teacher tier

### Requirement: Sampling ratios SHALL be configurable and deterministic

Every candidate source (normal, disagreement, hard, special, random) SHALL have a declared ratio in a frozen profile. Sampling SHALL be deterministic for a given (policy version, generation, seed, candidate pool).

#### Scenario: repeated sampling run
- **WHEN** the same pool and profile are sampled twice
- **THEN** the selected state ids and their source assignment are identical

### Requirement: State identity SHALL be a stable hash

Each teacher-labeled state SHALL carry a stable `state_id` derived from the canonical information state, usable for dedupe, caching, hard-state tracking and regression tracking.

#### Scenario: same state from two rollouts
- **WHEN** two rollouts visit the same information state
- **THEN** both rows carry the same `state_id`

### Requirement: Teacher results SHALL be cached by state and teacher identity

A teacher cache SHALL key on `state_hash + teacher_version + teacher_config_hash`. Cache reuse SHALL only be allowed when the requested budget is not higher than the cached run's completed budget.

#### Scenario: teacher upgraded
- **WHEN** the teacher profile changes
- **THEN** cached results under the previous teacher fingerprint MUST NOT be reused

#### Scenario: cache hit with lower budget
- **WHEN** a cached entry exists at 2048 simulations and the new request needs 512
- **THEN** the cached result MAY be reused; a 8192 request MUST re-search

### Requirement: Adaptive budget SHALL use confidence and conflict evidence

Budget promotion SHALL consider visit concentration, Q gap, search stability and policy/teacher conflict, in addition to the existing deterministic evidence; special/high-risk states SHALL escalate.

#### Scenario: high-policy-conflict state
- **WHEN** the policy strongly prefers an action with a clearly worse teacher Q
- **THEN** the state SHALL escalate to the top configured tier and be tagged as a hard source
