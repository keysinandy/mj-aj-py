## ADDED Requirements

### Requirement: Replay is a read-only offline capability

The debugger SHALL import a local game log and optional server timeline, HTTP dump, and execution trace, and export a machine-readable replay and self-contained HTML. It MUST NOT contact production endpoints, submit actions, change input evidence, or require a running web service. Ambiguous local game matches and conflicting game identities MUST be reported rather than silently combined. Output paths MUST NOT overwrite inputs, and existing outputs MUST require an explicit overwrite option.

#### Scenario: Export with network access unavailable

- **WHEN** valid local inputs are supplied with all network access disabled
- **THEN** the debugger generates replay.json and index.html, and the HTML operates without external assets or network requests

#### Scenario: Server timeline belongs to another game

- **WHEN** a supplied server timeline has an identity conflicting with the local log
- **THEN** import reports the conflicting evidence and does not merge the two games

#### Scenario: Output would replace evidence

- **WHEN** an output path resolves to an input file
- **THEN** export rejects the path even if output overwrite was requested

### Requirement: Raw evidence and provenance remain inspectable

The debugger SHALL retain each input's original content, role, content hash, source location, raw identifiers, and timestamps. Every normalized event and diagnostic MUST reference its contributing raw records. Normalization, deduplication, display redaction and sorting MUST NOT mutate originals. Malformed records, unknown event types, conflicts and truncated files MUST produce visible data-quality records rather than disappear.

#### Scenario: Normalize a duplicated event

- **WHEN** identical server events occur in multiple input records
- **THEN** reference timelines apply the fact at most once while preserving every raw reference, and any independently recorded repeated local application remains a distinct Observed execution step for diagnosis

#### Scenario: A JSONL record cannot be parsed

- **WHEN** a line is malformed between valid records
- **THEN** its raw content and location remain inspectable, a data-quality diagnostic marks its uncertainty, and later valid records remain available

### Requirement: Replay contracts and identities are versioned and deterministic

ReplaySession SHALL include schema and analyzer versions, input hashes, rule configuration, normalized events, frames, local steps, checkpoints and diagnostics. Stable identities MUST derive from input content, source roles and explicit identifiers rather than current time or absolute paths. Conflicting payloads for the same authoritative event identity MUST remain unresolved conflicts. Configuration or schema incompatibility MUST be reported explicitly.

#### Scenario: Recompile identical evidence

- **WHEN** identical inputs and configuration are compiled twice, including after moving the input directory
- **THEN** semantic event identities, reconstructed states, classifications and diagnostics are identical

### Requirement: Normalize the supported Mahjong protocol without inventing rules

Normalization SHALL support round initialization, deal, draw, discard, chi, pon, open kan, closed kan, added kan, pass, timeout, win, round end, game end, state request/response/merge, and SSE connection/notification records. The three kan types MUST remain distinct. Tile aliases SHALL map explicitly to the existing 34-tile domain; raw spellings MUST remain available. Unsupported READY/RIICHI or unknown protocol semantics MUST remain UNKNOWN and MUST NOT enable rules absent from the Hangzhou profile.

#### Scenario: Added kan is normalized

- **WHEN** a gang event identifies an upgrade of an existing pon
- **THEN** normalization produces KAN_ADDED with references to the original payload and available pon identity

#### Scenario: A notification contains no Mahjong event

- **WHEN** an SSE payload contains only seq and closed
- **THEN** it produces a notification/watermark fact and no draw, discard, chi, pon or kan transition

### Requirement: Preserve causal local order independently of server sequence

The debugger SHALL preserve server event sequence, local capture or file order, request/attempt identities, causal references and original clock domains. Explicit causal relationships MUST take precedence over wall-clock sorting. Old file order MUST be labeled derived when exact execution order was not recorded. Sequence gaps MUST NOT alone be classified as missing transport events. Unassociated steps MUST remain inspectable without fabricated game-event identities.

#### Scenario: Wall clocks disagree

- **WHEN** an HTTP response timestamp appears earlier than its causally linked request
- **THEN** request-before-response order is retained and the original timestamps and clock uncertainty are displayed

#### Scenario: Full-state request uses zero as its cursor

- **WHEN** a FULL request uses seq=0 and returns a snapshot at seq 184
- **THEN** the request remains associated with its real local position and no fictitious server event at seq 0 is created

#### Scenario: Private events leave sequence gaps

- **WHEN** a local stream skips server sequences for events hidden from the selected player
- **THEN** the debugger marks visibility or coverage limits and does not assert SSE loss merely from the skipped numbers

