# 线上房 a_30361c544db9：根因、修复与验证

诊断日期：2026-09-14。输入为本房 10 个 JSONL、当前源码、HANDOFF.md 和离线复现实验。本文先记录原房证据，再记录已落地的代码修复；原房数字不与修复后验收混用。

## 结论与原报告更正

主要故障是状态请求排队期间将 SSE 最新水位写进请求游标，跳过未消费事件。镜像因此缺少弃牌/吃碰等更新，随后长期跳过决策；统计又把无法评估的窗口当成可能损失，放大了代打和漏吃数字。

Q 频繁回退是独立的计算问题：完整搜索成本远超 16ms/7ms 预算，反应评估还缺少完整 Q0 回退层。二者可能通过线程调度相互影响，但无需这个推测就能分别复现。

原运行报告中的数字需要保留以下区别：

| 原指标 | 本次核查 |
| --- | --- |
| mirror_resets=25 | 25 次均为吃/碰牌与镜像 pending 不匹配，全部在游标前跳之后发生；另有 2 次评价输入异常、2 次动作 409 重锚，共 29 条 reset。 |
| auto_played=1757 | 兼容诊断计数，混合跳过决策、可能漏碰和推测代打，不是服务端打出的牌数。记录到的本人 discard timeout 是 288 条；它也不是未经分类的可避免损失数。 |
| C1_CONFIRM_NOT_CREATED=12 | 12 条全部关联到非上家的旧弃牌。该身份下根本不能吃，不能宣称有 12 次真实漏吃。 |
| canonical CLIENT_LOSS=14 | 包含上述 12 条错误关联及一个他家碰优先终止，原归因不可继续用于验收。另一个窗口存在明确确认 HTTP 返回晚。 |
| authoritative identity coverage=80/84 | 仅是脚本按字段统计的身份覆盖，不能证明事件链连续或窗口关联正确。 |
| gap_decision_impact=0 | 脚本只检查服务端 gap/快照重锚，漏掉了客户端主动跳游标；不能证明本房没有丢事件。 |
| Q0 391 / Q 69 | 分母是 460 次普通摸牌评价，Q0 回退率 85%。其余 37 次 draw 是既定胡/财飘/杠分支。另有 80/80 次反应决策因 time_budget 回退 legacy。 |

另需保留验收失败事实：窗口审计脚本退出码为 1；b2 结算重放累计 `[-10, -3, -3, 16]` 与 end `[0, -4, -4, 8]` 不符。运行进程正常退出不能代替事件/结算完整性验收。

## 1. 排队候选污染了增量请求游标（P0）

定位：`mj/platform/state_demand.py:486`。

```python
if self.kind_priority == SSE_DELTA and self.watermark_target is not None:
    self._candidate.default_seq = self.watermark_target
```

`default_seq` 本来由 BotClient 提供，代表镜像已消费的位置。候选等共享限流许可期间，SSE 线程通过 `wake.put()` 提交更大水位；上述分支将它覆盖。`admit_candidate()` 把被覆盖的值传给 `start_request()`，最后作为实际 `/state?seq=` 参数。`start_request()` 虽然有“水位不能充当游标”的保护，输入在更早的候选阶段已经坏了。

本房 791 条请求满足 `requested_seq > req.seq`，跳过的序号区间长度累计 2193。这里统计的是序号跨度，不将所有序号都断言为本视角必可见事件。25/25 次 pending 失步前都有这样的请求。

直接证据，b9 首次失步：

1. JSONL 行 90：本地消费到 seq=31，pending 还是座位 2 打出的 3w。
2. 行 95：本地 seq=31，实际请求 seq=34，返回的事件从 35 开始。
3. 行 101：本地 seq=37，实际请求 seq=40；行 102 收到 seq=41“座位 3 碰发”。
4. 行 103：镜像仍认为 pending 是 3w，与“发”不符，触发重锚。

同房 b0 行 1023 消费本人摸牌 seq=500 后，行 1029 的实际请求从 501 开始，丢掉中间的弃牌更新。后续 response 阶段暗手仍为 14 张（应为 13 张），下一次摸牌会成为 15 张。

离线最小复现，无网络和策略参与：

```python
d.submit_sse(31)
d.queue_candidate(default_seq=31)
d.submit_sse(34)                # 模拟排队期间的新 SSE
assert d.admit_candidate().seq == 31
```

修复前实现返回 34，断言失败；修复后保持候选游标 31，SSE 目标 34 只留在 reason ledger 中。在独立进程中验证 RESYNC 仍正确升级为 FULL/seq=0。

