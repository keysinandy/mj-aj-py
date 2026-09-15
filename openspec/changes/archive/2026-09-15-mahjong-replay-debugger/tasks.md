## 1. 合同与验收样本

- [x] 1.1 新增 `mj/replay_debugger/` 基础包，定义版本化 ReplaySession、RawRecord、NormalizedEvent、LocalStep、SeqFrame、GameState、Request、Diagnostic 和 Checkpoint 合同。
- [x] 1.2 实现来源、证据强度和知识状态，验证 unknown、hidden、not-applicable 与 known-empty 不混同，稳定 ID 不依赖当前时间或绝对路径。
- [x] 1.3 建立独立手工期望状态的正常摸打、吃、碰、明杠、暗杠和加杠夹具，包含手牌/牌河/副露来源与规则字段。
- [x] 1.4 建立通知延迟、跨 seq 请求、缺输入、遗漏应用、部分应用失败、merge 恢复、追踪丢失与时钟偏差的三源夹具，并建立 `docs/replay-debugger.md` 20 项验收到测试的映射表。

## 2. 证据导入与协议适配

- [x] 2.1 实现服务端 blocks/start_hands/events/rounds 导入，核对 game/round/seat 身份，保留原始字节引用、摘要、截断与冲突信息。
- [x] 2.2 实现新旧本地 JSONL 适配，解析 req/snapshot/events/decision/action/reset/end、SSE、window 与 state_reconcile，明确 summary 和完整 payload 的区别。
- [x] 2.3 实现可选 DumpingApi HTTP dump 与版本化 trace 导入，优先按逻辑请求/传输/attempt ID 关联，弱关联显式标 DERIVED 或 unresolved。
- [x] 2.4 实现事件类型和牌名别名规范化，保持三种杠独立；未知协议类型和 READY/RIICHI 不引入额外规则。
- [x] 2.5 验证重复事件去重保留全部 rawRefs、冲突拒绝合并、损坏行可定位且后续可读、缺可选输入与协议缺段的覆盖报告。

## 3. 因果时间线与双维度游标

- [x] 3.1 实现 server seq、capture ordinal、旧日志行序及显式因果边的统一索引；保存原始时钟域与精度，不单靠 ts 排序。
- [x] 3.2 实现 round/seq/Before/After/localStep 游标、真实事件帧、snapshot-only anchor 和未关联步骤，验证 seq=0 不制造事件、私有序号空洞不误判丢包。
- [x] 3.3 实现请求开始、物理 attempt、响应、merge 的独立步骤和跨 seq 引用，验证逻辑请求不重复计数、滞后应用保持全局本地顺序。
- [x] 3.4 增加游标首尾/前后/无效 seq/跨局边界与请求起点 180、响应 184 的确定性单元测试。

## 4. 三世界状态重建与业务投影

- [x] 4.1 实现独立的无副作用参考 reducer，以明确初始手牌和录制事件重建 Server；缺首摸、pass 或私有牌时保留 unknown，不采用补猜事实。
- [x] 4.2 实现只使用截至本地步骤完整输入的 Expected 状态，验证 SSE watermark 不生成牌局 transition、服务端隐藏牌与未来 response 不进入本地推导。
- [x] 4.3 实现基于实际 checkpoint/transition/merge 的 Observed 重建及失败部分修改保留；实现明确标 DERIVED 的旧 Mirror 兼容重建，证据缺段不能冒充实际状态。
- [x] 4.4 实现四座位手牌及计数、历史 river/called、meld/sourceDiscard 和加杠谱系，并验证本地缺历史快照不补造来源。
- [x] 4.5 实现共同业务投影与不变量：牌河移除/保留口径、认领牌不重复计数、wall 数、phase/turn/next_seat、pending、responding_seats、冻结及其他已知规则字段。
- [x] 4.6 实现 checkpoint 与顺序重放/随机 seek 的等价验证，覆盖缺段、回退、跨局和后来恢复不污染先前状态。

## 5. 可选执行追踪

