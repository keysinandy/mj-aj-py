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

- 暗手 23455m 124s EE W；
- 副露 789p；
- 弃 1s 与弃 4s 后均为 0 向听；
- 两者普通进张均为 11，等待为 5m×2、3s×4、E×2、W×3。

在这些更高优先级指标相同的条件下：

- 弃 1s 后的 24s standing shape MUST 优于弃 4s 后的 12s；
- shape-aware legacyV2 MUST 选择弃 1s；
- draw=5s 的 future branch MUST 能识别打 2s 留 45s 的结构升级。

#### Scenario: 用户牌例修复

- **WHEN** shape-aware legacyV2 评价该 golden fixture
- **THEN** selected discard SHALL 为 1s
- **AND** diagnostics SHALL 显示 24s 的 standing/future shape 优势
- **AND** MUST NOT 使用硬编码牌号特判
