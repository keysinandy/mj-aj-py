## ADDED Requirements

### Requirement: 窗口身份使用权威源弃牌序号

系统 SHALL 为可追踪的响应窗口生成稳定的
`WindowId = (game_id, round_id, discard_owner, source_discard_seq, tile)`，
并以 `WindowAttemptKey = (WindowId, phase)` 标识某一阶段的尝试。`peng → chi`
转换 MUST 复用同一个 `WindowId`，只有 `phase` 可以变化。
`WindowAttemptKey` 是逻辑窗口阶段键，不是物理请求 ID；每个逻辑 state/action
请求 MUST 另有唯一 `logical_request_id`，其物理尝试以该逻辑请求内递增的
`attempt_index` 标识。重复确认同一 `WindowAttemptKey` 时，记录必须能区分不同
的 `logical_request_id`/`attempt_index`。

#### Scenario: 同一弃牌从碰窗转入吃窗

- **WHEN** 同一局、同一轮、同一出牌者、同一源弃牌序号和牌值先产生
  `response_peng`，随后产生 `response_chi`
- **THEN** 两条记录的 `WindowId` 完全相同，且仅
  `WindowAttemptKey.phase` 分别为 `response_peng` 与 `response_chi`

#### Scenario: seq=0 重锚仍指向原窗口

- **WHEN** seq=0 权威快照仍显示相同轮次、出牌者、源弃牌序号和牌值
- **THEN** 重建后的确认需求继续使用原 `WindowId`，不得因 snapshot watermark
  不同而生成新窗口

#### Scenario: 新弃牌或换轮

- **WHEN** 轮次、出牌者、`source_discard_seq` 或牌值任一项变化
- **THEN** 旧 `WindowId` 失效并生成新的 `WindowId`，旧阶段不得授权新窗口动作

### Requirement: legacy 窗口身份不得承担跨重锚强去重

当生产事件缺少可确认的 `source_discard_seq` 时，系统 MUST 将窗口身份标记为
`legacy/unresolved`，并可记录牌河长度、副露数等弱 fallback 作为诊断信息；
但 `tile_discarded` 事件自身的 event `seq` 可作为该弃牌的 source sequence。
这些 fallback 和 `/state` 快照 `seq` MUST NOT 用于跨 seq=0 重锚的强去重、
窗口同一性证明或动作授权。只有 authoritative `WindowId` 才拥有与安全去重
相同的语义。

#### Scenario: 缺失源序号的旧协议

- **WHEN** 事件和快照均未提供可确认的源弃牌序号
- **THEN** 记录包含 `legacy/unresolved` 身份标签，且不把牌河/副露计数标为
  authoritative `WindowId`

#### Scenario: legacy 窗口经过 seq=0 重锚

- **WHEN** seq=0 重锚前后的牌河长度或副露数恰好相同，或 snapshot `seq` 与旧
  事件序号相同
- **THEN** 系统仍不得仅凭这些值跳过一次窗口确认或动作；必须重新依据当前
  phase、responding seats、截止时间和合法集判断

### Requirement: 每个 gid 的 StateDemand 最多一个物理请求在途

系统 SHALL 在 `BotClient` 与 `Api.game_state()` 之间维护 per-gid 的
`StateDemand` 协调器。协调器的事实字段至少包含：

```text
watermark_target
full_snapshot_required
reasons:
  SSE_DELTA:      wanted_seq, status
  RESYNC:         cause, requested_at, status
  WINDOW_CONFIRM: window_id, phase, deadline, status
generation
in_flight
```

其中 `status` 只能是 `SATISFIED`、`TERMINAL` 或 `PENDING`；只有 PENDING reason
参与当前调度。`kind_priority`、`effective_deadline` 和 `reason_mask` 可以缓存，
但 MUST 是从仍为 PENDING 的 reasons 派生的值，不能作为 reason metadata 的唯一
来源。系统 MUST 保证同一 gid 同时最多一个物理 `/state` 请求在途。所有物理尝试
（包括 retry）仍 MUST 经过该令牌共享的 `StateThrottle`。

