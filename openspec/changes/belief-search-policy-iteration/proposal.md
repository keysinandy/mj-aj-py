## Why

当前分支已经完成 shape-v2 的全合法舍牌 Fast EV、候选级 reward envelope、paired racing rollout teacher、HU/财飘/KONG/反应根动作扩展，以及两摸 EV2 的 batch frontier 热路径。最新基线为 `f625550cedae935b4c9e16d2cbb31922cda344d4`（`fix: stabilize shape-v2 two-draw evaluation`）。这些能力已经足以稳定定义“当前公开信息 + 指定 belief + 指定 continuation 下的动作价值”，但仍不足以逼近真正的最佳策略。

主要剩余偏差不是向听、U1 或 EV2 深度，而是：

1. `BeliefSampler` 仍使用 `uniform_unseen-v1`，未利用对手历史弃牌、PASS、吃、碰、杠等公开动作对隐藏手牌分布的约束；
2. rollout teacher 的 continuation 仍冻结在 `legacy/shape-v1`，因此估计的是 `Q^pi(s,a)`，不是随策略迭代不断提升后的动作价值；
3. Fast EV 的 `tail=zero` 只看有限本家未来摸牌，无法表达长程牌局价值、其他玩家先胡风险和未来公开动作对局面的影响；
4. 当前没有基于 information set 的多步搜索层，无法在隐藏信息与未来随机事件下直接优化全动作策略；
5. 当前 BC/策略网络尚未以搜索访问分布与长期价值作为统一 teacher，缺少“搜索 -> 蒸馏 -> 更强 continuation -> 再搜索”的策略迭代闭环。

本 change 将目标从“更准确的局部 EV 启发式”提升为：在公开信息、版本化隐藏状态后验和指定对手模型下，近似最大化本局 hero 最终积分期望，并用高预算 information-set search 作为离线 teacher，通过 policy/value 蒸馏形成可在线执行的低延迟策略。

## What Changes

- 新增 `belief-v2`：以公开事件历史为证据维护加权 hidden-world particles；初始化仍从合法 unseen 物料采样，但每次公开 DISCARD/PASS/CHOW/PONG/KONG/HU 等动作均按版本化 opponent likelihood 更新权重，并在 ESS 过低时确定性重采样。
- 新增概率型 opponent policy 接口：不仅返回动作，还返回 `P(action | actor_view)`；首版允许由 shape-v2/shape-v1 分值经 softmax 转为 likelihood，后续可替换为独立训练的 opponent policy network。
- 新增 `search-v1`：使用 root-sampling POMCP/Information-Set MCTS。每次 simulation 从 `belief-v2` 采样一个完整世界；树节点仅表示 hero 的 information history，禁止以 sampled hidden world 作为节点 key；对手动作按 opponent policy 采样，不使用二人零和 minimax。
- 新增可版本化 leaf evaluator：首版允许完整 rollout 到终局；当 value network 通过校准闸门后，可在深度/预算边界使用 `V(I)` 估计长期本局积分价值，替代 `tail=zero`。
- 新增 `policy-value` 蒸馏：策略头学习 search visit distribution，价值头学习 search/root value 与完整终局收益；所有训练输入固定 `oracle=False`，teacher 的 sampled hidden identity 不进入网络特征。
- 新增策略迭代闭环：`pi_k -> search(Q^pi_k) -> distill pi_{k+1} -> use pi_{k+1} as continuation/opponent model -> repeat`，并为每代策略冻结 belief/search/model/data 指纹。
- 新增 `policy-v3` 在线执行模式：默认只运行蒸馏后的 policy/value 网络 + legal mask + Fast EV/legacy 安全回退；高成本 POMCP 默认仅离线，只有独立性能闸门通过后才允许可选小预算在线搜索。
- 新增 belief 校准、search regret、policy distillation、迭代收益、线上窗口五层验收；“最佳策略”统一表述为指定 belief/opponent model 下的近似 Bayes-optimal 策略，不宣称全局精确博弈论最优。

## Capabilities

### New Capabilities

- `belief-v2`: 公开事件历史、粒子后验、动作 likelihood、ESS、重采样、belief marginals 和隐藏信息边界。
- `information-set-search`: root-sampling POMCP/ISMCTS、hero information-history tree、全合法根动作、对手概率策略、chance transition、终局/leaf value、预算和可复现性。
- `policy-value-iteration`: search teacher 数据、policy/value 双头目标、软标签、低置信样本权重、模型版本和 policy iteration 闭环。
- `online-optimal-strategy`: policy-v3 在线推理、legal mask、安全回退、shadow/opt-in、预算、日志、发布门槛。

### Modified Capabilities

- `rollout-ev-teacher`: uniform unseen teacher 保留为基线；新增从 BeliefState 采样和更强 continuation 的 teacher/search 接口，所有版本不得无标记混合。
- `bot-ev-decision`: shape-v2 继续作为可解释 Fast EV 与安全 fallback，不再承担“最终最佳策略”的主模型角色。
- `ev-policy-calibration`: 从 Q0/EV2 参数校准扩展为 belief、search、policy/value 和迭代版本的独立数据/统计契约。
- `ev-decision-evidence`: 增加 belief/search/policy provenance、搜索访问分布、leaf 来源、regret 与 fallback 证据。

## Impact

- 预计新增：
  - `mj/belief/{events,state,likelihood,resample}.py`
  - `mj/search/{tree,pomcp,leaf,profile,report}.py`
  - `mj/models/{opponent_policy,policy_value}.py`
  - `mj/training/{search_data,policy_value_train,policy_iteration}.py`
- 预计修改：
  - `mj/rollout/{belief,evaluator,simulator}.py`
  - `mj/decision/context.py`
  - `mj/bot.py`
  - `mj/features.py`
  - `mj/bc_data.py` / `mj/log2data.py`
  - 平台 Recorder/runner 与离线评估脚本。
- 规则真值继续由 `Game.legal_actions()`、`Game.step()`、`win.py`、`scoring.py` 提供；search/belief 不复制第二套规则。
- `shape-v2`、reward envelope、paired racing、all-root 和现有 OpenSpec change 不删除；本 change 依赖它们作为 baseline/fallback，并使用新的版本指纹阻止旧 teacher 与新 teacher 混用。
- 本次 change 不要求立即切换线上默认策略；默认保持现有稳定策略，直到 belief、search、distillation、4096 paired score、并发性能和线上逐窗验收全部通过。
