## 1. Baseline evidence extraction

- [x] 1.1 Add a read-only confirmation-chain extractor that joins source discard, SSE wake, WINDOW_CONFIRM demand, physical state attempts, resolver records, and terminal evidence by exact WindowId and phase.
- [x] 1.2 Preserve missing boundaries and evidence-quality metadata instead of inferring them from nearby records or server timeout rows.
- [x] 1.3 Replay all 30 frozen `ab371b6` game logs and write the diagnostic baseline matrix with per-window evidence and aggregate category counts.

## 2. Mutual root-cause classification

- [x] 2.1 Implement the mutually exclusive C1/C2/C3/C4/C5, `PROTOCOL_PHASE_UNOBSERVABLE`, and `UNRESOLVED` evidence gates.
- [x] 2.2 Add category counts, per-window evidence links, and transport contribution fields to the acceptance report without changing canonical lifecycle outcomes.
- [x] 2.3 Add an explicit baseline/fresh-denominator marker so diagnostic rooms cannot enter a later functional acceptance aggregate.

## 3. Evidence-gated runtime repair

- [x] 3.1 Verify the frozen baseline has no non-zero C1, C2, or C5 pattern requiring a runtime replay or fix; no runtime code is authorized by this evidence.
- [ ] 3.2 Fix only reproduced deterministic lifecycle defects; do not change state rate, sleep, retry/backoff, action retry, or submit margin.
- [ ] 3.3 Add a regression test for each deterministic runtime fix and prove that SUCCESS, STRATEGY_PASS, and RULE_PREEMPTED precedence remains unchanged.

## 4. Offline and online acceptance

- [x] 4.1 Add offline fixtures for same-batch source plus timeout, missing dispatch, late throttle, late HTTP/retry, timely authorization rejection, and missing phase evidence.
- [x] 4.2 Run the analyzer against all frozen baseline logs and assert that no category is forced when its evidence gate is incomplete.
- [ ] 4.3 Run the full test suite, OpenSpec validation, and a diff check before freezing a new commit.
- [ ] 4.4 Run 3--5 serial fresh rooms from the new clean commit only, preserving the fixed BOT/SSE/incremental-state/15-s rate manifest.
- [ ] 4.5 Produce per-room and cross-room reports, then decide the functional window gate separately from transport and game-layer status.
