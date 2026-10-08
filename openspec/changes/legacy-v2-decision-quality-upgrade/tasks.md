## 1. Freeze baseline / instrumentation
- [ ] 1.1 Record baseline SHA, legacyV2 profile/fingerprints, scoring flags and opponent versions
- [ ] 1.2 Add stage-specific latency, fallback, override and terminal-score telemetry without leaking hidden state
- [ ] 1.3 Create seed-paired end-to-end eval harness and golden public-state replay suite

## 2. Phase A — belief and danger (shadow first)
- [ ] 2.1 Define public-only feature projection + cache identity for Game and Mirror
- [ ] 2.2 Add opponent tenpai/response feature estimator (heuristic baseline)
- [ ] 2.3 Add tile-specific expected loss and multi-winner aggregation
- [ ] 2.4 Offline calibration set, Brier/log loss/reliability per phase and opponents' meld buckets
- [ ] 2.5 Shadow decision audit and no-change A/B
- [ ] 2.6 Gate confidence-bounded risk re-ranking; paired score and latency release test

## 3. Phase B — completion and common score EV
- [ ] 3.1 Compute hero draw opportunity / survival horizon probability
- [ ] 3.2 Define additive nonoverlapping ScoreEV components in hero net points
- [ ] 3.3 Reuse exact settle/scoring and test value sign/unit/invariants
- [ ] 3.4 Bounded top-frontier EV re-ranking, margin threshold and abstention
- [ ] 3.5 Prevent invalid Stage A winner certificate after re-ranking
- [ ] 3.6 Independent paired score + performance release tests

## 4. Phase C — joint reaction and HU
- [ ] 4.1 Enumerate PASS/CHI/PONG/KONG legal roots plus legal post-claim discards
- [ ] 4.2 Add bounded v1-reject rescue and common tempo-aware score unit
- [ ] 4.3 Preserve KONG structure gates, exact scoring and timeout transaction
- [ ] 4.4 Replace binary delay penalty with conditional survival EV (experimental flag)
- [ ] 4.5 Prove guaranteed conditional next-draw win remains conditional on reaching the draw
- [ ] 4.6 Paired reaction regret, HU timing, score and p99 acceptance

## 5. Phase D — budget and strategy route
- [ ] 5.1 Single decision-wide deadline shared across search branches
- [ ] 5.2 Escalate work only for overlapping candidates; bounded certificate/fallback
- [ ] 5.3 Optional hand-route persistence with reset and hysteresis
- [ ] 5.4 Compare with route feature disabled, avoid default enabling BigHandIntent
- [ ] 5.5 Long-tail benchmark, fallback audit, fixed-seed deterministic replay

## 6. Safety and release
- [ ] 6.1 Unit/property tests for hidden-state invariance and mirror parity
- [ ] 6.2 Verify all legal actions, frozen discard, score conservation, multi-winner rules
- [ ] 6.3 Verify Rust/Python ranking/partial parity where applicable
- [ ] 6.4 Publish score CI, effect sizes, stage buckets, sample counts, p50/p95/p99/max
- [ ] 6.5 Shadow -> canary -> paired A/B -> default; independent switch rollback per phase
- [ ] 6.6 Run openspec validation and diff checks before any release
