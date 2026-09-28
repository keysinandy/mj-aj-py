# Tasks

## 1. Shared runtime contracts

- [x] 1.1 Add JSON-safe StrategySnapshot and DecisionAudit builders; verify default legacyV2 serializes its actual profile values and does not include secrets.
- [x] 1.2 Attach effective snapshots to backend-created strategy callables/players; verify bot, policy, and random configs keep unsupported feature states distinct from disabled.
- [x] 1.3 Record the snapshot in online metadata and local arena records, plus a bounded audit per decision; verify values reuse the existing evaluation and do not make a second strategy call.

## 2. Session and replay integration

- [x] 2.1 Publish load state and effective snapshots for tournament, match, and local arena sessions; verify session API responses carry the resolved snapshot.
- [x] 2.2 Preserve snapshots and decision audits through online and local replay projection; verify old recordings without audits remain loadable and report the audit as unavailable.

## 3. Operator surfaces

- [x] 3.1 Add shared snapshot rendering to console and tournament pages; verify loading/error/unstarted states do not claim the strategy is loaded.
- [x] 3.2 Add the selected step's Decision Audit to the replay inspector; verify no path is inferred when audit data is absent.
- [x] 3.3 Add `--explain off|summary|verbose` to tournament CLI entry points; verify startup and per-decision output reuses the existing evaluation and verbose rows are capped at five.
- [x] 3.4 Add selection-time previews backed by the shared server snapshot builder to console and tournament strategy selectors.

## 4. Integration review

- [x] 4.1 Review the final diff for additive-only API/log changes, bounded audit payloads, consistent `decision_scope`, and unchanged strategy/transport call paths.
