# legacyV2 decision quality experiment

Frozen baseline: `991fd0efad376be0c59cca440a8a405a2589cf4c`. Production
discard fingerprint: `1215a0047e95ebe8`; reaction: `46534f7c4504d3ab`.
The baseline manifest records rules, kernel and opponent versions. All new
flags default to false. Existing `legacyV2` defaults and evaluator identities
retain their behavior and fingerprints.

The default online discard/reaction factories reuse immutable profiles in
bounded metadata caches, and cache their fingerprints. Search results and
budgets remain decision-local. Audit payloads remain independently mutable
copies. This configuration optimization is active without enabling new
quality policy flags; the production fingerprints are unchanged.

`LegacyQualityProfile` is a separate versioned profile; pass it to
`choose_action(..., quality_profile=profile, quality_calibration=model)`.
`VerifiedCalibration` copies and freezes a verified artifact before inference.
Pass the same profile to `strategy_snapshot(..., quality_profile=profile)` to
include its flags and fingerprint in the configuration snapshot. Each decision
has a `quality` audit, also preserved by `decision_audit`.

## Public information and value semantics

`LegacyDecisionFeatures` accepts only immutable public contexts, validates
visible material, and normalizes equivalent Game/Mirror phase names. Identity
contains public history, hero hand, rules and model/calibration versions. The
inference modules never retain the source Game, opponent hands or wall order.

Current game rules permit only one self draw winner. Immediate ron danger is
therefore exactly zero. Opponent estimates model tenpai, actual next-draw HU,
CHI/PONG/KONG responses and their conditional settlement multipliers.
Calibration labels may use concealed state **offline**; training, validation
and score acceptance seeds are disjoint. Reliability, Brier and log loss are
reported by phase, meld count, tile category and response target.

Score comparisons use hero net points: positive gain, negative loss. The
initial bounded value horizon ends at the next hero draw, with zero value for
a surviving nonterminal tail. Opponent HU events consume surviving probability
before hero HU; response branches alter seat order and next-draw opportunity.
This horizon is explicitly recorded; it is an estimate rather than an exact
full-game value. Independent full-game score tests determine release eligibility.
Conditional multiplier uncertainty and probability uncertainty are included.
Low event/sample coverage, overlapping intervals, missing calibration,
incompatible horizons, frontier pruning or partial Stage A metrics abstain to
the baseline. Stage A certificates apply only to the frozen comparator.

The v2 experimental profile adds `continuation_ev_enabled` (default false;
enabled by the B/C/combined presets). Its horizon is
`next_hero_draw_then_calibrated_terminal_tail`: exact legal next-draw HU value
and opponent losses before that opportunity remain separate; surviving,
nonwinning draws receive a calibrated terminal tail in hero net points.
Offline labels follow frozen legacyV2 and exclude first-draw HU, hands that
end before the draw, and roots whose hero hand changes through an intervening
claim. Buckets include shanten, live ukeire, remaining draw opportunities,
meld count, white count, dealer, scoring flags and multiplier chains. They
are approximate state values, not exact Q values. Unsupported cells abstain.
The table requires at least 32 independent training seeds and eight held-out
seeds per cell; its interval includes training standard error and held-out
bias. Labels are base-normalized and scaled back to room points at inference.
Calibration identity includes the frozen discard/reaction policy fingerprints.
Older probability-only artifacts cannot authorize tail overrides.
Without a tail model, non-tenpai joint comparisons and discard score overrides
abstain explicitly instead of ranking offensive progress using loss alone.

Joint reaction enumerates the legal PASS/CHI/PONG/KONG roots and all legal
post-claim discards. A v1 reject can enter a same-or-better-shanten rescue pool.
The root cap is three; an unproved admission or incomplete candidate group
abstains transactionally. Existing KONG gates remain mandatory, and multiplier
chains are scored once through the existing `ScoreValue` adapter.

Joint search retains minimum-shanten legal child discards and records its
selected child. An overridden CHI/PONG commits that child only after the final
deadline check. The next discard consumes the plan once, requiring matching
public-state hash, round identity, legal action and profile fingerprint.
`QualityDecisionState` can be passed as `quality_state` to callers rebuilding
Game/Mirror projections; persistent Game callers get per-seat state by default.
Changed state, disabled flags, timeouts and new rounds invalidate the plan.
Rebuilt-projection callers must retain this state per round and supply public
`gid`/`round_no`, or clear it when the round changes.
Counterfactual regret evaluation executes the committed child before resuming
the frozen continuation policy.

Forced-action and inactive windows skip inference. Decisions reuse belief
estimates and survival branches; value comparisons only expand the next-draw
horizon they consume. Multiplier uncertainty is weighted by the probability of
the loss event. Reports distinguish quality abstentions, committed discards,
baseline fallback and deadline overshoots.