当 `full_snapshot_required=true` 时物理请求 MUST 使用 `game_state(seq=0)`；否则
才使用 BotClient 当前已应用的 local cursor，而不是 `watermark_target`。`seq=0`
是全量快照模式，不参与 `watermark_target` 的数值 max，但返回快照中的实际 watermark 可以满足
`SSE_DELTA`。

#### Scenario: 一阵 SSE 帧合并为一个需求

- **WHEN** 同一 gid 在一个状态请求在途期间收到 100 个递增 SSE watermark
- **THEN** 协调器只保留一个待处理需求，`SSE_DELTA.wanted_seq` 及
  `watermark_target` 为其中最大值，不能为每一帧创建物理 `/state` 请求

#### Scenario: 窗口确认与普通追赶同时到达

- **WHEN** 同一 gid 已有 `SSE_DELTA` 需求，随后出现 `WINDOW_CONFIRM` 或
  `RESYNC` 需求
- **THEN** 需求合并为一次物理请求，`kind_priority` 取最高优先级，
  `reason_mask` 按位 OR，窗口/重锚 reason 的 metadata 均保留，
  `full_snapshot_required=true`，不能并发创建第二个同 gid 请求

#### Scenario: 十场彼此隔离

- **WHEN** 十个 gid 同时产生 SSE 和窗口需求
- **THEN** 每个 gid 各自最多一个物理请求在途，跨 gid 的许可仍由同一个共享
  throttle 仲裁，不因协调器而绕过 15/s 配额

### Requirement: StateDemand 合并字段遵循确定性规则

同一 gid 的新需求 MUST 按以下规则合并：只有真正的增量 watermark 才参与
`watermark_target = max(SSE_DELTA.wanted_seq)`（未知值保留 unknown 语义）；
`full_snapshot_required` 由仍为 PENDING 的 `RESYNC`/`WINDOW_CONFIRM` reason
派生，不能把 `seq=0` 当作数值 0 参与 max；每个 reason 的 metadata 必须原样
保留；`effective_deadline` 取仍有效窗口截止中的最早值；`reason_mask` 取
PENDING reasons 的 OR；`kind_priority` 遵循
`WINDOW_CONFIRM > RESYNC > SSE_DELTA`。已过期窗口必须转为终态或按明确规则
降级，不能继续以有效 urgent 需求占据优先级。只有语义状态实际变化时 generation
才递增，重复相同 watermark、重复 metadata 或更低 watermark 不得单独递增。

#### Scenario: 需求同时含 watermark 和窗口截止

- **WHEN** `SSE_DELTA.wanted_seq=120` 的普通需求与 deadline 更早、要求 seq=0
  的窗口确认需求合并
- **THEN** 合并需求保留 `watermark_target=120`、
  `full_snapshot_required=true`、最早有效 deadline 和 `WINDOW_CONFIRM` 优先级，
  物理请求使用 seq=0 而不是把 seq=0 与 120 做 max

#### Scenario: 过期需求重新仲裁

- **WHEN** 需求在队列中等待至 deadline 已过，而另一个窗口仍有未来有效截止
- **THEN** 已过期的 `WINDOW_CONFIRM` 标记 `TERMINAL` 并从 urgent demand 移除；
  若仍有 SSE/RESYNC PENDING reason，则按剩余 reason 重新派生普通优先级和截止，
  不得继续挤掉未来有效窗口

#### Scenario: 全量快照覆盖增量目标

- **WHEN** 当前 demand 同时要求 `SSE_DELTA.wanted_seq=120` 和
  `WINDOW_CONFIRM` 的 seq=0 快照，返回的全量快照实际 watermark=130
- **THEN** `SSE_DELTA` 可被标记为 `SATISFIED`，窗口 reason 仍必须独立按
  WindowId、phase、responding_seats 和截止条件判断

