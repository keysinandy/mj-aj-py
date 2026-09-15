# bot-react-decision Specification

## Purpose
TBD - created by archiving change bot-react-full-eval. Update Purpose after archive.
## Requirements
### Requirement: 吃/碰按"副露 + 最佳弃牌后站立牌面"完整评价

启发式 BOT 对合法 CHOW_LOW/MID/HIGH 和纯 PONG 窗口 SHALL 评价“副露完成 + 每种合法立即弃牌后的站立牌面”。legacy 模式保留原 `(shanten, ukeire)` 门槛及结构损失次序；shape-v1 MUST 使用 `bot-hand-evaluation` 的共享站立牌面评价，与 PASS 采用相同 profile、同向听/活墙档系数和完整计算层级。

- 吃碰动作移除本家消耗的两张牌，增加 locked 并更新吃牌额度/七对资格；对 need+1 态枚举合法弃牌得到 need 态，MUST 满足暗牌张数断言，覆盖所有 locked=0..4 的有效状态。
- 最低向听和同向听财神保护 MUST 与弃牌侧一致；shape-v1 不再以 `_discard_shape_cost` 作为唯一结构信号。
- 多个通过门槛的候选 MUST 限于当前 react mode 的合法集，按较低向听、较高完整共享评分及声明的稳定次序选择。
- `KONG_OPEN ∈ acts` 时整个 claim 窗口（含 PONG）MUST 沿用既有 legacy 决策；仅无 KONG_OPEN 的纯 PONG 窗口进入新框架。

#### Scenario: 吃后向听降低则执行吃
- **WHEN** 某吃选项在最佳合法弃牌后的向听低于 PASS 基准
- **THEN** BOT 在当前窗口的最低向听合格动作中按完整共享评价择优执行

#### Scenario: 两种吃法选最终牌面更优者
- **WHEN** 多种吃法合法且同为最低向听，其最佳弃牌后的完整共享评分不同
- **THEN** shape-v1 选择共享评分较高者，并记录相应立即弃牌及评价层级

#### Scenario: KONG 与 PONG 同窗时行为不变
- **WHEN** PONG 与 KONG_OPEN 同时合法
- **THEN** 整个 claim 窗口与既有 KONG 决策一致，不混入新评价

#### Scenario: 预测弃牌与真实弃牌语义一致
- **WHEN** 吃碰成功后的手牌、visible、规则、profile 和计算层级与预选状态一致
- **THEN** 实际弃牌与吃碰预选的最佳弃牌一致

### Requirement: PASS 基准为反应时点站立暗牌原样评价

反应侧 SHALL 保留 PASS 基准，对反应时点的 `13-3*locked` 张站立暗牌原样评价。MUST NOT 将他家刚打出的 pending 牌加进 PASS 手牌。legacy 使用原 `(shanten,ukeire)`；shape-v1 使用与吃碰后状态同口径的共享评分、可见牌、未知池和同向听/活墙档系数。预算不足时 PASS 与全部动作 MUST 一起回退到共同完整层级。

#### Scenario: 可碰但完整收益不足
- **WHEN** 碰后最佳弃牌与 PASS 同向听，而该模式的完整收益增量不足对应阈值
- **THEN** BOT 选择 PASS 并记录同口径基准、候选及阈值

#### Scenario: PASS 不能偷用他家弃牌
- **WHEN** 反应时点 pending 牌不在本家手牌
- **THEN** PASS 仍以原暗牌张数评价，该牌只在 visible 中体现

### Requirement: 可见牌快照在反应评价内复用

反应侧评价 SHALL 使用反应时点取得的单份 `visible_counts()` 快照贯穿 PASS 基准与全部吃/碰选项评价。该快照 MUST 满足不变量：claim 前 vis == claim 后、弃牌前 vis（他家 pending 牌此时已在牌河、本家手牌移入自家副露均不改变可见总数）。

#### Scenario: 快照不变量
- **WHEN** 在 react 阶段对同一局面取 `visible_counts(seat)`，并分别模拟"某吃选项成立且舍牌前"的可见牌计数
- **THEN** 两者逐牌相等

### Requirement: 七对分支获得自然偏向保护

反应侧 SHALL 依赖既有 shanten 的标准形/七对双分支保持最低向听约束；副露后必须退出七对分支。shape-v1 的整手评价 SHALL 在未副露且同最低向听时解释对子在雀头、面子及七对中的用途，不能把七对与副露收益同时累加，不能新增无条件“对子达到某数就禁止吃碰”的规则。

