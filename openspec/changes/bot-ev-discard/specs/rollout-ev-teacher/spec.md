## ADDED Requirements

### Requirement: Teacher 明确估计对象与离线执行边界

rollout-v1 SHALL 估计指定公开信息、belief 和冻结 continuation 下的 `Q^pi(s,a)`，只在离线运行。teacher 配置 MUST 包含根 scope、规则、belief、后续策略、内核、节点预算、采样计划和统计方法指纹；MUST NOT 宣称有限样本结果为真实墙最优或无模型误差的真实 EV。

#### Scenario: continuation 版本变化
- **WHEN** 本家后续策略从冻结 shape-v1 改为 shape-v2-fast
- **THEN** 生成新 teacher 指纹和数据版本，不与旧标签无标记混合

### Requirement: 从公开约束生成可能世界

BeliefSampler SHALL 从 PublicDecisionContext 的 unseen pool 无放回分配他家暗牌和完整墙，满足每种牌四张、阶段暗牌数、公开副露、墙长和可表达硬约束。它 MUST NOT 使用原 Game 的真实隐藏牌。初版均匀分配的条件和未建模的行为后验 MUST 显式记录。

#### Scenario: 世界物料守恒
- **WHEN** 为任何支持的上下文生成 sample
- **THEN** 三家暗牌数和墙长匹配上下文，每种牌 visible+hidden+wall 恰为四张，死墙仍留在完整墙中

#### Scenario: 隐藏信息与采样解耦
- **WHEN** 原始完整日志包含真实墙但上下文只暴露本家可见数据
- **THEN** 同 context/seed 生成相同可能世界，不因真实墙变化而变化

### Requirement: 恢复可推进的完整规则状态

世界构建器 SHALL 从上下文和采样材料完整设置 Game 状态，含四家链、吃额度、冻结、摸牌门禁、反应队列和进度。恢复后 MUST 验证根合法集与物料。MUST NOT 将 Mirror 决策投影直接深拷贝成模拟世界，也不得凭空补 PASS。无可靠反应适配时该状态 MUST 标 unsupported。

#### Scenario: 反应 root 的前序响应未知
- **WHEN** 不能确定当前 claim/chow 前有哪些响应已经完成
- **THEN** 拒绝该 teacher 状态，不用仅包含本家的队列模拟后续对局

#### Scenario: 候选之间互不污染
- **WHEN** 在 sample 中应用候选 A 并推进到终局，再评估候选 B
- **THEN** B 从该 sample 的原始克隆开始，原上下文和 A 之前的世界指纹未变化

### Requirement: 每个根候选共享随机世界与配对样本

所有活跃根候选 SHALL 对同一 sample_id 使用相同起始隐藏世界，并记录 world fingerprint。样本 id 与随机流 MUST 独立于候选遍历顺序和 worker 调度。候选差值 MUST 仅从共享 sample 的逐对收益计算，保留配对数和方差；根动作后的状态分歧是规则结果，不要求后续轨迹相同。

#### Scenario: 候选顺序重排
- **WHEN** 同配置下反转候选顺序或改变 worker 数
- **THEN** 每个 sample/候选的终局收益相同，配对差值和停止结论可复现

### Requirement: 后续策略只看当前行动者视角且不递归搜索

强制执行根动作后，所有玩家 SHALL 使用冻结的非 rollout continuation；每次调用只提供该玩家本家信息。continuation MUST 使用确定性节点预算和稳定 tie-break；wall-clock 安全超限使样本失败，不触发不确定的策略回退。MUST NOT 在未来决策递归调用 teacher 或读取完整 sample 的他家暗牌/未来墙。

#### Scenario: 后续玩家获得自己的暗牌
- **WHEN** rollout 推进到对手的摸牌决策
- **THEN** 该对手可使用其采样暗牌及当时公共信息，不能看到 hero 或其他对手暗牌与未来墙序

### Requirement: 收益来自完整终局且失败不能伪装成样本

每个有效 rollout SHALL 执行合法动作直到 Game.done，收益为本局 hero 结算增量，流局为零、他家自摸含负支付。非法动作、异常和未到终局 MUST 报告失败；MUST NOT 静默替换非法动作为 legacy、用截断估值或零分计入均值。一个候选失败时整组 sample MUST 保留失败状态，避免破坏配对。

#### Scenario: 他家先胡
- **WHEN** 某世界中对手先合法自摸
- **THEN** hero reward 为 settle 对应的负分，不因为 hero 未胡而记为零

#### Scenario: 最大步数超限
- **WHEN** 某模拟未终局即触发安全限制
- **THEN** 标记 incomplete_rollout 并保留失败分母，不产出有效终局标签

### Requirement: 序贯采样保留有效不确定性和歧义

teacher SHALL 支持预先冻结的 N0/batch/Nmax（初始 32/32/512），仅在一轮共享世界补齐后进行比较。候选淘汰/置信胜出 MUST 使用控制重复查看和多候选比较错误率的同时区间或有效序贯方法，alpha 和收益界证明进入指纹。普通重复 t/正态区间 MUST NOT 用来宣称 95% 置信胜出。到上限仍不能区分时 MUST 标 ambiguous，不造确定标签。

#### Scenario: 512 次仍分不清
- **WHEN** 达到 Nmax 后最佳与竞争动作的有效差值区间仍跨零
- **THEN** 保留均值、区间和 ambiguous=true，不作为无歧义 BC 硬标签

#### Scenario: 高番超出观测范围
- **WHEN** 稀有大番未在先前批次出现
- **THEN** 不能用此前观测最大收益设理论界或裁剪后续大番；区间方法必须覆盖规则允许范围

### Requirement: Teacher 输出足够的复算证据

输出 SHALL 包含上下文/配置指纹、候选 EV/样本数/有效区间、best/runner-up、paired delta/标准误、停止原因、歧义、失败、win_rate/平均自摸倍率/draw_rate。未自摸时平均倍率 MUST 为 null。批处理 SHALL 支持按 context/sample 标识确定性续跑，不能重复累计已完成样本。

#### Scenario: 中断后续跑
- **WHEN** 批处理保存部分完成的 sample 后重启
- **THEN** 已完成样本不重复计数，补齐剩余样本后与相同计划一次性执行结果一致
