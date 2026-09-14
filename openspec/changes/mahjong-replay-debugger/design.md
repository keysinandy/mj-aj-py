## Context

输入需求为 `docs/reply.md`。现有 `mj/replay.py` 可以解析服务端 `blocks/start_hands/events/rounds`，`mj/log_replay.py` 用当前 Mirror 重建本地日志并检查合法集，`scripts/render_sse_state_timeline.py` 可以导出自包含 HTML。它们尚未提供三份独立状态、逐次真实应用证据或以因果链为依据的首次故障定位。

2026-09-14 的只读探索确认了以下约束：

- `a_b85ff51c0958_r1_b0_t0` 的本地文件有 774 条 req，但没有 SSE 帧或 state_reconcile 记录；不能追溯未记录的收帧和执行步骤。
- `a_7b0691487195_r1_b0_t0` 有 878 条 SSE，payload 均只有 `seq/closed`。完整麻将事实由 `/state` 获取，而不是 SSE。
- Recorder 的 req.res 是摘要，snapshot/events 分开记录且没有直接请求关联；snapshot/events 写入在镜像应用之前。state_reconcile 说明应用边界，但没有每次 transition 前后状态。
- 决策 digest 只有手牌数量、墙数和局号，不能证明完整牌河、副露与手牌内容。
- Mirror 消费认领事件后从牌河移除被认领牌；文档要求展示保留 called 标记的历史牌河。Mirror 也不独立保存全部服务端 turn/phase 语义。
- 两份日志用现有工具回放均没有合法集错误，但各缺一局 round_ended，且存在协议跳过区间；这些是兼容样本，不是完整证据样本。

原文的产品目标保留；协议适配、证据强度和分类规则以本 change 的规格为实施契约。`docs/reply.md` 保持原样。

## Goals / Non-Goals

**Goals:**

- 一个离线命令生成可直接打开的复盘 HTML 和机器可读 JSON。
- 用服务端事件顺序和本地因果顺序独立重建三个世界，并保留原始证据。
- 显示四家牌桌、来源与未知状态，支持随机跳转、播放、诊断定位和请求检查。
- 精确日志能指出遗漏 transition 的第一处错误及恢复请求；旧日志明确展示可重建部分和证据缺口。
- 补充可选、可检测缺失的运行时追踪，保持请求调度、动作授权及 BOT 选择行为。

**Non-Goals:**

- 复盘器访问生产服务、提交动作、自动修复状态、自动下载服务端 timeline。
- 本次改动优化 Q/Q0、策略权重、限速、窗口时序或服务端协议。
- 用隐藏未来牌、自动代过、推测首摸等信息补造事实。
- 跨局统计、多人协作、数据库服务或前端构建工具链。可以选择不同 round，但统计限当前 round，并另列归属未知记录。

## Decisions

### 1. Python 编译复盘数据，静态 HTML 消费统一结果

新增独立包 `mj/replay_debugger/`：

| 模块 | 职责 |
| --- | --- |
| `model.py` | 版本化数据合同、来源、证据强度、游标、诊断 |
| `adapters.py` | 服务端导出、本地 JSONL、DumpingApi dump、trace 的只读适配 |
| `timeline.py` | 因果关联、局边界、本地顺序、SeqFrame 与 checkpoint 索引 |
| `state.py` | 独立参考 reducer、实际 trace 重建、历史牌河投影 |
| `diagnostics.py` | 业务 diff、不变量、请求分类、首次差异与恢复链 |
| `export.py` 和 `assets/` | JSON 输出与内嵌 CSS/JS 的自包含 HTML |
| `__main__.py` | 离线 CLI、输入身份检查和输出路径校验 |

预期入口为 `python3 -m mj.replay_debugger <gid或jsonl路径> --server <timeline.json> --trace <trace.jsonl> --http-dump <文件或目录> --round <局号> --out <目录>`；除本地日志与 out 外，其余参数可选。gid 查找仅搜索本地文件；多候选时展示歧义，不静默混合。输出为 `replay.json` 和 `index.html`，不得覆盖输入，已有输出需显式 `--overwrite`。

