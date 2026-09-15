## ADDED Requirements

### Requirement: Replay tracing is opt-in and preserves client behavior

The client SHALL provide a default-disabled replay trace option in local runner and match-runner entry points. Enabling it SHALL add execution evidence without changing BOT selection, action authorization, request demand, rate scheduling, retry or re-anchor semantics, and MUST NOT add network requests. Disabled tracing MUST NOT copy replay states or create trace output. Existing Recorder consumers and historical JSONL readers MUST remain compatible.

#### Scenario: Run with tracing disabled

- **WHEN** a game is run with default tracing configuration
- **THEN** existing logging and game behavior remain available and no replay state copies or trace sidecar are produced

#### Scenario: Compare enabled and disabled deterministic runs

- **WHEN** the same controlled game inputs and injected timing are run with tracing enabled and disabled
- **THEN** strategy decisions, action payloads, state request order, request modes and retry behavior match

### Requirement: Capture identity and time belong to the observation boundary

Trace records SHALL include trace schema version, capture session, available implementation/protocol versions, game identity, round/seat when known, record ID, local ordinal, capture timestamps and causal parents. Existing logical request, transport request, attempt and event IDs MUST be reused for correlation rather than replaced by ambiguous nearest-time matching. Record capture order and capture time MUST be distinct from queueing and disk-write time. Missing build or protocol metadata MUST be marked unknown.

#### Scenario: Capture is persisted after a later event

- **WHEN** background persistence is delayed after an observation
- **THEN** replay still has the original capture identity, order, causal references and timestamps and does not reinterpret disk completion as occurrence time

#### Scenario: SSE wakes a worker before persistence completes

- **WHEN** a notification causes a worker to request state before the notification is written to disk
- **THEN** capture metadata and causal references preserve receive-before-wake-before-dependent-request where that relationship was observed

### Requirement: Record actual Mahjong transition outcomes

When tracing is enabled, input parsing/application SHALL record the input identity, actual before state, completion/error/skipped outcome and actual after state for each local Mahjong transition boundary. Failed operations MUST retain any partial mutation. Processing completion boundaries MUST be recorded independently from successful transition records so a skipped application can be distinguished from an incomplete trace. Recorded state MUST include actual maintained business fields and identify unavailable fields as unknown rather than manufacturing a server-equivalent state.

#### Scenario: Application raises after changing a meld

- **WHEN** a transition mutates the live mirror and then raises
- **THEN** the trace records the input, before state, error and actual partially changed after state before re-anchor/reset handling

#### Scenario: A batch is only partly processed

- **WHEN** the client stops applying a batch after an error
- **THEN** completed transitions and the error boundary are recorded, and unprocessed events are not represented as successfully applied

### Requirement: Record request receipt and merge as separate facts

Tracing SHALL associate state request dispatch, each recorded physical attempt, full business response, actual pre/post merge state and response reconciliation using the existing request identity chain. Snapshot/events references MUST identify the response that supplied them. HTTP response completion MUST NOT be represented as successful merge or satisfied demand. Business payload capture MUST exclude authentication credentials while preserving the safe state/event data needed for offline reconstruction.

#### Scenario: Response arrives but merge fails

- **WHEN** a successful HTTP response is followed by an application error
- **THEN** response data, request/attempt IDs and the failed merge outcome are recorded separately, and the trace does not assert successful synchronization

#### Scenario: A retry returns the usable response

- **WHEN** one logical request has a failed attempt and a successful attempt
- **THEN** both attempts remain linked to one logical request and the applied response identifies the attempt that supplied it

### Requirement: Record connection lifecycle without fabricating Mahjong input

Tracing SHALL capture observed SSE connect, receive, parse failure, disconnect, reconnect and stream-closed boundaries, including connection identity and available reasons. Keepalive-only traffic MUST NOT be represented as state-changing input. A watermark notification MUST remain distinct from a complete state response or applied Mahjong event. Unknown disconnect reasons MUST remain unknown.

#### Scenario: SSE closes without a known transport cause

- **WHEN** the listener observes disconnection but no precise cause is available
- **THEN** it records disconnection and the connection identity with an unknown cause rather than asserting lost Mahjong events

### Requirement: Capture immutable state checkpoints and reset boundaries

Tracing SHALL capture full supported local-state checkpoints at initialization, mirror replacement/reset, round boundaries, and recovery from a trace gap. Mutable fields MUST be copied at capture so later mutations cannot rewrite earlier evidence. Checkpoints SHALL separately identify mirror fields, worker-owned state and unavailable fields. A reset or replacement MUST retain links to its cause and the previous state when captured.

#### Scenario: Live mirror changes after capture

- **WHEN** the bot mutates a hand or meld after a checkpoint was queued
- **THEN** the persisted checkpoint still represents the state at its original capture boundary

### Requirement: Trace persistence is bounded and loss is observable

Trace encoding and disk persistence SHALL use a bounded background queue without holding scheduling locks for I/O or blocking on queue capacity. Overflow, write failures and incomplete shutdown MUST NOT affect action authorization or cause extra requests. The trace SHALL expose incomplete coverage through dropped counts, ordinal gaps, explicit loss markers or missing completion footers, and SHALL provide the next available supported checkpoint before exact Observed replay resumes. It MUST NOT silently claim complete evidence after loss.

#### Scenario: Trace queue overflows during a response window

- **WHEN** the queue has no capacity for a new trace record
- **THEN** the game path continues without waiting, loss is counted, and the next persistable records identify incomplete coverage and a recovery checkpoint

#### Scenario: Process exits before the final trace flush

- **WHEN** the trace ends without its completion footer
- **THEN** offline import marks the tail incomplete and does not infer a missing local transition from absent tail records

### Requirement: Trace overhead and compatibility are validated offline

The change SHALL include controlled comparisons for tracing on/off, queue failure, parse/application failure, reconnect and shutdown. It SHALL measure capture overhead, queue high-water mark, dropped records and output size on a documented repeatable offline workload. Capture p95 SHALL meet the design target of at most 1 ms before tracing is declared ready; background disk latency MUST be reported separately. Existing recorder, mirror, transport, lifecycle and replay regression suites MUST remain passing.

#### Scenario: Tracing performance is reported

- **WHEN** the documented fixed workload is executed
- **THEN** the report includes workload/runtime configuration, on/off comparison, capture p95, persistence metrics, losses and whether the capture target passed