### Requirement: Cursor navigation has an explicit state boundary

The cursor SHALL identify game, round, server seq, BEFORE/AFTER, local step index and global local ordinal. Each event-backed SeqFrame SHALL expose serverBefore, serverEvent, serverAfter and related local-step references; a snapshot-only anchor MUST identify its unknown event and unavailable before-state. Requests crossing sequences MUST be referenced by one request identity at all relevant steps. Global local-step playback MUST follow local causal order rather than regrouped related-event sequence.

#### Scenario: Request spans multiple server frames

- **WHEN** a request starts while the local cursor is 180 and responds at watermark 184
- **THEN** its start, response and merge can be inspected at their own boundaries, with one logical request in statistics

#### Scenario: Jump to an absent seq

- **WHEN** a user enters a seq with no known event or snapshot anchor in the selected round
- **THEN** the current valid cursor is retained and the UI explains that the requested seq is unavailable

### Requirement: Reconstruct server truth only from server evidence

Server Ground Truth SHALL be reconstructed deterministically from independently supplied server timeline evidence, including available starting hands and recorded draws. Missing portions MUST remain unknown. The debugger MUST NOT guess a dealer's first draw, insert unrecorded passes as facts, or copy local state into Server Ground Truth. Local snapshots can be shown as authoritative observations at their recorded anchors but MUST NOT be presented as an independent complete server timeline.

#### Scenario: Server export contains all starting hands

- **WHEN** complete starting hands and subsequent events are supplied
- **THEN** server before/after states reproduce the recorded hand changes and public state

#### Scenario: No independent server export is supplied

- **WHEN** only local evidence is imported
- **THEN** local replay remains available and the server view explicitly reports its missing or partial independent coverage

### Requirement: Expected state uses only inputs locally available at the cursor

Local Expected State SHALL use an independent reference reducer and only complete input available by the selected local step. It MUST NOT use server-only hands, future responses or actual erroneous state as corrective inputs. A notification watermark MUST NOT supply Mahjong facts absent from its payload. Reference transition logic MUST NOT simply call the current online Mirror transition implementation as its sole oracle.

#### Scenario: Pon information has not arrived

- **WHEN** the server has recorded PON but the local side has received only a watermark notification
- **THEN** Expected does not apply PON and no missing-local-transition failure is asserted

#### Scenario: Complete pon input is available

- **WHEN** a local response supplies a valid PON event with sufficient known preconditions
- **THEN** Expected applies the independent pon transition at its modeled processing boundary without waiting for observed state to become correct

### Requirement: Observed state reflects actual recorded execution

Local Observed State SHALL be constructed from recorded checkpoints and execution changes, including partial mutations on failure. A snapshot/events input record alone MUST NOT prove successful application. Without sufficiently complete execution evidence, affected Observed fields and processing steps MUST remain unknown. The debugger SHALL offer explicitly DERIVED legacy reconstruction without presenting it as historical execution fact or a missing-transition proof.

#### Scenario: Input batch is logged but application is not observed

- **WHEN** an old log contains an events batch and no exact application evidence
- **THEN** a derived reconstruction is available, but actual per-event observed states and transition completion remain unverified

#### Scenario: A transition fails after partial mutation

- **WHEN** an execution trace records an error and a changed after-state
- **THEN** Observed retains the recorded partial state while diagnostics compare it against Expected

#### Scenario: State response has arrived but has not merged

- **WHEN** the cursor is after STATE_RESPONSE and before the actual STATE_MERGE completion
- **THEN** Observed retains its pre-merge state and the response is inspectable as available input

### Requirement: Maintain four player states and historical claim provenance

Every reconstructed world SHALL represent four seats with hand knowledge, known counts, historical rivers, melds and rule flags. River entries SHALL retain discard identities and called/calledBy/callType when evidenced. Melds SHALL preserve owner, type, tiles, source player and source discard when known. Added kan SHALL update the original pon's lineage. Missing history MUST remain unknown rather than be fabricated from a later snapshot.

#### Scenario: A discard is claimed

- **WHEN** player 1 pons player 3's recorded discard
- **THEN** the historical river retains that discard marked called by player 1, and player 1 has a pon referencing it

#### Scenario: Upgrade a pon

- **WHEN** a later recorded added kan upgrades that pon
- **THEN** the meld becomes KAN_ADDED and retains its original discard and creation provenance

### Requirement: Update hands using evidenced actions