### Requirement: 逻辑完成条件按 reason 分别保留

一次物理 `/state` 返回后，系统 MUST 对每个 reason 的 metadata 分别判断
`SATISFIED`、`TERMINAL` 或 `PENDING`，不得因为一次 seq=0 返回就清空全部
reason。只有 PENDING reason 继续留在 active `StateDemand`；reason 终止或完成后，
必须重新计算 `full_snapshot_required`、`kind_priority`、`effective_deadline` 和
`reason_mask`。至少：

- `RESYNC` 在 BotClient 成功应用权威快照按其 cause 完成重建且（若有）返回
  watermark 覆盖其目标时为 `SATISFIED`；仅请求了 seq=0 或收到无 snapshot 响应
  不得满足该 reason；
- `SSE_DELTA` 在返回 watermark 覆盖其 `wanted_seq` 时为 `SATISFIED`，否则为
  `PENDING`；
- `WINDOW_CONFIRM` 只有在 authoritative `WindowId` 仍相同、phase 正确、
  本家仍在 `responding_seats`、快照提供精确截止且截止仍有效时为 `SATISFIED`。
  phase 已过去、WindowId 已变化、deadline 已过或权威快照已证明本家不再有响应
  资格时为 `TERMINAL`；快照仍是旧 phase、服务端 watermark 尚未推进或缺少必要
  权威字段时为 `PENDING`。

#### Scenario: seq=0 满足重锚但未满足窗口确认

- **WHEN** 合并需求包含 `RESYNC | WINDOW_CONFIRM`，返回的 seq=0 快照已能重建
  镜像，但 phase 已变为 `draw` 或 WindowId 不再相同
- **THEN** `RESYNC` 标记 `SATISFIED`，`WINDOW_CONFIRM` 标记 `TERMINAL` 并保留
  明确 `terminal_reason`，不能把两者一起记为成功，也不能因为 `unconfirmed`
  这个模糊结果无限重试

#### Scenario: 返回 watermark 未覆盖普通需求

- **WHEN** 响应 seq 小于 `SSE_DELTA` 的 wanted_seq
- **THEN** `SSE_DELTA` reason 保留在待处理需求中，下一次请求继续追赶

#### Scenario: 旧快照不应结束窗口 reason

- **WHEN** 返回快照仍是旧 phase，或没有推进到能确认窗口的权威 watermark/字段
- **THEN** `WINDOW_CONFIRM` 保持 `PENDING`，并可在下一次 completion 重新协调；
  不得标记 `TERMINAL` 后丢失确认机会，也不得因一次旧快照形成无限无条件重试

### Requirement: generation 防止在途需求竞态

物理请求发出时 MUST 记录 `started_generation`。请求在途期间任何新 SSE、窗口
或重锚需求在语义状态发生变化时 MUST 更新当前 generation；重复相同 watermark、
重复 metadata 或更低 watermark 不得仅因到达而更新。返回时即使 generation 已
变化，系统也 MUST 先依据当前响应对最新 `StateDemand` 做 reason-specific
reconciliation；generation 变化本身 MUST NOT 触发补请求。只有 reconciliation
后仍有 PENDING reason 时，当前 completion 才能创建一个 successor request，且
每次 completion 最多一个 successor，不限制整个 demand 生命周期的 successor
总数。不得用旧响应覆盖新窗口。

#### Scenario: 在途期间窗口目标推进

- **WHEN** generation=7 的请求已发出，期间 SSE watermark 推进且新增窗口确认
  使 generation=8
- **THEN** 返回 generation=7 的响应按最新 reason/目标重新协调；只有仍有
  PENDING reason 时才合并一次 successor，且 successor 仍遵循优先级和共享 throttle

#### Scenario: generation 变化但响应已覆盖最新目标

- **WHEN** 请求发出时 `wanted_seq=100`，在途期间 SSE 推进到 103，返回快照
  watermark=110，且没有 PENDING 的窗口 reason
