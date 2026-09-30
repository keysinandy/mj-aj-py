# Mini-Suphx 训练计划 v2

> 状态：2026-09-24 当前执行版（2026-09-30 内核恢复后修订）
> 适用基线：main@32e3c4b2 及后续兼容提交
> 原则：单机是完整训练系统，双机只是横向扩容；BigHandIntent 只做 shadow/hard-state，不作为默认在线策略。

> 内核契约（2026-09-30）：训练机 Rust 内核必须为源码要求的
> `rust-weighted-two-ply-v5`（`mj/shanten.py` 的
> `WEIGHTED_TWO_PLY_KERNEL_REQUIRED`）。生成前未达到兼容即 fail-loud
> （`scripts/search_teacher_generate.py` 的 `require_compatible_kernel`
> 门禁，`--allow-degraded-kernel` 仅作 smoke/parity 显式逃逸）。每次生成
> 的 manifest 记录 `git_commit` 与**实际加载内核**（版本/兼容性/degraded/
> 爆头飘牌算子）。对外 v1 fallback 只是旧轮子的误加载现象，不是合法 teacher 行为。

## 1. 当前结论

当前生产/teacher 基线：

```text
ordinary discard     -> legacyV2
reaction / KONG      -> legacy reaction-v2
BigHandIntent        -> production default OFF
plus_one challenger  -> OFF
```

BigHandIntent Phase A 在 4,096 paired 中未证明积分提升，四人局耗时门槛失败；
Phase B 修复 frontier 容量后几乎没有 challenger，实际 override 为 0。因此后续不再
继续把“大牌能力”堆进 legacyV2 热路径。

训练的长期目标改为：

```text
stable legacy teacher
  + public BC anchor
  + DAgger distribution repair
  + selective search correction
  + terminal-score league RL
  = Champion-v1

Champion-v1
  + Oracle / belief / search / distillation
  + reaction capability ladder
  = Champion-v2
```

### 1.1 评估基线与内核 v5 对账（2026-09-30）

先前多数 paired/分数结论是在 **Rust v3 轮子误加载（LegacyV2 事务性回退 v1）** 下跑出的，
并不代表 v5 完整 weighted 行为。内核恢复 v3→v5 后，**旧评估基线不再可信**，进入训练前必须：

```text
1. 恢复 .venv 内核为 v5（pip/maturin 重建 mj_kernels），kernel_runtime_diagnostic()
   确认 weighted_kernel_compatible=True、degraded=False、爆头飘牌算子=rust。
2. 用 v5 重跑评估，重建基线（BC 胜率/均分、legacy 对照、GUARD 样例准入等）。
3. 校验 tests：test_shanten 的 required 断言与 v5 对齐；test_shape_guard 默认在线
   weighted profile 的 GUARD_SEED 准入语义须裁定后再冻结基线。
4. 小批量（少量 games/引用集）验收合法动作、搜索回退、manifest 指纹与吞吐，通过后
   再重新生成正式数据、重跑配对评估，最后进入 BC/后续训练。
```

评估口径：96 局噪声约 ±4%，关键结论以 192 局 `fair_match(n=192)` 为准；BC 基线
（旧 v3 降级期）为胜率 21.9%/均分 -0.98，**v5 须重新测定，禁用旧数字对齐新训练**。

## 2. 训练契约先收口

### 2.1 Public / Oracle 特征必须分离

当前 custom PPO 与 `MahjongDiscardEnv` 实际使用 public 75 planes，而旧 manifest
名称仍带 91-plane oracle 含义。正式 campaign 前冻结：

```text
public-v1:
  planes = 75
  scalars = 8
  use = BC-v1 / DAgger / PPO / runtime

oracle-v1:
  planes = 91 (75 public + 16 oracle)
  scalars = 8
  use = Champion-v1 后的 Oracle Guiding

big-hand-shadow-v1:
  runtime tensor = none
  use = metadata / hard-state / evaluation
```

BC-v1 与 PPO 必须使用同一个 public-v1 网络，不再通过“BC 91 → PPO 75”的隐式迁移
来声称同一 contract。

### 2.2 其他冻结契约

```text
model          = ResNet 6x128
policy head    = 109 logits
action scope   = discard-only-v1
value contract = round-score-v2-normalized
reward         = normalized terminal hero round score
```

## 3. Teacher 分层

### T0：Legacy teacher（唯一）

```text
legacyV2-offline
BigHandIntent disabled
required search complete / fail-loud
required weighted kernel = rust-weighted-two-ply-v5（兼容，degraded=False）
```

用途：大规模 BC 与 DAgger **唯一** label 来源。

任何 offline weighted label 无法完成时丢弃/报错，不允许偷偷 fallback 成低质量 label。

teacher 口径收口（2026-09-30）：

