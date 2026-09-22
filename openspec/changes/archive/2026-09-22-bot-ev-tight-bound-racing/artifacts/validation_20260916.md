# bot-ev-tight-bound-racing validation

Environment: repository worktree, `.venv/bin/python`, 2026-09-16.  The Rust
extension was rebuilt from `rust/` with the self-contained Zig linker available
in the virtual environment.

## Tests

```text
.venv/bin/python -m pytest tests/test_bot_ev_discard.py \
  tests/test_bot_ev_calibration.py \
  tests/test_bot_ev_tight_bound_racing.py -q -p no:cacheprovider
54 passed in 9.74s

.venv/bin/python -m pytest tests/ -q -p no:cacheprovider
522 passed, 9 subtests passed in 47.29s

python3 -m compileall -q mj scripts
git diff --check
```

## Rust rebuild and parity

```text
.venv/bin/python -m pip install -e rust/
Successfully built mj_kernels
Successfully installed mj_kernels-0.1.0

.venv/bin/python scripts/rust_parity.py --n 5 --bench 10
shanten 对拍通过: 150 手
ukeire 对拍通过: 150 手
shanten 加速 29.5x; ukeire 加速 76.5x
全部通过 ✓
```

The focused suite covers root transitions, existing-pong upgrade capacity,
Fast/rollout separation, unequal pair bounds, sparse active rows, failures,
resume deduplication, strict racing metadata, and public-output sanitization.

## Same-machine comparison

Four fixed `Game` contexts with three ordinary candidates each were evaluated
after a warm-up.  The old value is `theoretical_reward_bound(1)` and the new
value is the candidate envelope `fast_upper`.

| metric | old wide bound | new envelope |
| --- | ---: | ---: |
| bound min/max/mean | `1.1333679558887149e+23` | `98304.0` |
| Fast candidates pruned | `0/12` | `0/12` |
| Fast active candidates | `12/12` | `12/12` |
| Fast evaluator seconds, run 1 | `0.2221` | `0.2291` |
| Fast evaluator seconds, run 2 | `0.2221` | `0.2259` |
| paired teacher rollout calls | `36` | `36` |
| paired teacher attempted/valid rows | `6/6` | `6/6` |

This fixture demonstrates the bound reduction but does not claim a speed or
release-gate improvement; the observed pruning rate and rollout count remain
the measured result for this small sample only.

## Local 500-game mixed BOT baseline

Command:

```text
.venv/bin/python scripts/bot_local_baseline.py \
  --games 500 --seed-start 2026091600 \
  --output openspec/changes/bot-ev-tight-bound-racing/artifacts/bot_local_mixed_20260916.json
```

The workload assigns one of each BOT type to every game and rotates seats and
dealer position.  The rollout teacher uses one fixed public-context sample per
decision with its explicitly configured legacy continuation; this continuation
is not a harness fallback.  The all-root shape-v2 benchmark profile uses an
unknown bound mode so every ordinary root candidate remains comparable, with
no substitute action on evaluator incompleteness.

| BOT | score total | score mean | wins | draws | decisions | decision mean ms | decision p95 ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| rollout-teacher | `-2303` | `-4.606` | `9` | `13` | `22616` | `24.2163` | `109.6175` |
| shape-v2 | `-174` | `-0.348` | `120` | `13` | `22679` | `3.5695` | `18.4852` |
| shape-v1 | `1139` | `2.278` | `185` | `13` | `22674` | `4.2328` | `26.4956` |
| legacy | `1338` | `2.676` | `173` | `13` | `22566` | `0.0386` | `0.2091` |

All `500/500` games were valid, all per-game scores conserved to zero, and
all four BOT types received each seat `125` times.  Harness fallback count was
zero for every BOT, hidden-state access count was zero, and no sensitive hand,
wall, RNG, or world fields were written to the per-game artifact.  Native
evaluator branches remain separately visible: shape-v2 had `16391`
`only_legal_action` delegations; shape-v1 had `263` explicit legacy-level
decisions (including `185` `hu_or_piao_legacy`).

Artifact: `bot_local_mixed_20260916.json`, git revision
`6ef04c55235a6aef7771628e7961ca4c89a200e1`.

## Schema validation

```text
OPENSPEC_TELEMETRY=0 npx --yes @fission-ai/openspec \
  validate bot-ev-tight-bound-racing --strict --no-interactive
Change 'bot-ev-tight-bound-racing' is valid
```