Known hands SHALL be updated for deal, draw, discard, chi, pon, kan and actually applied synchronization. Hand content and hand count knowledge MUST be independent, so an unknown hand is not interpreted as zero tiles. Future knowledge MUST NOT be retroactively assigned to local steps.

#### Scenario: Own discard changes hand and river

- **WHEN** an evidenced own discard removes a tile present in the known hand
- **THEN** that world's hand loses one matching tile and its river gains the corresponding discard entry

#### Scenario: Opponent tiles are hidden but the count is known

- **WHEN** evidence supplies an opponent hand count without tile identities
- **THEN** the count is displayed with hidden or unknown tiles and no fabricated tile values

### Requirement: Compare normalized business state and validate transitions

The debugger SHALL validate evidenced claim preconditions and post-transition invariants, including source discard, source/acting seat, tile match, duplicate melds, hand sizes, known tile multiplicity and relevant phase/turn state. Comparisons MUST use common semantic projections for historical versus remaining rivers, wall counts, response turn versus next actor, and known rule flags. Claimed river entries MUST NOT double-count physical tiles. Unknown fields MUST reduce comparison coverage, not become zero/false or automatic violations.

#### Scenario: Current Mirror removed a claimed river tile

- **WHEN** the historical display retains a called tile and recorded Mirror state contains only unclaimed discards
- **THEN** the normalized projections compare equal and tile totals count the claimed tile only in its meld

#### Scenario: Pon references an unavailable discard

- **WHEN** a pon cannot be linked to any known eligible discard
- **THEN** the original event remains inspectable and a diagnostic distinguishes a proved invalid source from unknown history

#### Scenario: Relevant window state changes without changing the tiles

- **WHEN** phase, responding seats, pending-discard identity or authoritative deadline changes while hand and river tiles stay equal
- **THEN** the structured business diff reports the known authorization-related changes

### Requirement: Three visibility modes apply to every information surface

The UI SHALL support PLAYER_VIEW, LOCAL_KNOWLEDGE and OMNISCIENT. PLAYER_VIEW SHALL show the selected player's visible information at the server position; LOCAL_KNOWLEDGE SHALL show only information locally known by the selected step; OMNISCIENT SHALL show only recorded server information and label its source. Tables, event/request inspectors, raw-payload display projections, diffs and search MUST follow the selected visibility mode. Unknown, hidden, not-applicable and known-empty states MUST be distinct. Unavailable view data MUST NOT be filled from another world.

#### Scenario: Opponent hand exists only in server evidence

- **WHEN** the same cursor is viewed in LOCAL_KNOWLEDGE and then OMNISCIENT
- **THEN** the first view conceals the hand on every display surface and the second reveals only the server-recorded tiles with SERVER provenance

#### Scenario: A later response reveals a tile

- **WHEN** the user navigates to a local step before that response
- **THEN** neither the table nor inspectors, search or diff disclose that future tile as current local knowledge

### Requirement: Requests and attempts are first-class replay objects

Every recorded logical state request and physical attempt SHALL remain individually inspectable with available trigger/reasons, identities, cursor, method/endpoint, payload, status and timing. Start, response and actual merge SHALL be separate local steps when recorded. Strong ID joins SHALL take precedence; heuristic legacy associations MUST be labeled derived or unresolved. Statistics MUST distinguish logical requests, physical attempts, cancellations before send and missing records.

#### Scenario: State request retries once

- **WHEN** one logical request makes two recorded HTTP attempts
- **THEN** the inspector shows both attempts and statistics count one logical request and two physical attempts

#### Scenario: Old request has only a response summary

- **WHEN** no complete payload or exact request association is available
- **THEN** the inspector labels the summary and association limits instead of displaying a fabricated full response

### Requirement: Response analysis compares three distinct state boundaries

Request analysis SHALL separately expose request-to-response changes, actual pre/post merge changes and Expected/Observed differences at equivalent processing boundaries. DELTA responses MUST be applied to a known reference anchor before full-state comparison. Diff results MUST include compared-field coverage and uncertainty. Empty events or equal summaries MUST NOT alone establish an empty effective diff.

#### Scenario: State changed while a request was in flight

- **WHEN** the response matches the actual pre-merge state but differs from the state at request start
- **THEN** request-to-response changes are shown separately and are not counted as changes repaired by this merge

### Requirement: State request classification includes business necessity

