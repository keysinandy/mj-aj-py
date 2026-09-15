## Context

本 change 是 `docs/think.md` 对应的第一阶段结构优化。探索已检查当前代码、HANDOFF、主规格和冻结日志报告；这里只记录可复现的结构问题，不把 HTTP 延迟归因扩展成未经验证的调度结论。

- `StateDemand` 已实现 FULL 覆盖 DELTA、reason-specific reconcile 和在途 SSE 合并，继续复用。
- `StateDemand.start_request()` 在实际 HTTP 前固定请求并设置 `in_flight`；排队期间提交 WINDOW_CONFIRM 后，最新需求为 FULL，原计划仍为 DELTA。
- 各 StateDemand 都从 `state-1` 开始编号，现有 `X-Client-Request-Id` 因此不能独立区分不同 gid 的请求。
- BotClient 首次 RESYNC 直接收到 `finished` 时可留下 PENDING RESYNC；通用退出路径也缺少统一的需求关闭操作。
- 确认对象、chi 等待字典、StateDemand reason 都携带部分时间和重试状态；首次确认、快照后等待与普通等待路径有不同的既有时机。
- 当前 `_request()` 内的 429/网关/网络重试与每次物理许可已联通。本阶段不改变重试次数、退避或 timeout 选择。

探索阶段相关现有测试为 62 passed，另以直接调用复现了上述候选升级、ID 和结束清理问题。这是设计输入，不是本 change 的实现验收。

冻结 `ab371b6` 报告含 30 局：C4=22、阶段不可观测=28、未决=4，C1/C2/C3/C5=0，结束需求 clean=30。它不能证明结构重构将改善 C4，也未覆盖首次重锚就结束的边界。当前两个未归档 change 的任务和完成状态保持独立。

## Goals / Non-Goals

**Goals:**

- 同 gid 只有一个可调度候选或一个被独占的获取链，候选在发出前使用最新有效需求。
- 明确逻辑需求、排队候选、逻辑请求、物理 attempt、响应应用的边界。
- 统一确认状态的所有权，并阻止跨窗口或跨 reason 版本的错误完成。
- 所有退出路径可释放状态并保留准确的完成/中止证据。
- 保持调度与授权算法可对照，为下一阶段实验提供可信计数和关联字段。

**Non-Goals:**

- 不引入 asyncio、连接池替换、hedged GET、action retry 或服务端改动。
- 不启用 capacity reservation、minimum slack、新公平调度、自适应提前量或有界 retry/successor 新策略。
- 不调整 15/s 默认值、EDF 排序、现有普通等待/老化行为、429 反馈冷却、EAGER_CHI_*、提交余量或 socket timeout。
- 不改 BOT 策略、合法动作规则、WindowId 来源和 canonical outcome precedence。
- 不把请求清理干净等同于窗口完整、结算完整或成功对局。

## Decisions

### 1. 保留每局工作线程，提取职责明确的协作对象

| 模块 | 所有权与职责 |
| --- | --- |
| `state_demand.py` | 每 gid 的 reason ledger、独立 reason revision、watermark target、派生 mode/deadline 和需求关闭 |
| `state_fetch.py` | `StateFetchCoordinator` 串联候选注册、发送、结果交付、应用确认与 cleanup |
| `state_scheduler.py` | `StateScheduler` 暴露 register/update/acquire/withdraw 接口，管理候选视图，使用现有仲裁策略 |
| `throttle.py` | 继续持有唯一等待队列、Condition、物理许可节奏及 429 反馈；候选更新不新增第二条队列 |
| `window_confirmation.py` | `WindowConfirmation` 和 `WindowTiming`，持有一次 WindowAttemptKey 的观察状态和既有时间计算 |
| `api.py` | 同步 HTTP 执行、现有 retry/backoff、每 attempt 的许可与计时钩子 |
| `bot_client.py` | 每局镜像、事件应用、规则判断、动作和终止事实；将窗口核对结果交回协调器 |

SSE 线程只更新 watermark/唤醒需求，不读写 Mirror，不从 SSE 帧创建动作授权。只有已经取得窗口事实的工作线程或其明确的确认状态输入才能提出 WINDOW_CONFIRM。

选择这一方案是因为现有线程模型已经保证每局镜像的串行处理。仅给 Throttle 增加参数无法覆盖完成与清理边界；全面迁移异步模型会把 SSE、HTTP、策略和回放一起纳入变更。