选择此方案是因为现有代码与数据均为 Python/JSON，离线排查不需要 Node、HTTP 服务或数据库。现有 HTML 脚本作为样式与安全嵌入参考，保留原有入口；不继续把状态引擎塞进其模板字符串。

### 2. 证据不可变，来源角色与证据强度分离

主要合同为 `ReplaySession`、`RawRecord`、`NormalizedEvent`、`SeqFrame`、`LocalStep`、`GameState`、`StateRequestRecord`、`Diagnostic` 和 `Checkpoint`。根合同包含 `schemaVersion`、规则/分析器版本、输入摘要和各来源覆盖度。

- RawRecord 保存文件来源角色、内容摘要、行号/字节位置、原始内容与时间字段。相同事件在多个来源出现时保留所有 rawRefs，不破坏输入。
- 来源角色包括 SERVER_TIMELINE、SSE、STATE_RESPONSE、LOCAL_TRACE、LOCAL_DERIVED；本地收到的服务端事件仍须区分“原生产者”与“本地何时获得”，不能当作独立完整 Server Ground Truth。
- 证据强度包括 RECORDED、DERIVED、UNKNOWN；字段值的知识状态另分 KNOWN、UNKNOWN、HIDDEN、NOT_APPLICABLE。已知空集合是 KNOWN + []。
- 输入摘要、显式角色与规范化配置参与稳定 ID；绝对路径、生成时间不影响语义 ID。原始事件、诊断和推导状态保留可追踪引用。
- 损坏行、未知事件、冲突、截断和丢失追踪必须生成数据质量记录；格式错误不能静默丢弃。身份确定冲突的 gid/座位/round 不合并。

不采用“先合并所有数据再按时间排序”的方案，它会丢失信息何时被本地知道，以及各来源是否相互独立。

### 3. 页面双维度游标，底层保存全局本地因果顺序

```text
Cursor = { gameId, roundNo, seqNo, phase: BEFORE|AFTER,
           localStepIndex, localOrdinal }
```

`localOrdinal` 在新 trace 中表示同一采集 session/gid 的捕获顺序；旧日志以文件行序建立稳定展示序，但标记为 DERIVED，不能当作精确执行顺序。

- 服务端事件按 game/round/事件 seq 标识；同标识不同 payload 是冲突，不选择“最后一条”。
- 本地输入、解析、应用、请求开始、物理 attempt、响应、merge 和连接变化各为独立步骤。优先使用显式 ID 和已知 happens-before 关系；墙钟仅用于辅助显示。
- 一个请求可从 seq 180 跨到 184：请求只建立一个实体，起点/响应/merge 分别关联相应步骤，frames 可以引用同一请求。`requestedSeq=0` 是 FULL 请求模式，不能制造服务端 seq 0 事件。
- `seqNo` 是当前服务端观察锚点，`relatedSeqNo` 是该本地步骤所处理事件的 seq；允许滞后处理。跨 frame 的下一步沿全局本地序推进，不按 relatedSeqNo 重新排序执行。
- 只有已证实的事件形成服务端事件帧；仅有 snapshot 水位时可以建立明确的 snapshot anchor，事件内容为 unknown。序号空洞本身不能证明丢事件，私有摸牌不可见也可能造成空洞。
- 未能关联 round/seq 的本地记录进入“未关联步骤”，保持可查看及可计数，不猜成最近事件。
- 缺明确收包时刻的旧 events.received_epoch 表示消费时刻，req.ts 表示记录/完成时刻，不改名为精确网络到达或发送时刻。

这样既满足按 seq 检查牌局，也保留跨 seq 请求与延迟应用的真实先后。

### 4. 三份状态使用独立输入，实际观测缺失时不伪造 Observed

```text
Server timeline ── 独立参考 reducer ─────────→ Server Ground Truth
本地已经可用的完整输入 ── 独立参考 reducer ─→ Local Expected
实际 transition/merge 状态记录 ─────────────→ Local Observed
旧日志 ── 当前 Mirror 兼容回放 ─────────────→ Derived reconstruction
```

