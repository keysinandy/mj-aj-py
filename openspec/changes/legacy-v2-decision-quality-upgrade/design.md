## Architecture

### Public input and state

Implement `LegacyDecisionFeatures` from `PublicDecisionContext` (or an explicitly validated projection from Game/Mirror) with: hero seat/hand, per-seat public discard and meld sequences, pending action, public scoring flags, live wall count when available, stage, visible counts, legal actions. Reject unavailable or inconsistent fields; never pass full Game objects into opponent belief, rollout or cache keys.

`OpponentBelief.evaluate(context)` returns per-opponent calibrated tenpai probability, legal response probabilities for a proposed discard, optional tile-danger vector and uncertainty/coverage. Initial implementation uses explicit public heuristic plus offline calibration. Treat `mj/models/opponent_policy.py` as a reusable action likelihood provider, not a calibrated danger model by itself. Cache keys must include full public decision hash, model version and score rules.

`DangerEstimator.evaluate(context, legal_tiles, beliefs)` returns per-tile per-opponent probability of an *actual loss event*, settlement-conditional loss magnitude, joint-event adjusted expected loss and confidence. Distinguish CHI/PONG tempo harm from HU/ron settlement; do not sum mutually exclusive opponent wins without multi-winner rule semantics.

### Common value semantics

`ScoreEV(action)` uses terminal hero net settlement points. Standardize convention: positive is hero gain, negative loss; absolute score bounds based on game rules. Expose `win_ev`, `loss_ev`, `continuation_ev`, `tempo_ev`, `total_ev`, `model_version`, `coverage`, `uncertainty`, `complete`. Values are exclusive components by contract: continuation must exclude events already counted by win/loss. Existing `ScoreValue` and scoring implementation are truth source for exact settlement; approximations only for probabilities. When value models cannot produce comparable units, do not rank across them.

`CompletionEstimator` models available *hero* future self-draw opportunities based on live wall, seat order, interrupts and opponent survival; returns probability of reaching next hero draw, calibrated horizon distribution, and probability of completion before termination. Uniform unseen tile availability is conditional on obtaining a draw, not unconditional winning probability. `P(next_draw_win | reached next draw)=1` does not imply `P(reached next draw)=1`.

### Decision stages

A. `legacy_belief.py`, `legacy_danger.py`: shadow all legal discard danger and predicted eventual settlement, compare against observable outcomes with no hidden info at inference; use stage-aware calibrated risk. Optional candidate tie-break or EV rerank only after calibration and score acceptance.

B. `legacy_completion.py`, `legacy_value.py`: retain candidate generation and weighted two-ply. Rerank at most the bounded top frontier, with consistent net settlement units. Gate overrides by confidence and minimum estimated Q-margin; preserve former winner when ambiguous.

C. `legacy_joint_reaction.py`: compare PASS with CHI/PONG/KONG and their best legal child discard on a common horizon; v1 rejected candidate may enter challenger pool only under explicit bounded rescue gate; freeze/legality and claims from game legal_actions remain truth source. Integrate `legacy_kong.public_score_continuation` with shared score units and avoid double-counting immediate bonus. HU window replaces binary risk knockout with calibrated next-draw survival and opportunity loss, retaining exact guaranteed-conditional-win fast path and baseline fallback.

D. `legacy_budget.py`: global per-decision context budget, dynamic escalation only for candidate overlap, cancellation/safe partial proofs and stable deterministic fallback. `legacy_hand_plan.py`: optional plan state for ordinary/chiitoi/luxury, hysteresis on route switching, state reset on new round, public trajectory only; plans are soft values, never irrevocable locks.

### Integration contract

- Feature gates: `belief_shadow_enabled`, `danger_rerank_enabled`, `score_ev_rerank_enabled`, `completion_enabled`, `joint_reaction_enabled`, `survival_hu_enabled`, `adaptive_budget_enabled`, `hand_plan_enabled`. Defaults false.
- Add gates and versions to profile fingerprint and strategy snapshots; decision audits include `decision_scope`, `old_selected`, `new_selected`, `override_reason`, `risk_ev`, `score_ev`, `calibration_id`, `coverage`, `confidence`, `fallback_reason`, `elapsed_ms`; hide any concealed-state data.
- Use full reproducible feature extraction for local and online mirrors, with equality tests on equivalent public inputs.
- Guard `Stage A` early exit: no winner certificate based on legacy comparator if final override criteria can change ranking. Compute new metrics only on admitted bounded frontier; if a necessary root was pruned, either provide mathematically valid admission rescue or abstain to baseline.
- Offline teacher must declare complete/horizon/value model. No mixing Stage-A partial with Stage-B output for a shared comparison, no silent labels from fallback.

## Evaluation and release

Baseline frozen from main before implementation. Phase A: log-loss/Brier + reliability by game phase, number of opponent melds and claimed action type, no future leakage. Phase B/C: replay golden/anti-overfit, score conservation under settlement rules, seed-paired full-game evaluation vs baseline with confidence interval on mean hero score delta and opponent mix; report win rate, mean winning score, mean loss, net score, override count/regret, phase buckets, fallback. Phase D: p50/p95/p99/max across discard/reaction/kong/hu and end-to-end; native parity; default budgets must not regress beyond recorded gates. Suggested provisional gates: discard p95 <= baseline*1.10; p99 <= baseline*1.15; fallback <= baseline+1 percentage point; actual release requires pre-registered score-test sample, CI and non-regression criteria, not just point-estimate improvement. Separate shadow, canary, expanded A/B and rollback evidence. No phase default-on without all gates.

## Files and tests

Suggested isolated modules `mj/legacy_belief.py`, `mj/legacy_danger.py`, `mj/legacy_completion.py`, `mj/legacy_value.py`, `mj/legacy_joint_reaction.py`, `mj/legacy_budget.py`, `mj/legacy_hand_plan.py`. Tests: public info invariance, deterministic fixed seeds, no hidden-state access, legality/freeze, terminal score parity, multi-winner danger aggregation, next-draw survival consistency, rescue from v1 reject, global timeout, confidence-margin ties, rollout shadow no behavior drift, complete-vs-partial parity, configuration fingerprint.
