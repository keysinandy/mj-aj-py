# Belief/search/policy acceptance definition

本 change 中的“近似最佳”只表示：在冻结的规则、公开 information
history、belief profile、opponent policy、leaf evaluator 和 search profile
下，候选动作相对 high-budget information-set search reference 的
`Q_ref(best_ref) - Q_ref(candidate)` regret 不回归，并在独立 paired
round-score split 上满足预声明置信区间门槛。

有限 simulation、有限粒子、有限 value model 或有限 paired games 的结果
不得描述为真实墙最佳、绝对最优、无模型误差或精确博弈论最优。所有报告
必须独立标出 self-play、population opponent、legacy/shape-v1 opponent，
并记录 belief/search/policy/value/runtime fingerprints。