Server 和 Expected 可以共享无副作用的参考规则实现，但使用彼此隔离的状态对象与输入队列。Expected 只使用当前本地步骤以前可用的完整输入，不读取服务端独有手牌、未来输入或实际错误状态来修正自身。SSE 水位只改变已知水位/待取需求，不产生吃碰杠。

Observed 从实际记录的 checkpoint 与状态变更构建，记录应用失败时已经发生的部分修改。没有完整 trace 的区间将 Observed 对应字段标为 unknown；可并列展示 Derived reconstruction，但禁止把其一致性升级为实际应用成功。缺失应用记录也不能独自证明应用没有发生，须有完备追踪区间和明确处理完成/跳过/错误边界。

完整 state response 是两个 Local 世界都可使用的本地输入锚点，但 Expected 在输入可用后按参考规则推进，Observed 仅在实际 merge 记录到达后改变。Server 缺独立导出时显示不可用或部分已证实锚点，不能复制 Local 伪装成真相。与旧 `mj.log_replay` 的合法集对账作为交叉核验，不作为实际执行 oracle。

### 5. 独立展示模型保存历史牌河和规则信息

每座位维护历史 river、melds、hand/handCount、knowledge 和 flags。每张弃牌有稳定身份、来源事件及 called 关系；加杠更新既有碰副露，保留原来源弃牌。

比较业务状态前转换为共同口径：Mirror 的现存 river 与历史 river 的未认领投影比较；被叫走的牌仅在副露实体中计入牌数。跨缺段快照只能提供当前牌河，不能补造历史被认领牌、fromSeat 或弃牌 seq。

内部复用 0–33 牌索引，协议输入接受现有 w/b/t/字牌命名，并提供文档中 m/p/s 与用户 w/p/s 的显式别名映射。支持发牌、摸打、吃碰、三种杠、过、timeout、胡和局/场结束；READY/RIICHI 若无对应协议语义则保留 UNKNOWN，不引入立直麻将规则。

不变量按已知信息检查：手牌增减、认领来源、重复副露、总牌数、handCount、阶段和行动者。对手暗手未知时不能因手牌内容不可见而报缺牌。wall_remaining 与 live_wall_left 先按杭州规则扣除死墙转换；服务端 response turn 可能是弃牌者，不能直接与本地 next_seat 比较。局号、phase、responding_seats、pending 身份、抓打圈/冻结、财神/动作链、drawn 与杠后补牌状态也参与相应业务 diff，未知状态不默认成零或 false。

### 6. 请求采用业务语义分类与三个时间截面的 diff

StateRequestRecord 分离 logicalRequestId、transportRequestId、attemptIndex、trigger/reason revisions、requested cursor、response cursor、接收时间、应用时间。重试不增加逻辑请求数，取消前未发送的候选不增加物理 attempt 数。

每个请求计算：

- `requestToResponseDiff`：请求时状态与返回内容的比较；说明网络期间看到了什么新信息。
- `effectiveMergeDiff`：实际 merge 前后可比较状态的变化；避免把在途已经应用的变化重复计为修复。
- `expectedObservedDiff`：相同本地输入处理边界下应有与实际状态的差异；没有实际状态证据时为 unknown。

DELTA 应用到已知锚点后才能比较状态；空 events、摘要字段相同或 HTTP 200 不是“有效状态相同”的充分证据。空 diff 也必须携带覆盖字段，不能掩盖未观测字段。

先检查分类所依赖的事实是否冲突，无法裁决的冲突归 SUSPICIOUS。对一致证据使用以下确定性优先级：

1. 已证实本地执行错误被修复：RECOVERY_CAUSED_BY_LOCAL_TRANSITION。
2. 已证实断线未知区间被填补：RECOVERY_CAUSED_BY_RECONNECT。
3. 已证实本应取得的完整输入缺失被补齐：RECOVERY_CAUSED_BY_MISSED_EVENT。正常 SSE 唤醒后的首次取数不属于漏事件。
4. 有恢复事实但根因不足：RECOVERY_REQUIRED。
5. 满足窗口授权、阶段确认、freshness 或动作结果核对等业务需求且未修复：VALIDATION_ONLY。
6. 正常增量取得新进度：PROGRESS_UPDATE，作为原文分类的协议适配扩展。
7. 完整证据证明无新信息、无未满足业务需求、无恢复必要：REDUNDANT。
8. 无法满足上述证据条件：UNCLASSIFIED。

