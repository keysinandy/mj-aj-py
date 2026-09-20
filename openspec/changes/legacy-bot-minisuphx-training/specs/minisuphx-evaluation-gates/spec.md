# Specification: minisuphx-evaluation-gates

## ADDED Requirements

### Requirement: Candidate promotion SHALL use frozen paired schedules

Smoke, fast and full gates SHALL use reproducible paired schedules that freeze seed, hero seat, dealer, rule/config variant and opponent profile. Distributed execution SHALL preserve the same schedule identities.

#### Scenario: rows finish out of order
- **WHEN** PC-A and PC-B complete paired rows in arbitrary order
- **THEN** merge reconstructs the frozen schedule and the final report is independent of completion order

### Requirement: Full promotion SHALL be based on score and uncertainty

Champion promotion SHALL use hero round-score delta and a predeclared bootstrap confidence criterion as primary evidence. Training loss, episodic training reward or an unpaired win-rate improvement MUST NOT independently authorize promotion.

#### Scenario: training reward improves but paired score is flat
- **WHEN** the candidate shows better ep_rew_mean but full paired score CI does not meet the promotion threshold
- **THEN** it remains an experiment and the current champion is retained

### Requirement: Candidates SHALL be evaluated against legacy and current champion

A promotion candidate SHALL pass the configured legacy-strength gate and SHALL NOT violate the configured robustness threshold against the current champion.

#### Scenario: candidate farms legacy but regresses against champion
- **WHEN** candidate score is positive versus legacy but materially worse versus the current champion
- **THEN** promotion is blocked until the predeclared robustness criterion is satisfied

### Requirement: A frozen discard hard set SHALL guard critical regressions

The project SHALL maintain a frozen hard set covering disagreement, high-entropy, special-rule, high-risk and late-round discard states. Each candidate SHALL report action/KL/value/illegal diagnostics on this set.

#### Scenario: overall paired score rises but a critical class collapses
- **WHEN** the candidate shows a systematic regression on a protected hard-set class beyond the configured threshold
- **THEN** the candidate does not pass the full promotion gate

### Requirement: Illegal learned actions SHALL be zero for promotion

Any illegal learned action observed in offline hard-set, paired evaluation or runtime validation SHALL fail the candidate unless the event is proven to be an instrumentation error and the evidence is regenerated.

#### Scenario: mask mismatch produces one illegal discard
- **WHEN** the candidate selects an action outside the legal mask
- **THEN** promotion fails and the bug must be corrected rather than repaired by arbitrary legal fallback in evaluation

### Requirement: Oracle-derived candidates SHALL be evaluated public-only

A model trained with privileged Oracle inputs SHALL be eligible for deployment only after Oracle features are fully disabled and all promotion gates are rerun in the public-information environment.

#### Scenario: oracle_keep is nonzero during full gate
- **WHEN** a full paired report was produced with privileged features available
- **THEN** that report cannot authorize public deployment