### 2. 将候选与冻结请求分开，只有一条等待队列

```text
IDLE -> QUEUED -> ADMITTED -> IN_FLIGHT -> RECONCILING -> IDLE
          |          |                       |
          |          +-- 未开始 HTTP 可中止   +-- 仍有需求 -> QUEUED
          +-- 升级 / 合并 / 撤回

任意状态收到 close -> CLOSING -> 释放完成 -> CLOSED
```

- `QUEUED` 保存 gid、reason revision/metadata、候选编号及当前 mode/cursor/deadline 视图。此时不计物理发送，不设置代表物理链占用的 `in_flight`。
- 新需求更新同一个候选。FULL dominates DELTA；DELTA 的物理 seq 始终是本地已应用 cursor，watermark 只作为追平目标。
- 仲裁器仍按现有有效 deadline EDF、过期降级及普通 FIFO/老化规则选择。reason priority 在本阶段仍用于需求聚合，不新增跨 gid reason-class 排序。
- 在实际可发放许可时，重新核对候选并冻结 `StateRequest`。这是线性化边界：之前的升级纳入该请求；之后的新需求留待响应核对，不修改被冻结的 seq/mode。
- 如果所有 reason 在许可发放前已完成，则撤回候选且不消耗许可。发放后、HTTP 开始前发生停止则记录未发送取消，不退还许可，也不计物理 attempt。
- 唯一等待队列继续留在 StateThrottle。StateScheduler 是可更新候选的调度入口，不能先等待 scheduler 再进入第二个独立 throttle 队列。
- 并发重复 fetch 调用不得把同一个 StateRequest 返回给两个执行 HTTP 的调用者；通过同一获取链等待结果或明确返回 busy。

发送后的通用 retry 保留同一逻辑请求及固定参数，每个 attempt 仍单独 acquire。retry 期间同 gid 的新需求只能等待这一获取链完成；本阶段不取消已发送请求来抢发 FULL。

### 3. 完成检查持续到镜像应用结束

HTTP 完成后进入 RECONCILING，仍持有同 gid 的获取独占权。工作线程完成响应应用并交回 reason-specific 结果后，才允许下一次发送：

- SSE_DELTA：响应 watermark 覆盖最新目标，而且对应事件已连续应用或有效 FULL 已重建镜像，才能完成。不得在应用失败前根据返回 seq 提前宣称同步成功。
- RESYNC：必须有成功的全量镜像重建确认；seq=0、200 或含 snapshot 字段本身不构成完成。
- WINDOW_CONFIRM：由窗口 resolver 核对最新 WindowAttemptKey、阶段及授权条件，返回状态和对应 revision。
- snapshot/事件应用失败时保留待完成需求或发出明确 RESYNC，沿用现有有限恢复路径；不把失败应用当作成功消费。

本阶段协调器接收显式响应和传输结果，避免业务逻辑依赖可能被后续动作覆盖的 TLS 诊断；Api 的旧 TLS 读取面保留为兼容层。

### 4. 用 reason revision 防止跨代完成

全局 `generation` 继续用于诊断，但不代表“窗口已更新”。各 reason 独立维护语义 revision；重复、低 watermark 输入不制造新 revision，也不刷新等待年龄。

- SSE watermark 更新不使同一 WINDOW_CONFIRM 的结果失效。
- RESYNC 新原因在请求已经发出后到达，旧重建结果只有在明确证明覆盖该原因时才能完成它；没有覆盖证明则保持 PENDING。
- 新 WindowAttemptKey 替换旧确认后，旧结果不得按 reason 名称直接完成新确认。新响应若也包含新窗口事实，必须由 resolver 重新核对新 key/revision 后才能完成。
- 返回快照覆盖在途期间新增确认时，允许一条物理请求同时完成多个原因；没有完整授权事实则保留 successor 需求。
- successor 仅在应用和核对后仍有 PENDING reason 时创建。不能仅因为全局 generation 改变或曾经存在旧确认而生成。

这是对当前 reason-specific reconcile 的补强，不增加基于时间邻近、owner/tile 猜测或 snapshot watermark 的窗口身份 fallback。

### 5. 将确认状态和四种时间含义集中保存

