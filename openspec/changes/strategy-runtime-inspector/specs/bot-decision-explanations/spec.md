# Spec Delta

## ADDED Requirements

### Requirement: Live decisions expose normalized runtime audit state

Every newly recorded online strategy decision SHALL carry an explicit `decision_scope` and bounded runtime audit derived from that invocation's evaluation. The audit MUST distinguish configured, eligible, entered, and completed states, report actual Stage A/Stage B execution and fallback status, and preserve missing or unsupported values as unknown. Audit generation MUST reuse the existing result and MUST NOT change or repeat strategy evaluation. Existing records without audit fields MUST remain valid.

#### Scenario: LegacyV2 branch returns before weighted search
- **WHEN** a LegacyV2 discard is selected in a specialized scope that returns before weighted search
- **THEN** the audit reports the specialized scope and says weighted search was configured but not entered

#### Scenario: Search entered and then falls back
- **WHEN** search begins but the evaluator returns a fallback result
- **THEN** the audit records entered=true, completed=false, fallback=true, and the evaluator's actual reason

#### Scenario: Older decision record
- **WHEN** a reader encounters a decision record created before runtime audits existed
- **THEN** it leaves the audit missing and continues processing the record
