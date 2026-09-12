## 1. Timeline and identity facts

- [x] 1.1 Add Recorder timeline/authorization/terminal entries and stable field helpers while preserving old JSONL method signatures.
- [x] 1.2 Extend BotClient window confirmation, decision and action paths with WindowAttemptKey, logical request id, attempt index, stage timestamps, authorization snapshot sequence, exact deadline and identity provenance.
- [x] 1.3 Extend API/action diagnostics with server trace id when supplied, explicit headers-received/body-finished boundaries for HTTPError, and monotonic throttle fields without fabricating non-applicable values.
- [x] 1.4 Do not create an authorization/loss candidate for a pass-only response; persist authorization only after a non-PASS legal action is established.

## 2. Canonical lifecycle and safe recovery

- [x] 2.1 Add per-window lifecycle bookkeeping for AUTHORIZED, DECIDING, PASS/non-pass, POST result and terminal precedence without changing strategy or action retry behavior.
- [x] 2.2 Make 409 and uncertain action records link to authorization/decision evidence, mark the WindowAttemptKey attempted, issue only RESYNC, and record reconciliation outcome without reposting.
- [x] 2.3 Ensure `response_peng -> response_chi` pending/terminal transitions, PASS phase closure, and success precedence cannot reactivate a closed attempt or emit duplicate claim misses.

## 3. Acceptance resolver and layered report

- [x] 3.1 Implement canonical per-WindowAttemptKey resolution with mutually exclusive outcome/loss-stage precedence and raw/false/ canonical claim-miss counters.
- [x] 3.2 Add authoritative/legacy identity coverage, identity origin, first-seen source and `identity_unverifiable` handling; block strong window completeness when legacy eligible windows remain.
- [x] 3.3 Add 409 chain, physical retry/queue/backoff contribution, gap reason and decision-impact classification, plus demand terminal health to `scripts/window_acceptance.py`.
- [x] 3.4 Emit independent transport/window/game statuses and fixed acceptance sections, retaining protocol-skipped settlement as a game-layer result only.
- [x] 3.5 Keep normal action 409 chains separate from window 409 chains while reporting the all-action total and uncertain recovery chains.

## 4. Offline regression coverage

- [x] 4.1 Add resolver tests for same-window peng-to-chi pending, phase advancement, PASS-only phase closure, opponent preemption and success-over-stale-miss.
- [x] 4.2 Add decision/submit attribution tests for confirm loss, no decision, decision deadline overrun, submit abandon and identity-unverifiable records.
- [x] 4.3 Add 409/uncertain safe recovery tests, including authorization/deadline evidence and zero duplicate old-action POSTs.
- [x] 4.4 Add demand, gap, identity-origin, HTTPError boundary and acceptance fallback tests; run focused tests and the full pytest suite.

## 5. Frozen online acceptance

- [x] 5.1 Add or document run-manifest capture for clean commit, fixed BOT/15/s/SSE+incremental command and acceptance script version without secrets.
- [ ] 5.2 Run 3--5 serial independent rooms with 10 games each on one clean commit and save per-room reports.
- [ ] 5.3 Review hard-fail checks and cross-room aggregate; report transport/window/game completeness separately and leave OpenSpec 5.4 pending until the gate is satisfied.
