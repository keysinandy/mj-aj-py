## Context

本文基于 `keysinandy/mj-aj-py` 当前分支，基线 `HEAD=f625550cedae935b4c9e16d2cbb31922cda344d4`。现有 `bot-ev-discard` change 已实现 Fast EV、reward envelope、paired racing rollout teacher、all-root 扩展与 evidence contract；本设计不重复这些能力，而是在其上增加行为后验、information-set search 和 policy/value iteration。

### 当前实现与本 change 的边界

| 当前模块 | 已有能力 | 本 change 补足 |
| --- | --- | --- |
| `mj/decision/fast_ev.py` | 全合法舍牌、EV1/EV2、batch frontier、候选 envelope、完整层回退 | 继续作为 fallback/feature，不扩 EV3/EV4 作为主路线 |
| `mj/decision/root.py` | 显式 scope 下比较 HU/财飘/KONG/反应根动作 | search 根动作直接复用 legal set 与规则 transition |
| `mj/rollout/belief.py` | `uniform_unseen-v1` 合法 hidden world 均匀采样 | 加权 particles、公开动作 likelihood、ESS/resample、posterior sampling |
| `mj/rollout/evaluator.py` | CRN paired racing、candidate envelope、离线 Q^pi | search teacher 与更强 continuation；保留旧 teacher 为基线 |
| `mj/rollout/simulator.py` | actor-private continuation、完整世界推进 | 提供 search simulation adapter；策略只看 actor view |
| `mj/decision/context.py` | 当前 public snapshot 与完整性/隐私边界 | 增加公开事件历史 hash/版本，不泄露 hidden state |
| `mj/features.py` | 109 动作、默认非 oracle 输入 | 增加 belief summary / history embedding 的可选公开特征 |
| BC/PPO pipeline | 可训练策略模型 | 增加 search soft policy target、value target、iteration provenance |

## Goals / Non-Goals

### Goals

目标策略定义为：

```text
pi*(I) = argmax_a E_{omega ~ P(omega|I), transitions, opponent}[R_hero]
```

其中：

- `I` 为 hero 当前 information history：hero 暗手 + 所有截至当前的公开事件和公开状态；
- `omega` 为对手暗手与牌墙等隐藏状态；
- `P(omega|I)` 由版本化 belief model 给出；
- `R_hero` 为当前一局终局 hero settlement delta；
- opponent 行为由版本化 opponent model 或 self-play policy 给出。

工程目标不是数学证明全局最优，而是在固定模型/规则下，通过更高预算 search reference 测得的平均 root regret 持续下降，并在独立 paired games 中提升平均本局积分。

### Non-Goals

- 不通过读取真实 opponent hands / wall order 提升线上决策。
- 不把普通 perfect-information MCTS、minimax 或 sampled-world node key 当作 information-set search。
- 不用 EV3/EV4 替代 belief/search；允许其作为实验 feature，但不是主路线。
- 不在本 change 中修改杭州麻将规则、倍率、legal action 编码、平台窗口授权或网络协议。
- 不要求第一版在线运行 POMCP；高成本 search 默认离线 teacher。
- 不把 search teacher 的有限样本结果称为“真实墙最佳”“绝对最优”或“博弈论精确最优”。

## Decisions

### 1. 四层架构

```text
Public events + current public state + hero hand
                    |
                    v
             InformationHistory
                    |
            +-------+--------+
            |                |
            v                v
       BeliefState       ActorPolicy
     weighted particles   P(a | view)
            |                |
            +-------+--------+
                    v
          Root-sampling Search-v1
       (hero information-history tree)
                    |
          +---------+----------+
          |                    |
          v                    v
   complete rollout       ValueNet leaf
          |                    |
          +---------+----------+
                    v
          search Q / visit policy
                    |
                    v
         PolicyValue distillation
                    |
                    v
          policy-v(k+1) online
                    |
             next iteration
```

规则依赖方向保持单向：

```text
Game / legal_actions / scoring
            ^
            |
belief/search/simulator
            ^
            |
training / policy iteration
```

search 不创建独立规则实现；任何 transition 最终由 `Game.step()` 或已对拍的公开 transition adapter 执行。

### 2. InformationHistory 是 belief 与 search 的语义输入

仅保存当前牌河不足以推断行为后验，因为同一最终公开状态可能由不同 PASS/claim 顺序产生。新增 append-only `PublicEvent`：

