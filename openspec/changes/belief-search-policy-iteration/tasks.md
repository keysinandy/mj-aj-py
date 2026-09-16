## 1. P0 基线、目标与版本契约

- [ ] 1.1 冻结当前 `f625550` 及其 rules/profile/kernel/reward-envelope/paired-racing 指纹，记录本 change 的依赖基线和未完成线上 gate。
- [ ] 1.2 定义 `InformationHistory`、`PublicEvent`、`BeliefProfile`、`SearchProfile`、`PolicyValueModelManifest` schema 与 fingerprint；未知字段拒绝或显式兼容，不静默忽略排序/策略相关字段。
- [ ] 1.3 冻结 train/validation/final-test source groups、self-play 与 population opponent splits、seat/dealer/YCBK 分组、随机种子和禁止复用规则。
- [ ] 1.4 建立“近似最佳”的统一验收定义：high-budget information-set search regret + paired round score；文档中禁止把有限模型结果描述为绝对/真实墙最优。

## 2. P1 公开事件历史

- [ ] 2.1 新增 append-only `PublicEvent`/`InformationHistory`，覆盖 discard、pass、chow、pong、open/closed/add kong、hu、round boundary 和公开 draw transition。
- [ ] 2.2 从 `Game` 自博弈生成事件历史，并与当前 `PublicDecisionContext` 的 river/meld/chain/freeze/response cursor 做状态重放对拍。
- [ ] 2.3 从平台 Recorder/SSE 日志构建同语义事件；缺失响应顺序或不可靠历史必须标 `history_incomplete`，不得补猜。
- [ ] 2.4 实现顺序敏感 `history_hash`；验证 gid/seq/日志格式字段不改变语义 hash，而事件顺序/动作改变必须改变 hash。
- [ ] 2.5 增加隐藏信息不变性测试：改变原 Game 的其他玩家暗手和真实墙，不改变相同 public history 的 hash/features。

## 3. P2 Belief-v2 粒子后验

- [ ] 3.1 在现有 uniform unseen sampler 上实现 `BeliefState` 与加权 `Particle`，初始化 N 个合法 hidden worlds 并保持物料守恒。
- [ ] 3.2 实现 `ActorPolicy.distribution/probability/choose` 接口，以及基于 shape-v2/shape-v1 分值的首版 softmax likelihood policy。
- [ ] 3.3 对 DISCARD/PASS/CHOW/PONG/KONG/HU 公开事件实现 likelihood 更新；particle 下观察动作非法时权重严格为零。
- [ ] 3.4 实现 epsilon smoothing、log-weight 累加、log-sum-exp 归一化，避免浮点下溢；epsilon 不得复活规则非法 particle。
- [ ] 3.5 实现 ESS、deterministic systematic resampling、posterior reset 和相应 reason/provenance；同 seed/history/profile 必须可复现。
- [ ] 3.6 输出 opponent-hand/live-wall/dead-wall marginals、ESS/entropy/reset/resample 统计；默认 artifact 不写 sampled hidden hands/wall。
- [ ] 3.7 在模拟器 held-out worlds 上完成 belief calibration：Brier/log-loss/coverage 与 uniform unseen 基线比较；无改善不得进入 search 默认 profile。
- [ ] 3.8 可选实现 public-constraint rejuvenation kernel，并单独版本化/验收；首版 search 不以该任务完成为前置。

## 4. P3 Search-v1 信息集搜索

- [ ] 4.1 新增 root-sampling search simulator：每次 simulation 从 BeliefState 按权重采样一个 world，根 legal set 与 context 完全一致。
- [ ] 4.2 仅为 hero 决策建立 tree node；实现 `HeroInfoNodeKey = history_hash + hero private/public semantic state`，禁止 world fingerprint/他家暗手/真实 wall 进入 key。
- [ ] 4.3 实现 opponent/chance rollout：对手只看 actor view 并从 ActorPolicy 分布采样，chance draw 使用该 simulation 的 sampled world。
- [ ] 4.4 实现 PUCT statistics `N/W/Q/P`、稳定 tie-break、全合法 root action 扩展，以及 discard/HU/财飘/KONG/reaction 全动作支持。
- [ ] 4.5 实现 terminal reward=`Game.scores[hero]`、最大深度、最大 simulation、wall-clock 安全预算和搜索报告；异常 simulation 不以零分伪装。
- [ ] 4.6 首版 leaf 使用 `terminal-rollout-v1`；验证 search 不递归调用自身形成无界嵌套。
- [ ] 4.7 增加 determinization leakage 测试：同 public history 下替换 sampled hidden world 不得直接改变 node key/prior，只能通过 simulation return 影响统计。
- [ ] 4.8 建立 8k/16k simulation high-budget frozen reference，并报告低预算 search 的 action agreement、mean/p95 regret、seed variance 和 ambiguous rate。

## 5. P4 ValueNet 长期价值

