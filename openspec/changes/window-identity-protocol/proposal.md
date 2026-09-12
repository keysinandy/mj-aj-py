## Why

The frozen three-room baseline has five eligible `legacy_unresolved` windows.
They are first observed from snapshots without an authoritative source discard
sequence; client-side fallback identity is intentionally forbidden because it
cannot survive `seq=0` re-anchors or distinguish repeated same-tile discards.

This prevents strong window completeness even though transport and StateDemand
evidence are healthy. The missing fact belongs at the protocol boundary, not in
another client-side heuristic.

## What Changes

- Define a stable protocol field for the source discard identity:
  `source_discard_seq` or an opaque `response_window_id`.
- Require `response_peng` and `response_chi` for one discard to share that
  identity across incremental responses and `seq=0` snapshots.
- Require a new discard and a new round to receive a different identity.
- Require the identity to be independent of snapshot watermark, discard-list
  length, meld count, or whether the discard remains visible after a claim.
- Preserve explicit client `identity_origin` and `first_seen_via` diagnostics.
- Keep snapshot-only windows weak/diagnostic until the protocol is deployed;
  do not add guessed client fallback or silently promote legacy evidence.
- Define a protocol-skipped identity status for deployments that explicitly
  cannot provide the field, without upgrading those windows to strong
  completeness.

## Capabilities

### New Capabilities

- `window-identity-protocol`: Stable, cross-reanchor identity for a response
  window and its peng/chi phases.

### Modified Capabilities

None. The prior `online-room-window-attribution` change remains the client-side
identity and legacy-exclusion baseline; this change defines the protocol fact
needed to improve coverage.

## Impact

- Server/API response and event schemas must expose and preserve the stable
  window identity.
- `mj/platform/bot_client.py` and `scripts/window_acceptance.py` consume the
  field and report origin/coverage, without adding heuristic fallback.
- Protocol fixtures and online acceptance tests need explicit source identity
  and snapshot carry cases.
- This change is independent of state rate, retries, submit margin, and game
  settlement markers.
