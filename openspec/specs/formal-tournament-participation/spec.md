# formal-tournament-participation Specification

## Purpose

为杭州麻将 AI 提供一个面向正式锦标赛的稳定运行入口。该能力使用平台签发的报名令牌解析参赛身份，复用现有 Api、BotClient、策略层和 Recorder，持续运行多阶段锦标赛直到完成、淘汰或其他权威终态。

## Scope

本规范覆盖正式锦标赛 CLI 入口、报名身份解析、平台规则获取、多阶段生命周期、阶段间在线保活、动态 game 发现、每 token 并发与共享 state 预算、结构化终止结果、Recorder 默认启用、秘密脱敏以及相应验收测试。

## Non-goals

本规范不改变麻将规则、策略模型、游戏协议、SSE/state/action 实现、自由匹配流程或已有窗口确认、状态恢复、限速和安全动作传输机制；也不负责创建锦标赛、门户登录或报名。

## Requirements


### Requirement: Formal tournament entrypoint reuses the existing online client

项目 SHALL 提供以下专用模块入口：

~~~bash
python3 -m mj.platform.tournament_runner \
    --config local/platform.json \
    --strategy policy \
    --ckpt runs/bc0/best.pt
~~~

赛事 runner SHALL 只是围绕现有 `mj.platform.api.Api`、
`mj.platform.bot_client.BotClient`、`mj.platform.runner.make_decide` 和
`mj.platform.recorder.Recorder` 的生命周期与 CLI 封装。正式赛 game SHALL 继续使用
`BotClient.run()` 及其已有的 state 重建、`/notify`、`/state`、窗口确认、动作提交、
限速和恢复路径。

runner MUST NOT 复制或 fork 游戏状态循环、SSE 处理、状态获取、Mirror 转换、
StateDemand、StateScheduler、窗口确认、动作映射或动作恢复实现。

#### Scenario: Start a formal tournament

- **WHEN** 用户调用 `python3 -m mj.platform.tournament_runner`
- **THEN** runner 加载赛事配置和策略
- **AND** 为每个配置的报名令牌创建独立的 `Api` 和 `BotClient`
- **AND** 启动对应的正式赛生命周期
- **AND** 每个 worker 运行至赛事终态或正常淘汰

#### Scenario: An assigned game becomes active

- **WHEN** 正式赛事暴露一个 active game
- **THEN** runner 将该 game 委托给现有 BotClient game worker
- **AND** runner 不创建第二套游戏协议实现

### Requirement: Tournament configuration supports one or more tokens

赛事配置加载器 SHALL 接受一个或多个报名令牌，且 MUST NOT 要求恰好四个参与者。
加载器可以是新的 `load_tournament_config()`，也可以是明确扩展的 `load_config()`
契约；已有测试房和自由匹配配置行为 MUST 保持兼容。

最小配置 SHALL 支持：

~~~json
{
  "server": "https://10.240.169.190:18080",
  "tokens": {
    "main": "<tournament-registration-token>"
  }
}
~~~

每个 token SHALL 拥有独立的 `Api`、赛事身份、active game 集合、supervisor 状态和
终止结果。runner MUST NOT 从 token 数量推断参赛人数、阶段规模或赛事完成。

#### Scenario: Single tournament token

- **WHEN** 配置包含一个 token
- **THEN** runner 启动一个赛事 worker
- **AND** runner 不要求另外三个 token

#### Scenario: Multiple tournament tokens

- **WHEN** 配置包含 N 个 token
- **THEN** runner 启动 N 个独立 worker
- **AND** 一个 worker 的赛事身份或 active game 不能授权另一个 worker 发起动作

### Requirement: Registration token determines tournament identity

在注册、ready 或轮询赛事之前，每个 worker SHALL 调用：

~~~text
GET /api/me
Authorization: Bearer <registration-token>
~~~

worker SHALL 从响应读取 `tournament_id`、`user_id` 和 `active_games`，使用返回的非空
`tournament_id` 作为该 worker 唯一赛事 ID，且 MUST NOT 要求手工提供
`--tournament-id`。

