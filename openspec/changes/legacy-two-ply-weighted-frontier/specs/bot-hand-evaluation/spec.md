# Spec Delta

## MODIFIED Requirements

### Requirement: 预算耗尽回退到全候选完成层级

评价器 SHALL 依次形成完整 legacy、完整 exact two-ply 或 weighted frontier 结果。在线 weighted frontier MAY 返回 partial result only under its explicit coverage and retained-root acceptance conditions; otherwise节点和单调时钟预算 MUST 作用于整次决策，包括全部保留候选。未完成且不满足 partial 条件的前瞻 MUST 回退到最近完整层级，禁止将缺失特征当零与完整结果混合排名。内核耗时超额、节点量、覆盖率和回退原因 MUST 记录。

#### Scenario: Coverage-qualified online partial
- **WHEN** weighted frontier reaches the hard deadline after every retained root has the configured minimum coverage
- **THEN** the evaluator may rank the retained roots using only their committed weighted metrics, marks the layer partial, and records coverage and the hard-deadline reason

#### Scenario: Partial root cannot be silently ranked
- **WHEN** one retained root has insufficient coverage or no committed result
- **THEN** the entire future layer is ignored and the complete legacy action is returned with an explicit partial fallback reason

#### Scenario: Exact mode remains transactional
- **WHEN** exact two-ply is incomplete
- **THEN** all exact metrics are discarded and the complete legacy action is selected

#### Scenario: 最后一个候选前瞻被中断
- **WHEN** 部分候选完成 weighted frontier，另一个保留候选因预算未完成，而所有候选的完整当前 ukeire 已完成
- **THEN** 满足 partial 覆盖门槛时使用已提交的 weighted 结果，否则使用完整 legacy 结果并记录回退

#### Scenario: 基础新评价也未完整完成
- **WHEN** weighted frontier 阶段预算耗尽且保留候选没有达到 partial 覆盖门槛
- **THEN** 整次决策回退到完整 legacy 结果，不因为后遍历候选缺少分数而排除它

### Requirement: 直接进张与同向听改良分别计算

评价器 SHALL 分别输出当前 U1/p1 与 weighted future improvement。U1 是降低当前向听的有效未见数；weighted future improvement SHALL enumerate public draw kinds using `max(0,4-visible[tile])`, select the best legal child discard after each draw, and accumulate the draw's remaining weight when the child shanten improves. Future child ukeire and tile-kind diversity MUST use the same post-draw visible counts and must report weighted totals plus their denominator/coverage. A partial branch MUST NOT be treated as a complete improvement contribution outside the configured acceptance rule.

#### Scenario: Improvement is weighted by remaining copies
- **WHEN** two draw kinds both improve shanten but have remaining weights 4 and 1
- **THEN** the aggregate improvement weight increases by 5, not by two equal units

#### Scenario: Exhausted improvement tile contributes zero
- **WHEN** an improving tile has visible count four
- **THEN** it is omitted from the draw frontier and contributes zero improvement weight

#### Scenario: 13 与 89 的改良存在条件差异
- **WHEN** 合成整手中其他条件对称，13 摸 4 可转 34 且增加有效进张，而对照边张无相同改良
- **THEN** 对应可行路径贡献正改良分，并显示牌 4 的未见权重和最佳弃牌

#### Scenario: 改良所需牌耗尽
- **WHEN** 其余输入相同但上述牌 4 的 visible 增加到四张
- **THEN** 通过摸 4 的改良贡献变为零；其他牌的贡献独立计算

#### Scenario: 仅未知池缩小不算改良
- **WHEN** 摸入无用牌并原样摸切，子站立牌面与原手一致，仅 visible 和未知池分母变化
- **THEN** 该路径的 I 贡献为零，不能因 p1 分母缩小误报结构改善
