# Handoff: 杭州麻将平台窗口/SSE 验收（2026-09-10）

## 吃碰确认与统计修复（2026-09-10，当前）

- 修复前线上房 `a_d8af50e34e15`：10 场正常结束，7527 次 state，无记录到的 429/重试；2 次 chi 409。快照对照表明这两次可能是提前提交，而非单纯迟到。52 次“碰窗超时被代打”实际为 18 次本地估算放弃 + 34 次服务端 timeout，其中 18 次重复；另 16 个窗口首次收到弃牌时已同批收到 timeout。
- 吃候选先过滤合法集；估计时间/响应齐只触发主循环 seq=0 确认，只有最新快照的 response_chi 且本人在 responding_seats 中才提交。response_peng 快照保留精确 deadline，等待 SSE/转换点后再确认；不新增并行请求。chi 的 409/未知结果由快照分支在当前循环恢复。
- 碰窗秒级 ts 仅作确认提示：疑似接近截止时请求快照，不再直接判为代打。精确 window_deadline_ms 才用于截止放弃；决策后再次检查。
- 响应 POST 按局号、出牌者、牌值、牌河数量和各家副露数量防重；尝试集合在 seq=0 重锚后保留。明确拒绝/结果未知也不盲重发。
- timeout 按当前局 mirror.me 分类，删除共享 _current_me 与重复分类累加。本地 deadline 放弃只增 client_deadline_abandons，不再增 auto_played；历史总数不可直接与新口径比较。
- 紧急预测不再被动作后 lazy_floor 强制压住；默认 12.5/s 保持不变。每次物理 state 重试重新申请共享限流许可，单次逻辑请求的排队时间累计记录。
- 新增 tests/test_window_confirmation.py；调整旧测试提供权威确认快照。聚焦回归 60 passed, 2 subtests passed。本轮修复后未启动线上匹配，不能宣称线上超时或 409 已清零。
- 补充镜像/SSE/确认/动作日志回归：18 passed, 2 subtests passed（与聚焦集有重叠，不相加）；git diff --check 通过。不是完整测试集验收。

## 后续接口调度修复（2026-09-10）

- 修复 `_wait_wake`：原先收到 SSE 并清空队列后，`queue.Empty` 会令函数始终返回 False。懒等待因此误判没有积压，可能继续等待兜底超时。现在区分首次等待超时与成功唤醒后的队列耗尽，合并帧后正确返回 True。
- 修复共享 EDF：每次仲裁重新判断窗口是否过期，过期请求降为普通追赶（保留到达顺序和老化），不再抢占有效窗口；请求仍会执行以追平权威状态。限速保持 12.5/s。
- 新增 `tests/test_state_scheduling.py`：100 帧合并、十场中九个过期请求与一个有效窗口竞争、排队中截止过期。
- 最终聚焦回归：54 passed, 2 subtests passed；单独 SSE 匹配回归：3 passed（包含帧驱动、不可用回退、断线重连）。
- 未运行新的线上匹配，不能据离线调度修复宣称线上 409 已清零。扩大到 recorder/match 的组合回归在 56 项通过后挂起并中断，完整回归仍未收口。

## 目标与当前任务

继续处理 `python3 -m mj.platform.match_runner --games 10` 的窗口超时、409、服务器代打问题。当前默认传输模式已切换为 **SSE + `/api/games/{id}/state?seq=N`**，需要继续分析并修复吃/碰窗口竞态，不能把 409 单纯视为正常现象。

工作目录：`/Users/shenzeqi/Desktop/szq/inspire`
当前分支：`main`，用户已有大量未提交修改，**不要 reset、clean、覆盖或提交**，除非用户明确要求。

## 规则/协议定论

