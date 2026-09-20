## Context

本设计基于 `main@08d33cffc91660518cd32e5a934771ef9ff65aba`，目标是在不推翻现有 legacy BOT、BC、PPO、belief/search/distillation 与 distributed worker 基础设施的前提下，建立统一的 Mini-Suphx 训练主线。

现有关键事实：

- `mj/bc_data.py` 已能用 legacy BOT 自博弈生成 `(obs, mask, action, value)`；
- `mj/bc_train.py` 使用 masked CE + 花色增广 + value 辅助头，但仍把大量 shard 聚合到内存；
- `mj/train_ppo.py` 已有 BC-KL、Oracle annealing、shaping 与 BC checkpoint 迁移经验，但仍是 SB3 单机 rollout 架构；
- 既往 PPO 已观察到累积 policy drift，因此 BC anchor 必须作为长期稳定器；
- `distributed-search-distillation-workers` 已定义 PC-A coordinator/trainer + PC-B compute worker 的 Windows 双机架构；
- `belief-search-policy-iteration` 已定义 belief-v2、information-set search、PolicyValueNet 与 search distillation，不应在本 change 重复实现。

本设计将训练主线拆为两个明确里程碑：

```text
v1: Legacy -> BC/DAgger -> Discard-only RL -> League -> Champion-v1
v2: Champion-v1 -> Oracle Guiding -> Belief/Value/Search -> Distill/RL Iteration -> Champion-v2
```

## Goals / Non-Goals

### Goals

1. 建立可复现的 Legacy BC 基线与永久 BC anchor；
2. 仅把普通弃牌交给 RL，避免过早破坏 HU/KONG/CHOW/PONG 等稳定逻辑；
3. 在两台 Windows 机器上采用同步 Actor/Learner，而非跨厂商 DDP；
4. 所有 rollout 严格绑定 policy version，保持 PPO on-policy 语义；
5. 通过 paired score + bootstrap CI 决定 champion 晋级；
6. v2 只在 v1 稳定后引入 Oracle、Belief、Value、Search 与 reaction RL；
7. 所有 generation/data/opponent/checkpoint 均可追溯、可恢复、可回滚。

### Non-Goals

- 不在 v1 中训练完整 109-action end-to-end policy；
- 不在 v1 中实现异步 IMPALA/V-trace；
- 不在 v1 中引入 multi-round GRP；
- 不在 v1 中要求 RX 6600 训练梯度；
- 不重复实现已有 belief/search 体系；
- 不因训练框架变化而修改麻将规则真值。

## Decisions

### 1. 策略分层：HybridPolicy 是 v1 的线上语义

v1 的默认动作路由：

```text
Game state
  |
  v
Legacy decision gate
  |
  +-- HU / KONG / CHOW / PONG / PASS / special safeguard --> legacy
  |
  +-- ordinary discard ------------------------------------> learned discard policy
```

“ordinary discard” SHALL 指 legacy gate 已经判断当前动作类别为普通弃牌，且没有必须优先执行的 HU、KONG、特殊安全动作。

学习策略不得自行覆盖：

- HU 短路；
- 杠的规则合法性与 legacy 特殊判断；
- response window 中的 CHOW/PONG/PASS；
- 平台/规则要求的强制动作；
- 明确配置为 legacy safeguard 的动作。

该边界由独立 `HybridPolicy` 和 `MahjongDiscardEnv` 表达，不依赖调用方约定。

### 2. 模型基线：6x128，BC/RL 同构

v1 默认模型：

```text
Residual blocks: 6
Width: 128
Input: N_PLANES_ORACLE + N_SCALARS compatible layout
Policy head: 109 logits
Value head: scalar tanh
```

虽然训练时只开放 discard legal mask，但保留 109 维 action head，保证与现有 action encoding、search/distillation、未来 reaction unlock 兼容。

BC 与 RL 必须使用相同 backbone/policy/value 结构。禁止在进入 RL 时把 value head 换成新的随机 MLP 而仍声称“完整 BC 初始化”。

### 3. Value contract 统一为 [-1, 1]

统一目标：

```text
value_target = clip(hero_final_score / 24, -4, 4) / 4
terminal_reward = same scale
```

即 BC value 与 RL terminal return 都在 `[-1,1]`。

该 contract SHALL 版本化，例如：