`git blame` 指向提交 `5f4ce169`（state request lifecycle），早于此次 shape-v1 改动。现有 `test_delta_uses_local_cursor_not_sse_wake_watermark` 直接测试 start_request，遗漏“排队→收到新 SSE→获取许可”的路径。

已实施：

- 删除排队候选把 `watermark_target` 写入 `default_seq` 的覆盖路径；候选在 admission 前保持 BotClient 提供的本地 applied cursor，SSE 目标只留在 reason ledger 中。
- 增加“排队→新 SSE→admit”回归，覆盖 `31 → 34` 水位变化仍以 `seq=31` 发送；RESYNC/FULL 升级路径保持 `seq=0`。
- 修复后的两次线上 canary 均逐请求核对 cursor-at-queue/admit/send/apply，`cursor_ahead_of_applied=0`；不能用“所有 event.seq 必须连续”代替此检查，因为协议可能过滤本视角不可见事件。

## 2. 张数漂移持续跳过决策，兼容计数放大异常（P0）

定位：`BotClient._skip_drifted`（3468 附近）、`_lost_claim`（1734）、`Mirror.hand_count_ok`（351）。

- `_skip_drifted()` 增加 auto_played 后直接返回，不请求重建；同一异常镜像可持续到下一个快照。
- `_lost_claim()` 在张数不合法时直接返回 True。每收到本人 peng timeout 都增加 auto_played，即使无法证明有碰/杠可做。
- 本房重放识别到 34 段“本人 response timeout 时手牌张数异常”，34 段全部在游标前跳之后；已记录的 response 快照本身没有张数异常。
- 2174 条本人 peng timeout 中，869 条发生在张数漂移状态，会走上述保守记账。725 条本人 chi timeout 中，287 条发生在漂移状态。
- 本房 hu_failed=0，没有证据支持把这些漂移解释为代码注释中的旧服务端 hu_failed 问题。

已实施：生产 `mode="match"` 首次张数不变量失败即停止旧镜像，抛出 `_ActionResync` 走主循环的 `seq=0` FULL 重锚；不再把该状态推断为服务端 `auto_played`。窗口截止缺失也只记客户端状态放弃，等权威状态确认，不把它直接计入服务端代打。新增 `client_state_abandons`、`mirror_drift_resets`、`state_drift_auto_played`；旧 scripted adapter 保留兼容计数。`_lost_claim` 在生产模式对坏镜像返回不可评估，不再按每个 peng timeout 盲增代打/漏碰。

拆分指标：`mirror_invalid_episode`、`decision_skipped_invalid_state`、`server_timeout_discard`、`server_timeout_response`、`verified_eligible_window_loss`；只有确切服务端回声才能计入对应代打事实。未知合法性记录 unknown，不计为已证实漏碰。

## 3. 吃窗误归因、身份未确认和真实 HTTP 长尾（P0/P1）

定位：`BotClient._claim_legal`（1747）、`Mirror.build_game`（400）、`Game._legal_reacts`（168）、`scripts/window_acceptance.py`。

引擎正常流程在 `_begin_react()` 保证只有弃牌者下家能吃；Mirror.build_game 使用简化反应序列，没有校验 response_chi 的 owner 必须为本人上家。实际 `_make_chi_pending()` 有这个校验，诊断 `_claim_legal()` 却没有。游标跳过后旧 pending 没清掉，后续 timeout 就能给另一张旧弃牌构造“可吃”假象。

19 条 chi claim_miss 中，14 条关联的 owner 不是上家，包括原报告全部 12 条 C1。例：b0 本人座位 3，行 2688 把 seq=1467 的本人 chi timeout 关联到座位 0 在 seq=1451 打出的 8w；中间存在两次游标前跳。座位 3 不能吃座位 0 的牌。

另一个误分类：b2 行 2819/2826 两张快照显示座位 1 新增“碰 5t”，原吃窗因他家碰优先而结束。旧报告将其标成 PROTOCOL_PHASE_UNOBSERVABLE/CLIENT_LOSS；应按匹配的权威副露变化识别为规则抢先终止。不能仅凭任意副露数增加就进行此归因，须匹配局、牌、副露类型等。

116 条 unconfirmed 是观测次数：49 条 phase_not_reached（碰阶段尚未进入吃阶段）、61 条 identity_unknown、6 条 identity_confirmation_budget_exhausted。不能直接当成 116 个漏窗。FULL 快照未显式提供 source discard sequence，加上主动跳过源事件，使身份确认更困难；修完游标后还须验证剩余协议缺字段问题。

