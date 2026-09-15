# formal-tournament-participation Specification

## Purpose

为杭州麻将 AI 提供一个面向正式锦标赛的稳定运行入口。该能力使用平台签发的报名令牌解析参赛身份，复用现有 Api、BotClient、策略层和 Recorder，持续运行多阶段锦标赛直到完成、淘汰或其他权威终态。

## Scope

本规范覆盖正式锦标赛 CLI 入口、报名身份解析、平台规则获取、多阶段生命周期、阶段间在线保活、动态 game 发现、每 token 并发与共享 state 预算、结构化终止结果、Recorder 默认启用、秘密脱敏以及相应验收测试。

## Non-goals

本规范不改变麻将规则、策略模型、游戏协议、SSE/state/action 实现、自由匹配流程或已有窗口确认、状态恢复、限速和安全动作传输机制；也不负责创建锦标赛、门户登录或报名。

## Requirements


### Requirement: Formal tournament entrypoint reuses the existing online client

The project SHALL provide a dedicated module entrypoint:

~~~bash
python3 -m mj.platform.tournament_runner \
    --config local/platform.json \
    --strategy policy \
    --ckpt runs/bc0/best.pt
~~~

The tournament runner SHALL be a thin lifecycle and CLI wrapper around the
existing mj.platform.api.Api, mj.platform.bot_client.BotClient,
mj.platform.runner.make_decide, and mj.platform.recorder.Recorder.
Formal tournament games SHALL continue to use BotClient.run() and its existing
state reconstruction, /notify, /state, window confirmation, action submission,
throttling, and recovery paths.

The runner MUST NOT copy or fork the game state loop, SSE handling, state
fetching, Mirror transitions, StateDemand, StateScheduler, window
confirmation, action mapping, or action recovery implementation.

#### Scenario: Start a formal tournament

- **WHEN** the user invokes python3 -m mj.platform.tournament_runner
- **THEN** the runner loads the tournament configuration and strategy
- **AND** creates one independent Api and BotClient for each configured
  registration token
- **AND** starts the formal tournament lifecycle for each worker
- **AND** runs each worker until a tournament terminal condition or normal
  elimination

#### Scenario: An assigned game becomes active

- **WHEN** a formal tournament exposes an active game
- **THEN** the runner delegates that game to the existing BotClient game worker
- **AND** the runner does not create a second game protocol implementation

### Requirement: Tournament configuration supports one or more tokens

The tournament configuration loader SHALL accept one or more registration tokens
and SHALL NOT require exactly four participants. The loader MAY be a new
load_tournament_config() function or an explicitly extended load_config()
contract, but existing test-room and free-match configuration behavior MUST
remain compatible.

The minimum supported configuration SHALL be:

~~~json
{
  "server": "https://10.240.169.190:18080",
  "tokens": {
    "main": "<tournament-registration-token>"
  }
}
~~~

Multiple independent identities SHALL also be supported:

~~~json
{
  "server": "https://10.240.169.190:18080",
  "tokens": {
    "bot-a": "<token-a>",
    "bot-b": "<token-b>"
  }
}
~~~

Each token SHALL have its own Api, tournament identity, active-game set,
supervisor state, and termination result. The runner MUST NOT infer participant
count, stage size, or tournament completion from the number of configured
tokens.

#### Scenario: Single tournament token

- **WHEN** the configuration contains one token
- **THEN** the runner starts one tournament worker
- **AND** the runner does not require three additional tokens

#### Scenario: Multiple tournament tokens

- **WHEN** the configuration contains N tokens
- **THEN** the runner starts N independent workers
- **AND** one worker's tournament identity or active games cannot authorize
  actions for another worker

### Requirement: Registration token determines tournament identity

Before registering, readying, or polling a tournament, each worker SHALL call:

~~~text
GET /api/me
Authorization: Bearer <registration-token>
~~~

The worker SHALL read tournament_id, user_id, and active_games from that
response. It SHALL use the returned non-empty tournament_id as the only
tournament ID for that worker and SHALL NOT require a manually supplied
--tournament-id.