- **不新建**与线上 shape-aware 配置对应的独立 offline teacher 名。`legacyV2-offline`
  是唯一 T0；线上 shape-aware legacyV2（提交 2968b49 起为默认推理口径）是**推理配置**，
  不与训练 label 口径混称。在线配置只作推理兼容，不作训练标签或发布证据。
- 训练用 policy-source 仍取 `heuristic:shape-v2` 等冻结启发式轨迹源，但标签口径统一
  归到 `legacyV2-offline`，预算与标签口径不含混。
- **新旧 shard 不混用**：并入同一 dataset 的所有 shard 必须来自同一生成 manifest
  （`git_commit`、实际内核版本、teacher_budget/search/belief/population 指纹一致）。
  生成门禁默认拒绝 degraded 内核，故 v5 恢复后生成的 shard 不得与 v3 降级期产物混放；
  `search_dataset_merge` 拒绝 fingerprint 冲突。

### T1：Shadow teacher metadata

来自 BigHandIntent / legacy diagnostics：

```text
chiitoi_shanten
pair_units
natural_pairs
luxury_groups
luxury_upgrade_live
wild_count
WHITE_RICH
intent_strength
live_wall
opponent_melds
```

它只负责发现值得额外研究的状态，不决定 expert action。

### T2：Selective search teacher

只处理：

```text
legacy vs BC disagreement
RL vs BC/legacy disagreement
high entropy
top2 close
BigHand STRONG
baotou / piao near
wall-tail / high-value
platform real hard states
```

不做全状态 search。

## 4. 数据来源

| 数据源 | 状态分布 | 动作标签 | 主要用途 |
| --- | --- | --- | --- |
| Legacy BC | legacy self-play | T0 | 基础 BC |
| DAgger | learned rollout | T0 | 修复 distribution shift |
| Platform replay | 真实平台状态 | T0/T2 relabel | 真实状态分布 |
| Hard/Search | 困难状态 | T2 | 超越 legacy |

平台日志不是天然 expert。正确链路：

```text
platform log
 -> replay public state
 -> offline relabel
 -> shard
```

## 5. BC-v1

### 5.1 数据量

```text
Legacy BC 30k games
 -> BC0

DAgger D1 3k   executor legacy/learned = 70/30
 -> BC1
DAgger D2 3k   = 40/60
 -> BC2
DAgger D3 4k   = 10/90
 -> BC-v1
```

DAgger 无论谁执行动作，普通弃牌 label 都来自 T0。

### 5.2 数据加载

正式 campaign 前必须完成：

```text
ShardStreamingDataset
DataLoader
AMP
pin_memory
num_workers
strict checkpoint resume
dataset/teacher fingerprint validation
```

禁止再把正式大语料在训练前全部 `np.concatenate` 到内存。

### 5.3 BC-v1 的职责

BC-v1 是稳定 prior，不追求无限 imitation accuracy。冻结主要看：

```text
illegal = 0
no final-test leakage
paired vs legacy 不显著退化
value calibration 稳定
hard-set 无系统性错误
BigHand strata 无灾难桶
```

## 6. BigHandIntent 的训练角色

默认不把 BigHandIntent 加进 runtime 输入。

先用它做：

1. shard shadow metadata；
2. hard-state mining；
3. active sampling；
4. promotion 分桶。

可选消融：

```text
E0: policy + value
E1: policy + value
    + chiitoi_shanten auxiliary
    + luxury_live auxiliary
    + white_rich auxiliary
```

auxiliary head 只做 representation learning；若 paired score / hard-set regret
无改善就删除，不进入线上模型契约。

## 7. Selective Search Correction

BC-v1 冻结后，不立刻全量 RL。先从 hard-state pool 做少量 search correction：

```text
BC-v1 / legacy / BigHand / platform
        ↓
hard-state miner
        ↓
search teacher
        ↓
correction dataset
        ↓
BC-v1.1 candidate
```

只有 search teacher 提供同单位长期价值证据时，才纠正 legacy action。

## 8. PPO 主线

### 8.1 Reward

正式 Champion campaign：

```text
shape_k = 0
reward = terminal round-score-v2-normalized
```

shanten shaping 只保留 smoke/debug，不参与正式 promotion run。

### 8.2 PPO 参数

```text
rollout/update  = 16k~32k discard decisions
batch           = 1024
epochs          = 4
clip            = 0.15
gamma           = 1.0
gae_lambda      = 0.95
actor_lr        = 3e-5
critic_lr       = 1e-4
max_grad_norm   = 0.5
```

BC Prior KL：

```text
0..100k       lambda=1.0
100k..300k    1.0 -> 0.3
300k..600k    0.3 -> 0.1
600k+         0.05~0.1
```

持续监控：

```text
KL(live || BC)
KL(live || champion)
normalized entropy
value explained variance
clip fraction
illegal
```

## 9. Opponent League