若 `tournament_id` 为空，worker SHALL 在进入 game loop 前以 `TOKEN_NOT_BOUND` 终止。
HTTP 401 和不可恢复的 403 SHALL 是致命认证错误，worker SHALL 以 `AUTH_FAILED` 终止，
不得无限重试。

#### Scenario: Valid registration token

- **WHEN** `/api/me` 返回非空 `tournament_id`
- **THEN** worker 保存该赛事身份
- **AND** 后续赛事请求全部使用该身份

#### Scenario: Token is not bound to a tournament

- **WHEN** `/api/me.tournament_id` 为空
- **THEN** worker 在启动任何 game worker 前停止
- **AND** 终止原因为 `TOKEN_NOT_BOUND`

#### Scenario: Authentication is rejected

- **WHEN** `/api/me` 返回 HTTP 401 或不可恢复的 403
- **THEN** worker 报告 `AUTH_FAILED`
- **AND** worker 不会无限重试

### Requirement: Tournament rules are authoritative and token-scoped

解析赛事身份后，worker SHALL 从以下端点获取规则：

~~~text
GET /api/tournaments/me/rules
~~~

worker SHALL 使用返回配置，而不是用客户端默认值覆盖。至少 `M`、`Rounds`、
`BaseScore`、`YouCaiBiKao` 和平台支持的 timeout/window 参数 SHALL 被现有 game client
消费；`YouCaiBiKao` 及其他赛事级值 MUST NOT 被全局硬编码。

未知配置字段 SHALL 忽略。临时规则获取失败 SHALL 遵循有界赛事轮询重试策略；在没有
验证规则时，worker MUST NOT 静默使用默认规则启动 game。

#### Scenario: Tournament-specific rule configuration

- **WHEN** `/rules.config.YouCaiBiKao` 为 true
- **THEN** 该 worker 启动的 game 使用 `you_cai_bi_kao=true`

#### Scenario: Two tournaments return different rules

- **WHEN** tournament A returns YouCaiBiKao=false and tournament B returns
  YouCaiBiKao=true
- **THEN** the two workers use their own rule values
- **AND** one worker cannot overwrite the other's tournament configuration

#### Scenario: Server adds an unknown configuration field

- **WHEN** rules 响应包含不支持的字段
- **THEN** worker 忽略该字段
- **AND** worker 不会仅因该字段退出

#### Scenario: Two workers return different rules

- **WHEN** 赛事 A 返回 `YouCaiBiKao=false` 且赛事 B 返回 `YouCaiBiKao=true`
- **THEN** 两个 worker 分别使用自己的规则值
- **AND** 一个 worker 不能覆盖另一个 worker 的赛事配置

### Requirement: Formal tournament execution has no fixed normal game limit

正式赛事 runner SHALL 调用：

~~~python
BotClient.run(max_games=None)
~~~

runner MUST NOT 使用固定局数作为正常完成条件。game 或阶段结束后，除非平台报告终态
或明确表示参与者不再晋级，worker SHALL 继续轮询赛事。

之后出现在 `my_games` 和 `active_games` 中的新 game ID（包括加赛和重赛）无论已经
完成多少局都 SHALL 继续具备发现资格。

#### Scenario: A first game finishes

- **WHEN** 当前 game 完成
- **AND** 赛事不是终态
- **THEN** worker 继续轮询赛事

#### Scenario: A stage finishes with no active games

- **WHEN** 赛事进入 `stage_done`
- **AND** `active_games=[]`
- **THEN** worker 保持存活并认证轮询
- **AND** worker 不把空列表解释为赛事完成

#### Scenario: A tiebreak game appears

- **WHEN** 所有已知 game 完成
- **AND** 平台稍后将新的 game ID 加入参与者 game 列表
- **THEN** worker 发现并启动该 game
- **AND** worker 不因已完成的局数达到某个猜测值而退出

### Requirement: The tournament lifecycle follows authoritative platform status

赛事 supervisor SHALL 以平台状态作为生命周期权威，至少支持：

~~~text
registering
stage_open
running
stage_done
finished
closed
void
~~~

