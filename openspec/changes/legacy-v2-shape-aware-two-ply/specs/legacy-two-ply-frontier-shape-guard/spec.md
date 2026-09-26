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

#### Scenario: 拆面子候选进入比较
- **WHEN** 唯一最大直接进张候选需要拆掉一副已完成面子，而另一候选少 1 张直接进张但结构损失明显更小
- **THEN** 两个候选进入同一次加权前瞻比较，最终选择由比较结果给出，并记录护栏准入明细

#### Scenario: 进张差距超出护栏
- **WHEN** 次优候选的直接进张差距大于配置 slack
- **THEN** 该候选不进入护栏前沿，选择与护栏关闭时一致

#### Scenario: 护栏截断可复现
- **WHEN** 满足护栏条件的候选多于 `max_frontier_candidates`
- **THEN** 按声明的稳定排序截断，候选输入顺序变化不改变选择

### Requirement: 护栏审计字段

shape-aware profile SHALL 额外记录：

- standing shape signature/quality；
- shape quality version；
- guard 使用的是 legacy discard-cost 语义还是 standing-shape 语义；
- 因 standing-shape 被准入/淘汰的原因。

#### Scenario: 离线复核 shape guard

- **WHEN** shape-aware guard 改变了 frontier
- **THEN** evaluation SHALL 提供足以复算 admission 的 standing shape 字段

#### Scenario: 决策可离线复核
- **WHEN** 一次决策因护栏改变了比较集
- **THEN** 日志给出准入/截断明细与最终选择的加权指标，可离线复算
