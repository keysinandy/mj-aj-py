# bot-ev-reward-bound Specification

## Purpose

为 FAST EV 与 rollout 定义分离的收益包络与只允许同层严格上界的剪枝规则，并把 bound 的版本、模式与证书纳入评价指纹。

## Requirements

### Requirement: Fast EV 与 rollout 使用分离收益 envelope

收益评价器 SHALL 为每个受支持的公开上下文和根候选生成带版本的 `RewardEnvelope`，至少提供 Fast EV 的非负收益上界、rollout 终局 hero reward 的有符号范围及其绝对界。两个界 MUST 明确各自的模型假设，不得把 Fast EV 的 hero-only 上界当作包含对手终局的 rollout 上界。

#### Scenario: Fast EV 只有本家未来自摸

- **WHEN** 候选在 `uniform_unseen_no_opponent_actions_score_v1` 下计算 EV1/EV2
- **THEN** 所有模型叶子的本家收益均落在 `0` 到该候选的 `fast_upper` 之间，且该上界不依赖观测到的最大收益

#### Scenario: Rollout 中对手先胡

- **WHEN** 某共享世界中对手先合法自摸并对 hero 产生负支付
- **THEN** hero reward MUST 落在该候选的 `rollout_lower`/`rollout_upper` 范围内，并由 `rollout_abs` 覆盖

### Requirement: Reward envelope SHALL 由根动作后的公开规则资源证明

系统 SHALL 先应用现有合法根动作转移，再依据公开上下文、牌张守恒、活墙、locked/meld、chain/chain_piao、财神材料、七对/豪华潜力、4 白板、爆头和现有结算系数推导安全界。证书 MUST 保留这些资源上限和最终 multiplier/settlement factor；不得读取隐藏手牌身份、真实墙序或 sampled world 来收紧线上界。

#### Scenario: 普通弃牌与财飘的链状态不同

- **WHEN** 两个候选分别执行普通弃牌和满足规则财飘的白板弃牌
- **THEN** 普通弃牌的 envelope 清除旧 chain/chain_piao，财飘候选保留并增加对应链计数，不能使用同一个未区分的链上限

#### Scenario: 已有碰牌可加杠

- **WHEN** hero 或潜在获胜者已有公开 pong，且仍有可用 meld slot/活墙
- **THEN** 上界必须计入既有 pong 的一次加杠潜力，同时不得把加杠重复算成新的 meld slot

#### Scenario: 稀有高番路径未在样本出现

- **WHEN** 4 白板、爆头或豪华七对路径在当前观测样本中没有出现
- **THEN** envelope 仍覆盖规则允许的该路径，不能用历史观测最大 multiplier 替代理论上界

### Requirement: Fast EV 只允许同层严格上界剪枝

Fast EV SHALL 使用候选的 `fast_upper` 或明确声明的 scalar override 构造同层上界；只有当候选上界严格小于已完成候选的当前最优值时才可淘汰。上界相等 MUST 保留候选和稳定 tie-break；无法证明上界时 MUST 禁止该候选剪枝。

#### Scenario: 候选上界等于当前最优

- **WHEN** 未完成候选的 fast upper bound 等于已完成候选的最优值
- **THEN** 该候选继续计算或参与完整层级回退，不被上界淘汰

#### Scenario: 上界证明失败

- **WHEN** 上下文缺少推导所需字段或 envelope 返回 `unknown`
- **THEN** Fast EV 关闭上界剪枝并保留完整层级事务式回退，不把未知界填成零

### Requirement: Bound 版本、模式和证书必须进入评价指纹

profile、Fast EV explanation 和 teacher configuration SHALL 记录 bound version、override/derived/fallback 模式、收益单位和证书指纹。bound 实现、组件公式或模型语义改变 MUST 产生新的 profile/teacher fingerprint，旧证据不得无标记地与新界混合。

#### Scenario: 旧宽界兼容回退

- **WHEN** 新 envelope 暂不支持某个上下文，但调用方显式允许兼容 fallback
- **THEN** 使用旧宽界并记录 `legacy_conservative_fallback`，同时使该运行不能宣称使用了新的局面级剪枝