- 规则文档：`docs/杭州麻将对战平台.html`，线上只读核验为 guide v29；v28/v29 没有改变吃碰时序。
- 碰窗约 1 秒，吃窗约 1 秒且碰窗先于吃窗；弃牌窗约 3 秒。
- `/notify` SSE 只推状态变化提示，帧中的 seq 是包含式水位，不能直接作为 state 游标。
- 正确流程：首次 `/state?seq=0`；收到 SSE 后用本地已消费 seq 请求 `/state?seq=N`；gap/409/POST 结果不确定时 `/state?seq=0` 全量快照重建。
- 快照是对应 seq 的规范状态；响应窗口 `responding_seats` 不等于尚未响应成员，客户端要防重复提交。
- 动作 POST 不应通用重试：409 是明确拒绝；5xx/超时/响应丢失结果可能未知，不能重发旧动作。
- 单令牌 10 场共享 `/state` 限速器，当前默认 `state_rate=12.5/s`，窗口请求按 EDF 调度；共享排队是关键瓶颈。
- 线上指南限速为同一用户/令牌聚合 **16 次 `/state`/秒**，不是每场 16/s；客户端 12.5/s 是保守主动限速。历史约 15/s 实验出现 429 风暴，因此不能直接把默认改为 16，若实验应逐档比较 13.5/14/15，并观察 429、排队、409、截止放弃和 timeout。
- 健康 SSE 模式下，无状态变化的普通 `/state` 刷新不是必须；必须保留：初始 `seq=0`、SSE 帧后的本地游标增量拉取、窗口必要确认、gap/409/POST 未知结果后的 `seq=0` 重锚、SSE 断线回退、跨局/终局确认。
- 碰/吃窗口请求 `/state` 是为了获得权威弃牌/认领/timeout/phase/turn/deadline 状态，不是为了固定高频轮询；不能收到弃牌后直接 sleep 到 T+2 再 POST，否则会漏掉他家先碰、本人被代过或阶段已推进。
- 优化方向：每场一个在途 state 请求；合并多个 SSE 帧；窗口内只在 SSE/必要截止确认时拉取；避免同一窗口的 SSE 拉取和截止追赶重复占用全局配额；先做离线调度仿真，不只调 sleep。

## 已完成修改（已在工作树）

1. `mj/platform/match_runner.py`
   - `bot_transport_options()` 默认返回 `use_notify=True, long_poll=False`。
   - `--no-notify` 退回普通主动 `/state?seq=N`；`--no-long-poll` 保留兼容但不再切换到长轮询。
2. `mj/platform/api.py`
   - 动作 POST 单次提交，不重试 429/5xx/网络错误。
   - 新增 `ActionSubmissionError` / `ActionDeadlineExceeded`，区分 `uncertain`、timeout、deadline。
   - 动作结果不确定或明确 409 由 BotClient 重锚。
   - `game_snapshot()` 有界 seq=0 快照接口。
3. `mj/platform/bot_client.py`
   - 同批本人碰窗 timeout 不再连带丢吃窗。
   - 吃窗等待/发送前最终截止检查。
   - 409/动作未知结果通过 `_ActionResync` 进入 seq=0 重建，不重发旧动作。
   - 快照 response_peng/response_chi 恢复处理与窗口等待态。
   - SSE 监听/断流回退保留。
   - 新诊断统计：`client_deadline_abandons`、`response_409`、`post_uncertain`、`timeout_*`，以及新增分类：`my_timeout_discard`、`my_timeout_peng`、`my_timeout_chi`、`other_timeout_discard`、`other_timeout_response`、`platform_forced_discard`、`no_legal_response`、`stale_trigger_cancelled`。
   - `_current_me` 用于区分本人/他家 timeout。
4. `mj/platform/recorder.py`
   - events 记录 `received_epoch`。
   - action 记录 `started_at` monotonic、`started_epoch` epoch、deadline/message/transport 等。
5. `mj/logview.py`
   - 不再把 monotonic `started_at` 与服务端 epoch ts 相减。
   - 使用 `started_epoch` 比较；旧日志没有该字段时显示 `发送落点=不可推断`。