```text
value_contract = "round-score-v2-normalized"
```

任何旧的 `[-4,4]` checkpoint/rollout 不得无标记混入新训练。

### 4. BC 数据生成与 split

首次正式 BC campaign 默认：

```text
train seeds:      0 .. 29999
validation seeds: 1000000 .. 1001999
final-test seeds: 2000000 .. 2003999
```

seed domain SHALL frozen 到 manifest。validation/final-test 不能用于 DAgger 收集、PPO rollout、超参搜索或 champion 选择。

BC 数据通过分布式 job 生成，每个 job 输出独立 shard + manifest；worker 不共享 append 文件。

`bc_train.py` 改成真正的 shard-streaming/DataLoader 模式，禁止训练前把全部训练 shard 拼进单一常驻数组。

### 5. DAgger

DAgger 仅用于修复状态分布偏移，不改变 teacher identity。

推荐三轮：

```text
D1: legacy execute 70%, learned discard 30%, 3000 games
D2: legacy execute 40%, learned discard 60%, 3000 games
D3: legacy execute 10%, learned discard 90%, 4000 games
```

无论谁执行，普通弃牌 label 始终来自 frozen legacy BOT。特殊动作仍按 HybridPolicy gate 走 legacy。

每条 DAgger 样本保存：

```text
source_policy
teacher_policy=legacy
teacher_version
execute_action
teacher_action
disagreement
source_game
seed
git_commit
feature_contract
```

最终 `BC-v1` 作为永久 BC anchor，后续不得用 RL checkpoint 覆盖它的身份。

### 6. RL 环境只暴露普通弃牌决策

新增 `MahjongDiscardEnv`：

- 环境内部自动推进所有对手动作；
- hero 的 HU/KONG/reaction 由 HybridPolicy/legacy 自动执行；
- 只有 hero 的普通弃牌时才向 agent 返回 step；
- 一个 RL timestep = 一个实际普通弃牌决策；
- 统计指标以 `discard_decisions` 为主，不再把所有环境内部动作都计入训练步数。

该 env 必须能记录其自动执行的 legacy actions，供 replay/debugger 审计。

### 7. v1 PPO 为自有同步 Actor/Learner

`mj/train_ppo.py` 保留为单机实验/回归，但正式双机 v1 不以 SB3 自带 VecEnv 作为主架构。

同步 round：

```text
freeze policy_N
  -> publish manifest/checkpoint
  -> PC-A/PC-B actors collect exactly policy_N rollouts
  -> validate + merge
  -> learner update on PC-A RTX 2060
  -> publish policy_(N+1)
```

在一个 PPO update 内，所有 sample 必须来自同一 policy version。

### 8. 双机角色

默认：

#### PC-A: master-trainer
- coordinator
- CUDA learner on RTX 2060
- 1~2 optional rollout workers when learner idle
- BC/distillation trainer
- central merge/promotion

#### PC-B: compute-worker
- 6~10 rollout actors（需 benchmark）
- BC/DAgger game generation
- paired evaluation
- search/teacher jobs
- local SSD staging

RX 6600 在 v1：

```text
gpu_training=false
```

只有 ONNX/DirectML benchmark 证明推理吞吐提升且数值一致时，才允许单独 capability profile 启用 inference；不得影响 CPU fallback。

### 9. Rollout shard contract

每个 rollout result 至少包含：

```text
schema
campaign_id
generation
policy_version
policy_fingerprint
git_commit
worker_id
job_id

obs
legal_mask
action
old_log_prob
old_value
reward
done
episode_start

seed
dealer
agent_seat
episode_id
opponent_profile
opponent_versions
terminal_score

value_contract
feature_contract
env_profile
```

结果目录：

```text
runs/minisuphx/<run_id>/
  policies/policy_000037.pt
  policies/policy_000037.json
  rollouts/policy_000037/<job_id>/
    rollout.npz
    manifest.json
```

manifest 最后提交。未知/缺失 policy fingerprint、stale version、不同 value contract 的 shard 必须拒绝 merge。

### 10. PPO 默认超参

v1 初始 profile：

```text
rollout_per_update: 16384~32768 discard decisions
batch_size: 1024
ppo_epochs: 4
clip_range: 0.15
gamma: 1.0
gae_lambda: 0.95
actor_lr: 3e-5
critic_lr: 1e-4
max_grad_norm: 0.5
```

