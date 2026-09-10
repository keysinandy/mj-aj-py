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
- 线上验收房 `a_6e9d146f7906`（日志 `local/games/20260910/u_9812ba08fe2f_a_6e9d146f7906_r1_b*.jsonl`）：10/10 局完成，861 次动作全部成功，0 个 409、0 个 post_uncertain、0 次 client_deadline_abandons/auto_played；17 次重锚（16 次时间戳精度保护、1 次决策临界保护），均恢复成功。请求队列 p50=733.5ms、p95=886.8ms、max=1471.8ms；共享限流累计等待 5643962ms，deadline_missed=0。timeout_response=5290（其中 my_timeout_peng=2017、my_timeout_chi=612）；timeout 分类仍应结合服务端事件口径解读，不能等同于客户端丢牌。
- 对上述日志做合法集重放后的更正：`auto_played=0` 不等于零窗口损失。2629 个我方 response timeout 中，有 22 个 chi timeout 在镜像中仍有非 pass 合法动作；另有 17 次 peng 临界保护后直接进入 response_chi（16 次尚未决策、1 次已选 peng 但未 POST）。因此本轮至少存在服务端代过/窗口未提交，需把 22 个 chi 作为明确漏吃候选，把 17 个 peng 作为临界窗口漏碰候选；不能宣称“没有错过窗口”。

## 吃/碰/杠 机会损失日志（2026-09-10，claim_miss）

- 新增 `claim_miss` JSONL 记录（`mj/platform/recorder.py` + `BotClient._claim_miss`
  / `_claim_legal` / `_record_claim_timeout`），回答“规则允许吃/碰/杠却没成”
  的两类情况：① 策略已选动作但未落地（`chosen` 非空，reason 为
  `action_rejected`/`action_uncertain`/`*_boundary_resync`/
  `window_already_attempted`/`碰窗精确截止已到`/`吃窗*已关闭*`）；
  ② 服务端 timeout 时规则仍有合法动作但策略未决策（`chosen=null`，
  reason `server_timeout_peng`/`server_timeout_chi`）。无合法动作、策略明确
  `pass`、镜像张数漂移不可评估均不记录。
- 碰窗作废的 `_lost_claim()` 改为复用 `_claim_legal()`；`server_timeout_*`
  记录不再要求张数漂移时保守记账，只写“有非 pass 合法动作”的窗口。
- `Recorder.claim_miss` 额外写 `client_decision`/`chosen_legal`/`legal_check`
  （`current_mirror` / `not_decided` / `stale_or_mismatched`），并沿用
  `decision` id 与 `action` 记录配对。口径与统计命令见
  `docs/平台窗口修复与验收.md`「吃/碰/杠 机会损失日志」节。
- 聚焦回归：窗口/时序/镜像 9 个文件 67 passed, 2 subtests passed；`git diff --check`
  通过。这是局部回归，不是完整套件验收。已知 `tests/test_platform_recorder.py::TestRecorderFullGame::test_full_game_records`
  在本次改动前即失败（synth 决策日志 `-4 not in [9,15,...]`）并会拖挂同文件
  后续全场用例，与 claim_miss 无关，未修。
- 工具链同步：`mj/logview.py` 新增 `MISS` 行渲染（显示“策略已选/策略未决策”）
  与摘要分类计数（按 reason × 已决策/未决策）；`log_replay`/`log2data` 按
  type 过滤，新记录类型天然被忽略，无兼容问题。
- 回归补充：`test_409_recorded` 会永久挂起在 `BotClient.run()` 监督循环
  （409 重锚后场次不收敛到 max_games）；已用改动前 HEAD 的独立 worktree
  复现同样挂起栈，确认与 claim_miss 无关。

- 线上验证（默认 12.5/s，房 `a_59753d3945aa`，10/10 局）：actions=865 失败=0、
  409=0、post_uncertain=0、deadline_abandons=0、auto_played=1；`claim_miss=25`
  （chi timeout 未决策 23、peng timeout 未决策 1、peng 决策临界重锚已决策 1）。
  逐条按 pending 回放配对本地决策，23/23 确认“该窗口从无本地决策”，
  `not_decided` 标注准确。
- 该验证暴露的语义漏洞已修：原先 `_record_claim_timeout` 只看“有非 pass
  合法动作”，无法区分“未决策”与“已决策为 `pass`（吃窗 pass 不提交、
  服务端随后 timeout）”，会把策略主动过误记为未决策。现按窗口身份
  （`_window_key`）记录 `_window_decisions`：已决策为过 → 不写 claim_miss
  （碰窗也不计 `auto_played`）；已决策为吃/碰/杠 → `chosen` 记该动作；
  未决策 → `chosen=null`。新增 `tests/test_claim_miss.py`（4 用例）锁定。
- 注意：第一轮线上（`a_59753d3945aa`）跑的是**修补前**的构建；修补后已补跑
  第二轮 `a_8bbc394d729b`（10/10 局）：actions=925 失败=0、409=0、
  post_uncertain=0、deadline_abandons=0、auto_played=0；`claim_miss=41`
  （全为 chi timeout 未决策），独立回放 41/41 确认窗口内确无本地决策，
  无“已决策为过”被误写。本地 chi 决策 65 次全部提交成功（chi=65 动作）：
  可吃窗口 106 个 → 65 成功 / 41 未及决策 → 瓶颈是 T+2 前拿到权威
  `response_chi` 快照（`/state` 排队 max 1270ms 同量级），下一步应做调度
  优化而不是策略侧改动。

## 未及决策根因（2026-09-10，基于 a_8bbc394d729b 回放）

- `decide` 不是瓶颈：成功窗口 decide p50=5ms / max=26ms。
- 成功路径：权威 `response_chi` 快照在 T+1.1~1.7 到达（p50 T+1.52），
  POST 在 0.02~0.18s 内完成 → 落在 T+2 前。
- 失败路径（41 个）：41/41 窗口内无任何快照响应、无 `seq=0` 请求，只有
  2~3 次增量轮询，单次端到端 ≈1.0s；第 3 次请求在 T+1 前后发出、被排队
  拖到窗口关闭后才返回，批内直接是吃窗 timeout。
- 主因：`/state` 端到端 p50=819ms（排队 p50=739ms + hold p50=29ms，
  hold≥900ms 占 3.8%），10 场共享 12.5/s 使队列饱和、请求节奏≈1 次/秒，
  而吃窗只有 1s 宽且快照只能在 T+1 后才有意义 → 命中率 65/106≈61%。
- 放大器：`_make_chi_pending` 的 `needed` 含自己，但 `seen` 只由 `pass`
  或**他家** timeout 增长；无碰牌选项时不提交 pass、自己的 timeout 走
  `mine` 分支 → `seen < needed` 恒真 → 必然等到 `ready_mono`(T+1) 才请求
  权威吃窗快照，把“必须落在 T+1 之后的一次往返”变成硬路径。
- timeout 计数口径（同房回放，回答“这些数说明什么”）：`my_timeout_chi`
  683 = 642 无合法吃牌 + 41 有合法吃牌；`my_timeout_peng` 2238 全部无合法
  碰/杠。即这两个值是**服务端窗口关闭事件数**，绝大多数无事可做、零损失；
  只有与 `claim_miss` 交集的部分是真实损失。
- 修复方向（均未实施）：压低共享排队（提 state-rate/分令牌/隔离配额）；
  削减非窗口轮询占用（帧合并、每场单在途）；修正 `seen` 语义使 T+1 一到
  即可发起快照请求；兜底方案是“预决策 + 快照校验”，须保持确认后才 POST。

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
