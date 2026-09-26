## ADDED Requirements

### Requirement: 持财神非爆头听牌态使用爆头进张度量(自适应收手)

持财神且弃后站立手为普通听牌（非爆头听）时，同最低向听候选的进度信号 SHALL 继续使用爆头进张。shape-aware profile MAY 在 baotou tier、财神保护与爆头进张完全打平后使用 post-discard standing shape quality 作为下一层 tie-break；standing shape MUST NOT 覆盖更优的 baotou tier 或更大的 baotou_ukeire。

旧 `_discard_shape_cost` SHALL 保留在 standing shape 之后作为兼容 tie-break。shape-aware 开关关闭时，排序 MUST 与变更前逐项一致。

#### Scenario: 爆头进张打平后保留更好搭子

- **GIVEN** 持财神且 best_s=0
- **AND** 候选 A/B 的 baotou tier、财神保护、baotou_ukeire 相同
- **AND** A 留 24s、B 留 12s
- **WHEN** shape-aware baotou_scope 排序
- **THEN** A SHALL 在旧局部弃牌损失之前胜出

#### Scenario: standing shape 不覆盖爆头进张

- **GIVEN** 两候选 baotou tier 相同
- **AND** A 的 baotou_ukeire 大于 B
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