If tournament_id is empty, the worker SHALL terminate with TOKEN_NOT_BOUND and
SHALL NOT enter the game loop. HTTP 401 and non-recoverable 403 responses SHALL
be fatal authentication failures and SHALL terminate with AUTH_FAILED without
infinite retry.

#### Scenario: Valid registration token

- **WHEN** /api/me returns a non-empty tournament_id
- **THEN** the worker stores that ID as its tournament identity
- **AND** all subsequent tournament requests use that ID

#### Scenario: Token is not bound to a tournament

- **WHEN** /api/me.tournament_id is empty
- **THEN** the worker stops before starting any game worker
- **AND** its termination reason is TOKEN_NOT_BOUND

#### Scenario: Authentication is rejected

- **WHEN** /api/me returns HTTP 401 or an unrecoverable 403
- **THEN** the worker reports AUTH_FAILED
- **AND** the worker does not retry forever

### Requirement: Tournament rules are authoritative and token-scoped

After resolving the tournament identity, the worker SHALL fetch rules from:

~~~text
GET /api/tournaments/me/rules
~~~

The worker SHALL use the returned configuration rather than replacing it with
client defaults. At minimum, the supported fields M, Rounds, BaseScore,
YouCaiBiKao, and supported timeout/window parameters SHALL be consumed by the
existing game client. YouCaiBiKao and other tournament-level values MUST NOT be
hard-coded globally.

Unknown configuration fields SHALL be ignored. A temporary rules-fetch failure
SHALL follow the bounded tournament polling retry policy; the worker MUST NOT
silently start games using an unverified default rule set.

#### Scenario: Tournament-specific rule configuration

- **WHEN** /rules.config.YouCaiBiKao is true
- **THEN** games started by that worker use you_cai_bi_kao=true

#### Scenario: Two tournaments return different rules

- **WHEN** tournament A returns YouCaiBiKao=false and tournament B returns
  YouCaiBiKao=true
- **THEN** the two workers use their own rule values
- **AND** one worker cannot overwrite the other's tournament configuration

#### Scenario: Server adds an unknown configuration field

- **WHEN** the rules response contains an unsupported field
- **THEN** the worker ignores that field
- **AND** the worker does not terminate solely because of it

### Requirement: Formal tournament execution has no fixed normal game limit

The formal tournament runner SHALL invoke:

~~~python
BotClient.run(max_games=None)
~~~

The runner MUST NOT use a fixed game count as the normal completion condition.
After a game or stage ends, the worker SHALL continue tournament polling unless
the platform reports a terminal status or the participant is explicitly not
qualified.

New game IDs that appear later in my_games and active_games, including
tiebreak or replay games, SHALL remain eligible for discovery regardless of the
number of games already completed.

#### Scenario: A first game finishes

- **WHEN** the current game finishes
- **AND** the tournament is not terminal
- **THEN** the worker continues polling the tournament

#### Scenario: A stage finishes with no active games

- **WHEN** the tournament enters stage_done
- **AND** active_games=[]
- **THEN** the worker remains alive and authenticated
- **AND** the worker does not interpret the empty list as tournament completion

#### Scenario: A tiebreak game appears

- **WHEN** all previously known games finish
- **AND** the platform later adds a new game ID to the participant's game list
- **THEN** the worker discovers and starts that game
- **AND** the worker does not exit because a prior game count was reached

### Requirement: The tournament lifecycle follows authoritative platform status

The tournament supervisor SHALL use the platform status as the lifecycle
authority. It SHALL support at least:

~~~text
registering
stage_open
running
stage_done
finished
closed
void
~~~

The conceptual lifecycle SHALL be:

~~~text
BOOT -> REGISTERING -> STAGE_OPEN -> RUNNING -> STAGE_DONE
                                  ^                    |
                                  |                    v
                                  +-------------- STAGE_OPEN

