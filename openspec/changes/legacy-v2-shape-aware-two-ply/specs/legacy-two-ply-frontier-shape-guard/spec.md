## MODIFIED Requirements

### Requirement: 形状护栏前沿准入

shape-aware profile 下，“结构明显更优” SHALL 指 post-discard standing hand quality，而不是仅指打掉当前 tile 的 discard-local shape cost。

新 standing-shape guard MUST 使用独立版本化 gate；旧 shape_guard_shape_delta 的数值单位不得直接复用为 standing-shape encoded unit。

护栏仍只允许在：

- 相同最小 shanten；
- current ukeire 差距处于配置 slack；
- frontier 总数不超过 max_frontier_candidates；

的条件下扩围。

#### Scenario: 24s 候选不因旧弃牌损失被错误挡掉

- **GIVEN** 两个 root shanten/current ukeire 处于护栏可比较范围
- **AND** root A 留 24s
- **AND** root B 留 12s
- **AND** 旧 discard-local cost 对 A 不利
- **WHEN** shape-aware guard 运行
- **THEN** admission SHALL 使用 standing shape 判断
- **AND** MUST NOT 仅因旧 discard-local cost 拒绝 A

### Requirement: 护栏审计字段

shape-aware profile SHALL 额外记录：

- standing shape signature/quality；
- shape quality version；
- guard 使用的是 legacy discard-cost 语义还是 standing-shape 语义；
- 因 standing-shape 被准入/淘汰的原因。

#### Scenario: 离线复核 shape guard

- **WHEN** shape-aware guard 改变了 frontier
- **THEN** evaluation SHALL 提供足以复算 admission 的 standing shape 字段
