## ADDED Requirements

### Requirement: 评价版本明确声明动作范围

shape-v2 SHALL 显式选择并携带 `discard`、`hu-piao` 或 `all-root` scope；版本和 scope MUST 进入配置指纹。discard scope 仅优化无 HU/KONG 分支的合法舍牌；hu-piao 再覆盖无 KONG 分支的 HU 与舍牌；all-root 覆盖当前阶段全部合法动作。范围外行为 MUST 委托冻结策略并记录 delegated_reason，不能伪装成 v2 已完成评价。legacy 与 shape-v1 的显式调用 MUST 保持原语义。

#### Scenario: 首版遇到可 HU 状态
- **WHEN** profile 为 discard scope 且 HU 合法
- **THEN** 委托冻结 HU/财飘分支，记录范围外原因，不生成虚构的 HU EV

#### Scenario: 只有一个合法动作
- **WHEN** 抓打圈等规则使合法集只有一个动作
- **THEN** 直接返回该合法动作并记录 only_legal_action

### Requirement: 全合法舍牌进入批量 frontier

v2 的普通舍牌候选 MUST 等于规则合法集中的所有不同舍牌，包含财神和高于最低向听的候选。Python/Rust frontier SHALL 返回 tile、shanten、U1 和 ukeire_bitset，并区分结构进张与受 HU 门禁约束的 waits。未来 EV 节点同样 MUST 枚举全部合法舍牌，不得沿用旧内核的最低向听选择语义。

#### Scenario: 更高向听候选具有更高积分价值
- **WHEN** 完整同层积分模型给某个高于最低向听的合法候选最高值
- **THEN** 选择该候选，不因向听或非财神候选存在而将其排除

#### Scenario: Rust 不可用
- **WHEN** 缺少支持 v2 frontier 的内核
- **THEN** 使用语义一致的 Python 实现或整次回退冻结策略，报告内核/回退原因，不改用旧 best_future_discard 冒充全候选

### Requirement: 胡牌收益复用规则门禁和本局结算

ScoreValue SHALL 先验证刚摸牌、有财必拷响和杠补摸牌门禁，再用现有 hand_multiplier 与 settle 计算 hero 积分。standing13 MUST 为真实模型摸牌前的本家站立暗牌；普通舍牌、财飘、杠及抓打圈的状态变化 MUST 与 Game 一致。当前 Game.scores 为本局结算向量，MUST NOT 错减房间累计分。

#### Scenario: 庄闲收益不同
- **WHEN** 相同 base 与 mult 分别由庄家和闲家自摸
- **THEN** 本家收入分别为 24×base×mult 与 10×base×mult，四家增量之和为零

#### Scenario: 有牌型但门禁不允许胡
- **WHEN** 本家牌型成胡但不是刚摸牌，或不满足有财必拷响门禁
- **THEN** 不计入 HU 收益、不产生非法 HU 候选

#### Scenario: 普通舍牌断链
- **WHEN** 模型打出非财飘牌，或打白后不满足财飘条件
- **THEN** 清零对应 chain/chain_piao；之后的倍率计算不保留已断链收益

### Requirement: EV2 是明确假设下的两摸积分特征

EV2 SHALL 按 design 的无放回均匀未见牌、无对手动作模型计算最多两次未来本家自摸收益，第一摸合法 HU 只结算一次；未胡叶子的首版 tail 为零。EV1 已包含在 EV2 中，MUST NOT 重复累加。每次摸牌更新 remaining/visible，每次弃牌保持 visible；墙长和行动顺序 MUST 限制实际可达摸牌次数。模型假设、horizon 和 tail 版本 MUST 出现在解释中。

#### Scenario: 第一摸已胡
- **WHEN** P2 模型第一摸合法 HU
- **THEN** 该分支计入当次实际积分后终止，不再添加第二摸收益

#### Scenario: 活墙不足轮到本家
- **WHEN** 普通根弃牌后活墙少于四张且模型不包含抢副露或补牌
- **THEN** 未来本家自摸收益为零；活墙少于八张时不计算第二次本家自摸

#### Scenario: 摸后打牌仍为已见牌
- **WHEN** 模型从未知池摸入 t 后又将 t 打出
- **THEN** t 仍计入 visible，不回流到未知池

### Requirement: 在线积分模型与特征层级必须版本化

Fast EV SHALL 将已定义的公开特征映射到声明的本局积分单位，每个完整层使用单独校准的参数。未校准阶段 MUST 标记 uncalibrated，不宣称其分数是整局净 EV。EV2、结构潜力、风险项的输入/单位/缺失处理 MUST 可复算，模型 MUST NOT 使用隐藏状态或把未计算特征当零值完整输入。

#### Scenario: Q0 没有 EV2 特征
- **WHEN** 当前仅完成 Q0 层
- **THEN** 使用冻结的 Q0 模型，EV2 标未计算，不代入完整层模型并伪造零值

### Requirement: 决策仅提交完整全候选层级

一次决定 SHALL 共享节点和时钟预算，覆盖上下文、frontier、回退策略、评价与解释开销。高层未完成时 MUST 退回最近完整低层；Q0 未完成时 MUST 整次回退冻结策略。上界剪枝只有经当前模型证明严格不能胜出时有效；被剪枝项 MUST 保留证明信息。已完成值与不同层级分数 MUST NOT 混排。

#### Scenario: EV2 在中间候选超时
- **WHEN** Q0 全部完成，EV2 仅计算了一部分候选
- **THEN** 全部候选使用 Q0 排名，并报告高层中断原因与实际耗时

#### Scenario: 上界等于当前最优
- **WHEN** 候选的有效积分上界等于当前最优值
- **THEN** 不淘汰该候选，继续保持声明的稳定 tie-break

#### Scenario: 旧 Q 上界不适用于新模型
- **WHEN** 新模型加入积分倍率、负系数或新的 tail，尚无有效上界证明
- **THEN** 禁止使用 shape-v1 的 Q 上界剪枝；预算不足按完整层级回退

### Requirement: HU 财飘和 KONG 按同单位根动作价值分期接入

hu-piao scope SHALL 比较 HU 的即时积分和每种合法继续舍牌的同单位模型值；W MUST 通过规则判定是否财飘并完整更新链与抓打圈。all-root scope SHALL 再比较暗杠/加杠及反应窗 PASS/PONG/CHOW/明杠，正确执行补牌、locked、七对资格和墙尾限制。scope 扩展 MUST 使用独立校准与验收，不能借用普通舍牌报告。

#### Scenario: 弃胡财飘价值较高
- **WHEN** HU 与 W 合法且同一完整积分层认定财飘继续价值更高
- **THEN** hu-piao/all-root 可以选择 W，并给出即时 HU 与继续价值，不再用固定活墙阈值替代 EV 主比较

#### Scenario: 明杠与碰同窗
- **WHEN** all-root 中 KONG_OPEN、PONG 和 PASS 同时合法
- **THEN** 三者按同一 reward 定义与完整层比较；明杠包含补牌与链变化，碰包含后续合法舍牌

#### Scenario: 墙尾禁止杠
- **WHEN** 规则合法集不包含某个杠动作
- **THEN** 即使模型预测高收益也不加入或选择该动作
