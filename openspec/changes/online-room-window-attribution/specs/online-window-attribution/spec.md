## ADDED Requirements

### Requirement: Window lifecycle records are reconstructible

For every authoritative `WindowAttemptKey`, the client SHALL record enough structured facts to reconstruct source discard, confirmation request, authoritative authorization, decision, POST, server result, and terminal event. Records MUST distinguish the logical request id from its physical attempt index, and MUST preserve `window_id`, `window_attempt_key`, identity status/origin, authorization snapshot sequence, exact deadline, and stage timestamps when available.

#### Scenario: Authorized action has a complete timeline

- **WHEN** an authoritative response window is confirmed and a non-pass action is submitted
- **THEN** the log contains linked source, confirmation, authorization, decision, action and result records with the same WindowAttemptKey and a distinct logical/physical request identity

#### Scenario: Missing evidence is not inferred

- **WHEN** a window lacks an authoritative identity or an exact protocol deadline
- **THEN** the record keeps the missing value explicit and the acceptance result is weak/unknown rather than a guessed strong attribution

### Requirement: Window identities and phase transitions are stable

The client SHALL use the authoritative source discard identity for WindowId and SHALL never promote a snapshot watermark, discard count, meld count, or owner+tile fallback to authoritative identity. `response_peng` and `response_chi` for one discard SHALL share WindowId while retaining separate WindowAttemptKeys.

#### Scenario: Chi waits while the same window is still peng

- **WHEN** expected phase is `response_chi`, actual authoritative snapshot phase is `response_peng`, and WindowId is unchanged
- **THEN** confirmation remains `PENDING`, no claim miss is written, and successor confirmation remains eligible

#### Scenario: Peng is overtaken by chi

- **WHEN** expected phase is `response_peng`, actual phase is `response_chi`, and WindowId is unchanged
- **THEN** the peng attempt becomes terminal with `phase_advanced` and cannot submit peng again

#### Scenario: A new discard is observed

- **WHEN** source discard sequence, round, owner or tile identifies a different window
- **THEN** the previous attempt is stale/terminal and the new window is not merged with it

### Requirement: Authorization requires complete authoritative evidence

An attempt SHALL enter `AUTHORIZED` only when WindowId is authoritative and unchanged, phase matches, the bot seat is in `responding_seats`, a phase-specific exact deadline exists and is still valid, and the authoritative legal set is non-empty. A phase mismatch, missing identity, missing deadline or expired deadline MUST remain distinguishable.

#### Scenario: Valid authoritative open

- **WHEN** all authorization predicates hold before the exact deadline
- **THEN** the client records `authoritative_open`, snapshot sequence, exact deadline and deadline-left evidence, and may enter decision processing

#### Scenario: Phase is pending rather than closed

- **WHEN** chi confirmation sees the same WindowId in the preceding peng phase before the chi schedule deadline
- **THEN** the resolver returns `PENDING` and MUST NOT call claim-miss recording

### Requirement: Decision outcomes are explicit and identity-linked

Every response-phase decision SHALL record WindowId, WindowAttemptKey, identity provenance, authorization snapshot sequence, exact deadline, start/end timestamps, deadline-left values and one of `PASS`, `NON_PASS_ACTION`, or `NO_DECISION`. PASS closes only that phase and is not a claim miss.

#### Scenario: PASS closes only peng

- **WHEN** the bot chooses PASS in `response_peng` for a window that can later enter chi
- **THEN** the peng attempt closes as `STRATEGY_PASS` while the same WindowId's chi attempt remains eligible

#### Scenario: Authorized window reaches terminal without decision

- **WHEN** an authoritative open exists but no decision record exists before terminal timeout
- **THEN** canonical resolution is `DECISION / decision_not_started_before_terminal`

#### Scenario: Decision crosses the exact deadline

- **WHEN** decision starts before the exact deadline and finishes at or after it
- **THEN** canonical resolution is `DECISION / decision_deadline_expired_during_compute`

### Requirement: Non-pass submission and POST results are safe and attributable

Every completed non-pass decision SHALL end as `POST_OK`, `POST_REJECTED`, `POST_UNCERTAIN`, or an explicit client submit abandonment. Action records SHALL include authorization snapshot/deadline evidence, response timing, decision id, WindowAttemptKey and server trace id when available. A 409 or uncertain result MUST trigger RESYNC and MUST NOT retry the old action.

