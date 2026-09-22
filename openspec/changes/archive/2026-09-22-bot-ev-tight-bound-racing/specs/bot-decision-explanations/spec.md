## ADDED Requirements

### Requirement: Bound 与 paired racing 证据必须进入决策解释

Fast EV 和离线 teacher 的解释 SHALL 区分 derived/override/fallback bound，记录 bound version、收益支持范围或上界、组件证书和 profile/config fingerprint。teacher 还 MUST 记录 active candidates、paired delta interval、淘汰/停止原因及稀疏样本语义；未计算或不可证明内容必须保持 missing/unknown。

#### Scenario: Fast EV 使用局面级上界

- **WHEN** 某候选通过规则资源推导得到 candidate-specific fast upper bound
- **THEN** 解释显示其 bound 版本、组件、上界和是否实际用于严格剪枝，不把它写成观测最大值

#### Scenario: Teacher 淘汰候选

- **WHEN** 某候选因 paired delta 的上置信界严格小于零而退出 active 集
- **THEN** 离线解释保留对手 leader、pair bound、有效 paired n、区间、alpha/look 和 elimination certificate

#### Scenario: 后续 batch 缺少已淘汰候选

- **WHEN** 读取淘汰后的 teacher sparse row
- **THEN** 将该 action 标为未在该 batch 评估，而不是补写零 reward、失败 reward 或完整候选结果

### Requirement: Bound 与 racing 解释不得突破现有证据边界

新增解释和 teacher artifact MUST NOT 写入对手暗手、真实墙序、token 或完整 sampled world；线上实际 decision/action、transport、server auto discard 与离线 counterfactual/racing 结果仍须分开关联。评价证据不得改变窗口授权、动作重试或平台状态。

#### Scenario: 离线世界包含隐藏牌

- **WHEN** teacher 内部使用 sampled world 完成 paired rollout
- **THEN** 对外解释只保留 world fingerprint 和公共 envelope/统计字段，不导出隐藏牌身份或墙顺序

#### Scenario: 原线上没有实际策略决策

- **WHEN** 某窗口只有 timeout 或 server auto discard 而没有本家 decision
- **THEN** racing/teacher 结果只能标记为 counterfactual/offline evidence，不生成原线上策略动作
