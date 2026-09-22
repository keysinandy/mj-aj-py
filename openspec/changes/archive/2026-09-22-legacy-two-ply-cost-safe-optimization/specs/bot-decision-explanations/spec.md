# Spec Delta

## MODIFIED Requirements

### Requirement: 候选解释保留原始值与加权贡献

完整或被接受的 partial 解释 SHALL 包括候选合法性/淘汰原因、向听、当前 U1/p1、当前有效牌种类、future improvement weight、weighted/expected child ukeire、future 有效牌种类、shape 与 feed、覆盖率、预算、搜索 metrics、最佳后续弃牌、模型假设及最终选择理由。被接受的 partial 候选 MUST additionally expose observed improvement, improvement lower bound, improvement upper bound, and the covered-weight denominator used for partial means. 未计算字段 MUST 以缺失与状态表示，不能伪装为零分；partial 结果 MUST 标记 `complete=false` 与 `partial_accepted=true`，exact/online 模式和实际内核 MUST 可识别。

#### Scenario: Accepted partial includes decision-safe bounds
- **WHEN** online weighted frontier accepts a partial result
- **THEN** the selected candidate explanation contains covered/total weight, coverage, observed improvement, lower/upper improvement bounds, the partial mean denominator, and search counters

#### Scenario: Unsafe partial does not fake future values
- **WHEN** coverage is high but no root is proven safe by the improvement bounds
- **THEN** future metrics remain missing/null in the selected legacy fallback explanation, while the fallback reason and actual legacy level are recorded

#### Scenario: 直接进张相同但结构取舍不同
- **WHEN** 同向听候选的 U1 相同而 weighted frontier 选择与 legacy 不同
- **THEN** 解释显示造成差异的已完成 weighted improvement、child ukeire、coverage 和最佳后续路径

#### Scenario: 对子保护未胜出
- **WHEN** BOT 拆开一个天然对子
- **THEN** 解释可以复核剩余雀头来源、该对子公开碰牌潜力及胜出候选的 future 收益，不仅输出“对子权重较低”

#### Scenario: Singleton frontier records short-circuit
- **WHEN** current shanten and current ukeire leave only one retained root
- **THEN** the explanation records a one-ply short-circuit reason and does not claim that uncomputed future metrics are zero or complete
