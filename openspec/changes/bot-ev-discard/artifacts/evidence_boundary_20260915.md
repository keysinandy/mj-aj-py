# bot-ev-discard baseline evidence boundary

Captured 2026-09-15 from a clean worktree at the `shape-v1` baseline. The
authoritative machine-readable record is `baseline_manifest.json`.

## Confirmed from source

- `legacy` remains the production default. `shape-v1` is opt-in and currently
  ranks ordinary discards; HU/财飘, KONG, and the existing reaction paths remain
  frozen or delegated.
- `EvalContext` and the existing evaluator use the hero hand and public
  visible counts. They must not be treated as a complete world snapshot:
  `Mirror.build_game()` contains zero-valued opponent hands, placeholder wall
  material, and a hero-only reaction queue.
- `Game.scores` is a per-round settlement vector and `scoring.settle()` is the
  source for hero reward semantics.
- The installed Rust extension exposes the `rust-batch-v1` future-discard
  helper, not the v2 all-candidate frontier required by this change.

## Historical evidence kept separate

The archived shape-v1 verification, performance note, and online reports are
references for comparison only. They are not re-run results for this change and
do not establish the 4096 paired-game, concurrent-decision, or new-room gates.

## Prohibited evidence

No baseline artifact contains tokens, request bodies, opponent concealed tiles,
real wall order, source RNG state, or future replay events. Any future teacher
artifact must be derived from the public context and independently sampled
worlds, with unsupported or incomplete states retained as such.