6. 文档：`README.md`、`CLAUDE.md`、`PROGRESS.md`、新增 `docs/平台窗口修复与验收.md` 已有相应说明。

## 离线验证现状

最近聚焦回归：

```text
37 passed, 2 subtests passed
```

覆盖 timing/window/stale/hu_failed；更早的 focused SSE/recovery 测试也通过：

```text
56 passed
SSE transport switch: 3 passed
SSE match tests: 3 passed in ~28s
```

完整 `tests/` 曾因旧 `tests/test_platform_client.py::test_full_game_multiple_seeds` 的 synth FakeApi 决策序列兼容问题挂起，尚未收口，不能宣称全套通过。该问题在后续修改前应重新验证；不要把线上成功收官误当作完整离线回归通过。

## 线上运行记录

### 第一轮

房间 `a_21fedb072f00`，10 场完整收官。

汇总：

```text
games=10, rooms=1, actions=844, hu=7, err409=4, gaps=190,
auto_played=25, mirror_resets=0, hu_failed=0, decide_errors=0,
client_deadline_abandons=1, post_uncertain=0,
timeout_discard=87, timeout_response=7979,
throttle_waits=7581, throttle_wait_ms≈2325293, max_wait=1401.4ms
```

4 次全部 `response_chi`：`409 INVALID_ACTION: chi only in chi window`。没有镜像失步/重复动作/POST 不确定。

### 第二轮（用户要求“再运行对局，观察对局日志”）

执行一次 `--games 10`，由于退出/房间粒度实际完成 **18 场、2 房间**：

- 第一房 `a_e2b47e90ea65`：10 场结束。
- 第二房 `a_7325d8cca502`：10 场启动/最终统计合计包含 18 场（`games=18, rooms=2`）；日志显示所有已收官场有 end finished。

最终汇总：

```text
games=18, rooms=2, actions=1745, hu=23, err409=15, gaps=338,
auto_played=33, mirror_resets=0, hu_failed=0, decide_errors=0,
client_deadline_abandons=1, client_state_abandons=0,
response_409=15, post_uncertain=1,
timeout_discard=159, timeout_response=17253,
my_timeout_discard=36, my_timeout_peng=3623, my_timeout_chi=2396,
other_timeout_discard=123, other_timeout_response=12547,
platform_forced_discard=3, no_legal_response=0,
stale_trigger_cancelled=0,
throttle_waits=14978, throttle_wait_ms≈4051167.5, max_wait=1573.9ms
```

第二轮失败明细：

- `a_e2b47e90ea65` 第一房日志有 15 个失败动作：14 个 `response_chi / chi only in chi window`，1 个 `response_peng / gang 3t / not your response turn`；另有一个 reset。
- 同房具体吃失败牌包括 `4b,7w,2b,9b,5t,4t,5t,4w,9t,8w,3b,7t,5t,7w`。
- `a_7325d8cca502` 第二房出现 1 个 `status=0, <urlopen error timed out>` 的 `response_chi`，对应 `post_uncertain=1`；动作没有自动重发。
- 后续日志输出可见多次吃 409、碰窗代打、一次吃窗主动截止放弃；最终 18 场统计正常退出。

当前证据指向：SSE 已能稳定收官、无 mirror reset，但 **单令牌 10 场共享 state 限速排队（最大 1.57s）+ 服务端窗口推进/事件观测尾延迟** 是主要窗口竞态。`chi` 本地决策仍可能在服务端已离开 chi 阶段时 POST。`gang not your response turn` 说明 response_peng 当前 turn/窗口身份也有乐观判断问题。`post_uncertain=1` 是真实网络传输不确定案例，应保留“不重发旧动作”的策略。

日志目录：`local/games/20260910/`；本次 JSONL 不应手改。

## 补充：共享限速与窗口 state 请求原则

### `/state` 的 16/s 上限

