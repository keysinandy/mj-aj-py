# Proposal

## Why

The console and tournament pages currently identify a selected evaluator but do not expose the resolved runtime profile or whether a particular decision actually entered its configured search path. This makes it difficult to distinguish an enabled feature from an early return, budget fallback, or unsupported strategy path.

## What Changes

- Publish a shared, sanitized snapshot of the effective strategy configuration for console sessions, tournament sessions, and recorded games.
- Record a bounded per-decision audit for online and local arena games with an explicit decision scope (`baotou_scope`, `weighted_two_ply`, `legacy`, `reaction_v2`, `kong_continuation`, `fallback`, `policy`, `policy-v3`, `random`, or `unknown`), configured/eligible/entered/completed state, Stage A and Stage B status, fallback/timeout, selected action, and elapsed time.
- Display a backend-resolved configuration preview beside strategy selectors in the web console and tournament page, show the loaded snapshot in session status, and show the selected decision audit in replay.
- Add concise and verbose strategy explanation output to the tournament CLI.
- Keep strategy selection and action computation unchanged; old records without audit data remain readable and are shown as unavailable.

## Capabilities

### New Capabilities
- `strategy-runtime-inspector`: Shared effective configuration and per-decision runtime diagnostics across session, replay, and CLI surfaces.

### Modified Capabilities
- `bot-decision-explanations`: Add normalized runtime audit fields while preserving the existing evidence, privacy, and bounded logging contract.

## Impact

Affected areas include Python strategy construction and BotClient/Recorder metadata, clientd console and tournament session progress, online replay projection, the React console/tournament/replay views, and tournament CLI entry points. No strategy defaults, evaluator ranking, transport behavior, or Mahjong rules change.
