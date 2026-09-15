# state-request-lifecycle Specification

## Purpose
TBD - created by archiving change state-request-lifecycle. Update Purpose after archive.
## Requirements
### Requirement: Each game has one state fetch owner

The client SHALL distinguish pending logical reasons, a queued candidate, an
admitted logical request, physical HTTP attempts, and response reconciliation.
Each gid MUST have at most one queued candidate or one exclusively owned fetch
chain through reconciliation. Duplicate callers MUST NOT independently execute
HTTP from the same request plan. Different gids MUST remain independent while
sharing the token's physical state limiter.

#### Scenario: Concurrent callers request the same game

- **WHEN** two callers request state for one gid before its current fetch completes
- **THEN** they share the current operation or receive an explicit busy outcome, and no second HTTP chain starts

#### Scenario: A response is still being applied

- **WHEN** HTTP has completed but the game worker is still applying the response
- **THEN** that gid remains occupied for scheduling purposes and cannot dispatch its successor

### Requirement: Queued candidates use the latest pending demand

Before admission, same-gid demand changes SHALL update the existing candidate.
FULL MUST dominate DELTA when any pending reason requires a snapshot. The final
mode, sequence, reason revisions and deadline MUST be revalidated and frozen at
the admission boundary. A candidate update MUST notify the sole waiting queue
without resetting its original arrival order for duplicate demand.

#### Scenario: Confirmation upgrades a queued delta

- **WHEN** a DELTA candidate with local applied cursor 90 is waiting for a permit and WINDOW_CONFIRM is submitted for the same gid
- **THEN** one candidate remains and its eventual request uses FULL with seq=0 and carries both pending reasons

#### Scenario: Confirmation joins a queued resync

- **WHEN** RESYNC already requires FULL and WINDOW_CONFIRM arrives before admission
- **THEN** one FULL request carries both reasons without an extra queued request

#### Scenario: Ordinary incremental request preserves applied cursor

- **WHEN** the applied cursor is 90 and an SSE watermark asks to reach 100 without a pending FULL reason
- **THEN** the physical incremental request starts at seq=90 and retains 100 only as its reconciliation target

#### Scenario: A queued candidate becomes unnecessary

- **WHEN** all reasons are resolved before permit admission
- **THEN** the candidate is withdrawn without an HTTP attempt or consumed permit

### Requirement: Admission and closure have a deterministic boundary

Candidate freeze, permit issuance and closure SHALL be synchronized so an update
before freeze is included and an update after freeze is treated as in-flight
demand. Queue and demand locks MUST have a documented acquisition order. HTTP,
mirror application, strategy, sleeps and recorder I/O MUST occur outside these
locks.

#### Scenario: Upgrade races with permit issuance

- **WHEN** a WINDOW_CONFIRM update occurs concurrently with admission
- **THEN** it is included in the frozen request or retained for post-response reconciliation according to the freeze order, never lost or dispatched twice

#### Scenario: Close follows admission but precedes HTTP

- **WHEN** an admitted request is stopped before the HTTP invocation begins
- **THEN** it records cancellation before send, releases ownership, does not refund the permit, and records zero physical attempts for that request

### Requirement: State admission preserves the existing scheduling policy

All physical state attempts, including retries, SHALL acquire the same token's
StateThrottle permit through one waiting queue. This change MUST preserve the
configured rate and default 15/s, live-deadline EDF, expired-candidate demotion,
existing ordinary ordering/aging, and 429 cooldown. It MUST NOT introduce reason
class ordering across gids, capacity reservation, slack ordering, or a second
serial limiter queue.

#### Scenario: Expired work competes with an attainable window

- **WHEN** nine expired catch-up candidates and one live-deadline candidate compete
- **THEN** the live candidate wins according to the existing policy and expired work does not retain deadline priority

#### Scenario: A physical retry needs another permit

- **WHEN** an unchanged logical state request receives 429 and the existing transport policy retries
- **THEN** both physical attempts acquire a permit from the same limiter, preserving existing backoff and feedback behavior

#### Scenario: No lifecycle change affects a request stream

- **WHEN** a deterministic stream has no candidate upgrades or closure events
- **THEN** the refactored scheduler preserves the baseline ordering and rate behavior under the same clock and configuration

