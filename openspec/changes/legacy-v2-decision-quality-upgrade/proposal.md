## Why

基于 2026-10-08 main 分支代码审查（仅以 Python/Rust 接口及测试代码为事实依据），legacyV2 的普通弃牌主要使用 shanten/ukeire/weighted two-ply/shape comparator，反应阶段由 v1 accepted gate + U2 veto 处理，HU/KONG 则局部使用积分 continuation。当前缺口是各动作窗口比较单位不一致、对手公开信息风险估计薄弱、未来有效进张未充分考虑对手先胡和己方实际摸牌机会、特殊胡牌路径的 delay_factor 二元化，以及反应候选过早被 v1 gate 排除。

本 change 规定以安全可回滚的增量方式提高长期平均净积分；不预设任何新能力必然增益，也不改动默认策略直到性能、正确性及配对积分门槛通过。

## What Changes

- Phase A: public-only OpponentBelief / DangerEstimator；从三家弃牌、副露、回合、活墙构造听牌风险和弃牌预期损失，先 shadow。
- Phase B: CompletionEstimator + 统一 Score EV；估计自己在终局前获得的摸牌机会和牌局完成概率，校准不同决策窗口的统一净积分价值。
- Phase C: opponent-aware bounded continuation、联合 CHI/PONG/KONG/PASS 决策，以及胡牌窗口 survival-weighted delay EV；不把“下一摸必胡”误当“必能摸到下一张”。
- Phase D: 全窗口 SearchBudgetController、可靠的 partial certificate、hand plan persistence（七对/豪华七对/普通快胡等路线）。
- 严格公共信息边界、profile flag/fingerprint、shadow diagnostics、native/Python parity 和单项回滚。
- 现有 weighted two-ply、Rust frontiers、shape/marginal guard、既有 V1 fallback 保留为基线。

## Existing Code Anchors

- `mj/bot.py::choose_discard` / `_feed_risk` / `_hu_window_candidate` / `_choose_hu_window_action` / `choose_action`
- `mj/legacy_eval.py::LegacyTwoPlyProfile` / `_weighted_evaluation` / `_weighted_root_key` / `evaluate_standing_frontier`
- `mj/legacy_react.py::LegacyReactionProfile` / `choose_reaction_v2`
- `mj/legacy_kong.py::public_score_continuation`
- Reuse candidates: `mj/decision/context.py::PublicDecisionContext`, `mj/models/opponent_policy.py`, `mj/decision/score_value.py`, `mj/big_hand_intent.py`
- `mj/strategy_runtime.py` diagnostic snapshot and decision audit

## Capabilities

### New
- legacy-v2-opponent-belief-and-risk
- legacy-v2-unified-score-value
- legacy-v2-completion-and-survival
- legacy-v2-joint-reaction
- legacy-v2-adaptive-search-and-hand-plan

### Modified
- legacy-two-ply-weighted-frontier
- legacy-reaction-lookahead
- legacy-hu-window-arbitration
- bot-decision-explanations

## Invariants and rollout

1. 任何在线决策不得读取对手暗牌、真实牌墙顺序、未来随机状态；从 `PublicDecisionContext` 派生，复盘中隐藏信息变化不得改变相同公开状态下的评分。
2. 首发所有新开关关闭；shadow 不改变行动；逐阶段 release，不允许多个变量一起提升默认。
3. 危险估计缺失/无效、模型超时、候选阶段不一致、不安全 partial 时使用既有 legacyV2 顺序且记录 reason；不得混合不可比的价值单位。
4. 分数计算遵循项目现有 settle/scoring，正确处理多人胡牌和特殊玩法。任何估计概率都需要 calibration，不得声明确定。
5. root cap <=3 / existing online 50ms discard hard budget 作为第一轮约束；非弃牌窗口分别冻结现有预算。跨窗口调度必须显式证明不会超出端到端 SLA。
6. 单独保存 baseline SHA、配置 fingerprint、代码版本、seed、牌局对及性能样本。完整对局结果按相同初始条件配对，不能假设分叉后摸牌顺序仍一致。

## Out of Scope

- 不实施无界三层搜索/MCTS 或完全可见隐藏世界推理。
- 不直接开启 big_hand_enabled、speed_band、Pareto。
- 不把本变更中的定性优先级误作实测积分收益。
- 本 change 是设计规格，不包含策略代码实现和已通过验收的声称。