另设 `avoidability = NECESSARY|AVOIDABLE|UNKNOWN` 与证据列表。用于修复客户端缺陷的请求在当时可以必要，但若消除缺陷可避免；这种反事实说明单列，不把当时的恢复标成冗余。多重已证实原因保留为 contributingCauses；分类不会改变线上调度。

### 7. 首次差异与首次故障分开，诊断输出证明路径

所有诊断携带可比较字段、原始引用、证据等级、游标和明确的不可观测原因。

- `firstVisibleDivergence` 允许是服务端领先本地的正常延迟；使用 NOT_RECEIVED_YET / RECEIVED_NOT_PROCESSED，不能直接记故障。
- `firstConfirmedFailure` 只在相同已知输入边界、完备 trace、处理完成/失败证据下判定 PROCESSED_INCORRECTLY 或 MISSING_LOCAL_TRANSITION。
- 在缺段后第一次看见差异，只能定位“最早已证实位置”，附未知起点区间；不能宣称找到了缺段内的第一根因。
- 后续 turn/river/hand 连锁差异通过 causedBy 指向原始问题；实际 merge 确认恢复后记录 RECOVERED_BY_STATE、recoveredByRequestId 与可计算的恢复耗时。
- 服务器 timeout、未决窗口、策略选 PASS、实际 POST 和认领 echo 独立展示。MISSING_LOCAL_TRANSITION 指状态事实未应用，不代表 BOT 必须选择某个合法吃碰动作。
- 服务端时间与本地时间未校准时，差值显示为跨时钟观测差及精度，而不声称精确网络延迟；同进程 monotonic 差值可用于处理/请求时长。

### 8. 可选执行追踪作为新增证据来源

runner/match_runner 增加默认关闭的 `--replay-trace`。启用后生成版本化侧车 JSONL，并在现有日志的可关联记录上附加可选 trace 引用；旧工具继续忽略未知字段。增加独立追踪模块封装采集，避免在每条业务路径手写序列化。

采集 session 标识、commit/协议/trace 版本、gid、座位、局号；为同 gid 捕获事件分配 localOrdinal 和 recordId，并记录 causal parent。捕获点与排队/写盘时间分离。SSE 的 received/parsed/wake 边界在对应操作时捕获，不用之后的日志写入时间倒推。

以下边界记录实际事实：

- 初始 checkpoint、镜像替换/reset 与局边界；完整 Mirror 业务状态及工作线程实际持有的 phase/turn/next_seat 等分别标注，未持有的字段为 unknown。
- transition 开始/完成/失败，输入事件、关联响应、before/after 状态；失败保留部分修改。
- 请求调度/派发、每个已发生的物理 attempt、响应完整业务 payload、merge 开始/完成/失败及 state_reconcile；复用已有传输 ID，不创造额外请求。
- SSE connect、receive、parse error、disconnect、reconnect、closed；断线原因未知时不猜测。

捕获时复制可变状态，后台有界队列完成编码/写盘；不持有调度锁做 I/O，不等待队列腾空。溢出/写失败记缺失计数、ordinal 区间及下一可用完整 checkpoint；尾部无结束记录也判不完整。参考重建不能穿过该区间声称 Observed 完整。禁用追踪时不复制状态、不创建侧车。

替代方案“仅重跑当前 Mirror”无法观察线上实际漏执行；“每步同步 flush 完整 JSON”可能干扰窗口，因此采用可选捕获与后台持久化。采集保存业务 payload 和安全传输信息，不记录认证凭证；已有输入证据只读保存，不覆写脱敏。

### 9. 单一页面状态驱动所有面板

HTML 展示一个主牌桌和三世界对照摘要，可切换主牌桌来源。游标和视图变化通过同一渲染状态更新桌面、事件、本地步骤、请求、diff、诊断与时间。提供 round 选择、首尾/前后 seq、输入 seq、Before/After、本地步骤、播放/暂停、异常和请求/认领快捷跳转。