```text
DRAW_PUBLIC          # 仅公开“某座完成摸牌”，不含未知 tile identity
DISCARD(tile)
PASS(window/action-mode)
CHOW(action, tile/start)
PONG(tile)
KONG_OPEN(tile)
KONG_CLOSED_PUBLIC(...)   # 仅按平台公开程度保存
KONG_ADD(tile)
HU
ROUND_START / ROUND_END
```

每个事件至少包含：

```text
schema, gid?, round_no?, seq?, actor, phase,
event_type, public_payload, legal_actions_before?,
context_hash_before, event_hash, provenance
```

`InformationHistory.history_hash` 对事件顺序敏感。任何 hidden tile、真实 wall position、server-only future event 不进入 public payload。历史构建必须可从 Game 自博弈与平台日志分别复算得到同一语义事件。

### 3. Belief-v2 使用 weighted particle posterior

#### 3.1 初始化

在没有行为证据时，从当前 `PublicDecisionContext` 使用现有 uniform unseen 约束采样 `N` 个合法世界：

```text
particle_i = {hidden_hands, wall, weight=1/N, fingerprint}
```

必须满足现有物料守恒、暗牌数量、死墙和合法状态约束。

#### 3.2 公开动作 likelihood 更新

观察 actor 的公开动作 `a_obs` 后，对每个 particle：

```text
view_i = actor-private view under particle_i
p_i = OpponentPolicy.probability(a_obs | view_i, legal_i)
smoothed_p_i = (1 - epsilon) * p_i + epsilon / |legal_i|
weight_i <- weight_i * smoothed_p_i
normalize(weights)
```

若该 particle 下 `a_obs` 非法，则 likelihood=0；这属于硬约束，不使用 epsilon 修复非法世界。

首版 opponent likelihood 可由启发式分值 softmax 得到：

```text
P(a|s) = softmax(score(a)/temperature)
```

其中 score 来源必须版本化。后续允许替换为 `OpponentPolicyNet`。

#### 3.3 ESS 与重采样

```text
ESS = 1 / sum_i(w_i^2)
```

当 `ESS < ess_ratio * N` 时执行 deterministic systematic resampling；默认建议 `ess_ratio=0.5`，但进入 profile/fingerprint，可校准。

重采样后权重重置 `1/N`。为降低 particle impoverishment，可在 P1 后增加受公开约束的 rejuvenation kernel；其 acceptance 必须依据相同公开历史 likelihood，不得读取真实世界。

#### 3.4 Belief 输出

允许输出公开安全的 marginals：

```text
P(seat s holds tile t >= 1)
E[count_s[t]]
P(tile t is in live wall)
P(tile t is in dead wall)
ESS, entropy, particle_count
```

默认日志只保存 marginals/统计与 fingerprint，不保存 sampled hidden hands/wall。

### 4. OpponentPolicy 统一“动作选择”和“行为 likelihood”

接口：

```python
class ActorPolicy:
    def distribution(self, actor_view, legal_actions) -> ActionDistribution: ...
    def choose(self, actor_view, legal_actions, rng) -> int: ...
    def probability(self, action, actor_view, legal_actions) -> float: ...
```

首版实现：

```text
HeuristicLikelihoodPolicy
  feature/value source = shape-v2-fast if complete else shape-v1/legacy
  logits = normalized candidate value
  temperature = profile field
  epsilon = profile field
```

最终 search/self-play 推荐使用统一的 `PolicyValueNet` 作为 actor policy。线上真实玩家建模与 self-play policy 必须分开版本：

- `self_play`: 四家均使用当前策略代；
- `population`: opponent policy 由真实/历史玩家数据训练。

不得在同一个评估报告里无标记混合两种 opponent distribution。

### 5. Search-v1 采用 root-sampling POMCP，树只建 hero decision nodes

#### 5.1 为什么不使用普通 MCTS

完整 sampled world 不是 hero 可观测状态。若节点 key 包含 sampled opponent hands / wall，搜索会产生 determinization leakage：同一个公开决策根据算法内部已知隐藏牌选择不同动作。

#### 5.2 Simulation

每次 simulation：