```text
Gen0  0..50k
  legacy 100%
  # 只做系统 smoke

Gen1  50k..300k
  legacy 60%
  BC-v1 20%
  historical RL 20%

Gen2  300k..700k
  legacy 40%
  BC-v1 20%
  RL league 40%

Gen3  700k..1.2M
  legacy 25%
  BC-v1 15%
  RL league 60%
```

永久保持：

```text
legacy >= 20%
BC-v1 >= 10%
```

RL checkpoint 只有通过 manifest/illegal/basic paired smoke 才能进入 league。

## 10. 单机 / 双机统一架构

### 10.1 核心原则

```text
single-machine = 完整系统
dual-machine   = single-machine + remote workers
```

训练算法、checkpoint、seed、promotion gate 不因机器数量改变。

### 10.2 三个运行 profile

#### local-smoke

```text
actors = 1~2
rollout = 2k~4k
paired = 128/256
```

用于开发和回归。

#### single-machine

同一台机器运行：

```text
coordinator
learner
rollout actors
BC/DAgger workers
evaluator
artifact store
```

正式 PPO：

```text
rollout/update = 16k~32k
```

#### dual-machine

```text
PC-A
  coordinator
  learner
  merge
  checkpoint
  promotion
  optional local actors

PC-B
  additional rollout actors
  BC/DAgger generation
  paired evaluation
  hard-state/search jobs
```

只增加吞吐，不改变训练身份。

## 11. 当前硬件分工

### PC-A

```text
GTX 2060
i5-9400F
48 GB RAM
```

推荐：

```text
CUDA learner
coordinator
merge/promotion
2/4/5 actor 档 benchmark 后选择
```

不要按“CPU 占满”为目标；需要保留资源给 learner 数据准备、merge 和系统。

### PC-B

```text
RX 6600 8GB
i5-13400F
64 GB RAM
```

推荐：

```text
主要 rollout
BC/DAgger generation
paired evaluation
search teacher
hard-state mining
```

v1 默认 `gpu_training=false`。RX6600 只有在 DirectML/ONNX parity +
end-to-end throughput 明确更好时，才作为 inference accelerator。

## 12. 单机 PPO 调度

优先使用同步交替式：

```text
freeze policy_N
 -> collect rollout
 -> validate/merge
 -> pause formal collection
 -> PPO update on GPU
 -> publish policy_N+1
 -> next rollout
```

允许 evaluation/preprocessing 与 learner 重叠，但禁止 policy_N+1 发布前用旧 policy
提前采集下一正式 on-policy update。

## 13. Promotion

每 50k~100k decisions 生成 candidate：

```text
512 paired smoke
 -> 1024 fast
 -> 4096 full
```

固定：

```text
seed
hero seat
dealer
config/YCBK profile
opponent pool
source cluster
```

晋级真值：

```text
hero_round_score_delta
bootstrap 95% CI
illegal=0
hard-set no critical regression
```

BigHand 分桶：

```text
ordinary
CHIITOI opportunity
LUXURY_CHIITOI opportunity
WHITE_RICH
baotou/piao-near
```

七对率、豪华率、爆头率、财飘率只作解释，不作优化目标。

## 14. 完整 roadmap

```text
P0 Contract Repair
   public75 / oracle91 / shadow contracts

P1 Training Substrate
   streaming BC / resume / manifest

P2 Base BC
   30k -> BC0

P3 DAgger
   3k + 3k + 4k -> BC-v1

P4 Hard-State Mining
   disagreement / entropy / BigHand / platform

P5 Selective Search Correction
   -> BC-v1.1 candidate

P6 PPO Smoke
   0..50k, legacy only, shape_k=0

P7 League RL
   50k..300k
   300k..700k
   700k..1.2M

P8 Champion-v1
   4096 paired full gate

P9 Search / Distill / RL Iteration

P10 Champion-v2

P11 Reaction RL
   PONG/PASS
   -> CHOW/PASS
   -> remaining reactions
```

## 15. 当前执行优先级

下一步按下面顺序，不跳阶段：

```text
0. 恢复训练机内核为 v5，确认 weighted compatible、degraded=False（生成门禁已内置，
   旧 v3 shard 一律作废）
1. 修 public75 / oracle91 feature contract
2. 完成 streaming BC
3. 冻结 legacyV2-offline teacher fingerprint（v5 口径）
4. BigHand shadow metadata 接入 shard
5. 单机跑通 BC/DAgger/PPO/paired/resume 全闭环
6. 30k BC
7. DAgger 3k/3k/4k
8. 冻结 BC-v1
9. 50k PPO smoke
10. 单机 league
11. 加 PC-B 作为远程 worker
12. 4096 paired promotion
```

暂时不做：

```text
重新默认开启 BigHand Phase A
放宽 +1 shanten override
把 BigHandIntent 塞进线上 feature tensor
完整 109-action RL
全状态 search
大规模 Oracle
```