#### Scenario: 七对为更优分支时拒绝副露
- **WHEN** 七对向听严格优于任意吃碰并最佳弃牌后的标准形向听
- **THEN** 选择 PASS，不因结构奖励放弃最低向听分支

#### Scenario: 七对不领先时允许比较副露收益
- **WHEN** 标准形与七对同向听且某合法副露通过共享评价门槛
- **THEN** 可以执行该副露，但其评价已移除七对资格并解释对子用途变化

### Requirement: 反应侧决策性能受闸门约束

legacy SHALL 保持原始 ukeire 调用上限：吃窗 ≤34、纯碰窗 ≤12（legacy KONG 路径不计）。shape-v1 的新增计算 MUST 受全决策节点/时钟预算及完整层级回退约束，不再以原始 34/12 次限制冒充前瞻的完整工作量；节点与原始内核调用分别计数。

同机同内核冻结当前 legacy 基线与候选版本，交错各运行三次 200 局压测，包含解释开销的 elapsed/games 中位数增加 MUST ≤15%。十场并发时 SHALL 分阶段报告全决策延迟 p50/p95/p99/max、节点、额外评价时间及回退率；额外评价 p95 MUST 满足弃牌 ≤20ms、反应 ≤10ms。原始 `9f4f8de` 历史压测结果不得替代本次当前基线。

#### Scenario: 吞吐压测
- **WHEN** 在相同设备和 Python/Rust 环境下完成冻结版本的交错压测
- **THEN** elapsed/games 中位数增加不超过 15%，报告含样本数、版本、profile 和原始各次结果

#### Scenario: 前瞻中断不占用额外授权时间
- **WHEN** 节点预算、时钟预算或调用方已有计算截止触发
- **THEN** 返回共同完整层级结果，不延长动作截止、不重新发起状态请求或改变授权规则

### Requirement: 等向听时按版本化评价收益门槛执行

吃碰后向听更高时 MUST 拒绝；相同时 SHALL 使用 evaluator 对应门槛。legacy MUST 保持 PONG 进张增量 ≥2、CHOW ≥4。shape-v1 MUST 要求 `Q_claim-Q_pass >= tau[action]`，使用同一完整层级，阈值与 Q 同尺度并进入 profile 指纹；初始 tau 为 PONG=2/N、CHOW=4/N，后续校准范围按设计预声明。没有未知牌且向听相同时 MUST 选择 PASS，不以零阈值误接受动作。

#### Scenario: 碰的 legacy 边界增益
- **WHEN** legacy 中碰后与 PASS 同向听且进张增量恰为 2
- **THEN** 碰可被接受

#### Scenario: 吃的 legacy 边界增益
- **WHEN** legacy 中吃后与 PASS 同向听且进张增量为 3
- **THEN** 选择 PASS

#### Scenario: shape-v1 在完整分数边界接受动作
- **WHEN** shape-v1 中 N>0、同向听且完整 Q 增量恰好达到 profile 中的动作阈值
- **THEN** 该动作可被接受，诊断可复算比较式

#### Scenario: 不混合原始张数与归一化分数
- **WHEN** shape-v1 比较 PASS 与吃碰收益
- **THEN** 不将原始 2/4 张门槛直接与 Q 相减，使用 profile 中同尺度阈值

### Requirement: 弃牌排序使用共享评价且胡/飘路径保持不变

`choose_action()` 的 HU 优先与 `_should_piao` 财飘行为 MUST 保持既有语义。legacy 的 `choose_discard()` 排序 MUST 保持；shape-v1 SHALL 在合法性、最低向听及财神保护后使用共享评价，允许同向听中直接进张较少但完整收益更高者胜出。不得为了保留旧“进张永远压过结构”的测试而暗中禁用新排序；该旧断言应明确限定到 legacy，新的取舍按本变更规范验证。

#### Scenario: 现有 HU 与财飘回归
- **WHEN** 任一 evaluator 进入可 HU 或财飘分支
- **THEN** 结果与既有 HU/财飘规则一致

#### Scenario: legacy 弃牌不漂移
- **WHEN** 同一状态显式选择 legacy
- **THEN** 弃牌排序和旧冻结基线一致

#### Scenario: shape-v1 使用共同站立牌面评价
- **WHEN** 同一站立牌面分别由普通弃牌候选与吃碰后候选产生，评价输入与层级相同
- **THEN** 原始特征、共享 Q 和稳定次序语义一致