### Requirement: In-flight requests are not replaced or hedged

After freeze, the request's seq and mode SHALL remain fixed throughout its
existing transport retry chain. New reasons SHALL merge into the ledger for
reconciliation. They MUST NOT cancel sent HTTP solely for priority, start a
parallel same-gid FULL request, or produce a speculative successor.

#### Scenario: Delta is already in flight when confirmation arrives

- **WHEN** a WINDOW_CONFIRM is submitted after a DELTA request has started
- **THEN** the DELTA continues and the confirmation is checked against its applied result before any successor is dispatched

#### Scenario: An in-flight response already covers the new confirmation

- **WHEN** the applied response supplies complete authoritative evidence satisfying the new confirmation
- **THEN** that reason completes without an additional FULL request and the completion records the reason revision actually checked

### Requirement: Reasons complete only after successful application and matching evaluation

The worker SHALL apply the response before acknowledging semantic completion.
SSE_DELTA MUST require continuous application or a successful authoritative
rebuild covering the latest watermark target. RESYNC MUST require a successful
FULL rebuild covering its cause. WINDOW_CONFIRM MUST require the existing
window resolver's identity-linked result. HTTP 200, requested FULL mode, or a
returned watermark alone MUST NOT complete an unapplied reason.

#### Scenario: Mirror rebuild fails

- **WHEN** a seq=0 response contains a snapshot but applying it fails
- **THEN** RESYNC remains pending or enters the existing explicit recovery path, and the client does not record successful resynchronization

#### Scenario: New SSE watermark is already covered

- **WHEN** an in-flight response is successfully applied through seq=110 and the latest SSE target is 110
- **THEN** SSE_DELTA is satisfied by applied coverage and the generation change does not cause a successor

#### Scenario: Multiple reasons have different completion conditions

- **WHEN** a FULL snapshot rebuild succeeds but the expected chi phase is still pending
- **THEN** RESYNC can complete while WINDOW_CONFIRM remains pending

### Requirement: Completion is scoped to reason revision and window identity

Each reason SHALL have an independent semantic revision. Completion results
MUST identify the revision and, for confirmation, WindowAttemptKey that were
evaluated. Global generation changes MUST NOT by themselves invalidate valid
confirmation or authorize a successor. A stale result MUST NOT complete a newer
reason; coverage of a newer reason requires explicit evaluation of that reason.

#### Scenario: SSE changes while confirmation completes

- **WHEN** SSE_DELTA advances its watermark while an unchanged window confirmation is evaluated
- **THEN** the valid confirmation result remains applicable to its own revision

#### Scenario: Old result arrives after a new window replaces the demand

- **WHEN** window A has been replaced by window B and a resolver result is tagged with A
- **THEN** B remains pending unless the response is separately evaluated against B and its current revision

#### Scenario: A new resync cause arrives during a request

- **WHEN** a new RESYNC revision arrives after freeze and the old rebuild result does not prove it covers the new cause
- **THEN** that revision remains pending for a subsequent rebuild

### Requirement: Successors follow reconciliation of still-pending reasons

A successor SHALL be created only after the prior fetch has completed response
application and a current reason remains PENDING. It MUST preserve the relevant
WindowAttemptKey and existing observation budget across repeated observations
of that key. Completion, strategy PASS, a superseding window or authoritative
terminal evidence MUST prevent an obsolete confirmation successor. Existing
HTTP retry and confirmation retry limits MUST remain unchanged in this phase.

#### Scenario: Strategy pass completes the expected phase

- **WHEN** an applied authoritative snapshot leads to PASS for the expected phase
- **THEN** no successor is created for that completed phase, while a separately relevant chi phase can retain its own demand

#### Scenario: A pending observation requires another response

- **WHEN** the same confirmation is still pending after application and its existing observation budget permits another request
- **THEN** at most one successor is created with a new logical request id and a link to the preceding request

### Requirement: Window timing has one owner and explicit provenance

One confirmation state SHALL own the WindowAttemptKey, observation counters and
timing values. It MUST distinguish not_before, scheduler_deadline,
observation_budget_deadline and exact_window_deadline. Extracting this state
MUST preserve the existing branch-specific request timing, fixed constants,
retry budget, and authorization clock conversion. Only an authoritative
snapshot for the expected phase can supply its exact deadline.