真实传输问题例：b5 第一局 source seq=153（行 288），确认请求行 297 排队 0ms，但 HTTP 用时 1464ms。返回时快照已到本人 draw，超出当时用于调度的吃窗估算上界约 412ms；此前没有拿到 response_chi 授权。可确认请求跨过了窗口推进，单凭客户端数据不能进一步区分网络、网关或服务端耗时，也不能把碰窗的精确 deadline 当成吃窗精确 deadline。

已实施：

- `Mirror.build_game("response_chi")` 与 BotClient `_claim_legal` 都校验“本人必须是出牌者下家”；非法 owner 不再生成吃牌合法集或 claim_miss。
- 源窗口终止、镜像无效时撤销 source identity，禁止继续把陈旧 identity 标成 authoritative。
- 审计仍需用修复后日志同时核对请求游标与本地游标、合法吃牌位置、成功/PASS/他家优先/未知事实；原房报告不回写。
- 确认观测按 WindowAttemptKey 聚合终态；区分“还在碰阶段”“缺身份”“传输返回晚”。
- 游标修复后再复测真实 HTTP 长尾；保留共享限流器和单一在途所有权，验证现有 deadline-aware timeout 与阶段确认请求路径。客户端传输边界不足时再关联服务端 trace。增加限流速率无法解决排队为 0 的这条样本。

## 4. Q 完整搜索成本与回退层缺失（P1）

在线数据：普通摸牌评价 460 次，391 次 Q0/69 次 Q。69 次完整 Q 中，65 次为 0 向听、4 次为 1 向听，多候选和更远向听几乎没有完成 Q。33 次碰窗、47 次吃窗评价全部回退 legacy。

当前时间预算为弃牌 16ms、反应 7ms，节点上限分别 4096、2048。先枚举最低向听弃牌，对每个候选遍历下一摸，再遍历假想弃牌、进张；I 和 H2 重复计算不少相同后继。候选数乘后继分支数，使计算量快速扩大。

关键实现问题：

1. `evaluate_discard_candidates` 完成所有 Q0 后，逐候选算 Q。任一候选超预算，整次采用 Q0；此前完成的部分 Q 必须舍弃，否则会形成不公平的混合评分。
2. `evaluate_reaction` 直接计算 PASS 的完整 Q，再计算吃碰分支；预算耗尽直接返回 legacy，没有先完成所有 PASS/吃碰候选的 Q0。这与设计约定的完整层级回退不符。
3. `EvalContext.replace()` 每个假想状态都构建完整 dataclass，并重复扫描 34 张牌进行转换、可见张数检查；搜索热点在 Python 层。
4. `_base_features`/`_best_future_discard` 缺少决定内结果复用；当前主要缓存分解。Q 入口还重复计算已经完成的 Q0 基础特征。
5. Q0 基础计算和部分循环没有及时预算检查；只在 tick 检查时间，kernel() 仅计数，不能保证严格不超额。
6. 日志对共享 budget 的累积 nodes/kernel_calls 再求和，完整 Q 可能重复计数；回退到 Q0 又可能隐藏已放弃 Q 的工作量。反应回退记录缺少这些耗时/工作量字段。

离线实验直接使用本房记录点重建的可见牌面，开启 Rust shanten/ukeire；python 与 python3 均为 pyenv 3.11.14。放宽预算只用于诊断，不代表上线建议或收益验证。

- 分散抽样 12 个 Q0 回退摸牌输入，在单线程、预算 2000ms/1,000,000 nodes 下完成 Q，耗时约 55–959ms；4 个吃窗输入约 46–337ms。
- b0 decision 1：另一次带真实计数的完整复算约 966ms，实际 7189 个逻辑节点、17011 次内核调用，均已超出原预算；原结果字段却报告 42449/101643，证实共享计数重复累加。
- b0 decision 4：7ms 预算约 9.4ms 回退 legacy；宽预算完成约 314ms。该输入放宽后选 PASS，原记录选吃，说明回退确实影响新评价参与决策，但不据此宣称宽预算选择收益更高。
- cProfile 定位 `_best_future_discard`、`EvalContext.replace/__post_init__`、`_base_features` 为主要热点。剖析有额外开销，剖析秒数不作实时性能验收值。

已实施：

