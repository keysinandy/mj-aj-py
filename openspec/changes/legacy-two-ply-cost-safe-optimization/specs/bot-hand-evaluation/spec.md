# Spec Delta

## MODIFIED Requirements

### Requirement: 预算耗尽回退到全候选完成层级

评价器 SHALL 依次形成完整 legacy、完整 exact two-ply 或 weighted frontier 结果。在线 weighted frontier MAY 返回 partial result only when every retained root has a usable committed row and a decision-safe bound proves that the selected root cannot be overtaken by any unvisited draw weight; a coverage threshold alone MUST NOT authorize partial ranking. Otherwise节点和单调时钟预算 MUST 作用于整次决策，包括全部保留候选。未完成且不满足 partial 条件的前瞻 MUST 回退到最近完整层级，禁止将缺失特征当零与完整结果混合排名。内核耗时超额、实际 shanten work、节点量、覆盖率和回退原因 MUST 记录。

#### Scenario: Coverage-qualified but unsafe partial
- **WHEN** every retained root exceeds the configured coverage threshold but the improvement bounds still overlap
- **THEN** the evaluator rejects partial ranking and returns the complete legacy action with an explicit partial fallback reason

#### Scenario: Decision-safe partial
- **WHEN** every retained root has a committed row and one root's observed improvement lower bound is strictly greater than every other root's improvement upper bound
- **THEN** the evaluator may select that root without waiting for the configured coverage threshold, marks the result partial, and records both bounds

#### Scenario: Partial root cannot be silently ranked
- **WHEN** one retained root has no committed row or cannot produce a valid lower/upper bound
- **THEN** the entire future layer is ignored and the complete legacy action is returned with an explicit partial fallback reason

#### Scenario: Exact mode remains transactional
- **WHEN** exact two-ply is incomplete
- **THEN** all exact metrics are discarded and the complete legacy action is selected

#### Scenario: 最后一个候选前瞻被中断
- **WHEN** 部分候选完成 weighted frontier，另一个保留候选因预算未完成，而所有候选的完整当前 ukeire 已完成
- **THEN** 只有在 decision-safe bounds 证明胜者时使用已提交的 weighted 结果，否则使用完整 legacy 结果并记录回退

#### Scenario: 基础新评价也未完整完成
- **WHEN** weighted frontier 阶段预算耗尽且保留候选没有达到安全证明条件
- **THEN** 整次决策回退到完整 legacy 结果，不因为后遍历候选缺少分数而排除它

#### Scenario: Work budget covers inner DFS
- **WHEN** child evaluation invokes additional shanten calls while computing child ukeire
- **THEN** the weighted budget is charged against actual uncached shanten work and may stop before child-node count reaches the historical node limit

### Requirement: 直接进张与同向听改良分别计算

评价器 SHALL 分别输出当前 U1/p1 与 weighted future improvement。U1 是降低当前向听的有效未见数；weighted future improvement SHALL enumerate public draw kinds using `max(0,4-visible[tile])`, select the best legal child discard after each draw, and accumulate the draw's remaining weight when the child shanten improves. Future child ukeire and tile-kind diversity MUST use the same post-draw visible counts and must report weighted totals plus their denominator/coverage. For a partial branch, future ukeire means MUST be normalized by covered draw weight, while the total theoretical draw weight remains separately reported. A partial branch MUST NOT be treated as a complete improvement contribution outside the configured acceptance rule.

#### Scenario: Partial means do not treat unknown mass as zero
- **WHEN** a root has weighted child ukeire 40 over 20 covered draw weight and 10 additional draw weight remains unvisited
- **THEN** its reported partial future ukeire mean is 2.0, with covered weight 20 and total weight 30 shown separately

#### Scenario: Improvement bounds include only unknown mass
- **WHEN** a root has observed improvement weight `I` and unvisited draw weight `R`
- **THEN** its improvement lower bound is `I` and its upper bound is `I+R`

#### Scenario: 13 与 89 的改良存在条件差异
- **WHEN** 合成整手中其他条件对称，13 摸 4 可转 34 且增加有效进张，而对照边张无相同改良
- **THEN** 对应可行路径贡献正改良分，并显示牌 4 的未见权重和最佳弃牌

#### Scenario: 改良所需牌耗尽
- **WHEN** 其余输入相同但上述牌 4 的 visible 增加到四张
- **THEN** 通过摸 4 的改良贡献变为零；其他牌的贡献独立计算

#### Scenario: 仅未知池缩小不算改良
- **WHEN** 摸入无用牌并原样摸切，子站立牌面与原手一致，仅 visible 和未知池分母变化
- **THEN** 该路径的 I 贡献为零，不能因 p1 分母缩小误报结构改善

### Requirement: 缓存和解释支持并发隔离

缓存 SHALL 有容量上限，键 MUST 包括影响结果的手牌、locked、visible、规则/阶段/资格、profile 及层级。解释结果 SHALL 与单次调用关联，MUST NOT 通过可变全局“最近结果”在多个游戏线程间传递。预算相关缓存命中 MAY avoid work charging, but every uncached shanten evaluation MUST be counted in the weighted work metric.

#### Scenario: Cache hit does not consume DFS work
- **WHEN** a child shanten state is found in the decision-local cache
- **THEN** shanten work misses remain unchanged while the cache hit is reported in search metrics

#### Scenario: 相同手牌不同牌河
- **WHEN** 两个状态手牌相同但 visible 不同
- **THEN** 不能复用与可见牌有关的旧评价结果

#### Scenario: 多场同时决策
- **WHEN** 同一 BOT 进程十场线程交错产生候选解释
- **THEN** 每场记录的状态、profile、动作与解释来自自身那次决策
