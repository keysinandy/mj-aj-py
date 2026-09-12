## Why

The frozen `ab371b6` baseline has 22 confirmation losses with evidence that the
authoritative response completed after the window boundary. The current client
report proves lateness but cannot yet distinguish gateway/server processing,
HTTP header wait, body read, retry/backoff, or client-side scheduling from one
logical request. We need that attribution before changing the 15/s limiter or
retry policy.

## What Changes

- Preserve a complete transport chain for every linked `WINDOW_CONFIRM` logical
  request and physical attempt.
- Correlate logical request ids with HTTP timing boundaries, response status,
  retry-after/backoff, deadline-left values, and any server/gateway trace id.
- Extend the acceptance analyzer to classify transport contribution as queue,
  client HTTP wait, retry/backoff, server/gateway late response, or unknown
  without converting evidence into a scheduling conclusion prematurely.
- Add an offline join/replay report for the frozen 22 C4 and 4 unresolved
  baseline windows, including missing-correlation reasons.
- Define the evidence required before opening a separate rate/retry tuning
  change; do not change rate, sleep, retry, or submit margin here.

## Capabilities

### New Capabilities

- `window-confirm-transport-diagnostics`: Correlate logical confirmation
  requests with physical HTTP attempts and external gateway/server timing, and
  produce a conservative transport root-cause report.

### Modified Capabilities

None. The existing confirmation lifecycle and diagnostic matrix remain the
source of window identity and canonical outcome; this change adds transport
evidence resolution on top of them.

## Impact

- `mj/platform/api.py`, `mj/platform/recorder.py`, and
  `mj/platform/bot_client.py` may need additive transport/trace fields only;
  no scheduling behavior changes are in scope.
- `scripts/window_acceptance.py` gains transport-chain joining and report
  classifications.
- Tests gain fixtures for 200-late, 429/backoff, gateway error, queue delay,
  missing trace, and externally supplied server timing records.
- `local/acceptance/` remains diagnostic evidence and is excluded from fresh
  online acceptance denominators.
