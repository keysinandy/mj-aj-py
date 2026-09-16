# Specification: bc-runtime-release

## ADDED Requirements

### Requirement: Released normal path SHALL be network-only

After release gates pass, normal decisions SHALL consist of feature extraction, single network forward, legal masking and deterministic argmax. High-budget search SHALL remain offline.

#### Scenario: low policy margin
- **WHEN** model output is finite/legal but top1-top2 probability margin is small
- **THEN** a released `confidence_threshold=0` profile MAY still use the network action without invoking shape-v2

### Requirement: Safety fallback SHALL remain available for model/runtime failure

The runtime SHALL retain `shape-v2 -> legacy -> legal emergency` fallback for invalid model/manifest/features, non-finite output or runtime exception.

#### Scenario: NaN logit
- **WHEN** any required legal action distribution is non-finite
- **THEN** the network suggestion is rejected, fallback is explicit, and the event is counted

### Requirement: Release SHALL compare against shape-v2 and shape-v1

The distilled policy SHALL run same-seed/seat/dealer/YCBK/opponent paired games against shape-v2 and shape-v1. Primary score metric is hero round score.

#### Scenario: superiority claim
- **WHEN** reporting that distilled BC is stronger than shape-v2
- **THEN** frozen paired-score 95% CI lower bound for BC-minus-shape-v2 MUST be above zero

### Requirement: Runtime SHALL satisfy decision-window safety

Release evidence SHALL measure complete decision latency including feature extraction, belief summary preparation, model forward, legal mask and logging overhead.

#### Scenario: model forward is fast but preprocessing is slow
- **WHEN** end-to-end p95/p99 violates the window gate
- **THEN** the release fails regardless of isolated neural benchmark

### Requirement: Critical rule buckets SHALL not regress silently

Release reports SHALL include piao/baotou, seven-pairs, kong types, response actions, wall tail, dealer and YCBK diagnostics.

#### Scenario: global score improves but kong bucket regresses
- **WHEN** aggregate score passes while critical-kong regression threshold fails
- **THEN** the candidate remains blocked

### Requirement: Default switch SHALL be reversible

The released checkpoint SHALL retain shape-v2/legacy kill switches and a last all-gates-passed rollback checkpoint.

#### Scenario: post-release rule/platform change
- **WHEN** rules/profile/platform guide changes invalidate fingerprints
- **THEN** prior release evidence becomes stale and runtime can roll back without retraining during the decision window