`WindowConfirmation` 是同一 WindowAttemptKey 的唯一可变观察状态；需求 ledger 和传输日志保存带 revision 的不可变视图。旧 `_WindowConfirm` 异常可暂时作为引用该对象的兼容信号，chi 字典逐步移出重复状态。

`WindowTiming` 明确保存：

- `not_before`：既有逻辑决定的最早请求时刻；提取时保留首次立即确认、固定提前量和快照后等待之间的现有差异，不统一成新的提前算法。
- `scheduler_deadline`：本地排序估计；保留来源标签，不授予动作权限。
- `observation_budget_deadline`：本地确认观察预算。初始数值和继承方式沿用现有 retry_deadline 行为，但不再复用字段名称。
- `exact_window_deadline`：仅来自期望阶段的权威 snapshot，包含对应 WindowAttemptKey 和来源。peng 的精确截止加一个窗口长度仍是 chi 的本地估计。

时间等待和持续时间用 monotonic；协议 epoch/raw 值用于原有授权检查及诊断转换，保留现有授权余量和转换方式。本阶段不顺带改变时钟算法。

phase_pending、缺身份、缺精确截止继续保持未授权；本地观察预算耗尽记录 `confirmation_observation_budget_exhausted` 及具体原因，不能伪装成服务端窗口超时。旧 reason 别名保留在回放适配中。已有权威终止和动作安全判定保持不变。

### 6. 终止关闭可重复调用，且不掩盖失败

协调器提供幂等 close：禁止新需求/新候选，撤回排队项，标记剩余 PENDING reason 为 TERMINAL 并携带 `game_finished`、`inaccessible`、`worker_error` 或 `stopped` 等来源。

在途 HTTP 不能被假装释放。close 先进入 CLOSING，等调用返回或抛出后释放独占状态；期间返回的数据保留传输记录但不能触发新动作。retry 的下一 attempt 在显式停止后不再发送；这是退出处理，不是新的窗口重试预算。阻塞中的 urllib 调用仍受既有 timeout 约束，不承诺新增硬关闭时限。

完成清理后输出终态记录：queued=0、reason_mask=0、in_flight=false。finished 可以沿用分层完成判断；404 保留 inaccessible，错误/停止保留失败或部分证据。进程被强制结束、HTTP 尚未释放或日志缺尾时保持 partial/missing，不能制造 clean end。

### 7. 明确锁顺序和提交边界

Scheduler/Throttle Condition 保护唯一等待队列及许可；StateDemand 锁保护 reason 和冻结状态。两者同时需要时采用 scheduler -> demand 的固定顺序。

生产者先在 demand 锁内更新并释放，再通知 scheduler；禁止持有 demand 锁获取 scheduler 锁。兼容 Queue 的通知遵守相同约束，不在其 mutex 内回调 scheduler。临近发放时 scheduler 持锁获取 demand 视图并冻结；更新若发生在冻结之后，就属于在途新需求。

任何锁内均不做 HTTP、sleep、Mirror 应用、策略、日志 I/O 或用户回调。无变化输入不重置候选首次排队时间/FIFO serial。关闭与发放使用同一同步边界，保证关闭后的候选不能被重新派发。

### 8. 标识和计数按真实边界记录

- `candidate_id` 关联排队前后的需求事件。
- `logical_request_id` 在冻结请求时分配，形式为 `{run_id}:state:{gid}:{request_number}`；编号分配器跨同 gid 工作线程重启继续唯一，run_id 跨运行变化。
- `transport_request_id` 唯一标识每次物理 attempt，例如 `{logical_request_id}:attempt:{attempt_index}`。同一逻辑重试保留 logical id，successor 创建新 logical id 并带 `successor_of`。
- 保留 `X-Client-Request-Id` 和 `X-Client-Attempt-Index`，增加对应传输关联字段/头；ID 视为不透明字符串，不包含 token、完整 URL 或动作内容。
- 新增明确的 `logical_state_requests`（冻结请求）、`physical_state_attempts`（实际调用 HTTP）、`substituted_candidates`、`cancelled_before_send`；按 request/attempt id 去重聚合。
- 原 `physical_state_requests` 历史上在 start_request 增加，保留为旧逻辑请求口径并显式标记来源/版本，不静默改成物理 attempt。旧 `state_attempts` 与新物理统计对齐验证。
- 每次传输 attempt 保存冻结 reason 视图；响应后保存 `satisfied_reasons` 及各 revision，不能把收到响应后才出现的需求写成发送时已经携带。
- `http_start -> headers` 保持现有 urllib 聚合定义，不命名为准确 wire-send latency；headers、body、应用完成时间分别保留。