概念生命周期 SHALL 为：

~~~text
BOOT -> REGISTERING -> STAGE_OPEN -> RUNNING -> STAGE_DONE
                                  ^                    |
                                  |                    v
                                  +-------------- STAGE_OPEN

STAGE_OPEN -- qualified=false --> ELIMINATED
any stage ----------------------> CLOSED / VOID
final stage --------------------> FINISHED
~~~

`stage_done` SHALL 是阶段间状态而不是最终完成。worker 只允许在 `finished`、`closed`、
`void`、明确淘汰、用户中断或致命协议/认证错误时停止。

#### Scenario: Registering

- **WHEN** status 为 `registering`
- **THEN** worker 执行幂等 register/ready 处理
- **AND** worker 继续赛事轮询

#### Scenario: Qualified stage open

- **WHEN** status 为 `stage_open` 且 `qualified=true`
- **THEN** worker 确认该阶段出席
- **AND** worker 等待 `running` 或后续权威状态

#### Scenario: Not qualified

- **WHEN** status 为 `stage_open` 且 `qualified=false`
- **THEN** worker 正常以 `ELIMINATED` 终止
- **AND** worker 不报告未处理的程序异常

#### Scenario: Stage done

- **WHEN** status 为 `stage_done`
- **THEN** worker 保持存活
- **AND** worker 持续认证赛事轮询
- **AND** worker 等待下一阶段、重赛或终态

#### Scenario: Finished

- **WHEN** status 为 `finished`
- **THEN** worker 正常以 `FINISHED` 终止

#### Scenario: Void or closed

- **WHEN** status 为 `void` 或 `closed`
- **THEN** worker 正常以对应的 `VOID` 或 `CLOSED` 终止

### Requirement: Attendance confirmation is repeated for every stage

worker SHALL 把每次新进入的 `stage_open` 视为新的出席确认边界，前一阶段成功 ready
MUST NOT 被假设可以跨阶段继承。

出席处理 SHALL 使用现有包装：

~~~text
POST /api/tournaments/{tournament_id}/register
POST /api/tournaments/{tournament_id}/ready
~~~

同一阶段的正常重复轮询 SHALL 是幂等的，不得产生无界 ready 重试；新阶段 SHALL 触发
一次新的 ready 尝试。register 或 ready 返回 `TOURNAMENT_STARTED` SHALL 被视为状态
竞争并重新查询；stage_open 的 ready 返回 `NOT_QUALIFIED` SHALL 被视为阶段资格丢失，
不得无限重试。

#### Scenario: Advance to the next stage

- **WHEN** 参与者在上一阶段晋级
- **AND** 赛事进入新的 `stage_open`
- **THEN** worker 为新阶段执行 ready
- **AND** worker 不依赖上一阶段 ready 结果

#### Scenario: Three stages

- **WHEN** worker 观察到三个不同的 `stage_open` 阶段
- **THEN** worker 为每个阶段执行出席确认
- **AND** 第一阶段成功 ready 不会抑制后续确认

#### Scenario: Tournament-start race

- **WHEN** register 或 ready 返回 HTTP 409 `TOURNAMENT_STARTED`
- **THEN** worker 将其视为状态竞争
- **AND** worker 重新查询赛事
- **AND** worker 不仅因该响应终止

#### Scenario: Not-qualified response

- **WHEN** ready 返回 HTTP 409 `NOT_QUALIFIED`
- **THEN** worker 记录阶段资格丢失
- **AND** 权威生命周期确认后以 `ELIMINATED` 停止
- **AND** worker 不会无限重试 ready

### Requirement: Tournament presence is maintained between stages

在 `registering`、`stage_open` 和 `stage_done` 期间，worker SHALL 持续为自己的赛事
发起认证请求。没有 active game 时，worker MUST NOT 依赖 game SSE 作为在线心跳。

默认轮询间隔和有界退避 SHALL 保证：只要平台可达，针对该赛事的成功认证请求之间
不会出现 60 秒或更长的空档。另一个赛事或另一个 token 的请求 MUST NOT 作为本 worker
在线的证明。临时网络失败恢复后，worker SHALL 尽快恢复本赛事轮询，不得把临时空 game
区间转换为完成。