- **THEN** 即使 generation 已变化，也不创建 successor，`in_flight` 正确归零

#### Scenario: 在途期间没有新需求

- **WHEN** 请求返回时 reconciliation 后所有 reason 均为 `SATISFIED` 或
  `TERMINAL`
- **THEN** 不创建重复补请求，`in_flight` 正确归零；后续若新的语义需求到达，
  可在新的 completion 中创建新的 successor

### Requirement: 无合法反应的窗口不得长期持有 urgent

系统 SHALL 在 BotClient 完成安全的合法候选检查后，若当前弃牌对本家没有任何
非 pass 的碰、杠或吃候选时清除该窗口的 urgent deadline，回退为 `SSE_DELTA`
普通追赶。这里的“没有非 pass 候选” MUST 来自与当前 local phase 无关的结构性
合法候选证明，或来自当前 authoritative snapshot 对无响应资格的证明；不得仅凭
`mirror.legal_actions()`、本地尚未进入 `response_peng/response_chi`、stale
phase 或 unresolved identity 降级。`catch_play`、phase 未确认或 identity
unresolved 时，若不存在前述安全证明，必须保留 `WINDOW_CONFIRM`。该降级 MUST
NOT 提前授权动作；任何吃/碰动作仍须通过权威对应 phase 快照确认。

#### Scenario: 他家弃牌对本家无非 pass 合法动作

- **WHEN** 本地 `response_peng` 和适用的 `response_chi` 合法集均只有 pass
- **THEN** 该 gid 不再以 `WINDOW_CONFIRM` urgent 排队，后续状态刷新按普通
  SSE 需求处理

#### Scenario: catch_play 的本地 phase 不能作为降级证据

- **WHEN** 处于 `catch_play`，local mirror 尚未确认 `response_peng`/
  `response_chi`，或 window identity 为 unresolved，但手牌结构仍可能形成非
  pass 候选
- **THEN** 不得仅因 local legal action 集当前只有 pass 就清除 urgent；
  `WINDOW_CONFIRM` 必须保留，直到结构性证明或 authoritative snapshot 给出安全
  结论

#### Scenario: 有合法候选的窗口保持 urgent

- **WHEN** 本家存在至少一个非 pass 合法碰/杠或下家弃牌存在合法吃
- **THEN** 对应窗口确认需求保留 urgent 优先级，直到确认、关闭或过期降级

### Requirement: state/action 记录可证明的 HTTP 阶段边界

每个 state attempt SHALL 使用本地 monotonic 记录 `throttle_enter`、
`throttle_granted` 和 `queue_wait_ms`；其中前两者分别是进入共享
`StateThrottle` 等待前和取得许可时刻。action 若不经过 `StateThrottle`，这些
字段 MUST 为 `null`/`not_applicable`，不得伪造为 0；若 action 经过该 throttle，
则必须记录真实边界。每次物理 retry 都独立记录 throttle 边界。

每个物理 state/action attempt SHALL 记录本地阶段边界和派生耗时：
`http_start` 为 `urlopen` 调用前，正常响应的 `headers_received` 为 `urlopen()`
返回时，正常响应的 `body_finished` 为 `response.read()` 返回时；
`pre_read_ms`、`read_ms`、`total_ms` MUST 使用 monotonic 差值计算。urllib 的
HTTP 4xx/5xx 会抛出 `HTTPError`，此时 headers 已经到达，
`headers_received` MUST 记录捕获 `HTTPError` 的时刻；若 `e.read()` 成功完成，
`body_finished` 记录该时刻，读取失败则为 null。没有收到 headers 的网络异常
不得伪造该边界。DNS、connect、TLS、send 若未由传输库单独提供，必须标为
unavailable/null，不得从 `pre_read_ms` 拆猜。

#### Scenario: 响应头快但 body 慢

- **WHEN** `urlopen()` 很快返回而 `response.read()` 延迟明显
- **THEN** 记录的 `pre_read_ms` 较小、`read_ms` 较大、`total_ms` 为两者之和，
  并保留 headers_received 与 body_finished 边界