- [ ] 5.1 定义 value feature contract：hero hand、public context/history、可选 belief marginals/ESS、规则 scalars；强制 `oracle=False`。
- [ ] 5.2 生成 value dataset：从冻结 search/rollout contexts 保存 root/leaf information state 与完整 terminal reward，不保存 sampled hidden identity。
- [ ] 5.3 实现 `PolicyValueNet` value head 或独立 ValueNet；训练/验证 split 严格按 source group 隔离。
- [ ] 5.4 报告 MAE/RMSE、score bucket calibration、sign/ranking diagnostics，并与 `tail=zero`、Fast EV feature baseline 比较。
- [ ] 5.5 新增 `value-net-v1` 与 `hybrid-v1` leaf evaluator；artifact/profile 指纹不匹配或校准未过时自动禁用，回退 terminal rollout。
- [ ] 5.6 重跑 high-budget search reference 与低预算 search，确认 value leaf 在相同计算预算下降低 regret 或提高有效 simulations/s。

## 6. P5 Search 蒸馏与 PolicyValueNet

- [ ] 6.1 扩展训练样本保存 search visit counts、Q_by_action、root value、simulation count、ambiguity/confidence、belief/search/opponent/leaf fingerprints。
- [ ] 6.2 policy head 使用 109 动作空间和 legal mask，训练 soft visit distribution；不得把 near-tie/ambiguous 样本强制 one-hot。
- [ ] 6.3 value head 使用 search root value/terminal reward 的版本化目标；teacher estimate 与实际终局 score 保持独立字段。
- [ ] 6.4 增加样本权重策略：低 simulations、ambiguous、posterior reset、高 search variance 下调权重并记录规则。
- [ ] 6.5 训练 `policy-v1` 并在 frozen final contexts 上比较 network-only 对 high-budget search 的 KL/action agreement/root regret。
- [ ] 6.6 建立 policy inference microbenchmark、batch=1 CPU 延迟、内存和非法 action=0 测试。

## 7. P6 Policy Iteration

- [ ] 7.1 定义 `pi0` 为冻结 stable policy（建议 shape-v2-fast/fallback chain），用 search-v1 生成 `dataset0` 并训练 `pi1`。
- [ ] 7.2 允许 `pi1` 作为 rollout continuation/self-play actor policy，生成 `search1/dataset1`；版本切换必须生成新 fingerprints。
- [ ] 7.3 至少完成两轮 `pi_k -> search_k -> pi_{k+1}`，记录每代 root regret、paired score、belief calibration、search cost 与 policy latency。
- [ ] 7.4 设置停止规则：连续两代 final-split regret/score 无统计显著改善或出现 population regression 时停止，不无限自举。
- [ ] 7.5 self-play 与 population opponent 两条评估分别报告；真实线上候选必须通过 population split，不得仅凭 self-play 提升发布。

## 8. P7 离线策略验收

- [ ] 8.1 对 shape-v2、上一代 policy、当前 policy、search teacher 做同 context high-budget regret 比较，报告 mean/p50/p95 和 ambiguous coverage。
- [ ] 8.2 运行至少 4096 paired games，16 个 seat/dealer 组合均衡，YCBK 开关分组；主指标 `hero_round_score_points`，按 source game 聚类 bootstrap。
- [ ] 8.3 分别对 self-play、冻结 population opponents、legacy/shape-v1 opponents 做 robustness matrix；输出 score/win/mult/draw 与置信区间。
- [ ] 8.4 做行为消融：uniform belief vs belief-v2、shape-v1 continuation vs policy continuation、terminal rollout vs ValueNet leaf，确认每层真实贡献。
- [ ] 8.5 做特殊局面回归集：财飘、爆头、四白板、七对/豪华七对、暗/加/明杠、抓打圈、墙尾、response cursor、YCBK。

## 9. P8 Online policy-v3 与安全回退

- [ ] 9.1 在 bot/runner 增加显式 `policy-v3` opt-in；默认路径为 PolicyValueNet + legal mask，低置信/异常走 shape-v2/legacy fallback。
- [ ] 9.2 在线 BeliefState 首先 shadow：只维护 bounded particles/marginals 与日志，不影响动作；验证事件缺失、重连/FULL snapshot、跨局 reset。
- [ ] 9.3 Recorder/logview 增加 history/belief/search/model fingerprints、network confidence、fallback reason 和 counterfactual suggestion，不记录 hidden particles。
- [ ] 9.4 实际十场并发验证完整决策延迟与吞吐；policy-only 必须满足现有窗口 p95/p99，fallback/illegal/NaN 计数为零或有明确安全降级。
- [ ] 9.5 可选 shallow-search 建独立 profile，仅在 16/32/64 simulations 对实际窗口和十场并发全部达标后 opt-in；不得拖累默认 policy-only。
- [ ] 9.6 至少三个新房各十场逐窗验收 decision/action/deadline/transport/server outcome，确认 evaluator/search 不导致窗口损失。

## 10. P9 发布与持续迭代

- [ ] 10.1 冻结最终 belief/search/policy/value/runtime manifest，并检查所有 evidence fingerprint 自洽。
- [ ] 10.2 默认切换前要求：belief calibration 通过、search regret 优于 shape-v2、4096 paired score CI 不回归、性能/线上窗口全部通过。
- [ ] 10.3 发布后保留 shape-v2/legacy kill switch；任何规则/platform guide 变化使相关 search/model evidence 失效并要求重新验收。
- [ ] 10.4 将 replay debugger 接入 `search regret / belief marginals / actual vs suggested` 只读展示，不能使 UI 成为策略运行时依赖。
- [ ] 10.5 更新 README/docs 与 OpenSpec evidence index；仅在实现、独立证据和线上门禁完成后归档本 change。
