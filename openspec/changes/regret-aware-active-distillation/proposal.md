# Proposal: Regret-aware Active Distillation

## Why

Gen0 的降规模闭环证明了 teacher/reference/训练/配对链路可用，但训练目标与算力使用仍不理想：

1. **Teacher 算力利用率低**：大量搜索预算花在 easy state（低不确定、无分歧）。
2. **目标偏“模仿 Teacher”**：soft visit CE 不区分“几乎同分选错”与“灾难性选错”，与 reference regret 不完全一致。
3. **DAgger 新数据重复学习 easy case 且遗忘历史能力**：没有 replay 与 generation 配比。
4. **反馈周期长**：每个 checkpoint 直接跑完整 paired，方向错误的模型浪费大量算力。

本轮保持最终部署形态不变（**Search Teacher 训练期产出监督 → PolicyNet 学习 → 线上只部署 PolicyNet**），只优化训练闭环。

## Goal

同一 Teacher 预算下同时做到：

- mean reference regret ↓、p95/tail regret ↓、catastrophic action ↓；
- special state 不退化；
- 更快的 `pi_k → data → pi_{k+1}` 迭代（active sampling + teacher cache + 分级 gate）；
- 最终仍通过既有 paired-score 与 runtime gate。

## Non-Goals

- 不改网络结构（不加 Transformer / 不放大网络）。
- 不启用 value head（Phase 1 继续 policy-only）。
- 不同时引入大量 auxiliary loss（先 policy + ranking 两个实验）。
- 不放松既有 full paired / runtime 发布标准。

## Capabilities

### New

- `active-teacher-sampling`: candidate pool、可配置比例采样、state hash、teacher cache、自适应 budget。
- `regret-aware-policy-loss`: 保留 soft KL policy loss；新增 pairwise ranking loss；sample weight（teacher confidence × policy error × importance，clip）。
- `generation-replay`: recent/historical/hard/special 四桶 replay，generation quota 与 reservoir sampling。
- `hard-state-regression`: 永久 hard regression set、去重、每 checkpoint 对比与回归告警。
- `staged-evaluation-gates`: Offline → Hard-set → Fast paired(256–512) → Full paired(4096) → Runtime 五级门。

### Modified

- `search-teacher-dataset`: schema 增 `state_id`/student policy 字段/`policy_regret`/`state_source`/`sample_weight`；teacher cache；confidence 驱动的预算自适应。
- `search-bc-training`: loss profile 增 ranking/catastrophic 权重与权重函数契约。

## Impact

预计新增：

- `mj/training/active_sampling.py`
- `mj/training/teacher_cache.py`
- `mj/training/replay_buffer.py`
- `mj/training/hard_states.py`
- `mj/training/ranking_loss.py`（或并入 policy_value_train）
- `scripts/search_distill_gates.py`
- `scripts/search_candidate_pool.py`（或并入生成器）

预计修改：

- `mj/training/search_data.py`（schema）
- `mj/training/search_bc_train.py`（loss/权重）
- `mj/training/teacher_generate.py`（采样/缓存接入）
- `docs/search-distillation.md`、PROGRESS.md

实验顺序（每步只改一个变量，见 design §8）：
E0 baseline → E1 active sampling → E2 ranking loss → E3 组合 → E4 replay → E5 hard-set → E6 其他。