1. 从当前 `BeliefState` 按权重 sample 一个完整 world；
2. 从当前 root hero information history 开始；
3. hero 决策节点由 tree policy 选择/扩展动作；
4. `Game.step(action)` 推进；
5. chance draw 使用 sampled world 内的 wall；
6. 对手决策使用该 actor 自己的 actor-private view，由 `ActorPolicy` 随机采样；
7. 一直推进到下一次 hero 决策、终局或 leaf 边界；
8. 下一 hero node 的 key 使用更新后的 hero information-history hash，而不是 world fingerprint；
9. terminal 返回实际 `Game.scores[hero]`；leaf 使用指定 leaf evaluator；
10. backup 到经过的 hero tree edges。

树内不为 opponent 建 minimax 节点。四人麻将中其他三家不是单一合作 adversary；对手行为由指定 population/self-play policy 分布积分掉。

#### 5.3 Tree statistics

每个 hero info node/action 至少维护：

```text
N(s), N(s,a), W(s,a), Q(s,a), prior P(s,a)
```

首版推荐 PUCT：

```text
Q(s,a) + c_puct * P(s,a) * sqrt(N(s)) / (1 + N(s,a))
```

没有 PolicyNet 时，prior 可由 shape-v2 Fast EV 经稳定 softmax 得到；不能用 sampled hidden world 改 prior。

root 输出：

```text
visit_count, visit_policy, Q, mean, variance,
best_action, runner_up, simulations, terminal_rate,
leaf_rate, belief/search/profile fingerprints
```

### 6. LeafEvaluator 从完整 rollout 平滑迁移到 ValueNet

支持三种版本化 leaf mode：

```text
terminal-rollout-v1   # 从叶子按固定 policy 打到 Game.done
value-net-v1          # 通过校准的公开信息 ValueNet
hybrid-v1             # 高不确定/特殊动作使用 rollout，其余 ValueNet
```

`ValueNet` 目标为：

```text
V(I) = E[final hero round score | I, belief/opponent model]
```

输入只允许：

- hero private hand；
- public context/history feature；
- 可选 belief marginals/ESS/entropy；
- legal mask；
- 规则/profile scalars。

禁止 sampled hidden identity、未来 wall order、oracle planes。

ValueNet 必须在独立 contexts 上报告 MAE/RMSE、sign/ranking diagnostics 与 calibration by score bucket。未过闸门时 search 自动退回 terminal rollout leaf，不允许静默启用未校准 value。

### 7. PolicyValueNet 与 search distillation

建议复用现有 109 动作空间。模型至少两头：

```text
policy_head -> logits[109]
value_head  -> scalar round-score value
```

训练目标：

```text
policy_target = normalized search visit counts
value_target  = high-budget search root value / terminal outcome
```

不将 ambiguous search 强行变成 one-hot。每条训练样本保存：

```text
context_hash, history_hash,
belief_version/fingerprint,
search_version/profile,
continuation/opponent version,
policy_version source,
visit_counts, Q_by_action,
root_value, simulations,
confidence/ambiguity,
oracle=False
```

损失推荐：

```text
L = w_policy * CE(pi_search, pi_net)
  + w_value  * Huber(V_target, V_net)
  + regularization
```

低 simulation、ambiguous、search CI 未分离样本降低 sample weight，而不是删掉所有近似平局。

### 8. Policy Iteration

冻结每一代：

```text
pi_0 = shape-v2-fast / current stable policy
search_0(pi_0) -> dataset_0
train -> pi_1
search_1(pi_1) -> dataset_1
train -> pi_2
...
```

每代 continuation/opponent/profile 均进入 fingerprint。禁止使用 `pi_2` 生成的数据标成 `pi_1` teacher。

策略升级条件同时包含：

1. 对冻结 high-budget search reference 的平均 root regret 下降；
2. 独立 4096 paired games 平均 hero round score CI 下界不低于上一代；
3. legal/safety/oracle tests 全通过；
4. 推理性能满足线上预算；
5. 若使用 population opponent，必须在冻结 opponent split 上验证，不仅 self-play。

若某代只提升 self-play、但 population regression，则不得直接替换真实线上 profile。

### 9. 在线 policy-v3

默认线上路径：

```text
PublicDecisionContext + InformationHistory
          |
 optional BeliefSummary (bounded work)
          |
     PolicyValueNet
          |
      legal mask
          |
 confidence gate
    /               \
 network           fallback
 action            shape-v2/legacy
```