Optional shared budgets are capped at 50 ms discard, 10 ms reaction and 15 ms
HU/KONG. The same deadline reaches Python checks and native calls. Existing
native Stage A bounds limit escalation to overlapping candidates. These are
cooperative deadlines; actual end-to-end tails and any overshoots are measured.
Decision-wide deadlines and acceptance timings use `time.perf_counter()`;
the Windows `time.monotonic()` implementation on this host has 15.625 ms
resolution and cannot validate a 10 ms window. Clock implementation/resolution
are recorded in each new protocol, and coarse timing cannot pass the release gate.
Hand routes are soft public pair/meld evidence with hysteresis, do not lock
actions, and require a round identity. Callers retain a `HandPlan` across turns;
a new round resets it. BigHandIntent remains disabled.

## Local 2v2 acceptance

From the repository root:

```powershell
& .venv\Scripts\python.exe -m scripts.legacy_v2_quality_acceptance `
  --phase campaign --games 5000 --jobs 2 `
  --output-dir runs/legacy_v2_quality_acceptance
```

The campaign calibrates on 256 training and 128 validation matches, runs 256
shadow matches, then a 64-match canary and 256-match independent paired
test **for each of A, B, C, D and route**, followed by the requested 5000-match
combined experimental comparison. `--phase-games` controls the independent
phase sample size. All matches are local. A 5000-match
phase has 2500 independent seed pairs. Each pair holds seed/dealer fixed and
swaps candidate seats `(0,2)` / `(1,3)` in the second match. Execution order
alternates. After policy divergence, each trajectory follows its own draws.
The baseline in each arm is the frozen production `legacyV2` configuration,
including its existing shape and marginal structure guards.

Protocols are written before each run. JSON reports checkpoint every 50
matches; `.rows.jsonl` retains per-match scores, phase counters, timing samples,
HU timing and up to two counterfactual override audits per match for Phase C.
Counterfactual labels use a cloned offline world and a declared fixed baseline
continuation policy; no concealed state crosses back into online inference.
Mean hero net score and 95% intervals use independent seed pairs as samples.
Latency reports include count, p50/p95/p99/max for discard/reaction/KONG/HU and
end-to-end matches. Undercovered stages cannot pass a latency gate.

The preregistered score gate requires mean >= 0 and lower CI >= -0.10 points;
p95 <= baseline * 1.10, p99 <= baseline * 1.15; fallback rate <= baseline +
one percentage point. Action-changing phases also require observed overrides.
A no-override run is not evidence of a useful upgraded policy. `rollout.json`
records independent phase results, completion state and rollback instructions.
The campaign never automatically changes production defaults. Default rollout
needs review of the completed independent correctness, score and latency
evidence. Each phase can be rolled back by disabling its own flags.

For a probability-only ablation add `--disable-continuation`; use
`--seed-start` to freeze a separate holdout. Training and validation seed lists
remain disjoint from score-test seeds. The optimization evidence lives under
`runs/legacy_v2_quality_optimization/` and records sample sizes and incomplete
release gates explicitly.

The completed optimization summary is
`runs/legacy_v2_quality_optimization/summary.md` (`summary.json` contains the
scope latency and score evidence). The default configuration-path microbench
p95 decreased from 0.042700 ms to 0.005700 ms; this is configuration overhead,
not full decision latency. Independent C completed 512 matches / 256 seed
pairs with mean hero net score +0.024414 and 95% CI [-0.212143, +0.260971].
Its seven overrides were HU decisions; the trial had no reaction overrides.
B completed the same sample size with no overrides. No quality phase is
eligible for default rollout. The validation suite passed 271 tests and four
subtests; one historical replay test lacked its local JSONL input and is
listed explicitly in the summary.

## BigHandIntent +1 Python challenger experiment (2026-10-08)

The user-approved scheme 1 keeps the Rust v5 speed frontier homogeneous and
evaluates only the opt-in parallel +1 challenger through the existing Python
full-future implementation. A complete challenger may override a speed winner
established by complete results or the existing safe partial bounds. Deadlines,
native failures and incomplete challenger results retain the frozen speed
fallback. The combined frontier stays at three roots; Python work uses the
remaining search budget and is recorded in `big_hand_challenger_topup`.
Windows search timing now uses `perf_counter` instead of the 15.625ms-resolution
monotonic clock.

Four coarse points ran 512 games each and the leading two ran independent 2048
games each. Those initial scans always assigned the dealer to the candidate
team and remain exploratory. The corrected independent confirmation balances
dealer and hero seats separately in an eight-case cycle and alternates arm
order. Production opponents are fixed. A paired observation is two games;
2048 games represent 1024 independent source seeds, and score is the average
settlement difference over the two hero seats.

