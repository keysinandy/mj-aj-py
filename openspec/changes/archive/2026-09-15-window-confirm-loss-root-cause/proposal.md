## Why

The frozen `ab371b6` online baseline completed the observability and safety
loop, but its 473 eligible windows still contain 54 canonical `CONFIRM`
losses. The current canonical report intentionally treats these as one class;
the next repair must distinguish a missing demand, a missing physical request,
queue delay, HTTP/retry delay, resolver rejection, and a protocol phase that was
never observable before terminal timeout.

This is now an evidence problem rather than a room-count problem. The baseline
already has 30 clean `StateDemand` endings, zero hard safety failures, zero
duplicate old-action POSTs, and linked 409/uncertain recovery chains. Running
more rooms without a mutual root-cause matrix would not identify the next safe
code change.

## What Changes

- Add a per-`WindowAttemptKey` confirmation evidence matrix with mutually
  exclusive C1--C5/protocol-unobservable categories.
- Link source event, SSE wake, demand snapshot, physical `/state` attempts,
  throttle grant, HTTP/retry timing, resolver outcome, and terminal event in the
  matrix.
- Treat missing confirmation evidence conservatively: do not call it a client
  state-machine defect when the source discard and terminal timeout arrived in
  one response or the phase was never exposed authoritatively.
- Fix only deterministic client defects proven by C1, C2, or C5 evidence, and
  add fake-clock/replay regressions for each fix.
- Keep state rate, sleep policy, submit margin, action retry behavior, and
  settlement protocol outside this change.
- Require a new clean commit and a fresh 3--5 room acceptance denominator after
  deterministic fixes; the `ab371b6` rooms remain diagnostic baseline only.

## Capabilities

### New Capabilities

- `window-confirm-diagnostics`: Produce a reproducible confirmation-stage
  evidence matrix and root-cause attribution for every authoritative window.

### Modified Capabilities

None. The prior `online-room-window-attribution` change remains the lifecycle
and safety baseline; this change adds diagnostic resolution on top of it.

## Impact

- `scripts/window_acceptance.py` and its tests gain evidence linking and matrix
  output.
- `mj/platform/bot_client.py` may receive narrowly scoped lifecycle fixes only
  after the matrix proves C1/C2/C5.
- `tests/test_window_acceptance.py`, `tests/test_window_recovery.py`, and
  replay/fake-clock fixtures gain regression coverage.
- `local/acceptance/` baseline artifacts are read-only diagnostic evidence and
  are not part of the final acceptance denominator.
