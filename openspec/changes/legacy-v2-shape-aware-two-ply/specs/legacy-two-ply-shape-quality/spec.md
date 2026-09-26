## ADDED Requirements

### Requirement: 系统 SHALL 区分弃牌局部损失与弃牌后 standing hand 结构质量

legacyV2 SHALL 将“打掉某 tile 的局部结构损失”和“弃牌后 standing hand 的结构质量”建模为不同指标。

旧 discard shape cost MAY 保留用于兼容和诊断，但 MUST NOT 被继续当作 post-discard standing shape 的唯一真值。

#### Scenario: 124s 的两个弃牌候选

- **GIVEN** 当前局部结构包含 124s
- **WHEN** 比较弃 1s 留 24s 与弃 4s 留 12s
- **THEN** standing shape SHALL 独立评价 24s 与 12s
- **AND** SHALL NOT 仅因为弃 1s 的旧 discard shape cost 更高就认定 12s 更优

### Requirement: taatsu quality SHALL 至少满足稳定的四级顺序

对两张 suited incomplete sequence，在其他条件相同时，系统 SHALL 满足：

- RYANMEN > CENTRAL_KANCHAN > EDGE_KANCHAN > PENCHAN。

至少 MUST 满足：

- 23 > 24 > 13 > 12；
- 78 > 68 > 79 > 89。

#### Scenario: 中张坎张优于边张

- **GIVEN** 两个 standing hands 仅在 24s 与 12s 上不同
- **AND** shanten、ukeire 与其他结构相同
- **WHEN** 计算 standing shape quality
- **THEN** 24s SHALL 严格优于 12s

#### Scenario: 13s 优于 12s

- **GIVEN** 两个 standing hands 仅在 13s 与 12s 上不同
- **AND** shanten、ukeire 与其他结构相同
- **WHEN** 计算 standing shape quality
- **THEN** 13s SHALL 严格优于 12s

### Requirement: 完整手牌 shape SHALL 使用非重叠 decomposition

shape evaluator SHALL 避免对同一 tile copy 重复计算多个搭子关系。

完整 sequence、triplet、pair、taatsu 与 isolated unit 的 decomposition SHALL 确定且版本化。

#### Scenario: 234 不被重复计算

- **GIVEN** standing suit 含有 234
- **WHEN** 计算 shape quality
- **THEN** 同一组 tile copies MUST NOT 同时作为完整 234 与独立 23/34 两个搭子重复获得奖励

#### Scenario: 输入顺序不影响结果

- **GIVEN** 相同 tile counts
- **WHEN** 以任意枚举顺序执行 decomposition
- **THEN** 最终 shape signature 与 encoded quality MUST 相同

### Requirement: shape quality SHALL 不改变 shanten 与 ukeire 的主导地位

系统 MUST 保持 shanten、ukeire 与 ukeire types 对 shape quality 的优先级。

shape quality 只可在更高优先级的 shanten、ukeire 和 ukeire types 无法区分候选时参与排序。

#### Scenario: 更好形状不能覆盖更低向听

- **GIVEN** 候选 A 的 child shanten=0
- **AND** 候选 B 的 child shanten=1 且 shape 更优
- **WHEN** two-ply 选择 child
- **THEN** MUST 选择 A

#### Scenario: 更好形状不能覆盖更大直接进张

- **GIVEN** 两个候选 child shanten 相同
- **AND** A 的 ukeire 明显大于 B
- **AND** B 的 shape 更优
- **WHEN** two-ply 选择 child
- **THEN** MUST 选择 A

### Requirement: shape quality SHALL 是 public-state deterministic feature

shape evaluator MUST 只依赖 standing tile counts 与显式规则输入，不得读取 opponent concealed hands 或真实 wall order。

#### Scenario: 隐藏信息改变不影响 shape

- **GIVEN** hero standing hand 完全相同
- **AND** 仅对手暗牌或真实墙序不同
- **WHEN** 计算 standing shape
- **THEN** shape signature MUST 完全一致

### Requirement: 用户专项牌例 SHALL 固化为 golden contract

系统 MUST 固化以下专项：

- 暗手 23455m 124s EE w；
- 副露 789p；
- 弃 1s 与弃 4s 后均为 0 向听；
- 两者普通进张均为 11，等待为 5m×2、3s×4、E×2、w×3。

在这些更高优先级指标相同的条件下：

- 弃 1s 后的 24s standing shape MUST 优于弃 4s 后的 12s；
- 该完整牌例 MUST 实际进入 `baotou_scope`，shape-aware legacyV2 MUST 在 baotou tier / 财神保护 / baotou_ukeire 打平后用 standing shape 选择弃 1s；
- 普通 weighted two-ply 的 draw=5s future branch SHALL 使用独立 fixture 验证打 2s 留 45s 的结构升级，不得把两条路径混为一个验收。

#### Scenario: 用户牌例修复

- **WHEN** shape-aware legacyV2 通过真实 `choose_discard()` 评价该 golden fixture
- **THEN** `decision_scope` SHALL 为 `baotou_scope`
- **AND** selected discard SHALL 为 1s
- **AND** diagnostics SHALL 显示 1s/4s 的 baotou tier、baotou_ukeire、standing shape 与旧 discard shape cost
- **AND** `stage_b_entered` SHALL 为 false
- **AND** MUST NOT 使用硬编码牌号特判

### Requirement: baotou_scope SHALL 在爆头进度打平后使用 standing shape

当 hero 持白板财神且最小向听为 0、并且未触发推进收手或 baotou 预算回退时，系统 SHALL 保留既有 baotou 优先级：

1. baotou tier；
2. 财神保护（不主动弃白板财神）；
3. baotou_ukeire；
4. standing shape quality；
5. legacy discard shape cost；
6. feed risk；
7. stable tile。

standing shape MUST NOT 覆盖前三项。shape-aware 开关关闭时 MUST 恢复旧 key，不得产生行为漂移。

#### Scenario: baotou 进度相同由 standing shape 决胜

- **GIVEN** 两个候选的 baotou tier、财神保护和 baotou_ukeire 完全相同
- **AND** 候选 A 留下 24s，候选 B 留下 12s
- **WHEN** shape-aware baotou_scope 排序
- **THEN** A SHALL 在旧 discard shape cost 之前胜出

#### Scenario: 更高 baotou_ukeire 不被 shape 覆盖

- **GIVEN** 两个候选处于同一 baotou tier
- **AND** 候选 A 的 baotou_ukeire 严格高于候选 B
- **AND** 候选 B 的 standing shape 更优
- **WHEN** baotou_scope 排序
- **THEN** MUST 选择 A

#### Scenario: baotou fallback 不混入 shape 部分结果

- **WHEN** Rust baotou kernel 不可用、节点预算超限或 X/Y/Z 收手触发
- **THEN** SHALL 沿用既有整档 fallback
- **AND** MUST NOT 使用未完整比较的 standing shape 改变 fallback winner
