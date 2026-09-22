# Spec Delta

## MODIFIED Requirements

### Requirement: 候选解释保留原始值与加权贡献

完整解释 SHALL 包括候选合法性/淘汰原因、向听、U1/p1、I、H2、`future_improve_weight`、`future_ukeire`、可选的 `future_ukeire_mean`、结构与对子用途、所用层级下的 Q、加权贡献、最佳后续弃牌、模型假设及最终选择理由。legacy V1 的未来字段 MUST 说明其为一次未来摸牌、最佳摸切和 `4-visible` 权重模型，不得伪装为完整 EV、实测胜率或对手行为推断。未计算字段 MUST 以缺失与状态表示，不能伪装为零分；只有某字段在实际评价层级有定义时才参与排序。未来候选解释 MUST 保留所选未来弃牌以及导致 `future_improve_weight` 和 `future_ukeire` 的可审计摸牌权重摘要。

#### Scenario: 直接进张相同但未来取舍不同
- **WHEN** 同向听候选的当前 U1 相同，而 legacy V1 通过未来特征选择了不同于基线 legacy 的弃牌
- **THEN** 解释显示两者的当前进张、未来改良权重、未来进张、牌型损失、喂牌风险、最佳后续弃牌及最终排序差异

#### Scenario: 直接进张相同但结构取舍不同
- **WHEN** 同向听候选的 U1 相同而 shape-v1 选择与 legacy 不同
- **THEN** 解释显示造成差异的已完成分量及最佳分解/改良路径

#### Scenario: 20260920 的 9b 反事实重算可复核
- **WHEN** 离线重算 `u_9812ba08fe2f_a_e18e58e3acb8_r1_b9_t0` 的 `seq=100`，并比较 `9b` 与 `3t`
- **THEN** 结果同时标明原日志实际选择、V1 反事实候选、输入状态和评价版本；不得把离线选择写成原时点已执行动作

#### Scenario: V1 预算回退不补零
- **WHEN** legacy V1 只完成当前 ukeire，未来特征因预算或输入不完整而未完成
- **THEN** 解释将未来字段标记为缺失，记录实际 legacy 层级和回退原因，并按完整 legacy 排序展示，不把缺失字段序列化为 0

#### Scenario: 对子保护未胜出
- **WHEN** BOT 拆开一个天然对子
- **THEN** 解释可以复核剩余雀头来源、该对子公开碰牌潜力及胜出候选的其他收益，不仅输出“对子权重较低”
