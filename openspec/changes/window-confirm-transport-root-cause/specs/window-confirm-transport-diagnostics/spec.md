## ADDED Requirements

### Requirement: Logical confirmation transport chains are reconstructible

The client recorder and acceptance analyzer MUST represent each linked
`WINDOW_CONFIRM` logical request as one chain containing its logical request id,
request kind, requested sequence, reason metadata, physical attempt indexes,
statuses, and response sequence/snapshot presence. A retry or successor MUST
remain distinguishable from a repeated action POST.

#### Scenario: A 429 followed by a successful state response

- **WHEN** one confirmation logical request receives a 429 and then a 200
  response
- **THEN** the report contains one logical request with two physical attempts,
  preserves both statuses and retry-after/backoff data, and does not count an
  action retry

#### Scenario: A successor request follows an incomplete response

- **WHEN** a confirmation response leaves the latest demand pending and a
  successor logical request is created
- **THEN** the report links both requests to the same WindowAttemptKey while
  keeping their logical request ids and attempt indexes separate

### Requirement: Transport timing boundaries are additive and secret-free

Every recorded physical state attempt MUST preserve, when available,
monotonic-derived queue wait, request start, headers received, body finished,
HTTP duration, response status, retry-after/backoff, deadline-left at send and
response, and throttle applicability. Transport records MUST NOT include
tokens, credentials, URLs with secrets, response bodies, or full snapshots.

#### Scenario: A response is late without a retry

- **WHEN** a 200 state response has a negative response deadline-left and no
  retry/backoff
- **THEN** the report identifies an HTTP-response-late boundary, preserves the
  queue and HTTP durations, and does not label it retry-caused

#### Scenario: An action does not use the state throttle

- **WHEN** an action POST is recorded outside the shared StateThrottle
- **THEN** throttle fields are marked `not_applicable` rather than fabricated
  as zero

### Requirement: External gateway and server records use explicit joins

The analyzer MUST accept optional external timing records only when they join
through a unique `logical_request_id`, `server_trace_id`, or an explicitly
declared equivalent. It MUST retain source, clock domain, synchronization
quality, and duplicate/missing-match status. It MUST NOT join by nearest
timestamp, watermark, tile, phase, or response order alone.

#### Scenario: A server trace is uniquely linked

- **WHEN** a gateway/server row has the same unique trace id as one physical
  attempt and declares compatible timing semantics
- **THEN** the report links server arrival/handler/response boundaries to that
  attempt and records the join source and clock quality

#### Scenario: No external trace is available

- **WHEN** a client attempt has no matching gateway/server row
- **THEN** the report keeps the client transport evidence and marks the
  server/gateway split `UNRESOLVED` without claiming server fault

#### Scenario: Multiple external rows match

- **WHEN** an external key matches more than one row or has incompatible clock
  metadata
- **THEN** no strong join is made and the ambiguity is reported explicitly

### Requirement: Transport root-cause classes are conservative and exclusive

For each authoritative confirmation loss, the analyzer MUST assign at most one
primary transport evidence class from `QUEUE_LATE`, `HTTP_RESPONSE_LATE`,
`RETRY_BACKOFF_CONTRIBUTED`, `SERVER_GATEWAY_LATE`, `CLIENT_TRANSPORT_LATE`,
or `UNRESOLVED`. A measured contributor MAY be listed as secondary evidence,
but a retry/status count alone MUST NOT establish a missed-window class.

#### Scenario: Queue grant is after the deadline

- **WHEN** throttle grant or send time is after the applicable deadline and
  queue timing proves the wait
- **THEN** the primary class is `QUEUE_LATE`

#### Scenario: HTTP response is after the deadline

- **WHEN** the request was sent before the deadline but headers/body finished
  after it, with no external server split
- **THEN** the primary class is `HTTP_RESPONSE_LATE` and the report preserves
  the unresolved server/gateway attribution

#### Scenario: Retry occurs but the response is timely

- **WHEN** a request has a 429/502/retry-after or backoff but its response is
  observed before the exact deadline
- **THEN** the report records `RETRY_BACKOFF_CONTRIBUTED` as evidence only and
  does not classify the window as a missed-window transport loss

### Requirement: Diagnostics cannot trigger tuning or alter lifecycle safety

The transport diagnostic implementation MUST NOT change the configured state
rate, throttle policy, ordinary sleep, retry/backoff, action retry, submit
margin, canonical outcome precedence, or 409/uncertain re-anchor behavior.
Any tuning recommendation MUST cite the linked evidence matrix and be
implemented in a separate change.

#### Scenario: Baseline contains HTTP-late windows

- **WHEN** the analyzer finds C4 HTTP-late windows in the frozen baseline
- **THEN** it emits a transport contribution report while leaving runtime
  scheduling parameters unchanged

#### Scenario: Canonical success has late transport evidence

- **WHEN** a late transport record is followed by a successful action for the
  same WindowAttemptKey
- **THEN** canonical outcome remains `SUCCESS` and no client loss or action
  retry is introduced

### Requirement: Transport evidence is validated before fresh online rooms

The change MUST replay the frozen diagnostic baseline and run offline fixtures
for successful 200, 429/backoff, gateway error, queue delay, missing trace,
duplicate external match, and unsynchronized clocks before any fresh online
acceptance run. The baseline MUST remain excluded from a fresh denominator.

#### Scenario: Frozen baseline is replayed

- **WHEN** the analyzer runs on the three frozen `ab371b6` rooms
- **THEN** it preserves canonical outcomes, reports all 22 C4 and 4 unresolved
  transport candidates, and marks the result diagnostic-only

#### Scenario: Evidence is incomplete

- **WHEN** transport fields or external timing rows are missing
- **THEN** the report exposes the missing boundary and remains unresolved rather
  than inferring queue, server, or client root cause
