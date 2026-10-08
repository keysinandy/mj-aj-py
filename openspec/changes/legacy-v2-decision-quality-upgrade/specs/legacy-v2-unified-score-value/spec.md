## ADDED Requirements

### Requirement: Terminal score-compatible value
All score-based candidate comparisons SHALL use hero net settlement points, with positive meaning gain and negative meaning loss. Win, loss, tempo and continuation terms SHALL be mutually exclusive and SHALL NOT double count any terminal event.

#### Scenario: Exact terminal settlement available
- **WHEN** a candidate reaches a terminal settle event
- **THEN** predicted settled points SHALL agree with the existing scoring/settle rules for that event

### Requirement: Bounded value override
The system SHALL evaluate at most the admitted bounded candidate frontier, preserve legacyV2's original winner on uncertainty and only override when a declared confidence and net-value margin is met.

#### Scenario: Overlapping confidence intervals
- **WHEN** candidate value estimates are too close to determine a confident best action
- **THEN** no override SHALL occur and the original winner SHALL be retained

### Requirement: Valid stage and ranking certificate
A Stage A shortcut SHALL NOT claim a final winner if enabled score-based re-ranking could overturn the root order. Incomparable partial and complete horizons SHALL NOT be ranked together.

#### Scenario: Early winner under changed comparator
- **WHEN** the old ranking gives a Stage A winner but the score override requires missing metrics
- **THEN** the system SHALL complete the compatible stage or abstain to the baseline
