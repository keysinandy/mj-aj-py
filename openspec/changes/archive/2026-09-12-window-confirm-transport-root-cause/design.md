## Context

The frozen `ab371b6` baseline contains 22 authoritative confirmation windows
whose response completed after a recorded window boundary. The existing
diagnostic report can link a logical request to physical attempts and exposes
queue, HTTP, retry, and deadline-left measurements, but it cannot establish
whether the delay was caused by the client throttle, the client HTTP stack, a
gateway/server response, or a retry/backoff path.

The next safe step is evidence collection and offline correlation. The change
must not tune the shared 15/s limiter, ordinary sleep, retry policy, or action
margin while the source of delay is still ambiguous.

## Goals / Non-Goals

**Goals:**

- Make one transport chain reconstructible for every linked
  `WINDOW_CONFIRM` logical request and physical attempt.
- Preserve additive, secret-free timing and trace fields at the request,
  attempt, response-header, response-body, retry, and deadline boundaries.
- Join optional gateway/server timing records using explicit request or trace
  identifiers and report missing or clock-incompatible evidence conservatively.
- Produce mutually exclusive transport evidence classes for queue delay,
  client HTTP wait, gateway/server late response, retry/backoff contribution,
  and unresolved attribution.
- Replay the frozen baseline and provide a decision-ready report for a later,
  separately proposed rate/retry change.

**Non-Goals:**

- Changing state rate, throttle priority, ordinary sleep, retry/backoff,
  `SUBMIT_EPS`, action retry, or BOT strategy.
- Inferring server processing time from client latency alone.
- Joining external records by nearest timestamp, sequence, tile, or phase when
  no explicit correlation id exists.
- Treating a 429/502 count as a tuning mandate or as a functional window loss
  without a linked deadline boundary.
- Running fresh online acceptance rooms before the evidence implementation is
  reviewed and frozen.

## Decisions

### 1. Keep logical request identity stable across physical attempts

Every state logical request remains identified by `logical_request_id`; each
physical attempt adds an `attempt_index`. The record also carries request kind,
reason mask, requested sequence, and the WindowAttemptKey when applicable.
This lets a 429 followed by a 200 remain one request chain and prevents a
successor or retry from being mistaken for a duplicate action.

Alternative considered: identify attempts only by `(gid, phase, timestamp)`.
Rejected because concurrent windows and successor requests make timestamp
proximity ambiguous.

### 2. Preserve monotonic local timing and epoch correlation separately

Queue, HTTP, header, body, and backoff durations use monotonic-derived values.
Epoch timestamps are retained only for cross-process correlation. The report
must expose clock source and synchronization quality for external records; it
must not calculate an exact server deadline from unsynchronized clocks.

Alternative considered: convert every timestamp to wall-clock time and compare
directly. Rejected because wall-clock adjustments can manufacture or hide
deadline misses.

### 3. Use explicit external-log joins only

The analyzer accepts optional gateway/server timing rows keyed by
`logical_request_id`, `server_trace_id`, or an explicit documented alias. A
join is valid only when the key is unique and the source declares compatible
clock/timing semantics. Missing trace ids, duplicate matches, and clock
uncertainty remain visible as `UNRESOLVED` evidence.

Alternative considered: correlate by response sequence or nearest event time.
Rejected because `/state seq=0` responses can repeat watermarks and clock
domains may differ.

### 4. Separate observed transport facts from tuning conclusions

The report first records facts: throttle grant time, queue duration, request
start, headers received, body finished, status, retry-after, backoff, and
deadline-left. It then assigns an evidence class only when the boundary is
proven. A C4 count alone cannot change 15/s or retry settings; any tuning is a
separate change with a declared comparison plan.

### 5. Preserve lifecycle and safety precedence

Transport diagnostics are attached to canonical window resolutions and cannot
overwrite `SUCCESS`, `STRATEGY_PASS`, `RULE_PREEMPTED`, POST rejection, POST
uncertain, or an existing decision/submit attribution. This change adds no
action retry path and does not alter the existing 409/uncertain re-anchor rule.

## Risks / Trade-offs

- [Server logs are unavailable] -> Report the client-side boundary and mark
  the server/gateway split unresolved; do not claim server fault.
- [Trace ids are missing in old rooms] -> Preserve legacy report compatibility
  and use old data only for weak transport contribution analysis.
- [Clock domains are not synchronized] -> Compare durations and interval
  ordering only; mark cross-system deadline attribution unknown.
- [Additional recorder fields increase log volume] -> Keep fields additive,
  bounded, and secret-free; do not record URLs, tokens, bodies, or full
  snapshots in the transport chain.
- [A late response has multiple contributors] -> Record all measured
  contributors but select one primary evidence class by documented precedence;
  never double-count a window loss.

## Migration Plan

1. Add or verify additive client transport fields and offline fixtures.
2. Extend the acceptance analyzer with explicit external-log input and joins.
3. Replay the frozen `ab371b6` report and compare category counts and canonical
   outcomes with the existing baseline.
4. Review the resulting evidence matrix and decide whether a separate
   `window-confirm-scheduling` or `state-rate-retry-tuning` change is justified.
5. If a runtime transport change is proposed, freeze a new clean commit and
   start a fresh acceptance denominator; do not reuse the diagnostic rooms.

Rollback is limited to disabling the new diagnostic fields/input. No runtime
decision or action behavior is changed by this change.

## Open Questions

- Which gateway/server log source exposes `logical_request_id` or
  `server_trace_id` for the 22 baseline windows?
- Does the server trace include request arrival, handler start, response
  headers, and response body completion, or only a total latency?
- Are client and server epoch clocks synchronized closely enough for boundary
  comparisons, or must the report remain duration-only?
- If server-side correlation is unavailable, is adding an opaque trace header
  within the transport contract acceptable in a later protocol change?

## Baseline decision

The frozen replay confirms that all 26 transport candidates are client-only
evidence: the 22 C4 records remain `HTTP_RESPONSE_LATE` and the 4 retry-only
records remain `RETRY_BACKOFF_CONTRIBUTED` without a proven missed-window
boundary. None of the 25,748 physical state attempts in the baseline carries a
`server_trace_id`, and no separate gateway/server timing file is available in
the workspace. Therefore this change does not claim a server or gateway root
cause and does not justify a rate/retry tuning change or a fresh online room.
The new correlation headers and report input are ready for the next controlled
run; a tuning proposal can be opened only after that run supplies external
timing evidence.
