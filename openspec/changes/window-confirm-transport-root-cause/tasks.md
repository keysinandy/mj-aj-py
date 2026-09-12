## 1. Evidence contract

- [x] 1.1 Audit the existing API, recorder, and bot-client transport fields and document the additive logical-request, physical-attempt, trace, clock, and deadline schema.
- [x] 1.2 Define the external gateway/server timing input format, supported correlation keys, clock metadata, and explicit missing/duplicate-match outcomes.

## 2. Client transport evidence

- [x] 2.1 Preserve logical request id, request kind, reason metadata, attempt index, status, retry-after/backoff, and response sequence for every linked state request.
- [x] 2.2 Preserve monotonic-derived throttle, request-start, headers-received, body-finished, HTTP, and deadline-left boundaries without recording secrets, bodies, or full snapshots.
- [x] 2.3 Add recorder/API tests proving 429/502 retries stay in one logical chain and action POSTs mark throttle as `not_applicable` when appropriate.

## 3. External correlation and classification

- [x] 3.1 Add an acceptance input path for optional gateway/server timing rows and reject joins without a unique explicit correlation key.
- [x] 3.2 Record external source, clock domain, synchronization quality, missing matches, duplicate matches, and incompatible-clock outcomes.
- [x] 3.3 Implement mutually exclusive `QUEUE_LATE`, `HTTP_RESPONSE_LATE`, `RETRY_BACKOFF_CONTRIBUTED`, `SERVER_GATEWAY_LATE`, `CLIENT_TRANSPORT_LATE`, and `UNRESOLVED` evidence classes.
- [x] 3.4 Keep transport diagnostics adjacent to canonical window resolution without changing lifecycle precedence, 409/uncertain recovery, or action retry behavior.

## 4. Offline validation and baseline replay

- [x] 4.1 Add fixtures for timely 200, HTTP-late 200, 429/backoff, 502/gateway error, queue-late, missing trace, duplicate external match, and unsynchronized clocks.
- [x] 4.2 Replay the frozen `ab371b6` three-room baseline and verify all 22 C4 and 4 unresolved candidates retain conservative classifications and canonical outcomes.
- [x] 4.3 Run the full test suite, OpenSpec validation, and diff check; verify no rate, sleep, retry, margin, or strategy behavior changed.

## 5. Decision gate

- [x] 5.1 Publish a per-window transport report with primary class, secondary contributors, correlation quality, and unresolved evidence.
- [x] 5.2 Decide whether a separate `window-confirm-scheduling` or `state-rate-retry-tuning` change is justified; do not start fresh online rooms or tune parameters in this change.