STAGE_OPEN -- qualified=false --> ELIMINATED
any stage ----------------------> CLOSED / VOID
final stage --------------------> FINISHED
~~~

stage_done SHALL be treated as an inter-stage state, not as final completion.
The worker SHALL stop only for finished, closed, void, explicit elimination,
interruption, or a fatal protocol/authentication error.

#### Scenario: Registering

- **WHEN** status=registering
- **THEN** the worker performs idempotent register/ready handling
- **AND** the worker continues tournament polling

#### Scenario: Qualified stage open

- **WHEN** status=stage_open and qualified=true
- **THEN** the worker confirms attendance for that stage
- **AND** the worker waits for running or a subsequent authoritative status

#### Scenario: Not qualified

- **WHEN** status=stage_open and qualified=false
- **THEN** the worker terminates normally as ELIMINATED
- **AND** the worker does not report an unhandled program exception

#### Scenario: Stage done

- **WHEN** status=stage_done
- **THEN** the worker remains alive
- **AND** the worker keeps authenticated tournament polling active
- **AND** the worker waits for the next stage, replay, or terminal status

#### Scenario: Finished

- **WHEN** status=finished
- **THEN** the worker stops normally with termination reason FINISHED

#### Scenario: Void or closed

- **WHEN** status=void or status=closed
- **THEN** the worker stops normally with termination reason VOID or CLOSED

### Requirement: Attendance confirmation is repeated for every stage

The worker SHALL treat each newly entered stage_open as a new attendance
confirmation boundary. A successful ready from a previous stage MUST NOT be
assumed to carry over.

The worker SHALL use the existing API wrappers:

~~~text
POST /api/tournaments/{tournament_id}/register
POST /api/tournaments/{tournament_id}/ready
~~~

Attendance handling SHALL be idempotent per stage: normal repeated polling of
the same stage MUST NOT create an unbounded ready retry loop, while a new stage
MUST trigger a new ready attempt.

TOURNAMENT_STARTED returned from register or ready SHALL be treated as a state
race. The worker SHALL re-query tournament status and SHALL NOT exit for that
response. NOT_QUALIFIED returned from ready at stage_open SHALL be treated as
normal elimination or loss of stage eligibility and SHALL NOT be retried
indefinitely.

#### Scenario: Advance to the next stage

- **WHEN** a participant qualified in the previous stage
- **AND** the tournament enters a new stage_open
- **THEN** the worker performs ready for the new stage
- **AND** the worker does not rely on the previous stage's ready result

#### Scenario: Three stages

- **WHEN** the worker observes three distinct stage_open stages
- **THEN** it performs attendance confirmation for each stage
- **AND** a successful first-stage ready does not suppress later confirmations

#### Scenario: Tournament-start race

- **WHEN** register or ready returns 409 TOURNAMENT_STARTED
- **THEN** the worker treats it as a status race
- **AND** re-queries the tournament
- **AND** does not terminate solely for that response

#### Scenario: Not-qualified response

- **WHEN** ready returns 409 NOT_QUALIFIED
- **THEN** the worker records loss of stage eligibility
- **AND** stops with ELIMINATED when the authoritative lifecycle confirms that
  the participant is not qualified
- **AND** does not retry ready forever

### Requirement: Tournament presence is maintained between stages

During registering, stage_open, and stage_done, the worker SHALL keep making
authenticated requests for its own tournament. It MUST NOT depend on a game SSE
stream as an online heartbeat when no game is active.

The default polling interval and bounded backoff SHALL ensure that, whenever the
platform is reachable, successful authenticated requests for this tournament do
not have an idle gap of 60 seconds or more. Requests for another tournament or
another token MUST NOT be used as proof of this worker's online presence.

After a transient network failure, the worker SHALL resume this tournament's
authenticated polling as soon as practical and SHALL not turn a temporary
empty-game interval into completion.

#### Scenario: Long inter-stage wait

- **WHEN** the tournament remains in stage_done for several minutes
- **THEN** the worker continues authenticated tournament polling
- **AND** it remains eligible under the platform's online-confirmation window

