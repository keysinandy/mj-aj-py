## ADDED Requirements

### Requirement: Public-only opponent belief
The system SHALL derive all online opponent beliefs exclusively from hero-private information and publicly observable history. It SHALL NOT consume opponent concealed hands, true wall order, future outcomes or simulation-only concealed data during inference.

#### Scenario: Hidden hands are changed with same public state
- **WHEN** two snapshots have identical public state and hero hand but different opponent concealed tiles
- **THEN** belief output, candidate danger and selected action SHALL be identical under fixed configuration

### Requirement: Calibrated discard danger
The system SHALL estimate each legal discard's per-opponent response and settlement risk, including uncertainty and coverage. Multiple responder probabilities SHALL obey configured multi-winner settlement rules without double counting mutually exclusive outcomes.

#### Scenario: Danger estimate not usable
- **WHEN** public features are incomplete or risk model has insufficient calibration coverage
- **THEN** the system SHALL abstain from a risk override and use the unchanged legacyV2 comparator with a machine-readable reason

### Requirement: Shadow-safe deployment
The system SHALL support shadow-only evaluation whose outputs are logged but never alter an action.

#### Scenario: Shadow flag enabled
- **WHEN** shadow mode runs with all action override flags off
- **THEN** all selected actions SHALL match baseline legacyV2 for the same public contexts