Each request SHALL have a primary classification, contributing causes, evidence strength and avoidability of NECESSARY, AVOIDABLE or UNKNOWN. Supported primary classifications SHALL include RECOVERY_CAUSED_BY_LOCAL_TRANSITION, RECOVERY_CAUSED_BY_RECONNECT, RECOVERY_CAUSED_BY_MISSED_EVENT, RECOVERY_REQUIRED, VALIDATION_ONLY, PROGRESS_UPDATE, REDUNDANT, SUSPICIOUS and UNCLASSIFIED. Classification MUST use recorded business reasons and recovery evidence, and preserve unknown when those are unavailable. Unresolved conflicts in facts needed for classification SHALL produce SUSPICIOUS before selecting a positive classification. Multiple proved recovery causes SHALL use local-transition, reconnect, missed-event precedence while retaining the others. A request required to repair an existing error MUST NOT be labeled redundant merely because fixing the earlier error could have avoided it.

#### Scenario: Unchanged state confirms an actionable window

- **WHEN** a request supplies required authoritative phase, response eligibility or deadline confirmation without repairing state
- **THEN** it is VALIDATION_ONLY and is not classified as REDUNDANT solely from unchanged tiles

#### Scenario: Normal notification leads to new input

- **WHEN** an ordinary SSE wake is followed by its first successful state fetch containing new events without a proved recovery condition
- **THEN** the request is PROGRESS_UPDATE, not RECOVERY_CAUSED_BY_MISSED_EVENT

#### Scenario: Complete evidence establishes a redundant request

- **WHEN** a successful request adds no effective information, satisfies no outstanding business need and addresses no reconnect, unknown-state or recovery condition
- **THEN** it is REDUNDANT with the proof fields and AVOIDABLE status

#### Scenario: Recover a missed local pon

- **WHEN** complete evidence proves Expected contains a pon, Observed missed it at a completed processing boundary, and a recorded merge repairs it
- **THEN** the request is RECOVERY_CAUSED_BY_LOCAL_TRANSITION and links the missing action, earliest proved broken step and recovery request

#### Scenario: Reconnect repairs an unknown interval

- **WHEN** a recorded disconnect/reconnect interval is followed by state synchronization restoring unknown state without a stronger proved local-transition cause
- **THEN** the request is RECOVERY_CAUSED_BY_RECONNECT and retains the recovered interval

#### Scenario: Old evidence does not establish necessity

- **WHEN** only an unchanged response summary is available and business reasons or state comparison coverage are incomplete
- **THEN** classification and avoidability remain UNCLASSIFIED and UNKNOWN with the missing evidence explained

### Requirement: Detect missing transitions only with sufficient execution evidence

The debugger SHALL detect missing CHI, PON, KAN_OPEN, KAN_CLOSED and KAN_ADDED at completed or explicitly failed/skipped local processing boundaries. A confirmed MISSING_LOCAL_TRANSITION MUST require locally available complete input, valid known reference preconditions, complete execution coverage and a contradictory actual state. Missing trace records, an SSE watermark, a legal claim option or a server timeout alone MUST NOT prove a missed transition. Strategy choices and failure to apply recorded state facts MUST remain distinct.

#### Scenario: Received input is waiting for normal processing

- **WHEN** complete input is available but its local processing boundary has not completed
- **THEN** the difference is RECEIVED_NOT_PROCESSED, not a confirmed missing-transition failure

#### Scenario: Processing completed without applying an eligible kan

- **WHEN** complete trace records show a finished input-processing boundary, sufficient kan input, and an unchanged contradictory Observed meld
- **THEN** a confirmed MISSING_LOCAL_TRANSITION identifies the exact kan type and earliest proved local step

#### Scenario: Bot deliberately passed

- **WHEN** a legal pon option exists but the recorded decision is PASS and no pon event occurred
- **THEN** no missing-pon-transition error is generated from the strategy choice

### Requirement: Locate first visible divergence and first confirmed failure separately

The debugger SHALL expose firstVisibleDivergence and firstConfirmedFailure, including cursor, cause/event, expected/actual values, affected fields, evidence and recovery status. Normal server lead SHALL be distinguishable from local processing failure. Downstream discrepancies SHALL link to known earlier causes. If an evidence gap hides the origin, the debugger MUST identify the earliest proved location and uncertain origin interval rather than invent an exact first failure.

#### Scenario: Server leads the local input

- **WHEN** a server event precedes local arrival at the cursor
- **THEN** a visible divergence is labeled NOT_RECEIVED_YET without creating a confirmed failure

#### Scenario: Missing pon causes later turn mismatch

- **WHEN** a proved missed pon precedes a caused turn mismatch
- **THEN** firstConfirmedFailure identifies the missed pon and the later mismatch links to it

#### Scenario: First observation follows a log gap

