# Spec Delta

## ADDED Requirements

### Requirement: 尝试相位必须区分 Stage A 截断与 Stage B 中断

LegacyV2 的解释 SHALL 记录搜索实际走到的相位：只有 Stage A 被预算截断时报告
`future_shanten`，一旦进入 Stage B（无论完成还是被预算中断）MUST 报告 `two_ply`。
带哨兵 child ukeire 的行本身 MUST NOT 被当作 Stage-A-only 证明；Stage-A-only 只在
improvement bounds 证明胜者时成立，并 MUST 继续标记为缺失的 future ukeire 而不是零。
被中断的 Stage B MUST 保持 `search_used=false`、不提交部分 future 指标，并保留回退原因
与覆盖度。

#### Scenario: Stage B 被预算中断
- **WHEN** Stage A 已完整覆盖全部 draw，Stage B 因 work budget 或 deadline 未完成
- **THEN** 解释报告 `search_attempt_phase=two_ply`、`search_used=false`、回退原因与覆盖度，且不出现 child ukeire 数值

#### Scenario: Stage A 被预算截断
- **WHEN** 预算在 Stage A 未覆盖全部 draw 时耗尽
- **THEN** 解释报告 `search_attempt_phase=future_shanten`，不声称已进入 Stage B

#### Scenario: Stage A 证明胜者
- **WHEN** improvement bounds 在 Stage A 内证明唯一胜者
- **THEN** 解释报告 `future_shanten` 且 `search_used=true`，future ukeire 字段保持缺失状态

### Requirement: 解释与基准必须记录前瞻并行度

LegacyV2 的搜索指标 SHALL 记录本次决策实际使用的并行度（worker 数），并 SHALL 与
内核版本、预算和缓存容量一起保留，使同一 profile 下不同并行度的结果可区分。基准报告
SHALL 把完成率、`search_used_rate` 与 raw 延迟按并行度分别呈现，MUST NOT 用并行加速
单独证明决策质量或搜索完成率提升。缺少该字段的旧记录 MUST 按缺失处理，不得补造。

#### Scenario: 并行决策的解释
- **WHEN** 一次 weighted 搜索以多个 worker 完成
- **THEN** 解释的 search_metrics 暴露实际 worker 数，并在同一记录中保留 kernel 版本与预算

#### Scenario: 旧日志缺少并行度
- **WHEN** 读取在本 change 之前写入的 decision 记录
- **THEN** 解释元信息显示该字段缺失，logview/回放仍可处理，不推断历史并行度

#### Scenario: 基准对比
- **WHEN** 以不同并行度运行 100 状态基准
- **THEN** 报告分别给出 raw p50/p95/p99、complete、safe partial、fallback 与 search_used_rate
