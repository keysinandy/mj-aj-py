## ADDED Requirements

### Requirement: 窗口身份使用权威源弃牌序号

系统 SHALL 为可追踪的响应窗口生成稳定的
`WindowId = (game_id, round_id, discard_owner, source_discard_seq, tile)`，
并以 `WindowAttemptKey = (WindowId, phase)` 标识某一阶段的尝试。`peng → chi`
转换 MUST 复用同一个 `WindowId`，只有 `phase` 可以变化。

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
`StateDemand` 协调器。协调器至少包含 `wanted_seq`、`kind_priority`、
`deadline`、`reason_mask`、`generation` 和 `in_flight`，并保证同一 gid 同时
最多一个物理 `/state` 请求在途。所有物理尝试（包括 retry）仍 MUST 经过该
令牌共享的 `StateThrottle`。

#### Scenario: 一阵 SSE 帧合并为一个需求

- **WHEN** 同一 gid 在一个状态请求在途期间收到 100 个递增 SSE watermark
- **THEN** 协调器只保留一个待处理需求，`wanted_seq` 为其中最大值，不能为
  每一帧创建物理 `/state` 请求

#### Scenario: 窗口确认与普通追赶同时到达

- **WHEN** 同一 gid 已有 `SSE_DELTA` 需求，随后出现 `WINDOW_CONFIRM` 或
  `RESYNC` 需求
- **THEN** 需求合并为一次物理请求，`kind_priority` 取最高优先级，
  `reason_mask` 按位 OR，不能并发创建第二个同 gid 请求

#### Scenario: 十场彼此隔离

- **WHEN** 十个 gid 同时产生 SSE 和窗口需求
- **THEN** 每个 gid 各自最多一个物理请求在途，跨 gid 的许可仍由同一个共享
  throttle 仲裁，不因协调器而绕过 15/s 配额

### Requirement: StateDemand 合并字段遵循确定性规则

同一 gid 的新需求 MUST 按以下规则合并：`wanted_seq = max()`（未知值保留
unknown 语义）、`deadline` 取仍有效截止中的最早值、`reason_mask` 取 OR、
`kind_priority` 遵循 `WINDOW_CONFIRM > RESYNC > SSE_DELTA`。已过期窗口不得
继续以有效 urgent 需求占据优先级。

#### Scenario: 需求同时含 watermark 和窗口截止

- **WHEN** `wanted_seq=120` 的普通需求与 deadline 更早的窗口确认需求合并
- **THEN** 合并需求保留至少 `wanted_seq=120`、最早有效 deadline 和
  `WINDOW_CONFIRM` 优先级

#### Scenario: 过期需求重新仲裁

- **WHEN** 需求在队列中等待至 deadline 已过，而另一个窗口仍有未来有效截止
- **THEN** 过期需求降级为追赶/普通需求，不得继续挤掉未来有效窗口

### Requirement: 逻辑完成条件按 reason 分别保留

一次物理 `/state` 返回后，系统 MUST 对 `reason_mask` 中每个逻辑 reason
分别判断是否 satisfied，不得因为一次 seq=0 返回就清空全部 reason。至少：

- `RESYNC` 在权威快照重建且返回 watermark 覆盖其目标时完成；
- `SSE_DELTA` 在返回 watermark 覆盖其目标时完成；
- `WINDOW_CONFIRM` 只有在 authoritative `WindowId` 仍相同、phase 正确、
  本家仍在 `responding_seats`、快照提供精确截止且截止仍有效时完成。

#### Scenario: seq=0 满足重锚但未满足窗口确认

- **WHEN** 合并需求包含 `RESYNC | WINDOW_CONFIRM`，返回的 seq=0 快照已能重建
  镜像，但 phase 已变为 `draw` 或 WindowId 不再相同
- **THEN** `RESYNC` 标记 satisfied，`WINDOW_CONFIRM` 标记 stale/closed/
  unconfirmed 等明确结果，不能把两者一起记为成功

#### Scenario: 返回 watermark 未覆盖普通需求

- **WHEN** 响应 seq 小于 `SSE_DELTA` 的 wanted_seq
- **THEN** `SSE_DELTA` reason 保留在待处理需求中，下一次请求继续追赶

### Requirement: generation 防止在途需求竞态

物理请求发出时 MUST 记录 `started_generation`。请求在途期间任何新 SSE、窗口
或重锚需求 MUST 更新当前 generation；返回时若 generation 已变化，系统 MUST
先按返回事实分别结算旧 reason，再依据最新目标最多补发一次请求，且不得用旧
响应覆盖新窗口。

#### Scenario: 在途期间窗口目标推进

- **WHEN** generation=7 的请求已发出，期间 SSE watermark 推进且新增窗口确认
  使 generation=8
- **THEN** 返回 generation=7 的响应不直接结束协调；系统按最新 reason/目标
  合并一次补请求，补请求仍遵循优先级和共享 throttle

#### Scenario: 在途期间没有新需求

- **WHEN** 请求返回时当前 generation 与 `started_generation` 相同且所有 reason
  均 satisfied
- **THEN** 不创建重复补请求，`in_flight` 正确归零

### Requirement: 无合法反应的窗口不得长期持有 urgent

系统 SHALL 在 BotClient 完成本地合法集检查后，若当前弃牌对本家没有任何非 pass 的
碰、杠或吃候选时清除该窗口的 urgent deadline，回退为 `SSE_DELTA` 普通追赶。
该降级 MUST NOT 提前授权动作；任何吃/碰动作仍须通过权威对应 phase 快照确认。

