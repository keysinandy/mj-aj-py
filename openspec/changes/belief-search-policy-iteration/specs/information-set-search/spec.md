## ADDED Requirements

### Requirement: Search-v1 优化指定 belief/opponent model 下的本局积分期望

Search-v1 SHALL 估计 hero information history 下各合法根动作的期望终局 settlement，并明确绑定 belief profile、actor/opponent policy、leaf evaluator、规则和 search profile。它 MUST NOT 将结果描述为真实墙 oracle 或无模型误差的绝对最优。

#### Scenario: belief 版本变化
- **WHEN** belief 从 `uniform_unseen-v1` 切换到 `belief-v2`
- **THEN** search fingerprint、teacher artifact 和所有 Q/visit 标签必须变更版本，不能与旧结果无标记混合

### Requirement: 每次 simulation 从当前 posterior root-sample 一个完整世界

Search-v1 SHALL 对每个 simulation 从当前 BeliefState 按 particle 权重 sample 一个完整 world，并在该 simulation 内使用该 world 的隐藏材料和 chance outcome 推进。sample id/random stream MUST 与根候选枚举顺序和 worker 调度解耦。

#### Scenario: 相同 search seed 改变 worker 数
- **WHEN** 相同 context/belief/profile 使用不同 worker count 执行固定 simulation ids
- **THEN** 每个 simulation root world fingerprint、最终 reward 和聚合 root 统计一致

### Requirement: Tree node 只能表示 hero information history

树节点 key SHALL 基于 hero 可观测 information history、hero hand 和公开状态语义；MUST NOT 包含 sampled world fingerprint、其他玩家暗手、真实未来 wall 或任何 oracle 字段。相同 hero information history 必须合并到同一语义 node，即使来自不同 root-sampled worlds。

#### Scenario: 两个 sampled worlds 到达相同 hero observation history
- **WHEN** 两个 simulations 的隐藏材料不同，但推进到相同 hero hand + public event history
- **THEN** 它们更新同一个 tree node，而不是按 hidden world 分裂

### Requirement: 只有 hero 决策建立搜索节点

首版 Search-v1 SHALL 仅在 hero 需要执行合法动作时进入 tree selection/expansion。其他玩家动作 SHALL 由指定 ActorPolicy 从其 actor-private view 概率采样；chance draw SHALL 由当前 sampled world 决定。系统 MUST NOT 把其他三家建成联合 minimax adversary。

#### Scenario: 对手有多个合法舍牌
- **WHEN** simulation 到达对手摸牌决策
- **THEN** search 不选择使 hero 最差的动作，而从 opponent/self-play policy 分布采样一个合法动作

### Requirement: 所有动作合法性与状态推进仍由规则引擎权威决定

根动作和未来 hero tree actions MUST 来自对应状态 `legal_actions()`。每个 simulation 的状态改变 SHALL 使用 `Game.step()` 或经过规则对拍的 adapter；search MUST NOT 复制一套独立吃碰杠/HU/抓打圈规则。

#### Scenario: 根同时存在 HU、KONG、普通舍牌
- **WHEN** all-root state 的 `legal_actions()` 包含这些动作
- **THEN** search root 候选集合与 legal set 一致，使用统一 terminal/leaf reward 单位比较

### Requirement: Tree policy 保存可复算 N/W/Q/P

每个 hero info node/action SHALL 至少保存 visit count、累积 value、平均 Q 和 prior。首版 MAY 使用 PUCT；`c_puct`、prior source、tie-break 和 root exploration noise MUST 进入 SearchProfile。validation/final reference MUST 禁止未冻结的随机 exploration noise。

#### Scenario: 没有 PolicyNet prior
- **WHEN** search profile 未提供可用 policy network
- **THEN** 可以从仅依赖公开信息的 shape-v2/Fast EV 分数构造版本化 prior，不得用 sampled hidden world 调整 prior

### Requirement: Reward 为完整本局 hero settlement

terminal simulation SHALL 返回 `Game.scores[hero]` 的本局 settlement delta，包括 hero 自摸正分、他家自摸负支付和流局零分。非法动作、异常或未终局 simulation MUST 标失败；MUST NOT 以零分静默计入平均。

#### Scenario: 对手先胡
- **WHEN** sampled world 中某对手先合法自摸
- **THEN** search backup hero 的实际负 settlement，而不是零或“未胡”占位值

### Requirement: Leaf evaluator 可替换但必须版本化和安全降级

Search-v1 SHALL 支持 `terminal-rollout-v1`、通过校准的 `value-net-v1` 和显式 `hybrid-v1`。Value model fingerprint/feature contract 不匹配、输出非有限值或校准状态无效时 MUST 禁用该 leaf 并按 profile 使用 terminal rollout 或报告失败，不能静默使用错误 value。

#### Scenario: ValueNet artifact 与 search profile 不匹配
- **WHEN** load 的 model manifest 使用不同 belief/search feature fingerprint
- **THEN** search 不调用该模型，记录 `leaf_model_mismatch` 并使用声明的安全 fallback

### Requirement: 搜索预算耗尽时保留当前证据而不伪装完成

Search SHALL 受 simulation、depth 和可选 wall-clock budget 限制。预算耗尽时输出已有 visit/Q、实际 simulation 数、terminal/leaf/failure rate 和 `incomplete_budget`/confidence 状态；离线 teacher 若不足以区分动作 SHALL 标 ambiguous。

#### Scenario: 两个根动作在预算内近似平局
- **WHEN** 达到 simulation budget 后根动作 Q/visit 尚不稳定或 reference CI 跨零
- **THEN** 保留 soft visit distribution/ambiguity，不制造硬 one-hot 最优标签

### Requirement: High-budget reference 定义可测 regret

项目 SHALL 冻结一个明显高于训练/在线预算的 reference SearchProfile，用独立 seeds/contexts 估计 `Q_ref`。候选策略的 root regret SHALL 定义为 `Q_ref(best_ref) - Q_ref(action_candidate)`；报告 MUST 包含 mean、p50/p95、ambiguous coverage 和 search-seed variance。

#### Scenario: 低预算搜索与 reference 选择不同
- **WHEN** 低预算 search 选择 action B，而 reference 的最佳为 action A
- **THEN** regret 使用 reference 对 A/B 的同模型价值差，不用动作是否相同的 0/1 指标代替
