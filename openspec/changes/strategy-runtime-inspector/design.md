# Design

## Context

LegacyV2 already returns detailed evaluation data, and BotClient records it with each online decision. Local arena records currently preserve only the action list, although the in-process strategy result is already available. The tournament service publishes load progress, while match and console sessions do not yet publish an equivalent resolved configuration. Replay currently reduces online decision records to a small `DECISION` annotation.

## Goals / Non-Goals

**Goals:**

- Normalize the runtime configuration and decision path into shared, versioned JSON contracts.
- Carry those contracts through session progress, game metadata, replay steps, and CLI output.
- Preserve older records and keep audit payloads bounded and public-information safe.

**Non-Goals:**

- Changing strategy defaults, evaluator selection, action ranking, Mahjong rules, or request/response transport.
- Recomputing strategy results for verbose output.
- Claiming feature execution for strategies whose runtime does not report it.

## Decisions

### One Python contract module owns snapshot and audit serialization

Add a small `mj.strategy_runtime` module with JSON-safe snapshot and audit builders. Strategy factories attach the resolved snapshot to the callable/player they construct. BotClient obtains the snapshot from that runtime object and records it once in game metadata; each decision audit is derived from the already-returned evaluation. A config hash covers normalized public strategy fields only.

Alternative: let each UI and replay adapter reconstruct feature state from evaluator names. Rejected because it repeats defaults and would again conflate configured and executed paths.

### Session pages consume backend-published snapshots

Tournament and match runners publish load status and the snapshot in session progress. Local arena sessions publish the per-seat configured snapshots because their player objects are created in worker processes per game. The UI renders a common snapshot component and does not treat a selected form value as proof that loading succeeded. Strategy selectors also request a preview from the same backend snapshot builder; that preview is explicitly labeled as configured, while only session progress can report that loading succeeded.

Alternative: build a client-only preview from selected strategy controls. Rejected because it can drift from runtime defaults and profile resolution. The preview endpoint resolves the selected public configuration without loading a strategy or model.

### Replay projects only recorded audit data

Online replay attaches the Recorder's decision audit and game snapshot to the matched metadata/step. Local arena records store the compact audit next to its action index and a snapshot for each physical seat; replay associates that audit with the resulting action step. Missing audit fields remain absent, preserving the existing old-log contract. The React inspector renders audit fields as data and never infers a search path.

Alternative: replay evaluation from the visible hand to fill missing history. Rejected because that would be counterfactual and could be mistaken for the action chosen at runtime.

### CLI verbosity affects presentation only

The tournament CLI exposes `off`, `summary`, and `verbose`. Summary output uses normalized audit fields; verbose output reads up to five candidate roots from the current evaluation. Recorder audits remain bounded and omit verbose roots. No evaluator or game loop is called a second time.

## Risks / Trade-offs

- [Some evaluators do not expose detailed entry/timeout status] → Mark unsupported states as unknown or not applicable instead of manufacturing false values.
- [Audit metadata increases JSONL and API payload size] → Keep session snapshots compact and persist only bounded candidate summaries in each decision.
- [Old records lack normalized scope] → Display an explicit unavailable state and preserve the raw evaluator details where present.
- [Verbose CLI output may be noisy] → Keep it opt-in and cap candidate rows at five.

## Migration Plan

No migration is required. New session progress and game metadata are additive; old sessions and recordings remain readable. Rollback consists of removing the inspector surfaces while readers continue to ignore the additive fields.