完整 particle POMCP 默认不在线运行。可选 `shallow-search` 必须是单独 profile：例如小粒子数 + 16~64 simulations，仅在十场并发和真实窗口 p95/p99 通过后才允许启用。

网络动作不合法时禁止 repair 成任意合法动作；统一走明确 fallback 并记录原因。

### 10. Failure / Fallback

| 失败 | 行为 |
| --- | --- |
| event history 不完整 | belief 标 degraded；使用 uniform unseen 或稳定旧策略，记录原因 |
| particle 全部零权 | 从当前 public constraint 重新初始化 uniform particles，标 `posterior_reset` |
| ESS 很低 | deterministic resample；仍低则增加 particle/rejuvenation 或标低置信 |
| opponent likelihood unavailable | 使用冻结 fallback likelihood profile，不偷取 hidden info |
| search budget exhausted | 返回当前 root visit/Q 与 `incomplete_budget`；离线 teacher 可 ambiguous，线上按 confidence gate fallback |
| ValueNet 未通过版本/校准 | 使用 terminal rollout 或 Fast EV fallback |
| network 输出 NaN/非法 | 不提交；走 shape-v2/legacy fallback |
| belief/search/model 指纹不匹配 | 拒绝加载 artifact，不自动兼容 |

## Data Contracts

### BeliefProfile

```json
{
  "schema": "belief-profile-v2",
  "particle_count": 512,
  "ess_ratio": 0.5,
  "likelihood_policy": "policy-vN",
  "temperature": 1.0,
  "epsilon": 0.01,
  "resampler": "systematic-v1",
  "rejuvenation": "none-v1",
  "seed": 0
}
```

数值仅为初始默认建议；发布 profile 必须冻结实际值和 fingerprint。

### SearchProfile

```json
{
  "schema": "information-set-search-v1",
  "belief_profile_fingerprint": "...",
  "actor_policy_version": "policy-vN",
  "leaf_version": "terminal-rollout-v1",
  "simulation_budget": 2048,
  "max_depth": 64,
  "c_puct": 1.5,
  "root_noise": false,
  "reward": "hero_round_settlement_delta"
}
```

离线 train search 可启用 root exploration noise，但 validation/final reference 禁止噪声，并固定 seeds。

## Testing Strategy

### Correctness

- 同一 public history/context、同 seed/profile -> belief particles、weights、search root statistics 可复现。
- 修改真实隐藏手/真实 wall 但保持 public history 不变 -> belief/search 输入 hash 与策略分布不变。
- 每个 sampled world 物料守恒，actor policy 永远只收到 actor 自己暗手。
- search tree node key 不含 world fingerprint/opponent hand/wall。
- 所有根动作来自 `legal_actions()`；每个 simulation transition 由 Game 规则推进。

### Belief Calibration

在离线 simulator 中隐藏真实 world，只向 belief 提供 public events。报告：

- opponent tile-count marginal Brier / log loss；
- true held tile 的 posterior percentile/coverage；
- live-wall tile marginal calibration；
- ESS、reset rate、resample rate；
- `belief-v2` 相对 uniform unseen 的 held-out likelihood/calibration 改善。

### Search Quality

建立 8k/16k simulation 的冻结 high-budget reference。低预算版本报告：

```text
root action agreement
mean regret = Q_ref(best_ref) - Q_ref(action_test)
p50/p95 regret
search variance across seeds
ambiguous rate
```

### Policy Iteration

每代至少比较：上一代、当前代、shape-v2、high-budget teacher。主指标仍为独立 paired `hero_round_score_points`，同时报告 search regret。

### Online Gate

继承当前窗口门槛：完整实际调度下验证 discard/reaction p95/p99、fallback rate、illegal=0、evaluator-caused window loss=0；默认策略切换前至少三个新房逐窗验收。

## Rollout / Migration

1. `belief-v2` 先 shadow，仅输出 posterior diagnostics，不改变动作；
2. search-v1 仅离线，生成 teacher/reference；
3. 训练 policy-value，离线 paired games 与 regret 验收；
4. policy-v3 线上 shadow，记录“实际动作 vs policy-v3 suggestion”；
5. 通过性能与线上逐窗后 opt-in；
6. 多代 policy iteration 只有在独立 final split 未回归时升级；
7. 始终保留 shape-v2/legacy rollback。
