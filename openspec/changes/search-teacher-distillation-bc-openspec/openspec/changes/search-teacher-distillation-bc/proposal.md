# Proposal: Search Teacher Distillation BC

## Why

当前分支已经具备 `belief-v2`、`search-v1`、`SearchSample`、`PolicyValueNet`、`PolicyIterationRunner` 和 `policy-v3` 低延迟运行时，但“最强离线策略”与“线上 BC”之间还没有一条完整、可复现、以最终积分为目标的蒸馏流水线。

旧 `mj.bc_data` / `mj.bc_train` 面向 deterministic evaluator：每个状态保存单一 teacher action，训练目标以 one-hot masked CE 为主，并以 validation top-1 accuracy 选 checkpoint。该路径无法充分利用 search 已经产生的 `visit_counts`、`q_by_action`、`root_value`、uncertainty、belief/history 特征，也无法把“选错动作损失多少积分”纳入选模。

本 change 建立 Search Teacher Distillation BC：允许离线使用高预算 information-set search 作为最强 Teacher，把昂贵 search 能力压缩到一个 batch=1、legal-mask-safe、毫秒级的 PolicyNet；并通过 DAgger / policy iteration 持续收集 student 实际会访问的状态，逐代降低 high-budget reference regret 和提高独立 paired round score。

## Goal

主要目标：

1. 训练目标直接服务于“最大化 hero round score”，而不是单纯模仿动作。
2. Teacher 使用 `belief-v2 + search-v1 + frozen opponent population/continuation`，且不读取真实对手暗牌、真实墙顺序或未来事件。
3. BC 输入表达 Teacher 使用的 information state：hero private hand + public context/history + versioned belief summary。
4. Policy 训练默认使用 search soft visit distribution；Q 值和 root value 作为独立、版本化的辅助监督，不把 near-tie 强制 one-hot。
5. checkpoint 主选择指标从 top-1 accuracy 改为 frozen high-budget search reference regret。
6. 通过 `pi_k -> search_k -> dataset_k -> pi_{k+1}` 进行至少两轮迭代，防止纯离线 imitation 的 distribution shift。
7. 最终线上策略只执行 feature extraction + PolicyNet forward + legal masked argmax；正常情况下不依赖在线 POMCP，异常才进入 shape-v2 -> legacy fallback。
8. 只有在 reference regret、paired score、population robustness、特殊规则、延迟/窗口安全全部通过后，才具备替代当前默认策略的条件。

## Non-Goals

本 change 不声明求得精确博弈论最优策略，不把真实 wall / hidden hands 作为训练输入，不要求在线运行高预算 search，不修改杭州麻将规则、动作空间或 `Game.legal_actions()` / `Game.step()` 权威语义。

本 change 不要求第一版同时训练高精度 ValueNet。若现有 tanh value head 与未归一化积分 target 的单位契约未完成修订，P0/P1 允许 `policy_weight=1, value_weight=0` 先交付 policy-only distillation；Value v2 单独进入后续 gate。

## Existing Baseline

基线固定为 `HEAD=8a94fdeb1a7801289f2bd707b24b271d99bb8961`。

依赖当前已有能力：

- `mj.search`: belief-root-sampling information-set search、8k/16k reference profile、q/visit/variance/regret 报告。
- `mj.training.search_data.SearchSample`: 109-action legal mask、visit counts、q_by_action、root value、source group、belief/search/opponent/leaf provenance。
- `mj.training.policy_value_train`: soft-policy loss、KL/agreement/value diagnostics。
- `mj.models.PolicyValueNet`: public information + belief summary feature contract。
- `mj.training.policy_iteration`: generation provenance、stopping/promotion gates。
- `mj.decision.PolicyV3Runtime`: low-latency model path 与 shape-v2 -> legacy fallback。

## Capabilities

### New

- `search-teacher-dataset`: 从冻结 trajectory/source-group 中生成高质量、自适应 simulation budget 的 SearchSample 数据。
- `search-bc-training`: 使用 soft visit policy / optional Q-soft targets 训练 BC，并按 search regret 选模。
- `iterative-distillation`: DAgger / policy iteration 数据收集与 promotion/rollback。
- `bc-runtime-release`: policy-only 在线运行、性能、安全、paired-score 和 population release gate。

### Modified

- `policy-value-training`: 明确 policy-only 第一阶段与 Value v2 单位契约。
- `policy-v3-runtime`: 通过 release gate 后允许低 confidence 不触发昂贵 fallback，但模型异常、manifest mismatch、非法/非有限输出仍安全降级。

## Impact

预计新增：

- `scripts/search_teacher_generate.py`
- `scripts/search_bc_train.py`
- `scripts/search_bc_eval.py`
- `mj/training/teacher_budget.py`
- `mj/training/regret_selection.py`

预计修改：

- `mj/training/search_data.py`
- `mj/training/policy_value_train.py`
- `mj/models/policy_value.py`（仅当启用 Value v2）
- `mj/training/policy_iteration.py`
- `mj/decision/policy_v3.py`
- Recorder / evaluation artifacts

旧 `mj.bc_data` / `mj.bc_train` 保持 legacy/heuristic BC 兼容，但不得作为本 change 的“最强 Search Teacher BC”发布证据。
