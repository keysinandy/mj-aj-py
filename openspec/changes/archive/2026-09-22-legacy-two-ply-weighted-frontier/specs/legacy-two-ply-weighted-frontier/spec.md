# Spec Delta

## Purpose

Provides a public-information, probability-weighted two-ply discard frontier for online Legacy BOT decisions, with lazy child evaluation, bounded coverage, observable partial results, and an exact offline mode.

## ADDED Requirements

### Requirement: LegacyV2 is the canonical default with an explicit rollback

The product default discard evaluator SHALL be named `legacyV2` and SHALL
route to the weighted frontier profile. The historical `legacy` evaluator and
the exact `legacy-two-ply-v1` profile MUST remain available when explicitly
requested. Existing weighted rollout spellings, including
`weighted-two-ply-frontier-v1`, `weighted_two_ply`, and `legacy-v2`, SHALL be
accepted as aliases and normalized to the `legacyV2` profile metadata.

#### Scenario: Default bot route
- **WHEN** a caller constructs a bot without specifying an evaluator
- **THEN** the selected profile is `legacyV2` and its evaluation mode is
  `weighted`

#### Scenario: Explicit rollback
- **WHEN** a caller specifies `evaluator="legacy"`
- **THEN** the historical legacy ordering is used and the result is not
  relabeled as `legacyV2`

### Requirement: Weighted frontier uses only public unseen-tile mass

The weighted evaluator SHALL enumerate tile kinds only where `max(0, 4-visible[tile]) > 0` and SHALL weight every draw contribution by that remaining count. `visible` MUST include the observing hand, all public rivers, and all exposed melds; the evaluator MUST NOT read opponent concealed hands or wall order. The returned denominator SHALL distinguish theoretical unknown-tile mass from the physical live-wall count.

#### Scenario: Remaining copies determine draw weight
- **WHEN** a candidate has visible counts of 0, 1, 3, and 4 for four possible tiles
- **THEN** their draw weights are respectively 4, 3, 1, and 0, and the last tile is absent from the frontier

#### Scenario: Hidden state does not affect weighted values
- **WHEN** two local games have the same observing hand, public rivers, melds, rules, profile, and budget but different opponent concealed hands or wall order
- **THEN** root metrics, draw weights, coverage, and selected action are identical

#### Scenario: Drawn tiles remain visible
- **WHEN** a simulated draw consumes the final theoretically unseen copy of a tile and the child discards that tile
- **THEN** the child visible count remains four and the tile is not restored to the next-draw pool

### Requirement: Child evaluation is lazy and ordered by efficiency

For each root and simulated draw, the evaluator SHALL compute child shanten for every legal child discard before computing child ukeire. It SHALL compute child ukeire only for children tied on minimum child shanten, and SHALL use weighted ukeire followed by ukeire tile-kind count and deterministic policy tie-breaks to select among them. Draw branches SHALL be visited in descending remaining weight, then stable tile order.

#### Scenario: Worse-shanten child is not expanded
- **WHEN** two legal child discards have shanten 1 and 2
- **THEN** only the shanten-1 child is eligible for child ukeire evaluation

#### Scenario: Equal-shanten children retain policy tie-breaks
- **WHEN** two child discards tie on shanten and weighted ukeire
- **THEN** the evaluator may use ukeire tile-kind count, shape, feed risk, and stable tile order without changing the public-information metrics

#### Scenario: Lazy expansion preserves exact result
- **WHEN** all branches finish without a budget boundary
- **THEN** lazy evaluation returns the same selected child and aggregate weighted metrics as evaluating ukeire for every child

### Requirement: Online search exposes complete and acceptable partial outcomes

The online profile SHALL support a soft deadline and hard deadline, a maximum root frontier size, and a minimum accepted coverage. A root SHALL commit its completed metrics independently. A partial result MAY be selected only when partial mode is enabled, every root retained for final ranking has a usable result, and its covered draw weight divided by total draw weight meets the configured threshold; otherwise the evaluator MUST return the complete legacy action and an explicit fallback reason. Partial results MUST never be mixed with an incomplete root as if it were complete.

#### Scenario: Soft deadline stops low-value branches
- **WHEN** the soft deadline is reached while a root still has unvisited low-weight draw branches
- **THEN** the evaluator stops starting new low-weight branches, records covered and total weight, and may finish the current branch until the hard deadline

#### Scenario: Coverage-qualified partial result
- **WHEN** all retained roots have completed at least the configured coverage threshold and the hard deadline prevents remaining low-weight branches
- **THEN** the evaluator may select from those roots, marks `complete=false`, records `partial_accepted=true`, and exposes coverage for every retained root

#### Scenario: Critical root is incomplete
- **WHEN** any retained root lacks a usable result or falls below the minimum coverage
- **THEN** the evaluator discards partial ranking influence and returns the complete legacy selection with `fallback_reason=partial_not_acceptable`

### Requirement: Exact offline mode remains available and distinct

The system SHALL retain an exact two-ply mode that evaluates all retained roots and all remaining draw branches without accepting partial results. Online weighted mode and exact mode MUST have distinct profile names/fingerprints and MUST record the actual mode in evaluation metadata.

#### Scenario: Exact mode times out
- **WHEN** exact mode reaches its budget before every root and draw branch completes
- **THEN** it returns the complete legacy action and does not expose partial metrics as selected values

#### Scenario: Profiles are distinguishable
- **WHEN** the same state is evaluated once in online weighted mode and once in exact mode
- **THEN** the profile fingerprint or mode metadata distinguishes the decisions, even if the selected tile is equal

### Requirement: Search accounting is observable and bounded

The evaluator SHALL record root count, draw/child nodes, shanten and ukeire calls, shanten cache hits/misses, elapsed microseconds, total draw weight, covered weight, and coverage. Cache keys SHALL include every input that can change shanten or ukeire results, including hand, locked count, visible counts, rules/phase/legality, and profile layer; caches SHALL be decision-local or capacity-bounded and MUST NOT leak state between games.

#### Scenario: Metrics distinguish work from coverage
- **WHEN** a search returns complete or partial
- **THEN** its explanation includes both computational counters and `covered_weight/total_weight`, rather than using `nodes_total` as a probability proxy

#### Scenario: Different visible states do not share ukeire
- **WHEN** two decisions have identical hands but different public visible counts
- **THEN** their child ukeire values are computed or retrieved under distinct cache keys