The fair 2048-game confirmation triggered 69 overrides (66 with two whites,
three with three whites). Mean hero score difference was **-0.109863**, 95% CI
**[-0.267090, +0.033691]**. Python completion succeeded in 113/121 attempts;
eight deadline failures fell back. A separate 1024-game ablation against the
same BigHand profile with +1 disabled had mean **-0.197266**, 95% CI
**[-0.434570, -0.005859]**, providing no evidence for a useful +1 upgrade.
Initial hero-white score buckets are fixed before decisions; candidate-arm
maximum-white buckets are descriptive and cannot establish per-event effects.

Single-process interleaved four-bot performance ran 3x200 games per profile.
Discard p95 increased from 12.5066ms to 13.9266ms (+11.35%); median batch
elapsed/game increased 23.13%. Both exceed the 10% gates. The frontier cap is
checked on ordinary weighted-discard evaluations; HU arbitration action counts
are separate. No fallback override or illegal action was observed.

BigHand remains disabled by default. The user condition for Rust migration
(established score benefit with failed performance) was not met. The current
baseline fingerprint is `9a2d4dba9c305668`; the tested conservative profile is
`2ce9fa0e5717d68c`. Historical F1 fingerprints predate the added profile fields.
Module validation passed 94 tests and 16 subtests. Protocols, full confidence
intervals, white buckets, latency, paired seeds and source hashes live in
`runs/bh_scheme1/summary.md` and its JSON evidence. This exploratory confirmation
does not replace the existing 4096-game release requirement; the original
Phase A release performance task remains open.

## Other score experiments — 2026-10-08

Two alternatives were tested after BigHand: same-shanten speed-band/Pareto
discard search, and configurable ordinary same-shanten PONG/CHOW admission.
The paired evaluator uses one hero against three fixed production opponents,
balances hero/dealer independently over 16 combinations, alternates arm order,
and resamples source-seed score differences. It requires the compatible Rust v5
kernel. The historical four-bot speed-band benchmark changed all policies and
reported an always-dealer hero; its positive estimate is not a production-profit
test.

Four discard points ran 256 pairs each. Selected standard/wide bands then ran
1024 pairs each on independent seeds. Standard mean difference was -0.013672,
97.5% CI [-0.542969,+0.520508]; wide mean -0.109375, CI
[-0.652344,+0.421875]. Per-candidate alpha=.025 controls the two-candidate
family error at <=5%. Neither result establishes a benefit. Wider marginal
slack alone produced no settlement changes in its coarse sample.

Five reaction admission points ran 256 pairs each. Lower ratios 1.25/1.10,
optionally with lower absolute gains, had means -0.101563/-0.355469/-0.527344;
stricter ratios 1.75/2.0 gave -0.074219/-0.421875. These are exploratory results;
no candidate was selected for independent confirmation or full performance
acceptance. Admission defaults remain ratio=1.50, PONG minimum=4, CHOW=6.
Baotou/piao thresholds retain their original values. Newly admitted ordinary
claims still require the existing U2/tempo checks; incomplete transactions
return the original admission choice. Nondefault thresholds enter the profile
fingerprint; the default reaction fingerprint remains 46534f7c4504d3ab.

The experiment totals 8704 matches plus a 32-match identical-profile null
control and 8-match performance smoke. Null control had zero settlement
differences and equal claim counts. Performance smoke sample_size=false, so its
timings are not acceptance evidence. Relevant regressions passed 194 tests and
16 subtests. Production defaults stay unchanged; the existing release gates
remain in force. Protocols and complete paired evidence are in
`runs/discard_profile_20261008/summary.md` and
`runs/reaction_admission_20261008/summary.md`.

## Baotou score ties — 2026-10-08

Four configurations ran 512 independent balanced pairs each (4096 matches).
Exact score tie-breaking at the default progress weight and progress weight
1.0 caused no settlement differences. Weight 2.0, with/without score ties,
had mean delta -0.015625, 95% CI [-0.046875,0], one nonzero pair each.
The score tie-break attempted 25/26 windows but produced no overrides in the
paired hero sample. These zero-effect samples do not establish efficacy.

Opt-in tie-breaking keeps tier, White protection and weighted progress fixed,
requires nondecreasing immediate HU mass, examines at most three roots under
a shared 5ms cap, and aborts the entire comparison on timeout. A real audit
fixture verifies local next-draw score improvement; its baotou mass decreases
as legal HU mass increases at equal weighted progress. Local Q improvement
does not certify final score profit. Defaults and fingerprint stay unchanged.
Full evidence: `runs/baotou_score_20261008/summary.md`.

## Chiitoi terminal consistency and redundant KONG — 2026-10-09