#### Scenario: Successful action wins over late terminal evidence

- **WHEN** a response action succeeds and a later timeout or raw claim miss is observed
- **THEN** canonical result remains `SUCCESS`, no successor confirmation is created, and the raw row is counted only as `false_claim_miss`

#### Scenario: 409 is recovered safely

- **WHEN** a window action receives HTTP 409
- **THEN** the action is recorded as `POST_REJECTED`, the attempt is marked, exactly one seq=0 RESYNC chain is allowed, and the same physical action is never reposted

#### Scenario: POST result is uncertain

- **WHEN** an action transport error, response-read error or relevant 5xx leaves execution unknown
- **THEN** the result is `POST_UNCERTAIN`, the old action is never resent, and the follow-up reconciliation is recorded as applied, not applied, or unknown

### Requirement: Canonical attribution is mutually exclusive

The acceptance program SHALL emit exactly one primary outcome for each authoritative WindowAttemptKey using precedence `SUCCESS`, `STRATEGY_PASS`, `RULE_PREEMPTED`, `POST_REJECTED`, `POST_UNCERTAIN`, `SUBMIT`, `DECISION`, `CONFIRM`, `UNKNOWN`. Server timeout is terminal evidence only and MUST NOT be used as the primary root cause by itself.

#### Scenario: Opponent preempts a legal window

- **WHEN** another seat claims the same discard before the bot successfully acts
- **THEN** the attempt resolves as `RULE_PREEMPTED` with no client loss stage

#### Scenario: Identity cannot be linked

- **WHEN** a raw claim miss or terminal record has no authoritative WindowId
- **THEN** it is reported as `identity_unverifiable`/`UNKNOWN` and excluded from strong three-stage attribution

### Requirement: Acceptance reports identity, transport and gap evidence by layer

The acceptance script SHALL report logical eligible windows, authoritative/legacy identity coverage, identity origin, first-seen source, canonical outcomes and loss stages. It SHALL classify gaps as snapshot reanchor, SSE batch catchup, transport retry related, event discontinuity, or log truncation/unknown, with `decision_impact` in `true|false|unknown`. Missing decision evidence MUST NOT be assumed to mean `false` impact.

#### Scenario: Legacy identity blocks strong completeness

- **WHEN** one eligible logical window is `legacy_unresolved`
- **THEN** the room is `window_partial_identity` and cannot be declared `strong_window_complete` unless an explicit protocol-skipped identity rule applies

#### Scenario: Gap has unresolved decision impact

- **WHEN** an event discontinuity occurs and no authoritative rebuild boundary proves whether a decision was affected
- **THEN** `decision_impact` is `unknown` and the room cannot pass the gap hard-fail gate

### Requirement: StateDemand and layered room status are auditable

At room end the report SHALL preserve demand counters and source (`end`, `req_fallback`, or `missing`), and each gid MUST expose reason statuses, `reason_mask` and `in_flight`. Transport, window and game completeness SHALL be reported independently; missing `round_ended` evidence is `protocol_skipped` or `partial` only at game layer.

#### Scenario: Clean demand terminal

- **WHEN** a gid reaches a normal terminal end
- **THEN** `reason_mask == 0` and `in_flight == false`, otherwise the affected layer is not complete

#### Scenario: Settlement marker is unavailable

- **WHEN** transport and window logs are complete but the protocol has no round-ended settlement marker
- **THEN** transport/window may be complete while game status remains `protocol_skipped` or `partial`

### Requirement: Frozen online acceptance enforces safety gates

Final acceptance SHALL use a clean commit, fixed BOT strategy, fixed state rate 15, SSE plus incremental state, and three to five serial independent rooms of ten games. The report MUST include the run manifest, per-room evidence, hard-fail checks, and cross-room aggregate; old rooms MUST NOT enter the final denominator.

#### Scenario: Frozen run manifest

- **WHEN** a new room starts
- **THEN** commit, clean state, command, strategy, rate, transport, Python version and acceptance script version are recorded without secrets

#### Scenario: Hard safety failure

- **WHEN** a same-window old action is reposted, a pending confirmation writes claim miss, success/pass becomes client loss, or end demand remains dirty
- **THEN** acceptance stops with a code-fix failure and cannot mark the room passed
