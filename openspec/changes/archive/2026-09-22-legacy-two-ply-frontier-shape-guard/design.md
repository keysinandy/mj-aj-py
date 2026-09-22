# Design

## Context

现有在线评价路径（见 proposal.md - Why 与本仓库 `mj/legacy_eval.py`）：

1. `_root_features()` 只保留「最小向听 - 财神弃牌 - 当前直接进张最大」的候选作为 frontier；
2. `_weighted_evaluation()` 在 `len(frontier) == 1` 时短路（`frontier_singleton`，`level=legacy-one-ply`），直接采用该候选；
3. 加权前瞻（Rust `weighted_two_ply_frontier`）只在 frontier ≥ 2 时执行，其产出 `future_ukeire_mean` / `future_ukeire_types_mean` 等指标。

因此"进张唯一最大 + 结构损失大"的候选不会被复核。实测（2026-09-21 记录）：当日 2175 条决策中 809 条走短路；`actual_kernel=legacy`（缺 `mj_kernels`）时另有 606 条落到 legacy 键。两个样例：seq 107 打 4s（12 张进张/结构损失 13）胜过打 9w（11/3）；seq 154 打 5s（13/6）胜过打 5w/7w/2s（11/3）。

约束：在线 soft/hard 预算 40/50ms；frontier 已有 `max_frontier_candidates`（当前 3）上限；公开信息口径与门禁不得变化；默认行为与指纹不得漂移。

## Goals / Non-Goals

**Goals:**
- 让"结构明显更优、进张只差 1 张"的候选进入同一次加权前瞻比较，避免拆面子换 1 张进张的候选按构造成锁定。
- 护栏、短路与内核降级状态在决策记录里可审计、可离线复算。
- 默认关闭、可回滚；发布前必须有成对 A/B 与延迟证据。

**Non-Goals:**
- 不改 legacy 键排序、财神保护、抓打圈、反应窗口与公开信息口径。
- 不把直接进张从排序中降级（护栏只在"唯一最大进张"时扩围，不做全候选比较）。
- 不引入新的搜索算法或改变现有加权前瞻的模型假设。
- 不在本变更内切换护栏默认值（默认切换由闸门与后续证据决定）。

## Decisions

### D1. 用"进张 slack + 结构损失 delta"的近似前沿，而不是全候选或固定 top-K

护栏准入条件：`primary_ukeire - candidate_ukeire ≤ shape_guard_ukeire_slack` 且 `candidate.shape_loss + shape_guard_shape_delta ≤ min(primary.shape_loss)`。

- 备选 A（全候选比较）：语义最干净，但 frontier 从 1 涨到全部最小向听候选，预算必然超支。
- 备选 B（固定 top-K）：可能把"进张差 2 张但结构好"的候选排除，也可能纳入进张差很多的候选，不可解释。
- 选 D1：只在"结构可能被误伤"的场景（进张接近 + 结构显著更优）扩围，扩围规模受 `max_frontier_candidates` 限制。
- 默认参数：`slack=1` 张、`delta=8`（"拆刻子"量级；`_discard_shape_cost` 中刻子 +8）。扫描方案：`slack ∈ {0,1,2} × delta ∈ {6,8,10}`，事前声明。

### D2. 护栏只在 primary 为单例时生效

primary 有多个候选时，既有实现已经会跑加权比较（不是短路场景），保持现状可避免无谓扩围与行为漂移。

### D3. 短路条件改为"护栏后唯一"

`frontier_singleton` 判据从 `len(frontier) == 1` 改为 `len(guarded_frontier) == 1`；否则进入既有加权比较（预算、覆盖率、partial 语义不变）。记录中保留 `frontier_singleton` 原因，新增 `admitted_by` 与 `frontier_guard` 明细。

### D4. 内核降级时不启用护栏

护栏的价值来自加权前瞻的比较结果；没有原生内核时前瞻不可用，扩围只会改变 legacy 键的候选集（违背回退语义），因此直接跳过并记录 `shape_guard_skipped_reason=kernel_unavailable`，保证 `MJ_KERNELS=python` 对拍与缺内核场景的确定性一致。

### D5. 审计字段

`evaluation.frontier_guard = {enabled, policy, slack_ukeire, shape_delta, primary_tiles, admitted_tiles, dropped_tiles, skipped_reason}`，候选级 `admitted_by ∈ {primary, shape_guard}`；字段只增不改，未启用时为 `enabled=false` 且不改变既有字段。

### D6. 降级显式化放在运行侧

缺内核是环境事实而非策略事实：clientd/runner 启动时 MUST 输出一次诊断（实际内核、版本、降级影响面），决策记录沿用 `actual_kernel`/`kernel_fallback_reason`。前端/回放可选提示，字段缺失按 `legacy_unrecorded` 处理。

## Risks / Trade-offs

- **预算**：护栏把 frontier 从 1 扩到最多 `max_frontier_candidates`（3），加权节点近似线性增长；需在 40/50ms 内验收，必要时把护栏候选计入 partial 覆盖率门槛。
- **误伤**：结构损失小不等于该留（例如孤张与靠张结构不同）。护栏只是让候选进入比较，最终仍由加权前瞻决定；因此门槛设成"进张差 1 张 + 结构差一个刻子量级"，并在 A/B 中验证不产生新的劣化样例。
- **参数敏感**：slack/delta 直接决定扩围频率，必须先声明扫描网格再评估，避免事后挑参数。
- **记录体量**：新字段增加记录体积；候选级字段只在护栏生效时写入。
- **降级静默**（本次事故根因之一）：护栏与降级是两个独立机制，本变更只保证护栏不误用，运行侧诊断另行要求（D6），未装内核时护栏不会让行为"看起来变好"。

## Open Questions

- 无阻塞性未决项；`slack`/`delta` 的具体取值由 evidence 阶段的扫描结果决定，不改变本设计的接口与闸门。