#### Scenario: 他家弃牌对本家无非 pass 合法动作

- **WHEN** 本地 `response_peng` 和适用的 `response_chi` 合法集均只有 pass
- **THEN** 该 gid 不再以 `WINDOW_CONFIRM` urgent 排队，后续状态刷新按普通
  SSE 需求处理

#### Scenario: 有合法候选的窗口保持 urgent

- **WHEN** 本家存在至少一个非 pass 合法碰/杠或下家弃牌存在合法吃
- **THEN** 对应窗口确认需求保留 urgent 优先级，直到确认、关闭或过期降级

### Requirement: state/action 记录可证明的 HTTP 阶段边界

每个物理 state/action attempt SHALL 记录本地阶段边界和派生耗时：
`http_start` 为 `urlopen` 调用前，`headers_received` 为 `urlopen()` 返回时，
`body_finished` 为 `response.read()` 完成时；`pre_read_ms`、`read_ms`、
`total_ms` MUST 使用 monotonic 差值计算。DNS、connect、TLS、send 若未由传输
库单独提供，必须标为 unavailable/null，不得从 `pre_read_ms` 拆猜。

#### Scenario: 响应头快但 body 慢

- **WHEN** `urlopen()` 很快返回而 `response.read()` 延迟明显
- **THEN** 记录的 `pre_read_ms` 较小、`read_ms` 较大、`total_ms` 为两者之和，
  并保留 headers_received 与 body_finished 边界

#### Scenario: 响应头前阶段慢

- **WHEN** DNS/connect/TLS/等待响应头耗时较长而 body 读取很快
- **THEN** 耗时归入 `pre_read_ms`，不可观测的细分字段仍为 unavailable/null

#### Scenario: HTTPError 或 body 读取失败

- **WHEN** 物理请求在收到响应头后以 HTTPError 或读取异常结束
- **THEN** 已发生的 attempt、状态码和可用边界仍被记录，不能因 JSON 解析/读取
  失败而丢失物理尝试证据

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

记录器 MUST 区分逻辑 state/action 请求和其物理 attempts；每次物理 retry 都
记录独立 throttle/HTTP 阶段，逻辑记录聚合 attempts、429、queue 和最终结果。
新增 WindowId、WindowAttemptKey、reason、generation、raw header 及阶段字段
均为可选 JSON-safe 字段，旧日志缺失时读取为 unknown，不得破坏既有回放。

#### Scenario: 一个逻辑 state 经历 429 后重试

- **WHEN**第一次物理尝试返回 429，退避后第二次尝试成功
- **THEN** 两次物理 attempt 均有独立 throttle/HTTP 诊断，逻辑 req 记录总尝试数、
  429 数和最终状态，并能关联同一 gid/reason/generation

#### Scenario: 旧日志回放

- **WHEN**回放器读取没有新字段的历史 JSONL
- **THEN** 回放继续工作，并将 WindowId/阶段/generation 相关字段视为 unknown，
  不把 unknown 当作 authoritative 身份

### Requirement: 房间完整性分类隔离主验收分母

验收工具 SHALL 将房间分类为：

- `complete`：正常 end、必要日志完整且 round/game completeness 可判定；
- `partial`：工具中断、日志缺尾、缺 end 或关键事件无法闭合；
- `protocol_skipped`：协议本身不提供 `round_ended` 等结算证据，并明确记录
  无法验证的层级。

`partial` 与 `protocol_skipped` MAY 保留为诊断样本，但 MUST NOT 进入 rate、
claim_miss、queue 等房级 A/B 主指标。

#### Scenario: 正常收官完整房

- **WHEN**房间有正常 `end`、必要 req/snapshot/events/decision/action 日志，且
  round/game 完整性可判定
- **THEN**房间标记 `complete`，可进入房级主指标

#### Scenario: 工具中断或日志缺尾

- **WHEN**房间没有 end 或最后一局关键日志缺失
- **THEN**房间标记 `partial`，保留诊断数据但从主指标分母排除

#### Scenario: 协议不提供结算事件

- **WHEN**当前协议没有 `round_ended`，但传输和窗口日志完整
- **THEN**房间标记 `protocol_skipped` 并明确 game 层不可验证，不伪造 complete

### Requirement: 完整房报告覆盖 transport、window、game 三层指标

每个 `complete` 房报告 MUST 分层记录 transport（429、409、uncertain、各类
queue）、window（eligible、confirm requested/confirmed/submitted、timeout、
claim_miss）和 game（round started/ended、winner、score delta/final score）。
归一化指标至少包含 `state_429 / physical_state_attempts`、
`claim_miss / eligible_windows`、`WINDOW_CONFIRM seq=0 / eligible_windows`、
`duplicate_or_coalesced_state_demands / game`，并单独报告 complete/partial/
protocol_skipped 数量。

#### Scenario: 三至五个完整房基线

- **WHEN**使用启发式 BOT、SSE+增量 `/state`、15/s 收集 3～5 个 complete 房
- **THEN**每房均输出三层指标和上述归一化分母，partial/protocol_skipped 不混入
  房级 A/B 汇总

#### Scenario: demand 合并效果验证

- **WHEN**同一 gid 收到重复 SSE/窗口需求并由 StateDemand 合并
- **THEN**报告能够同时显示逻辑需求数、物理 state attempts 和
  `duplicate_or_coalesced_state_demands`，以证明物理请求减少而非只改变日志
  外观

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