- **WHEN** differing states are first observed after an interval with incomplete execution evidence
- **THEN** the result states that the origin is unknown within that interval and does not claim an exact root-cause step

### Requirement: Recovery diagnostics retain evidence and timing semantics

Diagnostics SHALL support FIRST_DIVERGENCE, MISSING_LOCAL_TRANSITION, SERVER_LOCAL_STATE_MISMATCH, LOCAL_EXPECTED_OBSERVED_MISMATCH, REDUNDANT_STATE_REQUEST, STATE_RECOVERY, SSE_DELAY, SSE_GAP, STATE_INVARIANT_ERROR, UNEXPLAINED_STATE_CHANGE and UNKNOWN_DATA. Recovery MUST require recorded merge evidence and link to its request. Timing SHALL preserve clock domain and precision; cross-clock deltas without calibration MUST NOT be labeled exact network latency. Server timeout, strategy decision, attempted POST and action echo MUST remain distinct facts.

#### Scenario: State actually repairs a known defect

- **WHEN** a recorded merge removes a proved Expected/Observed mismatch
- **THEN** diagnostics mark it RECOVERED_BY_STATE with the recovery request and a duration only to the precision supported by the clocks

### Requirement: Partial evidence remains useful and visibly partial

The debugger SHALL report per-source and per-interval coverage, tolerate missing optional sources, and resume from later supported anchors. It MUST NOT upgrade actual execution certainty from a successful legacy replay, absence of reset, zero auto-play or HTTP 200. Unknown request reasons, opponent tiles, source discards and unobserved application steps MUST remain unknown.

#### Scenario: Old room contains no SSE or actual transition trace

- **WHEN** that room is imported with snapshots and event batches
- **THEN** supported local derived states and request summaries are available while SSE arrival and precise Observed execution coverage are explicitly unavailable

### Requirement: One cursor atomically drives the complete inspection interface

The HTML SHALL show all four seats with rivers, melds, allowed hands and a clearly identified selected player, plus server/local/expected comparison, server event, local step, request, structured diff, diagnostics and timing panels. Any cursor or visibility change MUST update these panels from the same immutable selection. Structured diffs SHALL enumerate business-field changes rather than only raw JSON text.

#### Scenario: Change cursor across a pon

- **WHEN** the user moves from before to after a pon
- **THEN** table, claim provenance, hand changes, request/local-step details and diagnostics all use the new cursor without stale panels

### Requirement: Navigation supports playback and diagnostic jumps

The UI SHALL support first/last/previous/next seq, direct valid seq entry, round selection, Before/After, individual local steps, and play/pause in seq or local-step units. It SHALL support previous/next diagnostic, state request and chi/pon/kan, plus separate jumps to first visible divergence and first confirmed failure. Missing destinations MUST be explained without invalid cursor changes. Current-round summaries SHALL provide request classes, evidence coverage and confirmed missing actions with links; unassociated records MUST be listed separately.

#### Scenario: Jump to a diagnosed missing pon

- **WHEN** the user selects its diagnostic or first-confirmed-failure control
- **THEN** the cursor selects the linked local step, shows its evidence and state diff, and keeps all panels synchronized

#### Scenario: Local-step playback crosses a server frame

- **WHEN** playback reaches the last associated step of one frame
- **THEN** it advances to the next global local step and updates the server anchor without reordering delayed processing

### Requirement: Checkpoint seeking preserves deterministic results

Checkpoint-based seek SHALL reconstruct the same states, unknown intervals and diagnostics as replay from the beginning. Checkpoints MUST include reference/observed progress and diagnostic state, and MUST NOT leak future knowledge into an earlier cursor. Semantic analysis MUST be independent of export generation time. The implementation SHALL provide a repeatable seek benchmark and document input size, runtime and measured latency.

#### Scenario: Seek backward through a repaired defect

- **WHEN** a replay with a later recovery is sought backward using a checkpoint
- **THEN** earlier state, diagnosis and recovery-as-of-cursor match replay from the beginning, without showing the repair as already applied

### Requirement: Untrusted evidence renders as text in the offline viewer

Embedded JSON and raw-payload displays MUST escape HTML/script delimiters and MUST NOT execute log-supplied code. Evidence displayed under a restricted visibility mode MUST be projected without changing the original preserved record. The viewer SHALL explain that visibility modes are presentation semantics and not access control over the full exported evidence file.

#### Scenario: Raw payload contains a script terminator

- **WHEN** evidence contains a string with HTML or a script-closing sequence
- **THEN** the viewer displays it as text where visibility permits and executes no injected script
