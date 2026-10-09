## 1. Freeze baseline / instrumentation
- [x] 1.1 Record baseline SHA, legacyV2 profile/fingerprints, scoring flags and opponent versions
- [x] 1.2 Add stage-specific latency, fallback, override and terminal-score telemetry without leaking hidden state
- [x] 1.3 Create seed-paired end-to-end eval harness and golden public-state replay suite

## 2. Phase A — belief and danger (shadow first)
- [x] 2.1 Define public-only feature projection + cache identity for Game and Mirror
- [x] 2.2 Add opponent tenpai/response feature estimator (heuristic baseline)
- [x] 2.3 Add tile-specific expected loss and multi-winner aggregation
- [x] 2.4 Offline calibration set, Brier/log loss/reliability per phase and opponents' meld buckets
- [x] 2.5 Shadow decision audit and no-change A/B
- [ ] 2.6 Gate confidence-bounded risk re-ranking; paired score and latency release test

## 3. Phase B — completion and common score EV
- [x] 3.1 Compute hero draw opportunity / survival horizon probability
- [x] 3.2 Define additive nonoverlapping ScoreEV components in hero net points
- [x] 3.3 Reuse exact settle/scoring and test value sign/unit/invariants
- [x] 3.4 Bounded top-frontier EV re-ranking, margin threshold and abstention
- [x] 3.5 Prevent invalid Stage A winner certificate after re-ranking
- [ ] 3.6 Independent paired score + performance release tests

## 4. Phase C — joint reaction and HU
- [x] 4.1 Enumerate PASS/CHI/PONG/KONG legal roots plus legal post-claim discards
- [x] 4.2 Add bounded v1-reject rescue and common tempo-aware score unit
- [x] 4.3 Preserve KONG structure gates, exact scoring and timeout transaction
- [x] 4.4 Replace binary delay penalty with conditional survival EV (experimental flag)
- [x] 4.5 Prove guaranteed conditional next-draw win remains conditional on reaching the draw
- [ ] 4.6 Paired reaction regret, HU timing, score and p99 acceptance

## 5. Phase D — budget and strategy route
- [x] 5.1 Single decision-wide deadline shared across search branches
- [x] 5.2 Escalate work only for overlapping candidates; bounded certificate/fallback
- [x] 5.3 Optional hand-route persistence with reset and hysteresis
- [x] 5.4 Compare with route feature disabled, avoid default enabling BigHandIntent
- [ ] 5.5 Long-tail benchmark, fallback audit, fixed-seed deterministic replay

## 6. Safety and release
- [x] 6.1 Unit/property tests for hidden-state invariance and mirror parity
- [x] 6.2 Verify all legal actions, frozen discard, score conservation, multi-winner rules
- [x] 6.3 Verify Rust/Python ranking/partial parity where applicable
- [ ] 6.4 Publish score CI, effect sizes, stage buckets, sample counts, p50/p95/p99/max
- [ ] 6.5 Shadow -> canary -> paired A/B -> default; independent switch rollback per phase
- [x] 6.6 Run openspec validation and diff checks before any release

## 7. Continue optimization after the initial acceptance evidence
- [x] 7.1 Add calibrated, public-only nonterminal continuation values with distinct horizon, independent-seed uncertainty, held-out coverage and an opt-in flag
- [x] 7.2 Align overridden CHI/PONG estimates with their executed child discard; invalidate stale/changed plans and preserve atomic timeout fallback
- [x] 7.3 Skip forced/inactive inference, reuse decision-local estimates, bound the consumed horizon and weight multiplier uncertainty by its event probability
- [x] 7.4 Publish frozen-protocol before/after and independent B/C/shadow score, override, abstention and latency evidence without treating uncertain gains as release approval
- [x] 7.5 Run the relevant legacy/reaction/HU/shape/kernel regressions and strict OpenSpec validation (271 tests / four subtests passed; an additional historical-log assertion was unavailable because its local JSONL is absent)

Optimization evidence: `runs/legacy_v2_quality_optimization/summary.md` and
`summary.json`. Online metadata/fingerprint caching preserves the frozen
production profile fingerprints. Independent B had zero overrides; C's
positive point estimate has a CI crossing zero and failed score/latency gates.
The release tasks above remain pending and all quality flags remain off.
