## ADDED Requirements

### Requirement: PolicyValue 训练输入不得包含 sampled oracle hidden identity

Policy/value 模型输入 SHALL 只来自 hero private state、公开 context/history、合法动作和可公开推导的 belief summary。训练数据 MUST 标 `oracle=False`；sampled opponent hands、真实/sampled future wall order 和 oracle planes MUST NOT 进入 network feature，即使这些数据在 search simulator 内存在。

#### Scenario: search teacher 使用完整 sampled world
- **WHEN** 某 search sample 在内部知道三家暗手和 wall
- **THEN** 导出的 model feature 只包含该 decision 的 information state/belief summary，不序列化 sampled hidden identities

### Requirement: Policy head 学习 search soft visit distribution

Policy target SHALL 使用合法动作上的 normalized search visit counts 或等价 soft search policy。near-tie、ambiguous 或低预算样本 MUST NOT 被无条件转换为单一 best-action one-hot；系统 SHALL 保存原 visit/Q/confidence 并通过 sample weight 表达可信度。

#### Scenario: 两个动作访问量接近
- **WHEN** search root 的 A/B visit 分别为 510/490
- **THEN** policy target 保留近似 0.51/0.49 的分布或等价软标签，不强制 A=1/B=0

### Requirement: Value head 预测声明模型下的本局积分价值

Value target SHALL 使用版本化 high-budget search root value、完整 terminal outcome 或明确定义的混合目标，单位为 hero round settlement points。teacher estimated value 与真实终局 score MUST 保存在不同字段，并记录 target source/version。

#### Scenario: 同一样本既有 search value 又有实际终局
- **WHEN** dataset 同时保存 `search_root_value` 和 `actual_round_score`
- **THEN** 两者保持独立列，训练配置明确选择/混合目标，不覆盖原始实际 score

### Requirement: 训练样本带完整 provenance 和 source-group split

每条 search distillation 样本 SHALL 至少记录 context/history hash、belief/search/opponent/leaf/policy versions、simulation count、visit counts、Q_by_action、root value、ambiguity/confidence、legal mask 和 source_group。train/validation/final-test SHALL 按 source game/room/seed group 隔离，禁止同源 decision 的 counterfactual 候选跨 split 泄漏。

#### Scenario: 一个 source game 产生多个 search contexts
- **WHEN** 同一局生成多个决策样本和候选 counterfactual
- **THEN** 它们全部归属同一个 split，不因 context_hash 不同被拆到 train/final 两边

### Requirement: Policy Iteration 每代 continuation 和 teacher 都版本化

系统 SHALL 支持 `pi_k -> search_k -> dataset_k -> pi_(k+1)` 的迭代。任何 ActorPolicy、belief、leaf、rules 或 search profile 变化 MUST 生成新 teacher/model fingerprint；新策略作为 continuation 后不得沿用旧 `Q^pi` 标签身份。

#### Scenario: pi1 替换 shape-v1 continuation
- **WHEN** 第二代 search 使用 pi1 作为 self-play continuation
- **THEN** continuation version 与 search artifact 变化，dataset1 不能标记为 dataset0/shape-v1 teacher

### Requirement: 策略升级以 regret 与独立积分双重验收

候选 `pi_(k+1)` 升级 SHALL 同时满足：冻结 high-budget search reference 上平均 root regret 不高于上一代，且独立 paired games 的 hero round score 置信区间满足预声明门槛。仅训练 loss、BC accuracy 或 self-play win rate 改善 MUST NOT 单独授权升级。

#### Scenario: self-play 提升但 population 对局退化
- **WHEN** pi_(k+1) 在 self-play 胜过 pi_k，但冻结 population opponent split 上平均积分明显回退
- **THEN** 该模型不得作为真实线上默认版本，可保留为 self-play experiment

### Requirement: 迭代必须有停止与回滚条件

Policy iteration SHALL 预声明最大代数/算力和停止条件。连续多代 final-split regret/score 无稳定改善、belief/model drift、训练不稳定或线上性能回归时 MUST 停止升级，并保留最近通过全部 gate 的 model/profile 作为 rollback target。

#### Scenario: 连续两代提升不显著
- **WHEN** pi3/pi4 相对 pi2 的 final regret/score 区间均不能证明预声明改善
- **THEN** 停止自动迭代，不通过反复查看/换 seed 寻找发布结果