#### Scenario: No active games

- **WHEN** active_games=[] and the tournament is not terminal
- **THEN** the worker remains alive
- **AND** the worker continues tournament polling independently of game SSE

### Requirement: Tournament polling failures are retryable and bounded

The worker SHALL retry transient tournament GET failures, temporary 5xx
responses, connection resets, and timeouts with bounded backoff. The retry loop MUST NOT
busy-loop and MUST NOT infer elimination, completion, or a new stage from the
last successful status while the current status is unavailable.

After recovery, the worker SHALL fetch a complete authoritative tournament
status before making lifecycle decisions. The retry and keepalive policy SHALL
make a best effort not to cross the platform's online window solely because of
the client's retry schedule.

#### Scenario: Tournament polling temporarily fails

- **WHEN** several consecutive tournament GET requests fail transiently
- **THEN** the worker retries with bounded backoff
- **AND** does not declare elimination or completion
- **WHEN** a request succeeds again
- **THEN** the worker bases the next decision on the newly returned status

### Requirement: Game discovery is dynamic and tournament-scoped

For each successful tournament poll, the worker SHALL determine game workers
from:

~~~text
/api/me.active_games ∩ tournament.my_games
~~~

The worker MUST refresh this view during the lifecycle and MUST NOT cache a
fixed game ID list from startup or use game count as a completion signal.

Completed game IDs SHALL be tracked in _done_games or an equivalent durable
per-run set so that a game visible again in polling is not started twice. A game
belonging to another tournament SHALL never be operated by this worker.

#### Scenario: Newly assigned game

- **WHEN** a game ID appears in /api/me.active_games
- **AND** the same ID appears in tournament.my_games
- **THEN** the worker starts a game worker for that ID

#### Scenario: Foreign active game

- **WHEN** an active game appears in /api/me.active_games
- **AND** it is absent from the current tournament's my_games
- **THEN** the current tournament worker does not operate that game

#### Scenario: Completed game remains visible

- **WHEN** a completed game is returned again by polling
- **THEN** the worker does not start a duplicate game worker

### Requirement: Per-token game concurrency and state budget are preserved

When one token has multiple active games, the worker SHALL run one independent
game worker per active game. Each game SHALL retain independent Mirror, cursor,
decision, window, and lifecycle state.

All games for the same token SHALL share the single StateThrottle owned by that
token's Api. The formal runner MUST NOT create one independent /state budget per
game, and MUST NOT multiply the configured rate by the number of active games.
Different tokens MAY have independent throttles.

The default formal tournament state rate SHALL be 15 requests per second per
token unless explicitly overridden by the CLI for diagnostics.

#### Scenario: Multiple games are assigned simultaneously

- **WHEN** one token is assigned N active games
- **THEN** the worker runs N game workers concurrently
- **AND** all N workers use the same token-level state throttle

#### Scenario: Ten concurrent games

- **WHEN** one token has ten active games and --state-rate 15
- **THEN** the aggregate token budget remains 15 requests per second
- **AND** the client does not create ten independent 15-per-second budgets

### Requirement: Active games reuse SSE wakeups and incremental state

For an active game, the client SHALL continue using the existing communication
pattern:

~~~text
GET /api/games/{game_id}/notify
        |
        | state-change wakeup
        v
GET /api/games/{game_id}/state?seq=N
~~~

SSE seq SHALL be treated as an inclusive wakeup watermark only. It MUST NOT
directly replace the local /state cursor or be applied as a game transition.
/state responses remain the authoritative input for Mirror advancement.

SSE reconnect, polling fallback, and incremental cursor behavior SHALL remain
the existing BotClient behavior.

#### Scenario: SSE notification is received

- **WHEN** /notify delivers a newer sequence
- **THEN** the game worker schedules the corresponding /state demand
- **AND** it does not set the Mirror cursor directly to the SSE sequence

#### Scenario: SSE disconnects

