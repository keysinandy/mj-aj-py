## Context

The `ab371b6` baseline ran three serial rooms of ten games at fixed `state_rate=15`.
Across 473 eligible windows it recorded 54 canonical `CONFIRM` losses, but the
current report only knows that no authoritative open was followed by a stronger
terminal outcome. It does not yet prove whether the client failed to create a
demand, failed to dispatch it, waited in the shared throttle, received HTTP/retry
data too late, or correctly observed a phase that was already no longer
actionable.

The raw JSONL already contains the necessary boundaries: `tile_discarded`
events, SSE frames, `req` demand snapshots, per-physical state attempts,
throttle timing, `window_confirm` resolver records, and terminal events. The
design must consume those facts without changing the runtime scheduler.

## Goals / Non-Goals

**Goals:**

- Produce one deterministic diagnostic category for every authoritative
  confirmation-stage loss.
- Keep C1--C5 and `protocol_phase_unobservable` mutually exclusive.
- Preserve an explicit `evidence_quality` and an `unresolved` outcome when the
  log does not prove a category.
- Make C1/C2/C5 actionable for narrowly scoped client fixes and leave C3/C4
  available for a separate scheduling/transport change.
- Add offline fixtures for same-batch terminal delivery, successor requests,
  queue delay, retry/HTTP delay, timely phase mismatch, and missing phase data.

**Non-Goals:**

- Changing the 15/s rate, ordinary sleep, retry/backoff policy, or submit
  margin.
- Reclassifying a server timeout as a client loss without linked evidence.
- Guessing a missing WindowId or using snapshot watermark as source identity.
- Running more online rooms before the deterministic diagnosis and fixes are
  complete.

## Decisions

### 1. Keep canonical lifecycle resolution separate from root-cause diagnostics

The existing canonical precedence remains the primary lifecycle result. A new
`confirm_diagnostic` object is attached to each authoritative resolution and
does not overwrite `SUCCESS`, `STRATEGY_PASS`, `RULE_PREEMPTED`, POST outcomes,
or a proven decision/submit loss. This avoids turning a transport observation
into a duplicate functional loss count.

Alternative considered: replace `loss_stage=CONFIRM` directly with C1--C5.
Rejected because existing acceptance consumers need the stable lifecycle stage,
while the new matrix answers a different question: why confirmation evidence
was absent.

### 2. Link evidence by WindowId, phase, and bounded record order

Authoritative records are joined using the exact `(game_id, round_id,
discard_owner, source_discard_seq, tile, phase)` key. A state request is linked
only when its demand snapshot contains the same WINDOW_CONFIRM reason or its
explicit logical request chain identifies the window. Nearby seq/phase guessing
is not a join rule. Records without an authoritative key remain
`identity_unverifiable`.

Alternative considered: join every `WINDOW_PENG`/`WINDOW_CHI` request to the
nearest timeout. Rejected because concurrent windows and re-anchor snapshots
make proximity ambiguous.

### 3. Use evidence gates for the five categories

The analyzer applies the following first-match rules:

- `C1 confirm_not_created`: an authoritative source was observed before its
  terminal evidence, no WINDOW_CONFIRM demand or physical confirm exists, and
  the source was not delivered in the same response/batch as the terminal.
- `C2 confirm_not_dispatched`: a WINDOW_CONFIRM demand was pending, but no
  linked physical request exists before terminal.
- `C3 confirm_queue_late`: a linked physical confirm obtained throttle
  permission after its applicable local deadline, with queue delay as the
  supported dominant boundary.
- `C4 confirm_http_late`: a linked request has a response-after-deadline or
  equivalent physical HTTP timing boundary proving that it completed late,
  without C1--C3. Retry/backoff alone is retained as transport contribution
  evidence but MUST NOT prove C4 without a missed-window boundary.
- `C5 timely_response_not_authorized`: an authoritative response arrived
  before the applicable deadline and exposed the expected phase, but the
  resolver rejected it for a client-checkable authorization predicate.
- `protocol_phase_unobservable`: the source and terminal are observed without
  an authoritative expected-phase snapshot that could prove client rejection.

If none is provable, the category is `unresolved` with the missing evidence
listed. The analyzer MUST NOT force a category merely because a timeout exists.

### 4. Same-batch terminal delivery is not C1 by itself

When `tile_discarded` and the bot's response timeout are delivered in the same
events batch, the client had no separately observable confirmation boundary.
That record is classified as transport/protocol evidence according to the
request timing, not as a state-machine omission. This specifically prevents
the observed 16 no-confirm rows from being incorrectly treated as 16
deterministic C1 bugs.

### 5. Fix policy is evidence-gated

Only non-zero C1, C2, or C5 counts may authorize a runtime client fix in this
change. C3 and C4 produce a transport contribution report and a handoff to a
future scheduling/retry change. `protocol_phase_unobservable` produces a
protocol evidence requirement and does not justify client fallback behavior.

## Risks / Trade-offs

- [Incomplete old logs] → Keep category `unresolved` and report the exact
  missing boundary; never infer a false client loss.
- [Runtime logs lack a direct request-to-window link] → Extend recorder fields
  in a backward-compatible way and use old logs only for weak diagnostics.
- [Many C4 observations] → Do not tune rate in this change; export queue,
  HTTP, retry, and deadline-left distributions for a separate decision.
- [Changing acceptance output breaks consumers] → Add fields without removing
  existing canonical fields, and cover report schemas with tests.
- [Analyzer appears successful on fixtures only] → Require a baseline replay
  report over all 30 frozen game files before any online rerun.

## Frozen baseline review

The 2026-09-12 replay of the three frozen `ab371b6` rooms (30 JSONL files)
produced the following confirmation-loss matrix:

```text
C1_CONFIRM_NOT_CREATED:        0
C2_CONFIRM_NOT_DISPATCHED:     0
C3_CONFIRM_QUEUE_LATE:         0
C4_CONFIRM_HTTP_LATE:         22
C5_TIMELY_RESPONSE_NOT_AUTHORIZED: 0
PROTOCOL_PHASE_UNOBSERVABLE:   28
UNRESOLVED:                     4
```

The 22 C4 records contain 20 pure HTTP-response-late cases and 2 cases with a
429 plus 500 ms backoff. Seventeen have an explicit resolver response after
the exact deadline, seventeen have a physical attempt with negative response
deadline-left, and twelve have both boundaries; the union covers all 22.
No C4 record was sent after its deadline or obtained a late throttle grant.
The four unresolved records contain retry/backoff evidence, but every
recorded response remained before the exact deadline, so they remain
`UNRESOLVED`.

This evidence authorizes no runtime StateDemand, rate, sleep, retry, or submit
margin change in this change. The next implementation should be a separate
transport/scheduling investigation that correlates each logical request with
gateway/server timing and trace data. The 28 protocol-phase gaps and 5 legacy
identity windows remain separate protocol follow-ups. The baseline remains
diagnostic only and is excluded from any later fresh acceptance denominator.
