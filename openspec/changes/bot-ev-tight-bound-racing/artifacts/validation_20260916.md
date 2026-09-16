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

## Schema validation

```text
OPENSPEC_TELEMETRY=0 npx --yes @fission-ai/openspec \
  validate bot-ev-tight-bound-racing --strict --no-interactive
Change 'bot-ev-tight-bound-racing' is valid
```
