## ADDED Requirements

### Requirement: 护栏状态可见

LegacyV2 explanations SHALL distinguish singleton proven safe from singleton blocked by marginal structural divergence.

Decision-level fields SHALL include:

- frontier_singleton_proven
- frontier_singleton_blocked
- singleton_block_reason
- role_guard_slack
- role_guard_challengers

Candidate-level evidence SHALL include versioned marginal-role fields.

#### Scenario: 82 vs 77 still enters weighted search

- **GIVEN** the user 899s golden
- **WHEN** 9s has ukeire 82 and a role-preserving challenger has 77
- **THEN** explanation SHALL show that gap 5 is within shanten-3 slack
- **AND** SHALL show the 9s marginal structural loss
- **AND** SHALL mark challenger admitted_by=marginal_structure_guard

#### Scenario: 7899 is explained as redundancy

- **WHEN** one 9 is discarded from 7899s
- **THEN** explanation MAY show lost_pair_option=true
- **AND** SHALL show completed_meld_redundancy=true
- **AND** MUST NOT describe the tile as automatically protected

### Requirement: 候选解释保留边际结构字段

Candidate diagnostics SHALL preserve at least:

- marginal role version
- marginal loss tier
- lost pair option
- same-tile unseen
- lost taatsu option
- lost completed meld
- completed-meld redundancy
- alternative route count before/after
- singleton connectivity
- singleton live connectivity
- critical compound break

#### Scenario: 候选边际结构字段可复核

- **WHEN** marginal-role diagnostics are enabled for a LegacyV2 discard
- **THEN** every evaluated root SHALL expose the version, loss tier, route
  counts, public unseen count, connectivity, and critical-break flag
