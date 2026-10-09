# legacyV2 HU discard-delay margin

Online `legacyV2` now defaults to a minimum public next-draw reward multiple of
`1.10` before it may pass on an available HU to wait for a piao or baotou
discard. Immediate HU remains available, and an incomplete future evaluation
does not apply the new hurdle. Self-KONG replacement decisions are unchanged.
The rule reuses the reward values already computed by the reaction evaluator;
it adds no search or probability inference.

The low-level `LegacyReactionProfile.v2_online()` factory keeps its frozen
`1.0` value for controls and offline comparisons. The public online `legacyV2`
action path, `make_decide`, and clientd bot/match/tournament configuration use
`1.10` when the option is omitted. To restore the previous behavior, pass
`hu_discard_delay_min_gain_ratio=1.0` to `choose_action` or `make_decide`, or
set the same key to `1.0` in a clientd bot configuration.

## Acceptance evidence

The preregistered independent score comparison used 30,720 paired source
seeds (61,440 matches, seeds 4,600,000–4,630,719). The candidate gained
`+0.057389` points per match, with a 95% paired bootstrap interval of
`[+0.010938, +0.102604]`, and 169 HU-margin overrides. This passes the declared
positive-score gate against the frozen local opponents. It does not establish
the same gain against external platform opponents.

The separate public `make_decide -> choose_action` runtime benchmark compared
explicit `1.10` with `1.0` over three batches of 200 matches per arm. All
declared performance gates passed: median batch elapsed time changed `+0.115%`,
and discard, reaction, and HU p95/p99 latency stayed within their limits, with
no increase in fallback rate. This explicit-1.10 path is the same profile now
selected by the omitted online default.

Full score, runtime, and audit artifacts are recorded under
`runs/hu_delay_20261009/` in the experiment workspace. The candidate reaction
fingerprint is `0ca5ac2cb6570b82`; the frozen control fingerprint is
`46534f7c4504d3ab`.
