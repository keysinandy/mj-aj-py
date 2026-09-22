# bot-decision-explanations Specification

## Purpose
TBD - created by archiving change bot-shape-aware-evaluation. Update Purpose after archive.
## Requirements
### Requirement: 决策携带可识别的评价版本与实际层级

LegacyV2 evaluation SHALL expose whether weighted search influenced the selected
action and which phase completed: `future_shanten`, `two_ply`, or null. A
singleton short-circuit, unsafe partial fallback, or legacy fallback MUST report
`search_used=false`. A safe Stage-A-only result MUST expose improvement bounds,
coverage, and an explicit missing-future-ukeire state; it MUST NOT report missing
metrics as zero or complete.

#### Scenario: shape-v1 在运行中回退
- **WHEN** 运行配置为 shape-v1 而本次因预算回退 legacy
- **THEN** 日志同时记录请求的 evaluator 与实际 legacy 层级及原因，不将本次标成完整前瞻

#### Scenario: 内核缺失导致的降级被显式报告
- **WHEN** 客户端进程所在环境没有可用的原生 weighted 内核
- **THEN** 启动与决策记录显式标注降级与实际层级，回放/前端可提示"本次决策走 legacy 回退"，无需逐字段排查

#### Scenario: 护栏状态可见
- **WHEN** 一次决策启用了前沿护栏
- **THEN** 解释给出准入/截断明细与候选级 `admitted_by`，可复核哪些候选因护栏进入比较

#### Scenario: 读取旧日志
- **WHEN** decision 记录没有 evaluation 对象
- **THEN** logview 和回放仍可处理，评价元信息显示缺失而不补造

#### Scenario: Safe Stage-A partial is used
- **WHEN** future improvement bounds prove a winner before child ukeire
- **THEN** top-level explanation marks `search_used=true`, phase
  `future_shanten`, partial acceptance, and null future ukeire fields

#### Scenario: Stage B produces a complete result
- **WHEN** improvement is tied and Stage B completes for the retained frontier
- **THEN** explanation marks `search_used=true`, phase `two_ply`, complete=true,
  and exposes child ukeire/type metrics for each retained root

#### Scenario: Search result falls back
- **WHEN** deadline/work budget prevents a safe decision certificate
- **THEN** the action is the complete legacy result, `search_used=false`, and the
  fallback reason/coverage remain visible without partial future values

### Requirement: 候选解释保留原始值与加权贡献

The weighted benchmark SHALL report complete count, safe partial count, fallback
count, and `search_used_rate` separately from raw kernel latency. It MUST also
report stage-A-only versus full two-ply usage so a lower latency result cannot be
mistaken for an actually adopted LegacyV2 decision.

#### Scenario: 直接进张相同但结构取舍不同
- **WHEN** 同向听候选的 U1 相同而 weighted frontier 选择与 legacy 不同
- **THEN** 解释显示造成差异的已完成 weighted improvement、child ukeire、coverage 和最佳后续路径

#### Scenario: 对子保护未胜出
- **WHEN** BOT 拆开一个天然对子
- **THEN** 解释可以复核剩余雀头来源、该对子公开碰牌潜力及胜出候选的 future 收益，不仅输出“对子权重较低”

#### Scenario: 直接进张相同但未来取舍不同
- **WHEN** 同向听候选的当前 U1 相同，而 legacy V1 通过未来特征选择了不同于基线 legacy 的弃牌
- **THEN** 解释显示两者的当前进张、未来改良权重、未来进张、牌型损失、喂牌风险、最佳后续弃牌及最终排序差异

#### Scenario: 20260920 的 9b 反事实重算可复核
- **WHEN** 离线重算 `u_9812ba08fe2f_a_e18e58e3acb8_r1_b9_t0` 的 `seq=100`，并比较 `9b` 与 `3t`
- **THEN** 结果同时标明原日志实际选择、V1 反事实候选、输入状态和评价版本；不得把离线选择写成原时点已执行动作

#### Scenario: V1 预算回退不补零
- **WHEN** legacy V1 只完成当前 ukeire，未来特征因预算或输入不完整而未完成
- **THEN** 解释将未来字段标记为缺失，记录实际 legacy 层级和回退原因，并按完整 legacy 排序展示，不把缺失字段序列化为 0

