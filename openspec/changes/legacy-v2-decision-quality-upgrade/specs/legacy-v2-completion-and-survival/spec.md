## ADDED Requirements

### Requirement: Hero draw opportunity distribution
The system SHALL distinguish remaining live tiles from hero's remaining self-draw opportunities and estimate the probability of reaching a next hero draw using public seat order, interrupts and opponent behavior.

#### Scenario: Conditional guaranteed baotou win
- **WHEN** all unseen next draws are winning tiles but another player may end the hand before the hero draws
- **THEN** conditional next-draw win probability MAY equal one, but unconditional winning probability SHALL include probability of reaching that draw

### Requirement: Calibrated delay EV
When enabled, delayed HU, baotou, piao and kong lines SHALL be evaluated in compatible hero net-point units with survival and opportunity losses rather than a categorical risk-zero multiplier, while preserving exact legal actions and the existing guaranteed-conditional-win fast path.

#### Scenario: Delay estimate unavailable
- **WHEN** survival or score calibration is absent
- **THEN** the existing HU arbitration SHALL be retained and recorded as fallback