#### Scenario: Peng deadline is used to estimate chi timing

- **WHEN** a response_peng snapshot supplies a precise peng deadline and the client estimates the later chi window
- **THEN** the derived chi time is labeled local scheduling/observation information and never a chi exact deadline

#### Scenario: Local observation budget ends without phase authority

- **WHEN** the existing confirmation budget is exhausted without authoritative evidence that the expected phase expired
- **THEN** the client records confirmation_observation_budget_exhausted, emits no claim miss from that local exhaustion, and does not report server_deadline_expired

#### Scenario: Same window is still in peng

- **WHEN** chi confirmation observes the same authoritative WindowId in response_peng while its observation budget remains
- **THEN** it remains pending and cannot authorize chi or record claim miss

### Requirement: Action authority and uncertain POST recovery remain unchanged

The refactor SHALL preserve authoritative identity, phase, responding seat,
exact deadline, legal action checks and existing submit margins. SSE remains a
wakeup source. A 409 or uncertain POST MUST keep the attempted-window barrier,
request seq=0 RESYNC and never replay the old action. Canonical SUCCESS,
STRATEGY_PASS and RULE_PREEMPTED precedence MUST remain unchanged.

#### Scenario: Uncertain action is followed by coalesced state work

- **WHEN** action uncertainty requires RESYNC while state demand is already queued
- **THEN** the state work upgrades to FULL as needed and the old action is never resent

#### Scenario: Late timeout follows action success

- **WHEN** a window action succeeded before a later timeout or confirmation response
- **THEN** success remains canonical and the completed phase is not reopened

### Requirement: Closing a game releases demand without inventing completion

Closure SHALL be idempotent, reject new scheduling, withdraw queued candidates
and record terminal reasons with their actual cause. In-flight transport MUST
remain visibly owned until it unwinds; after closure its response cannot trigger
new actions or retries. Once cleanup finishes, queued count and reason_mask MUST
be zero and in_flight MUST be false. Resource cleanup MUST NOT promote stopped,
error, inaccessible or truncated runs to successful or complete game evidence.

#### Scenario: Initial resync receives finished

- **WHEN** the first FULL response states that the game is finished
- **THEN** RESYNC and all remaining pending reasons close with the game terminal cause, and the final demand record is clean

#### Scenario: Queued work is stopped

- **WHEN** a worker is stopped before admission
- **THEN** its candidate is withdrawn, no HTTP is sent, repeated close is harmless, and the run retains a stopped rather than successful outcome

#### Scenario: Stop occurs during HTTP

- **WHEN** close is requested while HTTP is active
- **THEN** the state remains CLOSING until transport unwinds, no successor or next retry starts, and any response cannot initiate an action

#### Scenario: HTTP returns 404 or a worker fails

- **WHEN** the fetch exits as inaccessible or worker_error
- **THEN** cleanup records that cause and releases resources without reporting normal game completion

#### Scenario: Process ends before transport cleanup

- **WHEN** no released terminal state can be recorded before process termination
- **THEN** acceptance retains partial or missing evidence instead of synthesizing a clean end

### Requirement: Structural acceptance preserves evidence boundaries

Implementation acceptance SHALL include deterministic race and transport tests,
the full test suite, strict OpenSpec validation, and compatibility replay of the
frozen three-room baseline before fresh online runs. Fresh acceptance MUST use
a clean implementation commit, BOT, SSE plus incremental state, rate 15 and
three to five serial independent rooms of ten games. Lifecycle, transport,
window identity and game settlement status MUST be reported independently.

#### Scenario: Frozen logs are replayed

- **WHEN** the new analyzer reads the 30 frozen ab371b6 game logs
- **THEN** canonical outcomes and the diagnostic matrix remain compatible, unavailable new fields remain unavailable, and those rooms are excluded from the fresh denominator

#### Scenario: A canary is followed by a code fix

- **WHEN** implementation changes after a canary room
- **THEN** final acceptance restarts its denominator for the new frozen version rather than combining versions

#### Scenario: Structural checks pass without proving latency improvement

- **WHEN** lifecycle and safety gates pass but C4 causality or identity/settlement evidence remains incomplete
- **THEN** the report states those limits separately and does not claim C4 improvement or strong window completeness from clean demand alone