#### Scenario: Long inter-stage wait

- **WHEN** 赛事在 `stage_done` 保持数分钟
- **THEN** worker 持续认证轮询
- **AND** worker 仍处于平台在线确认窗口内

#### Scenario: No active games

- **WHEN** `active_games=[]` 且赛事不是终态
- **THEN** worker 保持存活
- **AND** worker 独立于 game SSE 继续赛事轮询

### Requirement: Tournament polling failures are retryable and bounded

赛事 GET 的临时失败、暂时性 5xx、连接重置和超时 SHALL 使用有界退避重试。重试循环
MUST NOT 忙等，也 MUST NOT 在当前状态不可用时根据最近一次成功状态推断淘汰、完成或新阶段。

恢复后，worker SHALL 先获取完整权威赛事状态，再作生命周期决策；重试和保活策略 SHALL
尽力避免仅因客户端退避跨过平台在线窗口。

#### Scenario: Tournament polling temporarily fails

- **WHEN** 连续多个赛事 GET 暂时失败
- **THEN** worker 以有界退避重试
- **AND** worker 不声明淘汰或完成
- **WHEN** 请求再次成功
- **THEN** worker 根据新返回的权威状态作下一次决策

### Requirement: Game discovery is dynamic and tournament-scoped

每次赛事轮询成功后，worker SHALL 从以下集合确定 game worker：

~~~text
/api/me.active_games ∩ tournament.my_games
~~~

worker MUST 在生命周期内刷新该视图，MUST NOT 在启动时缓存固定 game ID 列表或使用局数
作为完成信号。已完成 game ID SHALL 放入 `_done_games` 或等价的本次运行持久集合，
防止轮询中重复出现时启动重复 worker；属于其他赛事的 game 绝不能由本 worker 操作。

#### Scenario: Newly assigned game

- **WHEN** game ID 出现在 `/api/me.active_games`
- **AND** 同一 ID 出现在赛事 `my_games`
- **THEN** worker 启动该 game worker

#### Scenario: Foreign active game

- **WHEN** active game 出现在 `/api/me.active_games`
- **AND** 它不在当前赛事的 `my_games` 中
- **THEN** 当前赛事 worker 不操作该 game

#### Scenario: Completed game remains visible

- **WHEN** 已完成 game ID 再次出现在轮询结果中
- **THEN** worker 不启动重复 game worker

### Requirement: Per-token game concurrency and state budget are preserved

一个 token 有多个 active game 时，worker SHALL 为每个 game 运行一个独立 game worker；
每局 SHALL 保持独立的 Mirror、cursor、decision、window 和生命周期状态。

同一 token 的所有 game SHALL 共享该 token `Api` 所有的单一 `StateThrottle`。正式 runner
MUST NOT 为每局创建独立 `/state` 预算，也 MUST NOT 随 active game 数量倍增配置速率；
不同 token 可以拥有独立限流器。正式赛默认 state rate SHALL 为每 token 每秒 15 次，
除非 CLI 为诊断显式覆盖。

#### Scenario: Multiple games are assigned simultaneously

- **WHEN** 一个 token 同时被分配 N 个 active game
- **THEN** worker 并发运行 N 个 game worker
- **AND** N 个 worker 使用同一个 token 级 StateThrottle

#### Scenario: Ten concurrent games

- **WHEN** 一个 token 有十个 game 且 `--state-rate 15`
- **THEN** token 总预算仍为每秒 15 次
- **AND** 客户端不创建十个独立的每秒 15 次预算

### Requirement: Active games reuse SSE wakeups and incremental state

active game SHALL 继续使用现有通信模式：

~~~text
GET /api/games/{game_id}/notify
        |
        | state-change wakeup
        v
GET /api/games/{game_id}/state?seq=N
~~~

SSE seq SHALL 只作为包含式唤醒水位，MUST NOT 直接替换本地 `/state` cursor 或作为
game transition 应用；`/state` 响应仍是 Mirror 前进的权威输入。SSE 重连、轮询 fallback
和增量 cursor 行为 SHALL 保持现有 BotClient 语义。