- **WHEN** the SSE stream disconnects because of a network failure
- **THEN** the existing reconnect or polling fallback resumes state fetching
- **AND** the game worker does not lose the game state

### Requirement: Authoritative snapshots are the recovery boundary

The existing GET /api/games/{game_id}/state?seq=0 path SHALL be used to rebuild
authoritative state when an event gap, Mirror inconsistency, action 409,
uncertain action result, window confirmation boundary, or untrusted local
cursor requires recovery.

The client MUST NOT continue submitting an action based only on an inferred
local state after such a condition. The tournament wrapper SHALL preserve this
behavior and SHALL not add an alternative recovery implementation.

#### Scenario: Local cursor cannot be trusted

- **WHEN** a game detects a gap or inconsistent Mirror
- **THEN** it issues a seq=0 authoritative resync through the existing client
- **AND** it does not submit an action from the stale cursor

### Requirement: Action submission is safe under rejection and uncertainty

The existing POST /api/games/{game_id}/action implementation SHALL remain the
only action transport for formal games. Supported payloads include:

~~~json
{"action":"discard","tile":"..."}
{"action":"pass","tile":""}
{"action":"hu","tile":""}
{"action":"peng","tile":"..."}
{"action":"gang","tile":"..."}
{"action":"chi","tile":"...","tiles":["...","..."]}
~~~

Chi actions SHALL explicitly include the two hand tiles selected for the
combination. The client MUST NOT blindly retry a physical action after a 409,
transport error, response-read error, or other result that leaves execution
unknown.

#### Scenario: Action returns 409

- **WHEN** an action POST returns HTTP 409
- **THEN** the game records the rejection
- **AND** it performs the existing seq=0 resync
- **AND** it never reposts the same physical action

#### Scenario: Action result is uncertain

- **WHEN** a network failure leaves it unknown whether the server executed the
  action
- **THEN** the game records post_uncertain
- **AND** reconciles using seq=0
- **AND** does not automatically resend the old action

### Requirement: Stage crashes remain observable and recoverable

When status=stage_done and stage_crashed=true, the worker SHALL emit a
structured warning with reason STAGE_CRASHED_WAITING, remain online, and
continue tournament polling. It MUST NOT terminate the entire tournament
because of the stage-crashed marker alone.

#### Scenario: Stage crashes and awaits administration

- **WHEN** the platform reports stage_done with stage_crashed=true
- **THEN** the worker reports STAGE_CRASHED_WAITING
- **AND** continues authenticated polling
- **AND** waits for a platform restart, replay, next stage, or terminal status

### Requirement: Termination is based only on authoritative terminal facts

The following conditions alone SHALL NOT terminate a formal tournament:

~~~text
active_games == []
my_games == []
current worker count == 0
games_played reaches a guessed number
no game appears for a period of time
status == stage_done
~~~

Normal tournament termination SHALL require finished, closed, void, or an
authoritative stage_open with qualified=false. A worker error or operator
interruption SHALL be reported separately from tournament outcome.

#### Scenario: Inter-stage idle period

- **WHEN** a participant has no active game for five minutes
- **AND** the tournament remains in stage_done
- **THEN** the worker remains alive and keeps polling

#### Scenario: Final game count is unknown

- **WHEN** a tournament creates an unanticipated replay or tiebreak game
- **THEN** the worker continues until the platform reports a terminal outcome

### Requirement: Each token produces a structured tournament result

Each token worker SHALL produce a machine-readable result containing at least:

~~~json
{
  "token_label": "main",
  "tournament_id": "...",
  "user_id": "...",
  "termination_reason": "FINISHED",
  "final_status": "finished",
  "final_stage": "...",
  "qualified": true,
  "games": 12,
  "actions": 1234,
  "hu": 3,
  "response_409": 0,
  "post_uncertain": 0
}
~~~

The result SHALL preserve stage transitions and existing game diagnostics when
available. termination_reason SHALL include at least:

~~~text
FINISHED
ELIMINATED
VOID
CLOSED
TOKEN_NOT_BOUND
AUTH_FAILED
PROTOCOL_FATAL
INTERRUPTED
~~~

Normal elimination MAY exit with process status zero, but SHALL be explicit in
the result and SHALL NOT be printed as an unhandled exception. Authentication
or protocol-fatal failures SHALL cause a non-zero process status.

The result, logs, and exception summaries MUST identify a token only by its
label and may include user_id, tournament_id, and game_id; they MUST NOT include
the bearer token.

#### Scenario: Player is eliminated normally

- **WHEN** the platform reports that the participant is not qualified for the
  next stage
- **THEN** the worker finishes with termination_reason=ELIMINATED
- **AND** the result is not represented as an exception

#### Scenario: Authentication fails

- **WHEN** a registration token is invalid
- **THEN** the process exits non-zero
- **AND** the result contains termination_reason=AUTH_FAILED

### Requirement: Recorder is enabled by default

The formal runner SHALL enable Recorder by default because formal tournament
evidence cannot be regenerated reliably. The CLI MAY disable it only with an
explicit --no-recorder flag.

When --no-recorder is supplied, the runner SHALL emit a clear warning that
formal tournament evidence is being discarded. Recorder output SHALL preserve
the existing game evidence contract without storing bearer tokens.

#### Scenario: Default recorder behavior

- **WHEN** the formal runner starts without --no-recorder
- **THEN** recording is enabled for games handled by the token workers

#### Scenario: Recorder is explicitly disabled

- **WHEN** the user supplies --no-recorder
- **THEN** the runner disables recording
- **AND** emits a warning that this is unsafe for a formal tournament

### Requirement: Registration secrets are never persisted or exposed

Registration tokens SHALL be read only from a gitignored local configuration or
a future secret provider. They SHALL NOT be recommended as direct CLI arguments
and SHALL NOT be written to Git, Recorder files, JSONL records,
request/response dumps, run summaries, or trace output.

Token redaction SHALL also apply to exception text, HTTP error bodies, dump
helpers, and traceback/log formatting. It MUST be impossible for a normal
authentication failure response containing the token to place the complete token
in captured stdout or stderr.

#### Scenario: Authentication error contains credential text

- **WHEN** the server returns a 401/403 error whose message contains the
  credential
- **THEN** captured stdout and stderr contain no complete bearer token
- **AND** Recorder, dump, trace, and summary outputs contain no complete token

#### Scenario: Normal diagnostic logging

- **WHEN** a worker logs an identity or game event
- **THEN** it may log token_label, user_id, tournament_id, and game_id
- **AND** it does not log the bearer token or Authorization header

### Requirement: CLI exposes formal tournament controls without a normal game cap

The formal entrypoint SHALL support:

~~~text
--config
--strategy policy|bot|random
--ckpt
--bot-evaluator
--state-rate
--dump
--no-recorder
--replay-trace
--trace-root
~~~

strategy=policy SHALL support a training checkpoint, strategy=bot SHALL support
the existing heuristic strategy, and strategy=random SHALL remain a
protocol/rule diagnostic option rather than a recommended tournament strategy.

The formal entrypoint MUST NOT expose or default to a normal --games N
termination condition. If a game limit is retained for diagnostics, it SHALL
be named --max-games-debug and its help text SHALL state:

~~~text
仅调试；正式锦标赛不要使用，会导致提前离赛
~~~

The default formal state rate SHALL be 15 per second per token.

#### Scenario: Standard formal command

- **WHEN** the user runs the policy command with --state-rate 15
- **THEN** the runner starts with no fixed game termination limit
- **AND** it continues through stage transitions and tiebreak games

#### Scenario: Diagnostic game limit

- **WHEN** the user supplies --max-games-debug
- **THEN** the runner clearly marks the run as diagnostic
- **AND** the help and runtime warning state that it MUST NOT be used for a
  formal tournament

### Requirement: Ctrl-C performs graceful shutdown