- [x] 5.1 新增默认关闭的 trace 采集模块和 runner/match_runner `--replay-trace` 接入，输出版本化侧车与可选关联引用；禁用时不复制状态、不生成侧车。
- [x] 5.2 实现 session/gid recordId、localOrdinal、capture 时间与 causal parent 分配，复用既有请求/传输/attempt ID，验证并发捕获和延后写盘的顺序语义。
- [x] 5.3 在输入解析、transition、batch 完成/失败/跳过、镜像替换/reset 和局边界记录真实前后状态，验证抛错后的部分修改及捕获后可变对象隔离。
- [x] 5.4 在 state 派发、物理 attempts、完整业务响应、merge 和 reconcile 边界接入 trace，明确 response 来源，确保错误路径不记录成功应用、不采集认证凭证。
- [x] 5.5 在 SSE connect/receive/parse/disconnect/reconnect/closed 边界采集证据，验证捕获先于依赖 wake 的因果关系以及未知断线原因保留。
- [x] 5.6 实现有界后台持久化、非阻塞溢出、写失败、丢失区间、恢复 checkpoint 与结束 footer，测试慢盘/满队列/异常退出不改变对局请求或动作行为。
- [x] 5.7 使用固定输入和注入时钟比较 trace on/off 的请求、重试、决策、动作和 re-anchor 行为；记录 capture p95、队列峰值、丢失与体积，达到 capture p95 ≤ 1 ms。

## 6. 请求分析与故障诊断

- [x] 6.1 实现带覆盖字段的 requestToResponseDiff、effectiveMergeDiff 和 expectedObservedDiff，测试在途变化、DELTA 锚点缺失、空 events 和相同摘要不构成完整无差异证明。
- [x] 6.2 实现请求主分类、contributingCauses 和 avoidability，覆盖必要无修复确认、正常 PROGRESS_UPDATE、确证冗余、三种已知恢复、未知恢复、冲突及旧日志 UNCLASSIFIED。
- [x] 6.3 实现五种认领的 MISSING_LOCAL_TRANSITION 检测；注入漏执行/错误执行与已处理边界，验证仅收到 SSE、等待处理、缺 trace、合法可碰但选 PASS 均不误报。
- [x] 6.4 实现 firstVisibleDivergence、firstConfirmedFailure、受影响字段、因果父诊断、未知起点区间与实际 merge 恢复链。
- [x] 6.5 实现诊断类型、证据跳转、各来源覆盖、当前 round 请求和缺失动作汇总，独立展示 timeout/decision/POST/echo；验证跨时钟差只显示支持的精度。

## 7. 离线 HTML 与命令行

- [x] 7.1 实现 CLI 本地 gid/路径查找、可选来源、round 选择和 JSON/HTML 导出，验证身份歧义、无网络工作、输入保护及显式输出覆盖。
- [x] 7.2 实现四家牌桌与主世界选择、三世界摘要、手牌/历史牌河/副露及本人座位标识，unknown 状态可读。
- [x] 7.3 实现单一游标驱动的 seq/localStep/Before/After 导航、播放暂停、首尾与直接跳转，所有桌面、事件、请求、diff、诊断和时间面板原子更新。
- [x] 7.4 实现 PLAYER_VIEW/LOCAL_KNOWLEDGE/OMNISCIENT 的统一信息投影，测试桌面、原始详情、请求、diff、search 均不泄露当前模式不允许的牌或未来本地信息。
- [x] 7.5 实现请求/attempt inspector、业务 diff、前后异常/请求/吃碰杠跳转、首次可见差异与首次确认故障的独立入口及当前 round 汇总。
- [x] 7.6 完成内嵌资产与恶意日志文本转义测试；通过离线浏览器交互检查无网络请求、无脚本注入、无控制台错误以及游标/视图面板同步。

## 8. 集成验证与交付

- [x] 8.1 用完整三源夹具执行从导入到 HTML 的端到端验收，逐项记录原文 20 项场景与本 change 增补场景的测试结果。
- [x] 8.2 用 `a_b85ff51c0958_r1_b0_t0` 和 `a_7b0691487195_r1_b0_t0` 的现有本地日志验证兼容导出、局选择、缺源和派生状态说明；保留来源路径/摘要，不将旧房升级为新 trace 验收。
- [x] 8.3 运行至少 2,000 seq / 20,000 localStep 的固定离线 seek 基准，报告环境、导出体积、内存与 seek p95，达到 ≤ 200 ms；复核重复编译与 checkpoint 结果一致。
- [x] 8.4 完成新增 replay/trace 测试、相关 recorder/mirror/transport/lifecycle 回归及完整 Python 测试，执行 OpenSpec strict validation、Python 语法与 diff 检查。
- [x] 8.5 更新 README 和复盘器使用文档，写明 CLI、输入获取边界、旧日志证据强度、trace 开关、分类语义及实际验收结果；最终报告区分已验证能力与仍未知的线上历史事实。
