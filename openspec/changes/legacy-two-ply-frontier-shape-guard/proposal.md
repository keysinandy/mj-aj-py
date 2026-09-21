# Proposal

## Why

2026-09-21 的线上记录（`a_a3de8f7235a2_r1_b9_t0` 等）暴露两件事：

1. **结构保真的候选根本没进比较**：当某个候选的当前直接进张唯一最大时，`_weighted_evaluation` 走 `frontier_singleton` 短路，加权前瞻从不执行 —— 于是"拆掉一副已完成面子换 +1 张进张"的候选按构造胜出（seq 107 打 4s：进张 12/结构损失 13，压过 9w 的 11/3；seq 154 打 5s：13/6 压过 5w/7w/2s 的 11/3）。当日 2175 条决策里 809 条被这条短路决定。
2. **内核缺失静默降级**：客户端进程所在解释器没有原生 weighted 内核时，整套评价退化成 legacy 键（当日 606 条 `native_weighted_kernel_unavailable`，`actual_kernel=legacy` 覆盖全部决策），只有翻记录 JSON 才能发现，运行日志与前端都不提示。

两者叠加让"结构损失"在唯一最大进张的场合完全失效，与既有排序意图（结构损失作为同进张的比较维度）和用户直觉都不一致。需要在**不放松在线预算**的前提下，把结构明显更优的近似进张候选纳入同一次前瞻比较，并让降级状态显式可见。

## What Changes

- 新增形状护栏前沿（shape-guard frontier）：primary frontier 只有一个候选时，额外纳入「直接进张差距 ≤ `shape_guard_ukeire_slack` 且结构损失比 primary 最优至少小 `shape_guard_shape_delta`」的最小向听候选，一起交给既有加权前瞻比较；仍受 `max_frontier_candidates` 截断。
- `frontier_singleton` 短路收紧为只对**护栏后**仍唯一的 frontier 生效；护栏纳入了多个候选时必须执行加权比较（预算/覆盖率/partial 语义不变）。
- 评价 JSON 增加护栏审计：`frontier_guard`（开关、slack、delta、准入牌、截断牌、跳过原因）与候选级 `admitted_by`。
- 内核不可用（缺 `mj_kernels`、`MJ_KERNELS=python`）时护栏 MUST NOT 改变比较集与选择，只记录 `shape_guard_skipped_reason=kernel_unavailable`，保持现有 legacy 回退与对拍路径。
- 降级显式化：运行侧（clientd/runner 启动与决策记录）显式报告"实际内核 = legacy / 降级原因"，前端与回放可据此提示，不再只在个别字段里可查。
- 护栏以 profile 开关提供；**2026-09-21 决定默认开启**（`weighted_online()` /
  `weighted_offline()` 显式置 true，精确/legacy V1 档案保持关闭）。闸门证据与风险
  记录见 design 与 `evidence/shape-guard-ab.json`：触发率 0.63%、护栏边际延迟 ≈ 0，
  但本机基线已超预算、离线 A/B 无收益指标，属"按决定开启、留可回滚开关"。
- 公开信息口径、抓打圈、财神保护、反应窗口门禁与 `legacy`/`MJ_KERNELS=python` 回退行为全部不变。

## Capabilities

### New Capabilities
- `legacy-two-ply-frontier-shape-guard`: 形状护栏前沿的准入条件、短路收紧、审计字段、内核降级解耦与默认切换闸门。

### Modified Capabilities
- `bot-decision-explanations`: 评价解释必须显式给出前沿护栏状态与内核降级（实际层级/原因），不再依赖逐字段排查。

## Impact

- 代码：`mj/legacy_eval.py`（`_root_features` / `_limit_weighted_frontier` / `_weighted_evaluation` / profile 旋钮 / 审计字段）、`mj/bot.py`（profile 透传与 info）、`mj/clientd`（启动降级诊断）、前端/回放对新增字段的展示。
- 性能：护栏最多把比较集从 1 个候选扩到 `max_frontier_candidates` 个，加权搜索节点与延迟上升，必须在 soft 40ms / hard 50ms 预算内验收；未开护栏时零开销。
- 兼容：精确/legacy V1 档案指纹与行为不变；weighted 在线/离线档案因默认开启护栏而
  **指纹变化**（`shape_guard_enabled` 进入 payload），旧记录仍可读，新增字段只增不改。
- 风险：护栏扩围可能把"该打的孤张"排除掉（结构损失小 ≠ 该留），因此以冻结回放的成对 A/B 与延迟验收为发布闸门，未达标保持默认关闭。
