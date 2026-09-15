## ADDED Requirements

### Requirement: Confirmation evidence is linked per authoritative window

The acceptance analyzer MUST build one confirmation evidence record for every
authoritative `WindowAttemptKey` that reaches canonical `CONFIRM`. The record
MUST link, when present, the source discard event, SSE wake, WINDOW_CONFIRM
demand, physical state attempts, throttle timing, HTTP/retry timing, resolver
records, and terminal evidence using the exact WindowId plus phase. Missing
links MUST remain explicit. For every linked logical physical request, the
report MUST retain request kind, logical request id, response sequence and
snapshot presence, retry/backoff facts, and the per-attempt status, queue,
deadline-left, and HTTP timing fields needed to reconstruct which boundary was
late.

#### Scenario: A linked confirmation request is reconstructible

- **WHEN** an authoritative window has a source event, a WINDOW_CONFIRM demand,
  a physical seq=0 request, and a terminal snapshot
- **THEN** the report links all facts to the same WindowAttemptKey and exposes
  the logical request id, per-request attempt details, attempt indexes, queue
  timing, HTTP timing, and terminal boundary

#### Scenario: An unlinked legacy record is not strongly joined

- **WHEN** a timeout or request has no authoritative WindowId
- **THEN** the report marks it `identity_unverifiable` and does not join it to a
  nearby authoritative window by seq or phase alone

### Requirement: Confirmation root causes are mutually exclusive

The analyzer MUST assign at most one `confirm_diagnostic.category` from:
`C1_CONFIRM_NOT_CREATED`, `C2_CONFIRM_NOT_DISPATCHED`,
`C3_CONFIRM_QUEUE_LATE`, `C4_CONFIRM_HTTP_LATE`,
`C5_TIMELY_RESPONSE_NOT_AUTHORIZED`, `PROTOCOL_PHASE_UNOBSERVABLE`, and
`UNRESOLVED`. A server timeout MUST NOT be used as a category by itself.

#### Scenario: Missing demand is classified as C1 only with a separate boundary

- **WHEN** an authoritative source event is observed in one response, no
  WINDOW_CONFIRM demand or physical confirm follows, and terminal evidence is
  observed in a later response
- **THEN** the record is `C1_CONFIRM_NOT_CREATED`

#### Scenario: Same-batch source and timeout are not automatically C1

- **WHEN** the source discard and the bot's response timeout are delivered in
  the same events batch
- **THEN** the record is classified from transport/protocol evidence or
  `UNRESOLVED`, and MUST NOT be called C1 solely because no confirm row exists

#### Scenario: A pending demand without a physical request is C2

- **WHEN** a WINDOW_CONFIRM reason is pending before terminal evidence but no
  linked physical request is recorded
- **THEN** the record is `C2_CONFIRM_NOT_DISPATCHED`

#### Scenario: A throttle grant after deadline is C3

- **WHEN** a linked physical confirmation obtains throttle permission after its
  applicable local deadline and queue timing is available
- **THEN** the record is `C3_CONFIRM_QUEUE_LATE` with queue evidence

#### Scenario: HTTP or retry timing is late

- **WHEN** a linked request has response-after-deadline or equivalent physical
  HTTP lateness proving that the response completed after the window boundary,
  optionally with retry/backoff, and no earlier category applies
- **THEN** the record is `C4_CONFIRM_HTTP_LATE` with the measured timing

#### Scenario: Retry evidence without a missed-window boundary is insufficient

- **WHEN** a linked request experienced retry/backoff but its recorded response
  still preceded the exact deadline and no other late boundary is available
- **THEN** the report preserves retry/backoff as transport contribution and
  leaves the root cause `UNRESOLVED` rather than asserting C4

#### Scenario: Timely expected-phase response is rejected by authorization

- **WHEN** a response arrives before the applicable deadline, exposes the
  expected phase, and the resolver rejects a client-checkable responding-seat,
  legal-set, or deadline predicate
- **THEN** the record is `C5_TIMELY_RESPONSE_NOT_AUTHORIZED`

#### Scenario: Expected phase is never authoritatively observable

- **WHEN** source and terminal evidence exist but no authoritative snapshot
  exposes the expected phase before terminal
- **THEN** the record is `PROTOCOL_PHASE_UNOBSERVABLE`, not a client loss caused
  by an inferred resolver rejection

### Requirement: Diagnostic attribution does not duplicate lifecycle loss counts

The confirmation diagnostic MUST be reported alongside canonical outcome and
loss stage. It MUST NOT overwrite stronger `SUCCESS`, `STRATEGY_PASS`,
`RULE_PREEMPTED`, POST, DECISION, or SUBMIT outcomes, and one raw timeout or
claim-miss row MUST NOT increment more than one primary loss count.

#### Scenario: Successful action has late terminal evidence

- **WHEN** a window has a successful action and a later raw timeout or claim
  miss
- **THEN** canonical outcome remains `SUCCESS`, the raw row is a false claim
  miss, and no confirmation diagnostic is counted as a client loss

#### Scenario: Diagnostic evidence is insufficient

- **WHEN** multiple categories are plausible but the recorded boundaries cannot
  prove one
- **THEN** the record is `UNRESOLVED` with missing evidence fields and the
  acceptance gate remains pending

### Requirement: Client fixes are gated by diagnostic evidence

Runtime changes in this change MUST be limited to deterministic defects proved
by non-zero C1, C2, or C5 records. C3 and C4 MUST be exported as transport
contribution evidence for a separate scheduling/retry change, and
`PROTOCOL_PHASE_UNOBSERVABLE` MUST be exported as a protocol evidence gap.

#### Scenario: Baseline has only transport or protocol evidence

- **WHEN** the baseline matrix has zero C1, C2, and C5 records
- **THEN** no StateDemand, rate, sleep, retry, or submit-margin code is changed
  by this change

#### Scenario: Deterministic defect is reproduced offline

- **WHEN** a C1, C2, or C5 pattern is reproduced by fake-clock or replay data
- **THEN** a narrowly scoped runtime fix and regression test may be added before
  a new frozen online run

### Requirement: Frozen baseline and fresh acceptance denominators are explicit

The report MUST identify `ab371b6` as a diagnostic baseline and MUST exclude
its rooms from the final acceptance denominator after any runtime fix. A new
clean commit MUST be frozen before the next 3--5 serial room run.

#### Scenario: Baseline report is generated without new rooms

- **WHEN** the analyzer runs against the three `ab371b6` rooms
- **THEN** it emits the complete root-cause matrix and does not mark the online
  functional gate complete

#### Scenario: Runtime fix starts a new acceptance series

- **WHEN** a deterministic fix is committed
- **THEN** the next manifest starts a new denominator at zero and records the
  new clean commit, fixed command, strategy, rate, and transport