- 反应决策改为 `legacy → 全候选 Q0 → 全候选 Q`：Q0 不完整才回 legacy；Q 深搜超时则原子回退到完整 Q0，禁止混用部分 Q 结果。反应 Q0 使用有界 B/C 形状代理，避免 7ms 窗口被首次分解 DFS 吃完；大预算离线调用仍可尝试完整 Q。
- 反应层先复用完整 Q0 的后继弃牌候选，再原子尝试 Q；当前完整 Q 的基础特征缓存/更深后继去重仍是后续性能任务，不能把本次 Q0 优化写成已完成的全量缓存。
- 外部输入完整校验一次；可信假想状态使用轻量内部结构与增量校验，保留材料守恒。禁止通过移除外部输入校验来掩盖坏镜像。
- 使用有证明的剪枝，例如两摸不可能胡的高向听分支跳过 H2；保留全候选比较，不按牌编码截 top-K。明确并测试后继选择使用完整 Q0 的约定，当前 `_best_future_discard` 实际仅按 p1 排序。
- Python 优化仍不达标时，将批量后继搜索及基础特征循环下沉 Rust，减少 Python/Rust 往返；以 Python 参考实现做定向和随机差分。
- 预算覆盖整次决定，记录实际总节点、内核调用、各层耗时、被舍弃的 Q 工作量与 fallback_stage。反应回退也记录完整诊断。
- 剩余授权时间可缩小计算预算；保留提交前最终检查。无需为了提高 Q 完成率把线上预算直接升到几百毫秒。

本次代码验证：全量 `pytest` 通过（393 passed, 2 subtests passed）；新增游标、非下家吃牌、反应 Q0 回退、生产状态放弃归因测试均通过。修复后 canary 房 `a_bc5acdc75e81` 完成 10 场：`cursor_ahead_of_applied=0`、`mirror_resets=0`、`mirror_drift_resets=0`、`auto_played=0`、`state_drift_auto_played=0`；反应层出现 163 次 Q0、3 次完整 Q，仅 3 次因 Q0 本身不完整回 legacy。窗口审计退出码为 0，170 个窗口中 169 个为完整权威身份、1 局仍有部分身份覆盖；另有 1 次正常动作 409 和 4 个确认/HTTP 竞态归因的 canonical loss，且 1 局结算回放仍受协议边界影响；这些未作为“零损失发布”宣称。完整摘要见 `openspec/changes/bot-shape-aware-evaluation/artifacts/online_repair_canary_20260914.json`。

最终代码追加 canary 房 `a_0b3ed63c88ae`：10 局在同一房间生命周期 404 边界统一结束，未取得 game-finished 结算，故不纳入完成局分母；在可观测区间内 `cursor_ahead_of_applied=0`、`mirror_resets=0`、`mirror_drift_resets=0`、`auto_played=0`、`response_409=0`、`post_uncertain=0`，110 个可归属窗口身份均为 authoritative，仍有 1 个协议阶段不可观测结果。该房仅作为最终代码的传输/窗口安全 canary，摘要见 `openspec/changes/bot-shape-aware-evaluation/artifacts/online_repair_canary_20260914_b.json`。

## 实施次序与验收

1. **P0 游标修复与镜像恢复**：新增排队中收到 SSE、十场交错、FULL 升级/重试/应用失败回归；最小复现先红后绿，旧动作不重发。验收要求 request cursor ahead 为 0，同一失步不会导致一整段持续跳过决策。
2. **P0 统计与归因修复**：覆盖非上家吃牌、陈旧 pending、未知身份、漂移 timeout、他家碰的快照证据。用本房日志验证误分类被正确降级/更正。原 1757/C1=12 不再作真实损失指标。
3. **P1 评价器层级与性能**：补反应 Q0，修计数，再按热点去重/剪枝/批处理。锁定 A–E 与同可见输入差分，报告分向听、候选数、冷热缓存、十场并发下 Q0/Q 完成率和延迟；不能只靠回退后 p95 合格宣称搜索达标。
4. **新的线上验收**：先一房确认 P0，再按既定发布计划做三个独立房。每房核查游标安全、镜像有效性、真实 timeout 与窗口关联、策略层级、动作提交及结算完整性。旧房记录用于诊断，不混入修复后的分母。

替代路线：全程 seq=0 可规避部分增量游标风险，但增加快照成本、丢失事件链与窗口身份，不作为常规方案；暂时仅使用 Q0 可作为显式标记的降级版本，但无法代表 I/H2 前瞻优化已完成。推荐按上述顺序修复根因。

证据：`local/games/20260914/u_9812ba08fe2f_a_30361c544db9_r1_b{0..9}_t0.jsonl`、修复房 `a_bc5acdc75e81` 与最终 canary `a_0b3ed63c88ae` 的同日 JSONL；原报告 `openspec/changes/bot-shape-aware-evaluation/artifacts/online_run_20260914_a30361c544db9.json`。本诊断更正原报告的因果/窗口解释，保留其原始计数用于追溯。
