## ADDED Requirements

### Requirement: Policy-v3 默认在线路径为低延迟网络策略而非完整 POMCP

线上默认候选 SHALL 使用 PolicyValueNet（可选 bounded belief summary）生成 action logits，经当前 `legal_actions()` mask 后选择动作。完整高预算 information-set search MUST 保持离线；任何在线 shallow-search 必须使用独立 profile、独立性能证据和显式 opt-in。

#### Scenario: 线上未启用 search profile
- **WHEN** strategy=`policy-v3` 且未指定 shallow-search
- **THEN** 单次决策不得启动 rollout/POMCP worker，只执行 bounded feature/belief/network/fallback 路径

### Requirement: Legal mask 是最终动作授权边界

网络/search 建议动作只有在当前引擎/镜像 legal set 中才可提交。输出非法动作、NaN、空分布或 model failure 时 MUST 不发送该建议，转入声明的 shape-v2/legacy fallback 并记录原因；不得随机挑一个合法动作掩盖错误。

#### Scenario: 网络 top-1 不合法
- **WHEN** policy head 最大 logit 对应动作不在 legal mask
- **THEN** masked distribution 不允许该动作；若所有合法 logits 非有限，则触发 model failure fallback

### Requirement: 在线 BeliefState 必须支持缺失历史和重连安全降级

在线 belief SHALL 能处理进程启动、FULL snapshot、SSE gap、重连和跨局 reset。无法获得完整公开行为历史时 MUST 标记 `history_incomplete/belief_degraded`，使用 uniform/public-only 初始化或直接绕过 belief feature；不得从 server placeholder/隐藏回放补足。

#### Scenario: 中途加入牌局只有当前 FULL snapshot
- **WHEN** 缺少此前 PASS/响应事件
- **THEN** belief 不宣称行为后验完整，使用降级 profile，并把状态暴露给 confidence/fallback gate

### Requirement: 在线策略保留可解释的 fallback hierarchy

Policy-v3 profile SHALL 明确 fallback 顺序，例如 `policy-v3 -> shape-v2 -> legacy`。每次 fallback MUST 记录 model/belief/search level、原因、耗时和实际 selected action。fallback 路径 MUST 继续满足当前规则合法性和窗口要求。

#### Scenario: belief posterior reset 且模型低置信
- **WHEN** 决策同时发生 posterior reset 并触发 network confidence gate
- **THEN** 按 profile 走 shape-v2/legacy，不强制使用不稳定 policy-v3 建议

### Requirement: 在线日志不得泄露 particle hidden material

Recorder/logview SHALL 记录 history_hash、belief/profile/model/search fingerprints、ESS/entropy、marginal summaries、network confidence、actual/suggested/fallback actions。MUST NOT 在可发布线上日志写入完整 sampled opponent hands 或 wall order。

#### Scenario: 开启 debug explanation
- **WHEN** policy-v3 输出详细 explanation
- **THEN** explanation 可显示“某牌在下家手中概率”等 posterior marginal，不显示任一完整 particle 世界

### Requirement: 发布前通过完整调度性能和逐窗正确性闸门

Policy-v3 默认切换前 SHALL 在真实调度方式下验证单局/十场并发的 p50/p95/p99/max、fallback rate、illegal count、NaN/error、决策窗口 deadline 和 evaluator-caused window loss。仅模型 forward microbenchmark 达标 MUST NOT 视为线上性能通过。

#### Scenario: network 很快但 belief/history 维护拖慢窗口
- **WHEN** batch=1 model forward 低于目标延迟，但十场并发完整决策 p95 超过窗口门槛
- **THEN** 发布 gate 失败，保持旧默认策略并优化完整路径

### Requirement: Shallow-search 只能作为独立可关闭增强层

若实现线上 16/32/64 simulation shallow-search，它 SHALL 使用 bounded particle/search budget、独立 profile/fingerprint、严格 deadline cancellation 和 policy-only fallback。其开启不得改变 policy-only profile 的证据或默认行为。

#### Scenario: shallow-search 在 deadline 前未完成
- **WHEN** 搜索预算未完成且剩余窗口低于安全阈值
- **THEN** 取消/忽略未完成 search，提交已通过 legal mask 的 policy-only/fallback 动作，并记录 `search_deadline_fallback`