若实现共享 optimizer，则仍须允许 actor/value 参数组独立 LR。

### 11. BC Prior KL

永久 reference = `BC-v1`。

loss：

```text
L = L_ppo
  + lambda_bc * KL(pi_live || pi_BC)
  + value_loss
  + entropy_term
```

推荐 schedule：

```text
0-100k discard decisions:      lambda=1.0
100k-300k:                     1.0 -> 0.3
300k-600k:                     0.3 -> 0.1
600k+:                         0.05~0.1
```

该 schedule 属于 profile，不是硬编码常量。

### 12. Dynamic Entropy

不使用固定 ent_coef 作为唯一控制。

对每个 state 计算合法动作归一化 entropy：

```text
H_norm = H(pi) / log(max(2, N_legal))
```

推荐目标：

```text
early: 0.30~0.35
late:  0.15
```

entropy coefficient 根据 EMA 的实际 entropy 与 target 误差动态调节，并限制 min/max。

日志必须记录：

```text
entropy_raw
entropy_normalized
entropy_target
entropy_coef
legal_action_count
```

### 13. Potential shaping 仅为早期脚手架

使用当前 shanten potential 但缩放到新 reward contract。

推荐：

```text
0-100k:       shape_k=0.005
100k-300k:    0.005 -> 0
300k+:        0
```

Champion promotion 的最终评估始终使用真实 terminal reward，不使用 shaped reward 作为牌力指标。

### 14. Opponent League

阶段：

```text
Gen0: legacy 100%

Gen1:
  legacy 60%
  BC-v1 20%
  historical RL 20%

Gen2:
  legacy 40%
  BC-v1 20%
  RL league 40%
```

后续可切 PFSP，但始终保持：

```text
legacy >= 20%
BC-v1 >= 10%
```

作为 anchor。

对手 policy 的“reaction 层”和“discard 层”必须独立声明，避免把“RL opponent”误解为所有动作都由 RL 控制。

### 15. v1 generation roadmap

默认：

```text
Legacy
 -> 30k BC games
 -> BC0
 -> DAgger 3k
 -> BC1
 -> DAgger 3k
 -> BC2
 -> DAgger 4k
 -> BC-v1 (frozen anchor)

 -> RL Gen0 0-200k discard decisions
 -> Gate
 -> RL Gen1 200k-600k
 -> Gate
 -> RL Gen2 600k-1.2M
 -> Full Gate
 -> Champion-v1
```

数字是默认 campaign profile，可调，但一旦 campaign 提交必须冻结。

### 16. Paired promotion gate

每 50k~100k discard decisions 产生候选 checkpoint。

默认层级：

```text
512-pair smoke
 -> 1024-pair fast gate
 -> 4096-pair full gate
```

paired schedule 固定：

- seed
- hero seat
- dealer
- YCBK/config variant
- opponent profile
- source cluster

主要指标：

```text
hero_round_score_delta
bootstrap 95% CI
win rate
draw rate
score variance
```

晋级至少要求：

1. vs legacy：平均 score delta > 0；
2. CI 满足预声明正向门槛；
3. vs current champion 不发生预声明显著回归；
4. illegal action = 0；
5. hard-set 无关键类系统性退化。

训练 loss、ep_rew_mean、单次 win-rate 不能替代 paired gate。

### 17. Discard Hard Set

建立冻结的 discard regression set，至少覆盖：

- BC vs legacy disagreement；
- RL vs BC/legacy disagreement；
- high entropy / top2 close；
- 财神/YCBK；
- 杠后；
- 白飘/爆头附近；
- 高倍风险；
- 墙尾；
- 多张同向听 discard；
- replay 中真实异常/争议状态。

每个候选都输出：

```text
action agreement
policy KL to BC
policy KL to champion
value error
top-k actions
illegal count
```

### 18. v2 Oracle Guiding

只有 Champion-v1 通过 full gate 后，Oracle 才能进入默认 roadmap。

Oracle Gen 从 Champion-v1 初始化。

推荐 profile：

```text
0-200k:      oracle_keep=1.0
200k-500k:   1.0 -> 0
500k+:       oracle_keep=0, LR *= 0.1
```

Oracle dropout 应支持 group-wise dropout：

- opponent A hidden features
- opponent B
- opponent C
- wall-related features

