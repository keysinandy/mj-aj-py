# Belief / information-set search / policy iteration

本 change 增加了可审计的公开事件历史、belief-v2 粒子后验、
search-v1 root-sampling 信息集搜索、policy/value 蒸馏数据契约以及
policy-v3 低延迟运行时。

## 运行时边界

- Game.legal_actions() 与 Game.step() 仍是唯一动作授权和状态推进来源。
- belief/search 的 fingerprint、规则版本、对手策略和 leaf evaluator 会进入
  profile 或 artifact provenance。
- policy-v3 只有显式 opt-in 才启用；默认线上策略仍是 legacy。
- policy-v3 不默认启动 POMCP。模型异常、非法/非有限输出或低置信度按
  shape-v2 -> legacy 降级，并把建议动作、实际动作、原因和耗时写入
  Recorder 的 additive decision fields。
- Recorder/logview 只保存公开摘要和 posterior marginals，不保存 sampled
  opponent hands 或 wall order。

## 本地接口

    from mj.decision import PolicyV3Runtime
    from mj.search import search_game
    from mj.training import SearchSample, ValueDataset

policy-v3 checkpoint 必须带有 policy-value-model-manifest-v1；没有 manifest、
未校准或 oracle artifact 会安全降级。ValueNet leaf 也会在 artifact/profile
不匹配时退回 terminal-rollout-v1。

## 验收状态

冻结基线、契约和验收定义位于
openspec/changes/belief-search-policy-iteration/artifacts/。8k/16k reference、
belief calibration、4096 paired games 和真实平台逐窗门禁仍是独立 evidence，
未完成前不得把有限 search/model 结果称为真实墙最佳或绝对最优，也不得切换
默认线上策略。
