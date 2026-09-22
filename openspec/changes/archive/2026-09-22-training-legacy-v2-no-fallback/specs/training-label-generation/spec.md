# Spec Delta

## ADDED Requirements

### Requirement: 训练标签必须来自评价器本身

以 legacyV2 系 evaluator 生成训练数据时 SHALL 使用不依赖在线时间预算的离线 profile：
排序、前沿上限与 partial 接受规则 MUST 与在线档一致，只放大幅度预算，使搜索要么完整
完成，要么以 bounds 证明的安全 partial 结束。生成过程 MUST NOT 把预算回退产生的 legacy
动作当作搜索标签写盘。

#### Scenario: 弃牌相位完整搜索
- **WHEN** 训练生成在一次弃牌决策上使用离线 legacyV2 profile
- **THEN** 该样本的 `label_level` 为 `weighted-two-ply-v1` 或 `weighted-two-ply-partial`，
  且 `label_fallback_reason` 为空

#### Scenario: 唯一前沿短路
- **WHEN** 保留候选只有一个
- **THEN** 样本记为 `legacy-one-ply`，仍视为评价器结论，不算回退

### Requirement: 搜索回退必须显式失败并可审计

当 legacyV2 系 evaluator 在弃牌相位真的回退到 legacy（原因不是作用域委托）时，数据生成
MUST 以显式错误终止该局，除非调用方显式允许回退。每个样本 SHALL 记录 `label_level` 与
`label_fallback_reason`，使"搜索标签 / 作用域委托 / 回退"可被审计与过滤；MUST NOT 以静默
方式把回退样本标记成搜索来源。

#### Scenario: 预算回退被拦下
- **WHEN** 某次弃牌决策因预算不足回退（如 `partial_not_acceptable`、`hard_deadline`）
- **THEN** 生成抛错并带上 seed/seat/原因，不写出该局的样本

#### Scenario: 显式允许回退
- **WHEN** 调用方显式允许回退
- **THEN** 该样本仍被写出，但 `label_fallback_reason` 必须记录回退原因

#### Scenario: 作用域委托不算回退
- **WHEN** 决策来自 discard-only 契约之外（`reaction_scope`、`baotou_scope`、
  `hu_kong_scope`）或唯一合法动作
- **THEN** 样本记为 `level="legacy"` 且 `label_fallback_reason` 为空，不被误判为搜索回退