#### Scenario: 响应头前阶段慢

- **WHEN** DNS/connect/TLS/等待响应头耗时较长而 body 读取很快
- **THEN** 耗时归入 `pre_read_ms`，不可观测的细分字段仍为 unavailable/null

#### Scenario: HTTPError 或 body 读取失败

- **WHEN** 物理请求在收到响应头后以 HTTPError 或读取异常结束
- **THEN** 已发生的 attempt、状态码和可用边界仍被记录；HTTPError 的
  `headers_received` 仍存在，只有成功完成 error body 读取时才填写
  `body_finished`，不能因 JSON 解析/读取失败而丢失物理尝试证据

### Requirement: Retry-After 与 server Date 同时保留 raw 和解析值

每个有响应头的物理 attempt MUST 原样保留 `retry_after_raw`（若存在）和
`server_date_raw`（若存在）。在可解析时额外保存 `retry_after_seconds` 与
`server_date_epoch`；Retry-After MUST 支持秒数和 HTTP-date 两种标准形态，无法
解析时解析字段为 null。server Date 和解析结果只能用于诊断，不得参与窗口
控制或改变本地 monotonic deadline。

#### Scenario: Retry-After 为秒数

- **WHEN**响应头 `Retry-After: 2.5`
- **THEN** 记录 raw 字符串和 `retry_after_seconds=2.5`

#### Scenario: Retry-After 为 HTTP-date

- **WHEN**响应头 `Retry-After` 为合法 HTTP-date
- **THEN** 保留 raw，并在本地可解析时记录相应的 seconds 值；解析失败不丢 raw

#### Scenario: server Date 不参与截止控制

- **WHEN** server Date 与本机时钟有偏差，且窗口存在本地 monotonic deadline
- **THEN** `deadline_left_at_send`/`deadline_left_at_response` 只由本地 deadline
  与 monotonic 时刻计算，不能用 server Date 调整是否发包

### Requirement: 逻辑请求与物理重试记录可关联且向后兼容

记录器 MUST 区分逻辑 state/action 请求和其物理 attempts；每个逻辑请求有唯一
`logical_request_id`，每次物理 retry 在该逻辑请求内使用递增 `attempt_index`。
每次物理 retry 都记录独立 throttle/HTTP 阶段，逻辑记录聚合 attempts、429、
queue 和最终结果。新增 WindowId、WindowAttemptKey、logical_request_id、
attempt_index、reason、generation、raw header 及阶段字段均为可选 JSON-safe
字段，旧日志缺失时读取为 unknown，不得破坏既有回放。

#### Scenario: 一个逻辑 state 经历 429 后重试

- **WHEN**第一次物理尝试返回 429，退避后第二次尝试成功
- **THEN** 两次物理 attempt 均有独立 throttle/HTTP 诊断，逻辑 req 记录总尝试数、
  429 数和最终状态，并能关联同一 gid/reason/generation 与
  `logical_request_id`，且 `attempt_index` 分别为 1、2

#### Scenario: 旧日志回放

- **WHEN**回放器读取没有新字段的历史 JSONL
- **THEN** 回放继续工作，并将 WindowId/阶段/generation 相关字段视为 unknown，
  不把 unknown 当作 authoritative 身份

### Requirement: 房间完整性分类隔离主验收分母

验收工具 SHALL 对每个房间分别记录 transport、window、game 三层状态，而不是
要求房间只有一个互斥完整性标签：

```text
transport_status: complete | partial
window_status:    complete | partial
game_status:      complete | partial | protocol_skipped
```

`partial` MUST 只排除受影响层；`protocol_skipped` MUST 只排除协议无法证明的
层。若协议本身不提供 `round_ended` 等结算事件，但 transport/window 日志完整，
则允许 `transport_status=complete`、`window_status=complete`、
`game_status=protocol_skipped`。各层的 rate、claim_miss、queue 等主指标只能
使用该层 status=`complete` 的房作为分母。

