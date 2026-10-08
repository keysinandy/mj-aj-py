# legacyV2 decision quality

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
