## MODIFIED Requirements

### Requirement: Logical confirmation transport chains are reconstructible

The client recorder and acceptance analyzer MUST represent each linked
`WINDOW_CONFIRM` logical request as one chain containing its logical request id,
request kind, requested sequence, reason metadata, physical attempt indexes,
statuses, and response sequence/snapshot presence. A retry or successor MUST
remain distinguishable from a repeated action POST.

New records SHALL provide a run-scoped unique logical_request_id across gids
and worker restarts, and a unique transport_request_id for each physical
attempt. They MUST associate candidate_id, frozen reason revisions,
WindowAttemptKey when available, and successor_of where applicable. A retry
keeps the logical id and advances its physical attempt identity; a successor
uses a new logical id. Correlation headers MUST carry the same opaque ids as
the corresponding records without including credentials or action contents.
Legacy ids MUST retain their known file/gid scope and MUST NOT be promoted to
unique external correlation evidence without an explicit unique join.

#### Scenario: A 429 followed by a successful state response

- **WHEN** one confirmation logical request receives a 429 and then a 200 response
- **THEN** the report contains one logical request with two physical attempts, preserves both statuses and retry-after/backoff data, and does not count an action retry

#### Scenario: A successor request follows an incomplete response

- **WHEN** a confirmation response leaves the latest demand pending and a successor logical request is created
- **THEN** the report links both requests to the same WindowAttemptKey while keeping their logical request ids and attempt indexes separate

#### Scenario: Concurrent games and worker restart allocate ids

- **WHEN** two gids issue their first requests and a worker later restarts for one gid in the same run
- **THEN** all logical ids remain distinct and every physical attempt has a distinct transport_request_id

#### Scenario: A reason is added after physical dispatch

- **WHEN** a response satisfies a confirmation submitted after the request was frozen
- **THEN** the dispatch record preserves its original reasons and the completion record separately identifies the later satisfied reason and evaluated revision

#### Scenario: Legacy state ids repeat

- **WHEN** separate historical files contain state-1 and provide no unique external identifier
- **THEN** internal replay uses the known file/gid scope and external attribution remains unresolved if the join is ambiguous

### Requirement: Transport timing boundaries are additive and secret-free

Every recorded physical state attempt MUST preserve, when available,
monotonic-derived queue wait, request start, headers received, body finished,
HTTP duration, response status, retry-after/backoff, deadline-left at send and
response, and throttle applicability. Transport records MUST NOT include
tokens, credentials, URLs with secrets, response bodies, or full snapshots.

New lifecycle records SHALL distinguish candidate creation, queue entry,
admission, HTTP start and response application completion. Deadline fields MUST
identify whether they refer to a scheduling estimate, local observation budget
or authoritative phase-specific exact deadline. Derived chi timing from a peng
deadline MUST NOT be recorded as an exact chi deadline. HTTP-start-to-headers
MUST retain the urllib aggregate boundary and MUST NOT be labeled measured
wire-send latency. Unknown timing boundaries MUST remain explicit; legacy
records retain their original compatibility interpretation without fabricated
provenance.

#### Scenario: A response is late without a retry

- **WHEN** a 200 state response has a negative response deadline-left and no retry/backoff
- **THEN** the report identifies an HTTP-response-late boundary, preserves the queue and HTTP durations, and does not label it retry-caused

#### Scenario: An action does not use the state throttle

- **WHEN** an action POST is recorded outside the shared StateThrottle
- **THEN** throttle fields are marked not_applicable rather than fabricated as zero

#### Scenario: Only an estimated deadline is available

- **WHEN** request scheduling has a local deadline but no authoritative exact deadline for the expected phase
- **THEN** exact deadline and exact deadline-left remain absent, and scheduling lateness alone cannot establish server-window expiry

#### Scenario: Headers precede body and application

- **WHEN** headers arrive before the body finishes and mirror application completes later
- **THEN** those boundaries remain separate and header receipt alone is not recorded as usable snapshot or action authorization

## ADDED Requirements

### Requirement: Demand and physical request counts use explicit lifecycle boundaries

New reports SHALL separately count logical input demands, queued candidates,
frozen logical_state_requests, substituted_candidates, cancelled_before_send,
successor requests and physical_state_attempts. Only actual HTTP invocation
MUST increment physical_state_attempts; each retry counts once and queued or
cancelled work does not count. The report MUST state metric version/source and
deduplicate request and attempt summaries using their identifiers.

The legacy physical_state_requests field MUST retain its historical logical
request interpretation when reading old logs; it MUST NOT silently become the
new physical attempt counter. Missing actual-attempt evidence MUST remain
missing rather than being reconstructed from logical counters. Comparisons
MUST show physical rate and requests normalized by relevant window/time
denominators as well as raw counts.

#### Scenario: A queued delta is substituted by full confirmation

- **WHEN** a candidate upgrades from DELTA to FULL before admission and succeeds without retry
- **THEN** the report records the substitution, one logical request and one physical attempt rather than two physical requests

#### Scenario: Logical request retries once

- **WHEN** one admitted logical request sends a 429 attempt and then a 200 attempt
- **THEN** logical_state_requests increases by one and physical_state_attempts by two

#### Scenario: Candidate is cancelled without sending

- **WHEN** pending work closes before its HTTP invocation
- **THEN** cancellation is recorded with its boundary and no physical attempt is counted

#### Scenario: Attempt appears in both event and request summaries

- **WHEN** the same transport_request_id is present in multiple diagnostic records
- **THEN** aggregate physical counters count the attempt once

#### Scenario: Old logs lack new counters

- **WHEN** the analyzer reads legacy demand counters without physical attempt evidence
- **THEN** it preserves the legacy count and source, marks new physical evidence unavailable, and does not infer a coalescing or latency improvement
