## 1. 固化实现前行为与边界复现

- [x] 1.1 将排队后 DELTA 无法升级、跨 gid/重启请求 ID 重复、首次 RESYNC 直接 finished 残留三个复现转成能在修复前暴露问题的回归测试。
- [x] 1.2 用可控时钟和 transport 固化现有 EDF/过期降级/普通排序、429 冷却、物理 retry 许可和退避的对照结果。
- [x] 1.3 固化首次吃窗确认、普通等待、快照后等待和未确认预算继承的请求时序，并保存旧三房回放矩阵及日志 schema 作为兼容对照。

## 2. 分离需求、候选与请求状态

- [x] 2.1 在 state_demand 中建立候选、冻结请求、获取阶段和独立 reason revision，保留 FULL dominance、本地 applied cursor 与 SSE watermark 分离。
- [x] 2.2 实现不重置重复需求等待年龄的候选更新、按 revision/WindowAttemptKey 接收完成结果，以及 pending-only successor 判定。
- [x] 2.3 覆盖新 SSE 不作废有效确认、旧窗口结果不能完成新窗口、新 RESYNC 原因缺乏覆盖证明时继续 pending 的测试。

## 3. 接入单队列调度与获取协调器

- [x] 3.1 新增 StateScheduler 调度入口并复用 StateThrottle 的唯一等待队列，接入候选 update/withdraw 通知且保留原仲裁策略。
- [x] 3.2 实现许可边界重新核对并冻结请求，覆盖排队 DELTA→FULL、RESYNC+WINDOW_CONFIRM 合并、过时候选无许可撤回。
- [x] 3.3 新增 StateFetchCoordinator，独占每 gid 的获取链直到响应应用完成；将 BotClient 主状态获取路径接入，并保持 legacy fake Api 的必要薄适配。
- [x] 3.4 接入 Api 每次物理 attempt 的许可、实际开始和结果钩子，返回显式 transport 结果并保留旧 TLS 兼容；验证普通 retry/backoff/timeout 选择没有改变。
- [x] 3.5 用线程屏障覆盖并发 fetch、升级与发放、关闭与发放竞态，以及 10 gid 共享额度；测试须经过真实 Api admission 和假 HTTP，验证无第二队列、无跨 gid 合并、无重复 GET/死锁。

## 4. 将完成确认放在响应应用之后

- [x] 4.1 调整协调器与 BotClient 的交接，在事件连续应用或 FULL 重建成功后完成 SSE/RESYNC，再由窗口 resolver 提交带 revision 的确认结果。
- [x] 4.2 覆盖镜像应用失败、在途新增需求被响应覆盖、在途 DELTA 无法满足确认时创建唯一 successor，以及 RECONCILING 期间禁止新发送。

## 5. 提取窗口确认状态

- [x] 5.1 新增 WindowConfirmation/WindowTiming，集中 WindowAttemptKey、观察计数、not_before、scheduler_deadline、observation_budget_deadline 和 exact_window_deadline；迁移异常对象/chi 字典中的重复状态。
- [x] 5.2 保留第 1.3 项的各入口时序和既有常数、重试预算、授权余量/时钟转换；补齐时间来源标签和本地预算耗尽的独立诊断，禁止将 peng 派生时间写成 chi 精确截止。
- [x] 5.3 验证 peng→chi pending、缺身份/缺截止、PASS/成功关闭、409/未知 POST 重锚以及旧动作不重发；保持既有 canonical precedence。

## 6. 统一终止与资源释放

- [x] 6.1 实现幂等 close/CLOSING/CLOSED 和统一 finally 清理，覆盖 finished、404、worker_error、停止路径；残留 reason 记录真实 terminal cause。
- [x] 6.2 实现停止后的排队撤回、已获许可未发送取消和在途等待释放；显式停止阻止下一 retry/动作/successor，不将未释放 HTTP 记录为 clean。
- [x] 6.3 覆盖首次 finished、重复 close、排队中停止、HTTP 中停止、404/异常和缺尾日志，验证资源 clean 与各层 success/complete 状态独立。

## 7. 关联标识与诊断兼容

- [x] 7.1 加入跨 gid/工作线程重启唯一的 candidate/logical/transport ID 分配与 successor_of，验证请求头、attempt 和 recorder 关联一致且不含秘密。
- [x] 7.2 记录冻结 reason 视图、实际 evaluated revision/satisfied_reasons、候选/许可/HTTP/响应应用边界及有来源的 deadline 字段。
- [x] 7.3 在 recorder 与 window_acceptance 中增加 logical_state_requests、physical_state_attempts、substituted_candidates、cancelled_before_send 和指标版本/来源；按唯一 ID 去重，并保留旧 physical_state_requests 解释。
- [x] 7.4 覆盖一次 429→200 的一逻辑两物理计数、取消零发送、重复摘要去重、跨 gid 旧 state-1 作用域、未知字段不补造，以及已有外部时序关联兼容。

## 8. 离线验收与实现版本冻结

- [x] 8.1 运行受影响的需求、调度、确认、传输、退出和验收脚本测试，再运行完整 `python3 -m pytest tests/ -q`；记录实际结果和未解决问题，不以探索阶段的 62 passed 代替本次验收。
- [x] 8.2 回放冻结 ab371b6 的 30 份日志，验证 canonical outcomes、C1/C2/C3/C5=0、C4=22、阶段不可观测=28、未决=4 的兼容性，新字段缺失保持显式且旧房不进入新分母。
- [x] 8.3 同步 README/HANDOFF，运行 `openspec validate state-request-lifecycle --strict --no-interactive` 和 diff 检查；审阅实际代码范围后冻结仅包含本 change 的实现版本，保留用户已有无关工作树改动。

## 9. 实施后的分层线上验收

- [x] 9.1 从干净的冻结实现版本生成无秘密 run manifest，固定 BOT、SSE+增量、state-rate=15，先运行一房 10 局 canary 并逐窗审核。
- [x] 9.2 canary 无硬失败后扩展到总计 3–5 个串行独立房、每房 10 局；版本如有修复则重新建立最终分母，旧诊断房和旧版本房不混入。
- [x] 9.3 输出逐房与跨房报告：真实物理速率、按窗口/时长归一化请求量、429、queue 分布、C1–C5、需求终态、重复 GET/POST 与授权安全；需求 dirty、旧动作重发、越权授权、限流绕过或可归因 C3 调度回归均不得通过。身份/阶段/结算限制单列，不以结构验收替代 C4 改善证明。
