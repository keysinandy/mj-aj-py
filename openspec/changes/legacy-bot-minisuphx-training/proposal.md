## Why

当前项目已经具备三条相互独立但尚未统一的能力：

1. `mj/bc_data.py` / `mj/bc_train.py`：可用 legacy BOT 自博弈生成监督数据并训练 Policy/Value 网络；
2. `mj/rl_env.py` / `mj/train_ppo.py`：可从 BC checkpoint 初始化 MaskablePPO，并通过 BC-KL、Oracle annealing、potential shaping 做单机 RL；
3. `belief-search-policy-iteration` 与 `distributed-search-distillation-workers`：已经定义/实现大量 belief、information-set search、policy/value distillation、两机 Teacher/paired worker 的基础设施。

但当前训练主线仍缺少统一的“版本化策略演进契约”。继续直接扩大 PPO 训练量会遇到几个已知风险：

- 现有 PPO 统一学习 109 动作，容易在 RL 早期破坏 legacy BOT 已经稳定的 HU/KONG/CHOW/PONG 行为；
- `train_ppo.py` 当前以单机 SB3 rollout 为中心，不适合作为 RTX 2060 + RX 6600 两台 Windows 机器的长期 Actor/Learner 架构；
- BC 与 RL value 目标尺度、policy/value head 迁移和 checkpoint contract 尚未完全统一；
- 之前 PPO 训练已经出现累积 policy drift，说明仅依赖单步 PPO clipping/target KL 不足以保护 BC 先验；
- 已有 belief/search/distillation 能力没有被纳入 Legacy BC/RL 的统一 generation 路线。

本 change 固化一个可逐步验收的 Mini-Suphx 路线：首先以 legacy BOT 为专家构建稳定 BC，然后只强化普通弃牌；在策略稳定超过 legacy 后，再引入 Oracle Guiding、Opponent League、Belief/Value/Search 与 Search Distillation。第一版不追求复现 Suphx 的硬件规模，而复现其最有价值的训练结构与稳定化思想。

## What Changes

- 定义统一的 Mini-Suphx 策略代际：
  - `BC-v1`：legacy BOT 行为克隆 + DAgger；
  - `RL Gen0/1/2`：discard-only RL，从固定 legacy 对手逐步进入 opponent league；
  - `Champion-v1`：通过完整 paired gate 的稳定公开信息策略；
  - `Oracle Gen`：从 Champion-v1 启动 Oracle Guiding 并退火到 public-only；
  - `Champion-v2`：结合 belief/value/search/distillation 的下一代策略。
- 新增 Hybrid Policy 契约：第一阶段只有“普通弃牌”由学习策略决定；HU/KONG/CHOW/PONG/PASS/特殊安全逻辑继续走 legacy，避免一次性解锁全部 109 动作。
- BC 数据与训练改为 shard-streaming、可恢复、带 manifest/fingerprint；固定 train/validation/final-test seed domains。
- 新增 DAgger 数据生成：执行策略逐步从 legacy 转向 BC/RL，但监督标签保持 legacy BOT。
- 统一 BC/RL value contract，使 BC value head 可直接作为 RL critic 初始化。
- 双机 RL 采用 synchronous Actor/Learner：PC-B 主要生成 rollout，PC-A RTX 2060 负责唯一梯度更新；第一版不做 RTX/RX 跨机 DDP。
- 每个 rollout 严格绑定一个 policy version；stale-policy shard 不得混入 on-policy PPO update。
- BC prior KL 与 shaping 均按训练进度退火；entropy 使用目标 entropy 控制而不是固定系数。
- Opponent League 逐代引入 BC、历史 RL 与 champion，并永久保留 legacy/BC 锚点。
- Champion promotion 使用冻结 paired schedule、平均积分与 bootstrap CI；训练 loss、ep_rew_mean、单次 win-rate 不能单独决定升级。
- v2 复用现有 `belief-search-policy-iteration`，而不是重新实现 belief/search；本 change 只定义其何时进入主训练线、如何与 Champion-v1/Oracle/RL 交替。
- reaction RL 按能力阶梯解锁：discard → PONG/PASS → CHOW/PASS → 其余 reaction；HU 与特殊安全规则保持独立护栏，除非后续 change 明确替换。

## Goals

- 在两台现有 Windows 机器上建立可长期运行、可恢复、可比较的 BC + RL 训练闭环；
- 首先得到一个在独立 paired games 上稳定超过 legacy BOT 的 discard 策略；
- 防止 RL 通过策略漂移破坏已知可靠的 legacy 行为；
- 让后续 Oracle/Belief/Search/Distillation 复用同一版本、数据、rollout 和 promotion contract；
- 所有策略代、数据代、对手池和训练 run 均可追溯到 git commit 与 frozen fingerprints。

## Non-Goals

- 第一版不做 RTX 2060 与 RX 6600 之间的梯度同步、参数服务器 all-reduce 或异构 DDP。
- 第一版不要求 RX 6600 参与 PyTorch 反向传播；其 GPU inference 仅在后续 benchmark 证明有收益后启用。
- 第一版不让 PPO 直接控制所有 109 个动作。
- 第一版不启用 Global Reward Prediction；当前训练目标仍为单局终局积分。只有训练单位升级到多局比赛/锦标赛后才重新评估 GRP。
- 第一版不要求在线运行高预算 information-set search；search 默认仍是离线 teacher。
- 不把有限 paired/search 结果描述为绝对最优、博弈论最优或真实墙最优。

## Capabilities

### New

- `minisuphx-training`
  - Legacy BC / DAgger / discard-only RL / Oracle Guiding / reaction unlock roadmap；
  - BC anchor、value contract、dynamic entropy、shaping/KL schedule；
  - opponent league 与 generation identity。
- `minisuphx-distributed-rl`
  - 两机 synchronous rollout；
  - policy-version lock；
  - rollout shard contract；
  - PC-A learner / PC-B actor capability matching；
  - crash-safe resume。
- `minisuphx-evaluation-gates`
  - frozen paired schedules；
  - fast/full gate；
  - champion promotion/rollback；
  - discard hard-set regression。

### Dependencies / Reused Capabilities

- `distributed-search-distillation-workers`：复用 coordinator / lease / heartbeat / immutable shard / paired-row merge。
- `belief-search-policy-iteration`：复用 belief-v2、information-set search、PolicyValueNet、search distillation 与 policy iteration。
- `formal-tournament-participation`、现有 replay/recorder：线上验收与真实窗口回归继续复用既有门禁。

## Impact

预计新增：

- `mj/hybrid_policy.py`
- `mj/rl_discard_env.py`
- `mj/training/dagger.py`
- `mj/training/ppo_rollout.py`
- `mj/training/ppo_learner.py`
- `mj/training/opponent_pool.py`
- `mj/training/minisuphx_manifest.py`
- `scripts/train_minisuphx.py`
- 对应单元/集成测试。

预计修改：

- `mj/bc_data.py`
- `mj/bc_train.py`
- `mj/model.py`
- `mj/train_ppo.py`（保留为单机实验/回归；不再作为双机生产 trainer）
- `mj/training/distributed_jobs.py` / worker runtime（增加 BC/DAgger/RL rollout job kinds）
- `mj/training/paired_eval.py`
- `mj/training/run_summary.py`

本 change 的第一发布目标是 `Champion-v1`。Oracle/Belief/Search/Distillation 属于同一 roadmap 的 v2 milestone，但只有在 v1 gate 完成后才能成为默认训练路径。