#### Scenario: Accepted partial includes coverage
- **WHEN** online weighted frontier accepts a partial result
- **THEN** the selected candidate explanation contains `covered_weight`, `total_weight`, `coverage`, `partial_accepted`, and the search counters used to reach it

#### Scenario: Incomplete fallback does not fake future values
- **WHEN** coverage is insufficient and the evaluator falls back to legacy
- **THEN** future metrics remain missing/null, while the fallback reason and actual legacy level are recorded

#### Scenario: Equal current ukeire has future evidence
- **WHEN** two roots have equal current shanten and current weighted ukeire but different future metrics
- **THEN** the explanation shows both roots' weighted improvement and expected child ukeire before shape/feed tie-breaks

#### Scenario: Accepted partial includes decision-safe bounds
- **WHEN** online weighted frontier accepts a partial result
- **THEN** the selected candidate explanation contains covered/total weight, coverage, observed improvement, lower/upper improvement bounds, the partial mean denominator, and search counters

#### Scenario: Unsafe partial does not fake future values
- **WHEN** coverage is high but no root is proven safe by the improvement bounds
- **THEN** future metrics remain missing/null in the selected legacy fallback explanation, while the fallback reason and actual legacy level are recorded

#### Scenario: Singleton frontier records short-circuit
- **WHEN** current shanten and current ukeire leave only one retained root
- **THEN** the explanation records a one-ply short-circuit reason and does not claim that uncomputed future metrics are zero or complete

#### Scenario: Fast but discarded search
- **WHEN** Rust returns partial rows but bounds overlap and legacy is selected
- **THEN** benchmark counts fallback and `search_used=false`, even though raw
  search time is recorded

### Requirement: 解释采样不改变选择或重复搜索

实时解释 SHALL 复用本次已计算结果，只保存选中项、legacy 最优项及至多另外三项，并记录总候选数；离线完整输出可保留全部候选。开启/关闭或截断解释 MUST NOT 改变动作、候选搜索、计算层级或预算。解释构造开销 MUST 进入性能验收。

#### Scenario: 精简与完整解释一致
- **WHEN** 相同状态和固定节点预算分别输出精简解释与全候选解释
- **THEN** 选择相同，共有候选的分量一致，不为输出而第二次搜索

### Requirement: 反事实重算不补造线上决策

系统 SHALL 按原日志关联策略决策、动作提交和窗口/服务端终态；MUST 区分 strategy PASS、确认未通过、未提交和 server auto discard。没有实际策略调用的窗口 MUST NOT 生成补造的线上 decision。离线重算 MUST 标为 `counterfactual_evaluation`，关联原事件并声明其不代表原时点实际执行。

#### Scenario: 8 筒窗口身份未知
- **WHEN** 回放案例 C 具有 `identity_unknown` 且没有该窗 decision/action，离线评价选择 PONG
- **THEN** 原因保留为确认未通过，PONG 只显示为反事实策略结果

#### Scenario: 4 筒超时代打
- **WHEN** 回放案例 D 包含弃 4p 与本家 `timeout kind=discard`，无配对决策
- **THEN** 记录为服务端代打；离线选择 4w 不得被说成原 BOT 曾选择或提交 4w

### Requirement: 日志关联保持本家视角和并发隔离

解释 SHALL 通过 gid 与 decision id 关联 action，反应侧沿用已有 WindowAttemptKey/身份质量信息。MUST NOT 用弱身份升级授权，不得记录令牌、对手暗手或真实墙序，也不得跨游戏混用解释。

#### Scenario: 相同牌值在不同窗口出现
- **WHEN** 同房相同牌值在不同回合被打出
- **THEN** 不仅按牌值关联解释和动作，仍使用各自决策及已有窗口身份；缺失强身份保持缺失

### Requirement: Bound 与 paired racing 证据必须进入决策解释

Fast EV 和离线 teacher 的解释 SHALL 区分 derived/override/fallback bound，记录 bound version、收益支持范围或上界、组件证书和 profile/config fingerprint。teacher 还 MUST 记录 active candidates、paired delta interval、淘汰/停止原因及稀疏样本语义；未计算或不可证明内容必须保持 missing/unknown。

#### Scenario: Fast EV 使用局面级上界