线上指南 v29 的 `/state` 限速是同一用户/令牌聚合 16 次/秒，10 场共用，不是每场 16/s。当前客户端主动限速 12.5/s 是保守值；历史约 15/s 实验曾触发 429 风暴，因此不能直接将默认改为 16。若要实验，建议 13.5、14、15 逐档对照，并记录 429、最大/分位排队、409、client deadline abandon、本人 timeout。

### 为什么窗口期间仍需要 `/state`

SSE 只推“状态变化”提示，不含完整弃牌、牌值、认领结果、phase、turn、responding seats 或权威 deadline。碰窗要用 state 获取弃牌并判断碰/杠/过；吃窗要确认碰窗是否结束、他家是否先认领、本人是否仍可吃。不能只等待到 T+2 后直接 POST。

### 普通请求是否必需

健康 SSE 且无状态变化时，不需要固定周期普通 `/state` 刷新。必须的请求是：初始 `seq=0`、SSE 帧后的本地游标增量拉取、窗口必要确认、gap/409/POST 结果不确定后的 `seq=0`、SSE 断线回退、跨局/终局确认。

推荐的调度优化是：每场最多一个在途 state 请求；合并多个 SSE 帧；窗口内只在 SSE/必要截止确认时拉取；避免同一窗口的 SSE 拉取和截止追赶重复占用共享配额。先做离线调度仿真，不要只调 sleep。

## 下一步建议（按优先级）

1. **先检查并修正第二轮新增统计/时间日志实现**：确认 `started_epoch` 已由所有动作成功/失败路径写入，`received_epoch` 不破坏旧 Recorder 测试；运行 timing focused tests。
2. **分析 response_peng `gang not your response turn` 前的事件批次**：从 `a_e2b47e90ea65_r1_b3_t0.jsonl` 的失败 action decision seq 往前找事件；验证是否他家认领/新事件已在同批，或者 `_act_window` 未检查 `responding_seats`/窗口身份。
3. **给 state 请求做真正窗口调度评估**：当前 SSE 仍在共享 12.5/s throttle 排队；不要贸然提高到 15/s（历史实弹曾引发 429 风暴）。可研究每场 SSE 唤醒合并、动作后抑制无关刷新、近窗抢占、多个令牌分摊，先加离线仿真测试。
4. **完善 `no_legal_response`/`stale_trigger_cancelled` 分类**：目前值为 0 可能只是没有在所有路径调用，不应解读为实际没有这些情况。明确区分：无合法选项、策略主动过、本人 timeout、平台 forced discard、客户端 deadline abandon、旧触发被新事实取消。
5. **重新跑 focused tests，再谨慎决定是否需要第三轮线上。** 线上对局是有副作用的自由匹配，运行前需用户明确（已明确过一次但不要无限重复）；不并跑重负载。
6. **完整测试修复**：旧 FakeApi 测试在 seed=1 seat=2 / seed=2 seat=0 等组合可能 StopIteration 后重派挂起；需查状态机为何额外调用决策，或让 FakeApi/测试符合新快照行为，不能直接禁掉失败重派。

## 重要实现注意

- `logview.py` 的 `WINDOW_SPAN` 仍按 draw=3, peng=1, chi=2；事件 ts、局部 epoch、monotonic 必须分开。
- 当前 `auto_played` 仍是兼容总数，不要直接报告为漏窗；优先报告新分类和服务端事件证据。
- 线上日志缺少数据时要明确“未记录到”，不能声称“没有发生”。
- 不要修改引擎、模型权重或策略选择；范围只在平台窗口、传输、记录/诊断和测试。
- 用户要求先做“分析”，若要继续改代码，先做离线复现/设计，避免仅调 sleep 秒数。
- 已向用户解释：服务端 16/s 是令牌聚合上限；窗口请求 state 为获取权威竞争状态；健康 SSE 无状态时普通 state 不是必需。以上原则已补入本交接文件。
