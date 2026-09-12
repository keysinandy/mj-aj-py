## ADDED Requirements

### Requirement: Every authoritative response window has a stable identity

The protocol MUST expose either a stable `source_discard_seq` or an opaque
`response_window_id` for an eligible response window. The identity MUST remain
stable through `response_peng`, `response_chi`, and `seq=0` snapshots, and MUST
be independent of the state watermark.

#### Scenario: Peng and chi share one discard identity

- **WHEN** one discard opens a peng phase followed by a chi phase
- **THEN** both phases expose the same authoritative window identity while the
  client keeps separate phase attempt keys

#### Scenario: A full snapshot preserves an open window identity

- **WHEN** the client requests `/state seq=0` while the response window remains
  open
- **THEN** the snapshot carries the same window identity as the source discard
  and does not substitute the snapshot watermark

### Requirement: New windows and rounds receive different identities

The server MUST issue a different identity for a new discard or new round. A
window identity MUST NOT depend on discard-list length, meld count, owner/tile
alone, or whether the discard remains visible after a claim.

#### Scenario: Repeated same-tile discards are distinct

- **WHEN** the same seat discards the same tile again in the same or a later
  round
- **THEN** each discard has a distinct authoritative identity

#### Scenario: A claimed discard is no longer reused

- **WHEN** another player claims a discard and the next snapshot is rebuilt
- **THEN** the claimed window identity is not assigned to the next response
  window

### Requirement: Identity provenance and mismatches are observable

The protocol and client records MUST preserve the identity value, field origin,
first-seen path, and any mismatch between event and snapshot identities. A
missing, changing, or malformed identity MUST be reported as weak/invalid
evidence and MUST NOT be promoted by a client heuristic.

#### Scenario: Event and snapshot identities disagree

- **WHEN** a source event and an authoritative snapshot expose different window
  identities for the same pending response
- **THEN** the client records both values, rejects the identity as authoritative,
  and prevents strong cross-reanchor attribution

#### Scenario: Snapshot-only identity remains legacy

- **WHEN** a snapshot contains an eligible response phase but no protocol window
  identity and no carried authoritative event identity exists
- **THEN** the client records `legacy_unresolved` and acceptance excludes it
  from strong identity coverage

### Requirement: Protocol coverage is a separate acceptance dimension

Acceptance MUST report eligible, authoritative, legacy, and protocol-skipped
identity counts separately. Missing protocol identity MUST NOT downgrade healthy
transport or StateDemand evidence, but MUST prevent `strong_window_complete`
unless an explicit protocol-skipped rule applies.

#### Scenario: Legacy identity blocks strong window completeness

- **WHEN** at least one eligible window remains `legacy_unresolved`
- **THEN** window evidence is `partial_identity` and cannot be declared strong
  complete

#### Scenario: Protocol version explicitly skips identity

- **WHEN** the server declares that its protocol version cannot provide a window
  identity
- **THEN** acceptance reports `protocol_skipped_identity` separately and does
  not pretend the affected windows are authoritative
