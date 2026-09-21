# legacy-two-ply-v1 evidence

`legacy-two-ply-v1-benchmark.json` records the reproducible fixture, Rust/Python
parity run, same-seed safety probe, decision timing, and release gates for this
change.  The 8 ms profile is intentionally transactional: if the frontier does
not finish, the action and ranking revert to complete legacy and the explanation
records the fallback.  The measured fallback rate is not a release pass, so the
profile remains explicit opt-in and `legacy` remains the default.

The full suite was rerun with local socket binding enabled.  The only failures
were pre-existing ONNX export/player tests because `onnx` is not installed in
this environment; the focused bot, shanten, recorder, log, and new evaluator
tests pass.
