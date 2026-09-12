## Context

当前客户端已经把 SSE 作为唤醒源、以权威 `/state` 快照授权动作，并通过每个 gid 的 `StateDemand` 合并 `SSE_DELTA`、`RESYNC` 和 `WINDOW_CONFIRM`。`WindowId` 也已经禁止使用 snapshot watermark 冒充源弃牌序号，动作 409/传输不确定结果会触发 `seq=0` 重锚且不重发旧动作。

剩余问题集中在证据闭环：`window_confirm`、`decision`、`action`、`claim_miss` 和 timeout 仍是相互独立的日志行，验收脚本只能用有限启发式连接；窗口 409 缺少完整授权快照/响应边界；legacy identity 与真实窗口无法严格分开；gap、StateDemand 收束和三层完整性也没有统一的 canonical 结果。

约束是保持现有 BOT 策略、规则、15/s shared throttle、sleep/retry/margin 和安全动作恢复语义不变。本 change 的输出必须兼容旧 JSONL，并且不把无法证明的事实升级成强归因。

## Goals / Non-Goals

**Goals:**

- 为 authoritative `WindowAttemptKey` 建立可重建的 source → confirm → authorization → decision → POST → result → terminal 时间线。
- 让 acceptance 对每个窗口输出互斥的 canonical outcome、loss stage/reason、identity/evidence quality。
- 记录 action 409/uncertain 所需的授权快照、精确 deadline、HTTP 边界、server trace（若协议提供）和 RESYNC 链。
- 把 `legacy_unresolved`、缺少 identity、缺尾日志、协议缺少 settlement marker 与客户端真实损失分层表达。
- 保持旧日志可读，旧证据进入 `identity_unverifiable` 或 `req_fallback` 等明确弱证据边界。

**Non-Goals:**

- 不修改 BOT 策略、吃/碰/杠规则、15/s 默认速率、普通轮询/sleep/retry 或提交 margin。
- 不把 snapshot watermark、牌河长度、副露数量或 owner+tile 猜成 WindowId。
- 不重发已发送动作；不把服务端 timeout、raw `claim_miss` 或一次 409 直接当作客户端根因。
- 不把缺少 `round_ended` 的 game 层伪造成 complete，也不把旧房加入冻结版本通过分母。

## Decisions

### 1. 使用 Recorder 结构化事件作为事实源，acceptance 离线生成 canonical resolution

在 `Recorder` 增加开放字段的 timeline/authorization/terminal 记录入口，并让 BotClient 在已有窗口、决策、动作和重锚路径写入同一组 `window_id`、`window_attempt_key`、`logical_request_id`、`decision_id` 和 `attempt_index`。不在运行时维护一个必须持久化的复杂状态机；验收脚本按日志事实重建状态。

这样可以兼容旧日志，也能让线上运行不因观测代码异常中断。代价是缺失记录不能被补猜，脚本必须返回 `UNKNOWN`/`identity_unverifiable`。

### 2. 以 phase-independent WindowId 分组、以 WindowAttemptKey 分阶段

peng 与 chi 共享 WindowId，分别用 `(WindowId, response_peng)` 和 `(WindowId, response_chi)` 建立 attempt。比较 `peng→chi` 时只比较 WindowId，不比较完整 attempt key；反向 `chi→peng` 只可终止旧 phase，不能创建新窗口。

同一个 logical request 可以有多个物理 state attempts；同一个 WindowAttemptKey 也可以经历 successor state requests，但非 PASS 动作始终最多一次 POST。`logical_request_id` 与 `attempt_index` 因此分开记录。

### 3. 使用单调时间计算时序，epoch 只用于跨进程/协议边界

队列进入、限速获准、HTTP start、headers received、body finished、decision 和 POST 的本地耗时使用 monotonic；协议 `window_deadline_ms` 作为 `exact_deadline_at` 的 epoch 事实保存，并在本地比较时映射为 monotonic。HTTPError 视为已收到 headers，error body 完成才设置 body_finished。

### 4. 用显式终态优先级消除重复损失

离线 resolver 按 `SUCCESS > STRATEGY_PASS > RULE_PREEMPTED > POST_REJECTED > POST_UNCERTAIN > SUBMIT > DECISION > CONFIRM > UNKNOWN` 收束一个 attempt。raw `claim_miss`、服务器 timeout 和 action 结果全部保留，但不能绕过该优先级产生第二个主归因；若 raw miss 被更高证据覆盖，报告 `false_claim_miss`。

### 5. identity coverage 是窗口 evidence 的硬门槛

只有显式 source field、`tile_discarded` 自身 event seq 或同一连续 pending 生命周期中安全 carry 的 source seq 才算 authoritative。legacy window 可以用于当前动作判断和弱诊断，但不能参加强三段式归因、跨重锚去重或 `strong_window_complete`。

### 6. 恢复链只记录事实，不调整策略参数

409/uncertain 记录授权快照、deadline、客户端 request timing、server trace header（没有则 null），然后只允许 `seq=0` RESYNC。rate/retry/margin 只作为 `transport_contributed` 证据输出，若需调参另开 change。

## Risks / Trade-offs

- [旧日志缺少 WindowId 或 timeline 字段] → 保留兼容扫描，但标记 `identity_unverifiable`/`unknown`，不放入强证据分母。
- [观测入口异常影响主循环] → Recorder 字段写入沿用静默失败和兼容签名；验收脚本对坏行降级而不猜测。
- [服务端只提供 terminal timeout、不提供 exact deadline/trace] → 分别记录 `server_timeout_before_authoritative_confirm` 和 `server_trace_id=null`，不伪造本地 deadline 或服务端根因。
- [一个弃牌窗口同时产生 peng 与 chi 记录] → phase-independent eligible key 合并统计，phase-specific lifecycle 保留，避免把一个机会算两次。
- [线上没有真实 miss] → 仍要求验证 SUCCESS、STRATEGY_PASS、RULE_PREEMPTED、AUTHORIZED、decision、POST 的关联链；不能以零 miss 代替证据。

## Migration Plan

1. 先增加 Recorder/BotClient/API 的字段和离线 resolver，保持旧字段不变。
2. 增加 fake-clock、replay、409/uncertain、identity、gap 和 demand terminal 测试，跑全量 pytest 与 `git diff --check`。
3. 在 clean commit 上保存 run manifest，以固定 BOT/15/s/SSE+incremental 命令串行运行 3--5 个独立房，每房 10 局。
4. 每房分别生成 transport/window/game 分层报告，再生成 cross-room 汇总；旧房只作诊断，不进入最终分母。
5. 若发现 rate/retry 或 settlement 协议问题，记录为后续独立 change；本 change 可回滚到上一 clean commit，不需要数据迁移。

## Open Questions

- 服务端是否会在 action 409 的 response header 中稳定提供 trace id；客户端只读取协议实际提供的 header，不做猜测。
- 线上协议是否最终能为 snapshot-only 窗口提供 `source_discard_seq` 或 opaque `response_window_id`；在确认前保持 legacy exclusion。
- 3--5 个新房中是否出现足够的真实 409/uncertain/decision loss，以验证 acceptance 不是只对 fake fixture 工作。
