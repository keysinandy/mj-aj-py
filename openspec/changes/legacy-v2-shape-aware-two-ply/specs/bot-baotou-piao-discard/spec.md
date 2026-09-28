## ADDED Requirements

### Requirement: 持财神听牌态按爆头与当前自摸胡牌加权进度排序

持财神且 best_s=0 时，同 baotou tier 候选在既有财神保护顺序之后，SHALL 比较
`1.5 × baotou_ukeire + current_selfdraw_hu_ukeire`，不得把 baotou_ukeire 和普通自摸
胡牌进张作为两个先后级别。`current_selfdraw_hu_ukeire` SHALL 来自该候选自己的弃后站立手，
只统计当前规则允许的自摸胡牌听口及其未见张数；YCBK 开启时 SHALL 按 `_can_hu` 门禁过滤。
爆头听 tier 0 的爆头进张和当前合法自摸胡牌进张都按所有可摸牌型计数。shape-aware
profile MAY 在 tier、财神保护和加权进度分完全打平后以 standing shape quality 作 tie-break；
旧 `_discard_shape_cost` SHALL 保留在 standing shape 之后。关闭 shape-aware 时仍使用相同
加权进度公式，但不使用 standing shape。

#### Scenario: 组合进度胜过单项爆头进张

- **GIVEN** 持财神、YCBK 关闭且两个候选处于相同 baotou tier
- **AND** 弃 2筒的 baotou_ukeire=7、current_selfdraw_hu_ukeire=9
- **AND** 弃 9条的 baotou_ukeire=5、current_selfdraw_hu_ukeire=21
- **WHEN** legacy baotou_scope 排序
- **THEN** 弃 9条 SHALL 胜出（28.5 > 19.5）

#### Scenario: 加权进度打平后保留更好搭子

- **GIVEN** 持财神且 best_s=0
- **AND** 候选 A/B 的 baotou tier、财神保护和加权进度分相同
- **AND** A 留 24s、B 留 12s
- **WHEN** shape-aware baotou_scope 排序
- **THEN** A SHALL 在旧局部弃牌损失之前胜出

#### Scenario: standing shape 不覆盖更高的组合进度

- **GIVEN** 两候选 baotou tier 相同且财神保护顺序相同
- **AND** A 的 `1.5 × baotou_ukeire + current_selfdraw_hu_ukeire` 大于 B
- **AND** B 的 standing shape 更好
- **WHEN** 排序
- **THEN** MUST 选择 A

### Requirement: 爆头进张计算受预算与回退约束

shape-aware baotou tie-break SHALL 复用本次候选的 post-discard standing shape，不得为 baotou_scope 隐式启动 generic weighted two-ply、Stage B 或新的未来枚举。Rust baotou kernel 不可用、节点预算超限或 X/Y/Z 收手时，仍 MUST 整档回退旧路径，且不得让部分 shape 结果污染 fallback。

#### Scenario: baotou_scope 不伪装 two-ply

- **WHEN** `_choose_discard_baotou()` 完成并直接返回
- **THEN** evaluation SHALL 标记 `decision_scope=baotou_scope`
- **AND** `stage_b_entered=false`
- **AND** future-shape 字段保持 missing/null