而非只支持“整局全部开/全部关”。

Oracle 退火完成后的最终候选必须在 `oracle_keep=0` 的 public-only 环境中通过 full gate。

### 19. v2 Belief/Value/Search 复用已有 change

本 change 不重新定义 `belief-v2` / `information-set-search` 的内部算法。

集成顺序：

```text
Champion-v1
 -> Oracle-guided public policy
 -> belief/search policy-value teacher
 -> hard-state search dataset
 -> distill
 -> RL refinement
 -> search again
 -> Champion-v2
```

Search teacher 默认只处理：

- high entropy；
- top2 close；
- hard-set；
- regret/active-sampling 挑出的状态。

不要求每个在线弃牌都运行 search。

### 20. v2 Value multi-task

在不改变主要 value contract 的前提下，可增加辅助 heads：

```text
P(win)
P(deal_in)
P(draw)
expected_score
```

这些辅助目标只作为 representation learning，不得替代 promotion 的真实 paired score。

### 21. Reaction RL 解锁顺序

reaction RL 必须按 capability 阶梯进行：

```text
v1: discard only
v2.1: + PONG/PASS
v2.2: + CHOW/PASS
v2.3: + remaining reactions
```

每次解锁都必须单独：

- 定义 action subset；
- 建立 legacy anchor；
- 建 hard-set；
- paired gate；
- rollback target。

HU 默认继续规则/legacy safeguard，除非未来 change 明确证明由学习策略接管更安全、更强。

### 22. Checkpoint / Manifest

Checkpoint 除模型权重外必须保存或旁挂 manifest：

```text
schema
git_commit
generation
model_arch
feature_contract
value_contract
action_scope
bc_anchor_fingerprint
optimizer state
scheduler state
global_discard_decisions
ppo_update_index
entropy_controller_state
rng states
opponent_pool_fingerprint
dataset/rollout fingerprints
```

resume 必须验证关键 fingerprint；不兼容时拒绝继续训练，不允许“尽量加载”。

## Failure / Fallback

| Failure | Behavior |
| --- | --- |
| BC shard fingerprint 不一致 | 拒绝训练 |
| rollout policy version stale | 拒绝 merge |
| worker crash | lease 过期后 retry，同 job_id |
| SMB publish 失败 | 保留本地 stage，不标 success |
| RL action 非法 | 视为训练/运行错误；线上走明确 legacy fallback 并计数 |
| KL/entropy/value 爆炸 | 停止当前 candidate，回滚上一个 champion |
| paired full gate 不通过 | candidate 不晋级，可继续作为 experiment |
| Oracle public-only gate 失败 | 保留 Champion-v1，不上线 Oracle candidate |
| belief/search profile 未过自身 gate | v2 回退到 network-only Champion |

## Rollout Plan

### Phase A: Training substrate
- streaming BC；
- unified value contract；
- full checkpoint/resume；
- shared manifest/fingerprint。

### Phase B: Hybrid + DAgger
- HybridPolicy；
- MahjongDiscardEnv；
- 30k BC + 10k DAgger；
- freeze BC-v1。

### Phase C: Single-machine custom PPO
- 在 PC-A 上先跑 50k~100k discard decisions；
- 验证 exact BC init、BC-KL、dynamic entropy、resume；
- paired smoke 不退化。

### Phase D: Two-machine synchronous RL
- 接入 distributed job store；
- PC-B rollout；
- strict version-lock；
- Gen0/1/2。

### Phase E: Champion-v1
- hard-set；
- 4096 paired full gate；
- freeze promotion manifest。

### Phase F: v2
- Oracle Guiding；
- belief/search/distill 集成；
- RL/Search iteration；
- reaction capability unlock。

## Acceptance Summary

Champion-v1 至少满足：

- BC-v1 provenance 完整；
- 双机 rollout 与单机相同语义；
- stale rollout 100% 拒绝；
- illegal action = 0；
- full paired gate 相对 legacy 正向；
- current champion robustness 不回归；
- Windows 两机 crash/restart 可恢复；
- rollback 一键可用。

Champion-v2 额外满足：

- 最终 public-only；
- belief/search 自身 gate 通过；
- search/distillation 相对 Champion-v1 有独立 paired 改善；
- Oracle 不成为线上隐藏信息依赖；
- reaction unlock 若启用，逐能力单独验收。
