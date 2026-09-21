# Spec Delta

## MODIFIED Requirements

### Requirement: 候选解释保留原始值与加权贡献

完整或被接受的 partial 解释 SHALL 包括候选合法性/淘汰原因、向听、当前 U1/p1、当前有效牌种类、future improvement weight、weighted/expected child ukeire、future 有效牌种类、shape 与 feed、覆盖率、预算、搜索 metrics、最佳后续弃牌、模型假设及最终选择理由。未计算字段 MUST 以缺失与状态表示，不能伪装为零分；partial 结果 MUST 标记 `complete=false` 与 `partial_accepted=true`，exact/online 模式和实际内核 MUST 可识别。

#### Scenario: Accepted partial includes coverage
- **WHEN** online weighted frontier accepts a partial result
- **THEN** the selected candidate explanation contains `covered_weight`, `total_weight`, `coverage`, `partial_accepted`, and the search counters used to reach it

#### Scenario: Incomplete fallback does not fake future values
- **WHEN** coverage is insufficient and the evaluator falls back to legacy
- **THEN** future metrics remain missing/null, while the fallback reason and actual legacy level are recorded

#### Scenario: Equal current ukeire has future evidence
- **WHEN** two roots have equal current shanten and current weighted ukeire but different future metrics
- **THEN** the explanation shows both roots' weighted improvement and expected child ukeire before shape/feed tie-breaks

#### Scenario: 直接进张相同但结构取舍不同
- **WHEN** 同向听候选的 U1 相同而 weighted frontier 选择与 legacy 不同
- **THEN** 解释显示造成差异的已完成 weighted improvement、child ukeire、coverage 和最佳后续路径

#### Scenario: 对子保护未胜出
- **WHEN** BOT 拆开一个天然对子
- **THEN** 解释可以复核剩余雀头来源、该对子公开碰牌潜力及胜出候选的 future 收益，不仅输出“对子权重较低”
