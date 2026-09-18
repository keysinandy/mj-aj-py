# Spec Delta

## MODIFIED Requirements

### Requirement: 弃牌排序使用共享评价且胡/飘路径保持不变

`choose_action()` 的 HU 优先 MUST 保持既有语义；`_should_piao` 财飘行为除墙门收紧至
活墙可摸张数 ≥ 6（见 `bot-baotou-piao-discard` 的墙量守卫）外 MUST 保持既有语义。
不持财神状态下 legacy 的 `choose_discard()` 排序 MUST 保持；持财神状态下的排序 SHALL
遵循 `bot-baotou-piao-discard` 的爆头听优先档与爆头进张度量。shape-v1 SHALL 在合法
性、最低向听及财神保护后使用共享评价，允许同向听中直接进张较少但完整收益更高者
胜出。不得为了保留旧“进张永远压过结构”的测试而暗中禁用新排序；该旧断言应明确
限定到 legacy（不持财神状态），新的取舍按本变更规范验证。

#### Scenario: 现有 HU 与财飘回归
- **WHEN** 任一 evaluator 进入可 HU 或财飘分支且活墙可摸张数 ≥ 6
- **THEN** 结果与既有 HU/财飘规则一致

#### Scenario: 不持财神的 legacy 弃牌不漂移
- **WHEN** 同一不含财神的状态显式选择 legacy
- **THEN** 弃牌排序和旧冻结基线一致

#### Scenario: 持财神的 legacy 弃牌走新排序
- **WHEN** 持财神状态显式选择 legacy
- **THEN** 排序遵循 `bot-baotou-piao-discard` 的爆头听优先档与爆头进张度量

#### Scenario: shape-v1 使用共同站立牌面评价
- **WHEN** 同一站立牌面分别由普通弃牌候选与吃碰后候选产生，评价输入与层级相同
- **THEN** 原始特征、共享 Q 和稳定次序语义一致