PLAYER_VIEW 只显示选定玩家在所选服务端位置可见的手牌；LOCAL_KNOWLEDGE 严格限制到当前本地步骤已知信息；OMNISCIENT 只展示已由服务器记录的真实手牌。牌桌、diff、搜索和原始 payload 检查器统一走视图投影，避免旁路显示隐藏或未来牌。无对应数据的模式说明缺失，不用另一份世界补齐。

单文件内含导入证据，观察模式是展示边界，不是文件访问权限隔离。第一版不提供向无权查看完整证据者分享的权限系统。JSON 嵌入转义 script 终止符，原始内容按文本渲染，外部脚本/网络请求数量为零。

checkpoint 保存状态、输入处理位置、未知区间和诊断累积状态；回退/随机 seek 与从头重放必须一致。语义 JSON 不含非确定性生成时间；HTML 的生成说明与确定性分析结果分开。

## Risks / Trade-offs

- [历史日志缺真实应用证据] → 提供派生视图和逐来源覆盖报告；完整诊断由受控 trace 夹具证明，不能追补不存在的线上事实。
- [独立 reducer 增加维护成本] → 复用协议解析，使用独立手工黄金状态和故障注入验证；在线 Mirror 只做兼容参照。
- [断线、私有事件和轮翻转造成 seq 空洞] → 保留缺段原因和区间，只在可比且已知的字段上判断故障。
- [追踪增加决策线程工作] → 默认关闭、有界非阻塞持久化、捕获耗时/大小基准；捕获成本目标 p95 ≤ 1 ms，在同一固定离线负载上验证，未达标则优化后再完成任务。
- [HTML 体积和长局跳转] → 导出单局、共享事件引用和 checkpoints；基准采用至少 2,000 个 seq 与 20,000 个本地步骤，记录导出体积、内存与 seek p95，目标 seek p95 ≤ 200 ms。
- [运行时错误后状态已经部分修改] → 记录失败后的实际状态，不在观测分支静默回滚；参考分支报告前置条件错误。
- [输入与独立服务端资料不匹配] → 导入前核对身份与座位，不用相近时间或牌型自动配对。

## Migration Plan

1. 先实现版本化合同、导入器和纯离线参考重建，旧日志可独立使用。
2. 实现可选 trace 与确定性 FakeApi/SSE 集成样本，默认运行路径不启用追踪。
3. 接入诊断和 HTML，输出命令/数据覆盖说明；既有 CLI 保持兼容。
4. 完成新功能测试、相关 recorder/replay/platform 回归、完整 Python 测试、OpenSpec strict validation、语法与 diff 检查、浏览器离线冒烟和性能基准。
5. 用两份现有房间日志验证兼容与降级，用完整合成/受控 trace 验证因果故障定位。无需启动新的线上房即可验收本 change；后续线上试采单独报告，不将旧房计为新采集验证。
6. 回退可关闭 `--replay-trace` 并保留已有日志和导出，无数据迁移或生产部署步骤。

### 原文 20 项验收映射

| 原文项 | 实施验收 |
| --- | --- |
| 1–2 | 三源导入、原始引用、normalized timeline；缺源模式单列 |
| 3–6 | seq/本地步骤/Before/After 跳转与面板一致性 |
| 7–9 | 四家历史牌河、全部认领类型、本人手牌与未知对手 |
| 10–11 | 三视图及桌面/详情/diff/search 信息边界 |
| 12–13 | 逻辑请求/attempt/response/merge 明细、业务 diff 和证据分类 |
| 14–15 | 五种认领遗漏、正常通知/传输延迟与实际处理故障区分 |
| 16–18 | 首次可见差异/首次确认故障/恢复链和快捷导航 |
| 19–20 | 重复重放、checkpoint seek 一致性、unknown 与无证据降级 |

## Open Questions

- 无需用户再选择框架或协议即可开始实现。真实资料中服务端导出的字段变体和具体 trace 成本在适配器/基准任务中验证。
- 原问题房独立服务端 timeline 是否可取得尚未确认；缺少该输入不阻断开发，导入器保留 optional 输入和覆盖说明，不主动访问门户。