仅有旧 `state-1` 的日志按 gid/file 等已知作用域读取，不能生成跨运行唯一性或猜测外部关联。新物理计数以实际 attempt 记录为依据，不从 coalescing 比率倒算。

### 9. 分阶段验证，避免引入算法实验

先用 fake clock、可控 transport 和线程屏障验证冻结边界、更新/关闭竞态及 reason 核对，测试必须走真实 Api admission 钩子和协调器，不能只测 StateDemand 或 `_head()`。

窗口提取用既有场景对照确认请求时序、PASS/成功/409/未知 POST、pending peng-to-chi、合法集和精确截止语义。新增结构修复允许减少被替代请求，但没有需求升级/退出的对照路径应保持原有调度与 retry 行为。

通过 focused/full tests、OpenSpec strict validation、diff check 和冻结三房兼容回放后，冻结新实现版本。按照现有主规格先运行一房 canary，无硬失败后扩展至总计 3–5 个串行独立房、每房 10 局；策略 BOT、SSE+增量、state-rate=15 固定。旧诊断房不计入分母；若 canary 后改代码，旧版本房也不进入最终版本分母。

逐房报告物理速率、每窗口/每时长请求量、429、queue 分布、C1–C5、需求关闭及动作安全，单独列身份、阶段和结算缺失。物理启动速率依据当前 limiter 的时间窗定义验证，不能用许可次数代替真实 HTTP 尝试，也不能声称客户端发送节奏证明服务端到达节奏。

## Risks / Trade-offs

- [冻结与更新竞态] → 明确线性化边界、固定锁顺序，并覆盖许可同时到达、升级和 close 的屏障测试。
- [提取确认状态时改变既有时机] → 先做行为对照，保留现有固定值与各入口差异，算法统一留给后续 change。
- [清理把错误记录成成功] → 资源释放状态与 transport/window/game 完成状态独立，验证 stopped/error/inaccessible 终态。
- [旧指标被重新解释] → 增加 schema/source 标签和独立 attempt 计数，冻结回放必须保持 canonical 窗口结果。
- [旧 fake Api 绕过新协调器] → 保留小签名兼容，但增加真实 Api+假 HTTP 的集成测试覆盖实际发送链。
- [普通请求仍可能在持续紧急流量下等待过久] → 如实保留和记录现有行为；本阶段不声称提供 bounded fairness，后续调度 change 单独定义取舍。
- [HTTP 内部仍可能长时间重试] → 保持窗口重试策略基线，显式停止可阻止下一 attempt；deadline-aware retry 属于后续实验。
- [线上样本与网络条件不同] → 不设置本阶段 C4 必须下降的结论，以结构/安全不变量及无可归因回归为验收依据。

## Migration Plan

1. 固化边界复现和现有时序/重试对照，加入候选、revision、确认状态和传输结果类型。
2. 接入单队列 scheduler/fetch coordinator，迁移 BotClient 状态获取入口，完成发放前升级和应用后核对。
3. 提取确认状态并接通所有正常/异常/停止清理路径，保留旧接口所需的薄适配。
4. 增加 ID、attempt/candidate 记录与版本化聚合，运行完整离线验证及旧三房回放。
5. 冻结提交并进行独立新房验收；提交之前保留用户已有工作树改动，不把无关文档删除纳入提交。
6. 回退以停止新房、恢复到冻结前的已知版本完成；已发动作和在途 HTTP 不做中途策略切换，不重发旧动作。新日志采用增量字段，旧分析器仍可读取基础记录。

本阶段不为已经存在的 coalescing 或安全修复添加关闭开关，也不引入未启用的 reserve/slack/lead 参数。后续算法实验再各自提供独立开关和对照策略。

## Open Questions

- 网关/服务端是否可提供与新 ID 关联的外部时序证据尚未验证；这影响后续 C4 根因和算法实验，不阻塞本阶段生命周期实现。
- `window-identity-protocol` 的服务端身份字段仍未确定；本 change 保持现有权威/legacy 分界，不将该协议任务隐含纳入客户端重构。
