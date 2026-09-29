## MODIFIED Requirements

### Requirement: 护栏状态可见

LegacyV2 explanations SHALL expose:

- current_speed_ratio
- speed_band_threshold
- in_competitive_speed_band
- speed_dominated / speed_dominated_by
- pareto_dominated / pareto_dominated_by
- pareto_vector
- frontier before/after cap
- singleton proven/blocked reason

#### Scenario: 57 versus 48 enters weighted search

- **GIVEN** 2w has 57/17
- **AND** 4s(log 4t) has 48/15
- **THEN** explanation SHALL show 4s ratio≈0.842
- **AND** shanten-2 threshold=0.82
- **AND** speed_dominated=false
- **AND** taatsu-loss vs connected-singleton role difference
- **AND** whether 4s survived Pareto/cap into weighted search

### Requirement: 候选解释保留 speed/Pareto/marginal 字段

Candidate diagnostics SHALL preserve all comparator-relevant speed-band, Pareto, marginal-role, standing/future evidence needed to reproduce the decision.

#### Scenario: Fallback does not fake weighted completion

Admission diagnostics MAY remain when weighted search falls back, but future metrics SHALL remain missing/null and final search_used SHALL reflect baseline fallback.