#### Scenario: SSE notification is received

- **WHEN** `/notify` 提供更新的 seq
- **THEN** game worker 调度对应的 `/state` demand
- **AND** worker 不直接设置 Mirror cursor 为 SSE seq

#### Scenario: SSE disconnects

- **WHEN** `/notify` 因网络故障断开
- **THEN** 现有重连或轮询 fallback 恢复 state 获取
- **AND** game worker 不丢失 game 状态

### Requirement: Authoritative snapshots are the recovery boundary

现有 `GET /api/games/{game_id}/state?seq=0` SHALL 用于在事件缺口、Mirror 不一致、action
409、action 结果未知、窗口确认边界或不可信本地 cursor 时重建权威状态。

客户端 MUST NOT 仅凭推断的本地状态继续提交动作；赛事 wrapper SHALL 保留该行为，且
不得添加另一套恢复实现。

#### Scenario: Local cursor cannot be trusted

- **WHEN** game 检测到缺口或 Mirror 不一致
- **THEN** game 通过现有客户端发起 seq=0 权威重同步
- **AND** game 不从过期 cursor 提交动作

### Requirement: Action submission is safe under rejection and uncertainty

正式赛 SHALL 继续以现有 `POST /api/games/{game_id}/action` 作为唯一动作传输。支持的
payload 至少包括：

~~~json
{"action":"discard","tile":"..."}
{"action":"pass","tile":""}
{"action":"hu","tile":""}
{"action":"peng","tile":"..."}
{"action":"gang","tile":"..."}
{"action":"chi","tile":"...","tiles":["...","..."]}
~~~

chi action SHALL 明确包含组合所选的两张手牌。客户端在 409、传输错误、读取响应错误
或其他未知执行结果后 MUST NOT 盲目重发同一物理动作。

#### Scenario: Action returns 409

- **WHEN** action POST 返回 HTTP 409
- **THEN** game 记录拒绝
- **AND** game 执行现有 seq=0 重同步
- **AND** game 永不再次提交同一物理动作

#### Scenario: Action result is uncertain

- **WHEN** 网络故障导致服务端是否执行动作未知
- **THEN** game 记录 `post_uncertain`
- **AND** game 使用 seq=0 对账
- **AND** game 不自动重发旧动作

### Requirement: Stage crashes remain observable and recoverable

当 status 为 `stage_done` 且 `stage_crashed=true` 时，worker SHALL 发出带
`STAGE_CRASHED_WAITING` 原因的结构化 warning，保持在线并继续赛事轮询。仅因 stage
crashed 标志不得终止整个赛事。

#### Scenario: Stage crashes and awaits administration

- **WHEN** 平台报告 `stage_done` 且 `stage_crashed=true`
- **THEN** worker 报告 `STAGE_CRASHED_WAITING`
- **AND** worker 继续认证轮询
- **AND** worker 等待平台重启、重赛、下一阶段或终态

### Requirement: Termination is based only on authoritative terminal facts

以下条件单独出现时 SHALL NOT 终止正式赛事：

~~~text
active_games == []
my_games == []
current worker count == 0
games_played reaches a guessed number
no game appears for a period of time
status == stage_done
~~~

正常赛事终止 SHALL 要求 `finished`、`closed`、`void` 或权威 `stage_open` 且
`qualified=false`。worker error 或 operator interruption SHALL 与赛事结果分开报告。

#### Scenario: Inter-stage idle period

- **WHEN** 参与者五分钟没有 active game
- **AND** 赛事仍为 `stage_done`
- **THEN** worker 保持存活并继续轮询

#### Scenario: Final game count is unknown

- **WHEN** 赛事创建未预期的重赛或加赛 game
- **THEN** worker 持续运行直至平台报告终态

### Requirement: Each token produces a structured tournament result

每个 token worker SHALL 产生机器可读结果，至少包含：

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

结果 SHALL 保留阶段转换和可用的既有 game 诊断。`termination_reason` 至少包括：

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