Complete natural seven pairs incorrectly returned shanten 0 in Python and
Rust, while the legal win engine accepted HU. Completed pair count >=7 now
returns -1, preserving 13-tile distances. Decomposition uses the shared
formula; ready ukeire excludes impossible fifth copies. Weighted ABI stays V5;
the repaired semantic identity is hangzhou-chiitoi-terminal-v2. Both system
Python and the virtual environment load the same repaired native SHA256
028ab48c59075477b03d8d56dc7781ca9750240bf9caf5ecada7ebeb580d6901.

Against a preserved pre-fix V5 DLL, 1024 balanced pairs gave mean delta
-0.040039, CI [-0.120117,0], 3 changed trajectories and 1 nonzero settlement.
The null control had no trajectory/score changes. This repairs correctness;
the sample does not establish profit. Python/native differential checks passed
3000 shanten, 3000 ukeire, 3000 baotou and 750 piao-mask cases. Native and
source hashes are recorded in runs/chiitoi_terminal_20261008 artifacts.

An independent opt-in redundant self-KONG trial also failed confirmation:
1024-pair screen +0.060547, independent 4096-pair mean -0.005127, CI
[-0.046387,+0.037109]. Existing structure/progress gates remained; the
experimental exception only covered the same fourth tile selected by the
baseline discard. Defaults remain off. No profit-based promotion or holdout
retuning. Full evidence: runs/kong_progress_20261008/summary.md.

## Minimum reward for HU discard delays — accepted optional candidate

The repaired-kernel audit found 66 low-margin delays in 512 matches: an
available HU was declined for about 1%-2% public next-draw reward upside,
conditional on opponents not ending the round first. New explicit reaction
knob hu_discard_delay_min_gain_ratio defaults to 1.0. It only excludes
piao/baotou discard roots below the configured immediate-HU reward multiple;
self-KONG replacement comparison stays intact. Incomplete frontiers do not
apply the new hurdle. It adds no search or belief inference.

Both 1.10/1.25 screens gave +0.164063 in 512 pairs, CI [-0.117188,+0.609375].
Only 1.10 was selected. Independent 4096 pairs gave +0.037842, CI
[-0.054932,+0.132324], 21 overrides and 19 nonzero settlements. No established
profit in that earlier sample. The preregistered final independent 30720-pair
test (61440 matches, seeds 4600000..4630719) gave **+0.057389 points/match**,
95% paired bootstrap CI **[+0.010938,+0.102604]**, 169 overrides and 153 nonzero
settlements. No interim score checks or pooling selected samples. Final CI
lower >0 passes the declared profit gate. The single-hero/three-production-
opponent sample has unique seeds and balanced independent hero/dealer seats;
source/native identity, legal actions and conserved settlements were checked.
This is a modest measured improvement against the local production opponent,
not a measured claim about platform opponents. Candidate reaction fingerprint
0ca5ac2cb6570b82; frozen baseline 46534f7c4504d3ab. Protocols and acceptance:
runs/hu_delay_20261009/summary.md and final_acceptance_audit.json.

The preregistered clean core runtime benchmark (3x200 matches per arm,
single process, alternating arm order, no concurrent agent computation) passed
all gates. HU p95/p99 changed -38.296%/-42.108%, discard -0.053%/+0.224%,
reaction -6.762%/-7.575%, and median batch elapsed -2.409%. HU samples were
805/735 and fallback rates did not increase. The earlier overlapping run is
diagnostic only; use performance_clean.json and performance_summary.md.

Public `make_decide -> choose_action` runtime also passed a separately fixed
3x200 benchmark (seeds 4800000..4800599), run in one process after the score
pool ended. HU p95/p99 changed -43.090%/-43.467%, discard -0.272%/-0.828%,
reaction -2.850%/+2.254%, median batch elapsed +0.115%, with no fallback
increase. HU samples were 786/726. Forced legal actions are counted separately
from incomplete-search fallback. The four matching policies per runtime arm
are not a score test; runtime results are not pooled into the profit sample.

The verified optional integration is now in the worktree. It forwards the
same margin through the public action API, local players, matching and
tournament configs; effective reaction fingerprints match snapshots and
decision records. Main source text matches the tested isolated copy, allowing
only CRLF/LF differences. Default and explicit 1.0 rollback hashes remain
unchanged. The integrated regression run passed 112 tests; one existing
match-runner test failed identically before integration because its fake
Session lacks update_progress and was deselected. Fresh system Python and
virtual-environment processes verified a real legal HU override, conserved
settlement, rollback, and the same repaired V5 binary.

Defaults remain at 1.0. Opt in with
`make_decide("bot", evaluator="legacyV2", hu_discard_delay_min_gain_ratio=1.10)`
or pass the same option to `choose_action`. Clientd bot configs accept
`"hu_discard_delay_min_gain_ratio": 1.10`; an explicit 1.0 restores the frozen
behavior. Invalid thresholds and unsupported strategies are rejected before
player/session creation. No main policy source changed during the score run.
