# Specification: search-teacher-dataset

## ADDED Requirements

### Requirement: Teacher SHALL optimize round-score Q under public information

The generator SHALL use a frozen information-set Search Teacher whose return unit is `hero_round_score_points`. Teacher search MUST consume only hero-private and public information plus versioned belief posterior; it MUST NOT consume real future wall order, other-player true concealed tiles or future events as observable input.

#### Scenario: hidden simulator truth exists
- **WHEN** a simulator world contains complete hidden hands and wall order
- **THEN** those values MAY drive simulation transitions but MUST NOT enter model features, information-set node keys, SearchSample payload or runtime explanation

### Requirement: Teacher configuration SHALL be immutable and fingerprinted

Every dataset SHALL record rule, belief, opponent population, continuation, leaf, search, teacher-budget and feature-contract fingerprints.

#### Scenario: continuation changes
- **WHEN** `pi_k` replaces a previous continuation policy
- **THEN** subsequent teacher rows MUST receive a new teacher/search provenance and MUST NOT be merged under the previous teacher fingerprint

### Requirement: Search budget SHALL be adaptive and deterministic

The generator SHALL support deterministic search tiers 512, 2048, 8192 and 16000 simulations. Tier promotion SHALL depend only on declared current-state evidence and profile thresholds.

#### Scenario: easy state
- **WHEN** lower-budget search produces a stable, low-variance separated best action
- **THEN** the row MAY stop at the lower tier and MUST record completed simulations and stop reason

#### Scenario: hard/disagreement state
- **WHEN** top actions remain close, search is ambiguous, or frozen baselines disagree
- **THEN** the state SHALL escalate to a higher tier up to the configured maximum

### Requirement: Forced actions SHALL not consume expensive teacher search

States with exactly one legal action SHALL be marked forced and SHALL not run high-budget search. A versioned deterministic subset MAY be retained for model sanity.

#### Scenario: only PASS is legal
- **WHEN** the legal mask contains exactly one action
- **THEN** the generator records the forced action without spending 2k/8k/16k search

### Requirement: SearchSample SHALL preserve soft evidence

Each non-forced sample SHALL preserve legal mask, visit counts, q_by_action, root value, simulation count, ambiguity, confidence and teacher uncertainty/provenance.

#### Scenario: near tie
- **WHEN** two legal actions have nearly equal Q/visits
- **THEN** both probabilities/evidence remain in the sample and are not collapsed into a one-hot hard label

### Requirement: Source groups SHALL not leak across splits

Every decision and counterfactual derived from the same source game/room/seed SHALL belong to exactly one of train, validation or final-test.

#### Scenario: multiple decisions from one game
- **WHEN** ten SearchSamples come from the same source game
- **THEN** all ten receive the same split assignment

### Requirement: Dataset SHALL support deterministic resume

The generator SHALL derive stable work identities from source group, context/history, teacher profile and search seed; completed rows MUST NOT be double-counted on resume.

#### Scenario: interrupted generation
- **WHEN** a job restarts after partial completion
- **THEN** the final dataset and sample fingerprints match an uninterrupted run with the same declared inputs
