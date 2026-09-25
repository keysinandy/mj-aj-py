## MODIFIED Requirements

### Requirement: 决策携带可识别的评价版本与实际层级

shape-aware legacyV2 SHALL 额外区分实际弃牌决策 scope：

- `baotou_scope`：持财神、best_s=0 的专用爆头推进分支直接返回；
- `weighted_two_ply`：进入 weighted frontier / Stage A/B；
- `legacy`：回退或冻结基线排序。

baotou_scope MUST NOT 被记录为 weighted search 已使用；若该分支直接返回，`stage_b_entered` MUST 为 false，future-shape 指标 MUST 保持 missing/null。

#### Scenario: 财神专用分支提前返回

- **WHEN** 持财神且 best_s=0，`_choose_discard_baotou()` 成功选出弃牌
- **THEN** explanation SHALL 标记 `decision_scope=baotou_scope`
- **AND** SHALL NOT 声称 weighted two-ply 或 Stage B 参与了最终动作

### Requirement: 候选解释保留原始值与加权贡献

对于 shape-aware baotou_scope，解释 SHALL 至少保留参与最终 tie-break 的候选级字段：

- baotou tier；
- 是否为财神弃牌；
- baotou_ukeire；
- standing shape signature/quality；
- legacy discard shape cost；
- feed risk；
- stable tile；
- `baotou_shape_used` / 是否由 standing shape 改变 winner。

#### Scenario: 124s 财神牌例可复核

- **GIVEN** 牌串 `23455m 124s EE w`、副露 `789p`
- **WHEN** 1s 与 4s 的 baotou tier / baotou_ukeire 打平
- **THEN** explanation SHALL 显示旧局部弃牌损失 5/3 与新的 standing shape 比较
- **AND** 可明确归因 shape-aware 模式为何由旧选 4s 改为选 1s
