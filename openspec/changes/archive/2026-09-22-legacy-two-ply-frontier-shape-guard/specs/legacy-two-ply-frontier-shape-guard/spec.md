# Spec Delta

## Purpose

约束在线 weighted 弃牌评价的候选比较集：在"唯一最大直接进张"时也要让结构明显更优的近似候选进入同一次前瞻比较，并让护栏、短路与内核降级状态可审计、可回滚。

## ADDED Requirements

### Requirement: 形状护栏前沿准入

评价器 SHALL 先按最小向听与财神门禁形成候选集，取当前直接进张最大者为 primary frontier。当 primary frontier 只有一个候选时，SHALL 额外纳入同时满足「直接进张差距 ≤ `shape_guard_ukeire_slack`」与「结构损失比 primary 最优至少小 `shape_guard_shape_delta`」的候选；护栏候选与 primary 候选合并后 MUST 仍受 `max_frontier_candidates` 截断，截断排序 MUST 稳定声明（直接进张降序、结构损失升序、喂牌风险升序、牌编号）。护栏关闭（开关关闭或 slack 为 0）时，比较集与现有实现完全一致。

#### Scenario: 拆面子候选进入比较
- **WHEN** 唯一最大直接进张候选需要拆掉一副已完成面子，而另一候选少 1 张直接进张但结构损失明显更小
- **THEN** 两个候选进入同一次加权前瞻比较，最终选择由比较结果给出，并记录护栏准入明细

#### Scenario: 进张差距超出护栏
- **WHEN** 次优候选的直接进张差距大于配置 slack
- **THEN** 该候选不进入护栏前沿，选择与护栏关闭时一致

#### Scenario: 护栏截断可复现
- **WHEN** 满足护栏条件的候选多于 `max_frontier_candidates`
- **THEN** 按声明的稳定排序截断，候选输入顺序变化不改变选择

### Requirement: 短路只在护栏后唯一时生效

`frontier_singleton` 短路 MUST 只适用于护栏前沿仍只有一个候选的情形。护栏纳入多个候选时 MUST 执行既有加权前瞻比较，并沿用既有的预算、覆盖率与 partial 接受条件；未完成时的回退层级与原因 MUST 与现状一致，MUST NOT 把缺失特征当零参与排序。

#### Scenario: 护栏扩围后仍为单候选
- **WHEN** 没有任何候选满足护栏准入条件
- **THEN** 允许短路并记录原因为 frontier_singleton

#### Scenario: 护栏扩围后预算不足
- **WHEN** 护栏前沿有多个候选而加权前瞻未在预算内完成
- **THEN** 按既有 partial/回退规则处理并记录原因，不把缺失值当零排序

### Requirement: 护栏审计字段

评价 JSON SHALL 记录护栏策略与实际生效情况（开关、slack、delta、准入牌、被截断牌、跳过原因），并在候选级标注 `admitted_by`（`primary` 或 `shape_guard`）。护栏未启用时 MUST NOT 改变既有字段的语义与取值。

#### Scenario: 决策可离线复核
- **WHEN** 一次决策因护栏改变了比较集
- **THEN** 日志给出准入/截断明细与最终选择的加权指标，可离线复算

### Requirement: 护栏在内核降级时不改变结果

实际内核不是原生 weighted 内核时，护栏 MUST NOT 改变候选比较集与最终选择，MUST 保持既有 legacy 回退语义，并记录 `shape_guard_skipped_reason=kernel_unavailable`。`MJ_KERNELS=python` 对拍路径 MUST 与该语义一致。

#### Scenario: 缺原生内核时开启护栏
- **WHEN** 运行进程没有可用的 weighted 原生内核而护栏已开启
- **THEN** 选择与护栏关闭时完全相同，并显式记录跳过原因

### Requirement: 默认值与切换记录

护栏 SHALL 以显式 profile 开关提供。默认值 MUST 记录在 profile 定义里（当前
`weighted_online()` / `weighted_offline()` 为开启，精确/legacy V1 档案为关闭），且
每次默认变更 MUST 留下证据：全量测试与 `openspec validate --strict`、成对 A/B（同一预算，
报告触发率、决策变化数、逐条差异与延迟分位）、参数扫描方案事前声明。若证据显示护栏使
p95 超出预算或产生新增窗口损失，MUST 回退默认并记录。

#### Scenario: 默认开启后的证据留档
- **WHEN** 默认从关闭切换为开启
- **THEN** change 的 evidence 记录触发率、变化条数、延迟分位与基线对照，并保留关闭开关

#### Scenario: 延迟或窗口损失回归
- **WHEN** 开启后 p95 超出预算或出现新增窗口损失
- **THEN** 回退默认值并在 evidence 中记录回退原因
