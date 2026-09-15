## 1. Contract and configuration foundations

- [x] 1.1 Define token-scoped tournament context, normalized rules, lifecycle states, termination reasons, and JSON-safe result contracts without retaining bearer tokens.
- [x] 1.2 Add a tournament-specific configuration loader that accepts a non-empty one-or-more-token mapping and validates server/token labels while preserving `load_config` and `load_match_config` compatibility.
- [x] 1.3 Confirm and cover the existing `Api` wrappers for `/api/me`, `/api/tournaments/me/rules`, tournament status, register, and ready; classify authentication, race, elimination, transient, and protocol errors for the supervisor.
- [x] 1.4 Add a shared redaction helper for bearer tokens, Authorization headers, exception text, HTTP bodies, dump payloads, and traceback/log formatting.

## 2. Tournament worker and lifecycle supervisor

- [x] 2.1 Add `mj.platform.tournament_runner` with the documented module CLI, strategy/checkpoint construction through `runner.make_decide`, and one worker per configured token.
- [x] 2.2 Implement token preflight through `/api/me`, persist only non-secret identity facts, fail early with `TOKEN_NOT_BOUND` for an empty tournament ID, and map fatal 401/403 responses to `AUTH_FAILED`.
- [x] 2.3 Fetch and normalize the token-scoped tournament rules before starting games; prevent an unverified/default rule set from reaching a formal game after a rules failure.
- [x] 2.4 Implement authoritative lifecycle handling for `registering`, `stage_open`, `running`, `stage_done`, `finished`, `closed`, and `void`, with `stage_done` treated as an inter-stage wait.
- [x] 2.5 Establish a stable stage identity/transition key from the platform response or confirmed fixture contract, and make register/ready attendance idempotent per stage while re-confirming each new stage.
- [x] 2.6 Handle `TOURNAMENT_STARTED` as a status race and `NOT_QUALIFIED` as stage eligibility loss; ensure neither creates an infinite retry loop or an unhandled normal-elimination exception.
- [x] 2.7 Add bounded tournament polling backoff for transient GET failures, 5xx, connection resets, and timeouts; after recovery require a fresh authoritative status before making lifecycle decisions.
- [x] 2.8 Maintain authenticated tournament presence during `registering`, `stage_open`, and `stage_done`, with no successful-poll gap of 60 seconds or more when the platform is reachable.
- [x] 2.9 Ensure normal execution invokes `BotClient.run(max_games=None, stop=...)`, never uses a guessed game count or empty active-game list as completion, and stops only on authoritative terminal facts, explicit elimination, fatal failure, or interruption.

## 3. Dynamic game execution and resource isolation

- [x] 3.1 Discover games on every successful poll from `/api/me.active_games ∩ tournament.my_games`, reject foreign games, and maintain a per-token completed-game set that prevents duplicate workers.
- [x] 3.2 Start one independent game worker per active game while preserving each game's Mirror, cursor, decision, window, and lifecycle state; stop creating new workers once shutdown begins.
- [x] 3.3 Construct exactly one `Api` and one `StateThrottle` per token, share them across that token's game workers, and keep different token identities and budgets isolated.
- [x] 3.4 Pass the immutable token-scoped rule snapshot into existing game setup and Recorder metadata, including `M`, `Rounds`, `BaseScore`, `YouCaiBiKao`, and supported timeout/window settings; ignore unknown fields.
- [x] 3.5 Preserve the existing BotClient `/notify` wake-only, incremental `/state`, seq=0 recovery, window authorization, action mapping, and no-blind-resend behavior; do not introduce a second game protocol loop.
- [x] 3.6 Support optional safe state/action dumps through the existing `DumpingApi` boundary without exposing credentials or changing physical retry/recovery semantics.
- [x] 3.7 Record stage-crash waiting as `STAGE_CRASHED_WAITING` while continuing the same token's authenticated lifecycle and dynamic game polling.

## 4. Results, recording, and operator controls

- [x] 4.1 Aggregate per-token structured results with identity, final status/stage, qualification, stage transitions, game/action counters, diagnostics, and one of the specified termination reasons.
- [x] 4.2 Set process exit status from worker outcomes so normal elimination is explicit and successful while authentication/protocol-fatal outcomes are non-zero.
- [x] 4.3 Enable `Recorder` by default for formal games, support explicit `--no-recorder` with a clear unsafe warning, and close recorder resources on every terminal path.
- [x] 4.4 Apply redaction at stdout/stderr, exception, result, Recorder, dump, replay-trace, and traceback boundaries; add a safety assertion that a fixture token never appears in captured output or artifacts.
- [x] 4.5 Expose `--config`, `--strategy`, `--ckpt`, `--bot-evaluator`, `--state-rate`, `--dump`, `--no-recorder`, `--replay-trace`, and `--trace-root`, with a 15/s-per-token default.
- [x] 4.6 If a diagnostic game limit is retained, expose only `--max-games-debug`, mark the run as diagnostic, and print the required warning that it can cause early departure from a formal tournament.
- [x] 4.7 Implement Ctrl-C/SIGINT handling with a shared stop event, bounded worker joins, no new workers or actions after shutdown starts, preserved logs, and `INTERRUPTED` results for affected workers.

## 5. Focused tests and compatibility gates

- [x] 5.1 Build a deterministic fake tournament API/clock fixture for identity, rules, stage transitions, errors, game assignments, recorder calls, and token isolation.
- [x] 5.2 Add configuration, API classification, token preflight, authoritative rules, unknown-field, and secret-redaction tests in `tests/test_tournament_runner.py`.
- [x] 5.3 Add lifecycle tests for direct `FINISHED`, `CLOSED`, `VOID`, normal `ELIMINATED`, `registering`, repeated stage ready, `TOURNAMENT_STARTED`, `NOT_QUALIFIED`, and `stage_crashed` waiting.
- [x] 5.4 Add inter-stage tests proving that `stage_done` and empty `active_games` do not terminate, authenticated polling remains below the online gap threshold, and transient failures use bounded retry before a fresh status decision.
- [x] 5.5 Add dynamic discovery tests for late tiebreak/replay games, foreign active games, repeated completed games, multiple games per token, and no fixed normal game cap.
- [x] 5.6 Add resource and protocol regression tests proving one StateThrottle per token, independent token rules/identities, `BotClient.run(max_games=None)`, existing SSE/state/action recovery, and no duplicate action after 409/uncertain POST.
- [x] 5.7 Add Recorder-default, `--no-recorder`, CLI help, structured-result, exit-status, and graceful-interruption tests.
- [x] 5.8 Run the focused tournament tests and the complete existing test suite; verify existing test-room and free-match runner behavior remains unchanged.
- [x] 5.9 Run `OPENSPEC_TELEMETRY=0 openspec validate formal-tournament-participation --strict --no-interactive`, Python compilation, and `git diff --check`; report each result separately.
- [x] 5.10 Add the documented, credential-gated online acceptance checklist for single-token lifecycle, multi-stage presence, dynamic game discovery, shared state budget, evidence, and rollback; do not treat an online run as complete until its actual evidence is reviewed.