正常淘汰可以使用进程状态零，但必须在结果中明确且不能打印为未处理异常；认证或
协议致命错误 SHALL 导致非零进程状态。结果、日志和异常摘要只可用 token label 标识
token，可包含 user_id、tournament_id、game_id，但 MUST NOT 包含 bearer token。

#### Scenario: Player is eliminated normally

- **WHEN** 平台报告参与者不具备下一阶段资格
- **THEN** worker 以 `ELIMINATED` 结束
- **AND** 结果不表现为未处理异常

#### Scenario: Authentication fails

- **WHEN** 报名 token 无效
- **THEN** 进程非零退出
- **AND** 结果包含 `termination_reason=AUTH_FAILED`

### Requirement: Recorder is enabled by default

正式 runner SHALL 默认启用 Recorder，因为正式赛事证据无法可靠再生。CLI 只有在显式
传入 `--no-recorder` 时才可以关闭；关闭时 SHALL 发出正式赛证据被丢弃的清晰警告。
Recorder 输出 SHALL 保持现有 game evidence 契约且不得保存 bearer token。

#### Scenario: Default recorder behavior

- **WHEN** runner 不带 `--no-recorder` 启动
- **THEN** token worker 处理的 game 默认启用记录

#### Scenario: Recorder is explicitly disabled

- **WHEN** 用户传入 `--no-recorder`
- **THEN** runner 关闭记录
- **AND** runner 警告这对正式赛事是不安全的

### Requirement: Registration secrets are never persisted or exposed

报名 token SHALL 仅从 gitignored 本地配置或未来 secret provider 读取。token MUST NOT
作为直接 CLI 参数推荐，也 MUST NOT 写入 Git、Recorder、JSONL、请求/响应 dump、运行
摘要或 trace。脱敏 SHALL 覆盖异常文本、HTTP 错误 body、dump helper 和 traceback/log
格式化；包含 token 的普通认证错误响应不得使完整 token 出现在 stdout 或 stderr。

#### Scenario: Authentication error contains credential text

- **WHEN** server 返回的 401/403 错误消息包含 credential
- **THEN** 捕获的 stdout 和 stderr 不包含完整 bearer token
- **AND** Recorder、dump、trace 和 summary 不包含完整 token

#### Scenario: Normal diagnostic logging

- **WHEN** worker 记录身份或 game 事件
- **THEN** 日志可以记录 token label、user_id、tournament_id 和 game_id
- **AND** 日志不记录 bearer token 或 Authorization header

### Requirement: CLI exposes formal tournament controls without a normal game cap

正式入口 SHALL 支持：

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

`strategy=policy` SHALL 支持 training checkpoint，`strategy=bot` SHALL 支持现有启发式
策略，`strategy=random` SHALL 保留为协议/规则诊断选项，不作为推荐赛事策略。

正式入口 MUST NOT 暴露或默认使用正常的 `--games N` 终止条件。若保留诊断局数参数，
参数名 SHALL 为 `--max-games-debug`，帮助文本 SHALL 明确：

~~~text
仅调试；正式锦标赛不要使用，会导致提前离赛
~~~

正式赛默认 state rate SHALL 为每 token 每秒 15 次。

#### Scenario: Standard formal command

- **WHEN** 用户使用带 `--state-rate 15` 的 policy 命令
- **THEN** runner 在无固定局数终止上限下启动
- **AND** runner 继续处理阶段转换和加赛 game

#### Scenario: Diagnostic game limit

- **WHEN** 用户传入 `--max-games-debug`
- **THEN** runner 将该运行明确标记为 diagnostic
- **AND** 帮助和运行时警告说明不得用于正式锦标赛

### Requirement: Ctrl-C performs graceful shutdown

收到 KeyboardInterrupt 或 SIGINT 时，runner SHALL 设置共享 stop event、停止创建新的
game worker，并允许 active worker 通过现有 shutdown 路径离开。runner SHALL 尽力关闭
active Recorder、保留已写证据，并为受影响的每个 worker 发出结构化 `INTERRUPTED` 结果。