On KeyboardInterrupt or SIGINT, the runner SHALL set a shared stop event,
stop creating new game workers, and allow active workers to leave through the
existing shutdown path. It SHALL attempt to close active recorders, preserve
already written evidence, and emit a structured INTERRUPTED result for each
affected worker.

The shutdown path MUST NOT delete or truncate previously written formal
tournament logs, and MUST NOT submit new actions after shutdown has begun.

#### Scenario: Operator interrupts an active tournament

- **WHEN** the process receives Ctrl-C while games or stage polling are active
- **THEN** the shared stop event is set
- **AND** no new game worker is created
- **AND** active recorder resources are closed when possible
- **AND** the final result reports INTERRUPTED

### Requirement: Formal tournament acceptance covers lifecycle and regression gates

The implementation SHALL add focused lifecycle tests in
tests/test_tournament_runner.py. Tests SHALL cover at least:

- single-stage completion with FINISHED;
- multi-stage stage_done to stage_open progression;
- repeated ready for each stage;
- normal elimination;
- empty active-game periods without premature exit;
- online polling intervals during a long stage_done;
- dynamic tiebreak game discovery;
- per-token rule isolation;
- action 409 and uncertain POST recovery without duplicate action;
- token redaction from output and artifacts;
- graceful interruption and structured results.

The focused tests and the existing full test suite SHALL pass. The test-room
mj.platform.runner and free-match mj.platform.match_runner behavior SHALL remain
unchanged. OpenSpec strict validation, compilation/diff checks, and any online
acceptance SHALL report actual results separately from planned gates.

#### Scenario: Direct final completes

- **GIVEN** a tournament enters its final stage and the final game completes
- **WHEN** the platform returns status=finished
- **THEN** the runner exits normally with termination_reason=FINISHED

#### Scenario: Multi-stage qualification works

- **GIVEN** a participant finishes a stage and the platform returns stage_done
- **WHEN** the platform later returns stage_open with qualified=true
- **THEN** the runner remains alive during the gap
- **AND** performs ready for the new stage
- **AND** discovers games for that stage dynamically

#### Scenario: Empty inter-stage game list does not terminate

- **GIVEN** stage_done and active_games=[]
- **WHEN** five minutes pass without a game
- **THEN** the runner continues tournament polling
- **AND** does not report FINISHED or ELIMINATED without authoritative
  evidence

#### Scenario: Online presence is maintained

- **GIVEN** the tournament remains in stage_done
- **THEN** successive successful authenticated polls for that tournament are
  less than 60 seconds apart

#### Scenario: Tiebreak is discovered

- **GIVEN** the initial final games have completed
- **WHEN** the platform adds a new game to both my_games and active_games
- **THEN** a new game worker starts for that game
- **AND** the worker does not stop because the initial game count was reached

#### Scenario: Token isolation and shared throttle are preserved

- **GIVEN** two tokens and multiple games for one token
- **THEN** each token uses its own tournament identity and rules
- **AND** each token's games share only that token's state throttle

#### Scenario: Existing runners do not regress

- **WHEN** the existing test-room and free-match test suites run after the
  formal runner change
- **THEN** their prior lifecycle and game-count behavior remain unchanged

### Requirement: Formal tournament scope excludes unrelated protocol and strategy work

This change SHALL be limited to the formal tournament lifecycle wrapper,
configuration and lifecycle observability needed to expose its structured
result, and the corresponding tests.

It SHALL NOT:

- create or administer tournaments;
- automate portal login or registration;
- modify Mahjong rules;
- modify policy, EV, heuristic, PPO, or random strategy logic;
- implement a new /state, /notify, or /action protocol;
- implement free matching through /api/match;
- replace existing SSE, state recovery, window confirmation, throttling, or
  safe action transport;
- fork the existing game execution client.

#### Scenario: Existing game protocol is sufficient

- **WHEN** a formal tournament game is assigned
- **THEN** the implementation changes only how the tournament supervisor obtains
  and retires games
- **AND** the game state/action protocol remains the existing BotClient
  implementation