#### Scenario: 正常收官完整房

- **WHEN**房间有正常 `end`、必要 req/snapshot/events/decision/action 日志，且
  round/game 完整性可判定
- **THEN** transport、window、game 三层均标记 `complete`，可进入对应主指标

#### Scenario: 工具中断或日志缺尾

- **WHEN**房间没有 end 或最后一局关键日志缺失
- **THEN**受缺失证据影响的层标记 `partial`，保留其他层的可用数据；只从受影响
  层的主指标分母排除

#### Scenario: 协议不提供结算事件

- **WHEN**当前协议没有 `round_ended`，但传输和窗口日志完整
- **THEN** transport/window 标记 `complete`，game 标记 `protocol_skipped` 并明确
  game 层不可验证；transport/window 指标仍可使用，不伪造 game complete

### Requirement: 房报告覆盖 transport、window、game 三层指标

每个房报告 MUST 分层记录 transport（429、409、uncertain、各类
queue）、window（eligible、confirm requested/confirmed/submitted、timeout、
claim_miss）和 game（round started/ended、winner、score delta/final score）。
归一化指标至少包含 `state_429 / physical_state_attempts`、
`claim_miss / eligible_windows`、`WINDOW_CONFIRM seq=0 / eligible_windows`，并
分别报告：

```text
logical_demands
coalesced_demands
successor_requests
physical_state_requests
suppressed_duplicates
coalescing_ratio = 1 - physical_state_requests / logical_demands
```

`logical_demands` 统计每次提交给协调器的逻辑需求事件；一次 completion 因仍有
PENDING reason 而产生 successor intent 时，`successor_requests` 与
`logical_demands` 各增加一次，避免 successor 使 `coalescing_ratio` 变成负数；
`coalesced_demands` 统计被合并进已有 pending/in-flight demand 且改变语义状态的
事件；`suppressed_duplicates` 统计相同或被更高 watermark 支配、未改变语义状态
的重复事件；`physical_state_requests` 统计协调器新启动的物理 `/state` 请求，
retry 另计为 `physical_state_attempts`。当 `logical_demands=0` 时
`coalescing_ratio` MUST 为 null。上述 demand 字段 MUST 按 gid/game 输出，不能再
合并成单一的重复/合并指标。报告还 MUST 单独输出各层
complete/partial/protocol_skipped 数量。

#### Scenario: 三至五个房基线

- **WHEN**使用启发式 BOT、SSE+增量 `/state`、15/s 收集 3～5 个房间
- **THEN**每房均输出三层指标和上述归一化分母；transport/window/game 各自只按
  对应层的 complete 房汇总，protocol_skipped 只排除 game 层，partial 只排除
  受影响层

#### Scenario: demand 合并效果验证

- **WHEN**同一 gid 收到重复 SSE/窗口需求并由 StateDemand 合并
- **THEN**报告同时显示 `logical_demands`、`coalesced_demands`、
  `physical_state_requests`、`suppressed_duplicates` 和
  `coalescing_ratio`，并另列物理 attempts，以证明请求减少而非只改变日志外观

### Requirement: 本 change 保持线上运行变量冻结

本 change 的线上验收 MUST 使用启发式 BOT、默认 SSE+增量 `/state` 和 15/s；
不得在同一变更中调整 rate、sleep、release-aware EDF、动作 POST 重试或 BOT
策略。409/未知动作结果仍 MUST 先 seq=0 重锚，禁止盲重发旧动作。

#### Scenario: 观测基线运行

- **WHEN**执行线上窗口调度验收
- **THEN**命令显式包含 `--strategy bot --state-rate 15`，报告记录传输模式和
  房间完整性分类，不把 policy/random 房混作本基线

#### Scenario: 动作结果不确定

- **WHEN**动作 POST 返回 409 或未知网络结果
- **THEN**客户端只记录失败/不确定并请求 seq=0 权威重锚，不自动重发原动作