shutdown 开始后 MUST NOT 删除或截断既有正式赛日志，也 MUST NOT 提交新的动作。

#### Scenario: Operator interrupts an active tournament

- **WHEN** game 或阶段轮询期间进程收到 Ctrl-C
- **THEN** 设置共享 stop event
- **AND** 不再创建新的 game worker
- **AND** 尽可能关闭 active Recorder 资源
- **AND** 最终结果报告 `INTERRUPTED`

### Requirement: Formal tournament acceptance covers lifecycle and regression gates

实现 SHALL 增加 `tests/test_tournament_runner.py` 的聚焦生命周期测试，至少覆盖：

- 单阶段 `FINISHED`；
- `stage_done` 到下一 `stage_open` 的多阶段推进；
- 每阶段重复 ready；
- 正常淘汰；
- 空 active-game 期间不提前退出；
- 长 stage_done 的在线轮询间隔；
- 动态 tiebreak game 发现；
- token 级规则隔离；
- action 409 和 uncertain POST 的无重复动作恢复；
- 输出和 artifact 的 token 脱敏；
- 优雅中断和结构化结果。

聚焦测试和现有全量测试 SHALL 通过；测试房 `mj.platform.runner` 与自由匹配
`mj.platform.match_runner` 行为 SHALL 保持不变。OpenSpec strict validation、编译、
diff 检查和任何线上验收 SHALL 分开报告真实结果与计划门槛。

#### Scenario: Direct final completes

- **GIVEN** 赛事进入最终阶段且 final game 完成
- **WHEN** 平台返回 `status=finished`
- **THEN** runner 正常退出且 `termination_reason=FINISHED`

#### Scenario: Multi-stage qualification works

- **GIVEN** 参与者结束一个阶段且平台返回 `stage_done`
- **WHEN** 平台稍后返回 `stage_open` 且 `qualified=true`
- **THEN** runner 在间隔期间保持存活
- **AND** 为新阶段执行 ready
- **AND** 动态发现该阶段 game

#### Scenario: Empty inter-stage game list does not terminate

- **GIVEN** `stage_done` 且 `active_games=[]`
- **WHEN** 五分钟没有 game
- **THEN** runner 继续赛事轮询
- **AND** 没有权威证据时不报告 `FINISHED` 或 `ELIMINATED`

#### Scenario: Online presence is maintained

- **GIVEN** 赛事保持 `stage_done`
- **THEN** 针对该赛事的连续成功认证轮询间隔小于 60 秒

#### Scenario: Tiebreak is discovered

- **GIVEN** 初始 final games 已完成
- **WHEN** 平台将新 game 同时加入 `my_games` 和 `active_games`
- **THEN** 启动该 game worker
- **AND** 不因初始 game 数量达到而停止

#### Scenario: Token isolation and shared throttle are preserved

- **GIVEN** 两个 token 且其中一个拥有多个 game
- **THEN** 每个 token 使用自己的赛事身份和规则
- **AND** 每个 token 的 game 只共享本 token 的 StateThrottle

#### Scenario: Existing runners do not regress

- **WHEN** 正式 runner 改动后的现有测试房和自由匹配测试运行
- **THEN** 原有生命周期和局数行为保持不变

### Requirement: Formal tournament scope excludes unrelated protocol and strategy work

本 change SHALL 限定于正式赛事生命周期 wrapper、配置、为结构化结果所需的生命周期
可观测性及对应测试。

本 change SHALL NOT：

- 创建或管理赛事；
- 自动化门户登录或报名；
- 修改麻将规则；
- 修改 policy、EV、启发式、PPO 或 random strategy 逻辑；
- 实现新的 `/state`、`/notify` 或 `/action` 协议；
- 通过 `/api/match` 实现自由匹配；
- 替换现有 SSE、状态恢复、窗口确认、限速或安全动作传输；
- fork 现有 game execution client。

#### Scenario: Existing game protocol is sufficient

- **WHEN** 正式赛事分配一个 game
- **THEN** 实现只改变赛事 supervisor 获取和回收 game 的方式
- **AND** game state/action 协议仍由现有 BotClient 实现