- **WHEN** 某候选通过规则资源推导得到 candidate-specific fast upper bound
- **THEN** 解释显示其 bound 版本、组件、上界和是否实际用于严格剪枝，不把它写成观测最大值

#### Scenario: Teacher 淘汰候选

- **WHEN** 某候选因 paired delta 的上置信界严格小于零而退出 active 集
- **THEN** 离线解释保留对手 leader、pair bound、有效 paired n、区间、alpha/look 和 elimination certificate

#### Scenario: 后续 batch 缺少已淘汰候选

- **WHEN** 读取淘汰后的 teacher sparse row
- **THEN** 将该 action 标为未在该 batch 评估，而不是补写零 reward、失败 reward 或完整候选结果

### Requirement: Bound 与 racing 解释不得突破现有证据边界

新增解释和 teacher artifact MUST NOT 写入对手暗手、真实墙序、token 或完整 sampled world；线上实际 decision/action、transport、server auto discard 与离线 counterfactual/racing 结果仍须分开关联。评价证据不得改变窗口授权、动作重试或平台状态。

#### Scenario: 离线世界包含隐藏牌

- **WHEN** teacher 内部使用 sampled world 完成 paired rollout
- **THEN** 对外解释只保留 world fingerprint 和公共 envelope/统计字段，不导出隐藏牌身份或墙顺序

#### Scenario: 原线上没有实际策略决策

- **WHEN** 某窗口只有 timeout 或 server auto discard 而没有本家 decision
- **THEN** racing/teacher 结果只能标记为 counterfactual/offline evidence，不生成原线上策略动作

### Requirement: 尝试相位必须区分 Stage A 截断与 Stage B 中断

LegacyV2 的解释 SHALL 记录搜索实际走到的相位：只有 Stage A 被预算截断时报告
`future_shanten`，一旦进入 Stage B（无论完成还是被预算中断）MUST 报告 `two_ply`。
带哨兵 child ukeire 的行本身 MUST NOT 被当作 Stage-A-only 证明；Stage-A-only 只在
improvement bounds 证明胜者时成立，并 MUST 继续标记为缺失的 future ukeire 而不是零。
被中断的 Stage B MUST 保持 `search_used=false`、不提交部分 future 指标，并保留回退原因
与覆盖度。

#### Scenario: Stage B 被预算中断
- **WHEN** Stage A 已完整覆盖全部 draw，Stage B 因 work budget 或 deadline 未完成
- **THEN** 解释报告 `search_attempt_phase=two_ply`、`search_used=false`、回退原因与覆盖度，且不出现 child ukeire 数值

#### Scenario: Stage A 被预算截断
- **WHEN** 预算在 Stage A 未覆盖全部 draw 时耗尽
- **THEN** 解释报告 `search_attempt_phase=future_shanten`，不声称已进入 Stage B

#### Scenario: Stage A 证明胜者
- **WHEN** improvement bounds 在 Stage A 内证明唯一胜者
- **THEN** 解释报告 `future_shanten` 且 `search_used=true`，future ukeire 字段保持缺失状态

### Requirement: 解释与基准必须记录前瞻并行度

LegacyV2 的搜索指标 SHALL 记录本次决策实际使用的并行度（worker 数），并 SHALL 与
内核版本、预算和缓存容量一起保留，使同一 profile 下不同并行度的结果可区分。基准报告
SHALL 把完成率、`search_used_rate` 与 raw 延迟按并行度分别呈现，MUST NOT 用并行加速
单独证明决策质量或搜索完成率提升。缺少该字段的旧记录 MUST 按缺失处理，不得补造。

#### Scenario: 并行决策的解释
- **WHEN** 一次 weighted 搜索以多个 worker 完成
- **THEN** 解释的 search_metrics 暴露实际 worker 数，并在同一记录中保留 kernel 版本与预算

#### Scenario: 旧日志缺少并行度
- **WHEN** 读取在本 change 之前写入的 decision 记录
- **THEN** 解释元信息显示该字段缺失，logview/回放仍可处理，不推断历史并行度

#### Scenario: 基准对比
- **WHEN** 以不同并行度运行 100 状态基准
- **THEN** 报告分别给出 raw p50/p95/p99、complete、safe partial、fallback 与 search_used_rate
