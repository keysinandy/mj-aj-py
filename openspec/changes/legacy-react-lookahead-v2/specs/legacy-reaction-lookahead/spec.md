## ADDED Requirements

### Requirement: 系统 SHALL 提供可复用的 legacy standing U2 前瞻

系统 SHALL 在 legacy evaluator 层提供可对一个或多个 standing hand 进行“一次公开摸牌 + 最佳合法弃牌”前瞻的 reusable API，并复用现有 weighted two-ply Rust kernel / FutureEvaluation 语义。

该 API MUST：

- 接收稳定 root id、standing hand、locked、visible 和版本化 profile；
- 返回每个 root 的 `future_improve_weight`、future ukeire/mean、future types/mean、best discard distribution、coverage、complete/partial/fallback diagnostics；
- 使用公开 unseen mass `max(0,4-visible[t])`，不得读取真实墙序或对手暗手；
- 不得在 reaction 层复制一套独立 DFS；
- 支持 online 安全 partial 与 offline require-complete 两种模式。

#### Scenario: 多个 post-claim standing 一次进入共同 frontier
- **WHEN** 一个 CHOW/PONG 动作存在多个最低向听的立即弃牌候选
- **THEN** 所有候选作为同一 standing frontier 被评价，返回同 profile、同 coverage 规则的 FutureEvaluation

#### Scenario: hidden wall 不可见
- **WHEN** 本地 Game 实例包含完整墙序和对手暗牌
- **THEN** standing U2 的输入仍只使用 hero hand、公开牌河/副露形成的 visible 与公开规则字段

### Requirement: Legacy Reaction Profile SHALL 区分 v1、v2-online、v2-offline

系统 SHALL 提供版本化 reaction profile，并把 reaction 行为与 discard profile 显式路由。

- `evaluator="legacy"` MUST 使用冻结的 `legacy-shape-progress-v1`；
- `legacyV2` aliases SHALL 使用 `legacy-react-v2` online profile（仅当性能/coverage 门禁启用，否则显式 profile-disabled 回 v1）；
- `legacyV2-offline` SHALL 使用 require-complete 的 offline profile；
- profile 的 future mode、预算、coverage、tempo guard、版本号 MUST 进入 fingerprint/诊断。

#### Scenario: 显式 legacy 保持 v1
- **WHEN** 同一固定 reaction state 分别在 `5e0a405` 与新代码中以 `evaluator="legacy"` 执行
- **THEN** 动作和核心 reason 与冻结 v1 fixture 一致

#### Scenario: offline 不允许静默 v1 标签
- **WHEN** `legacyV2-offline` 的 U2 评价因内核、预算或输入导致不完整
- **THEN** 标签生成 fail-loud / 标记失败，不得返回 v1 动作并声称是完整 v2 标签

### Requirement: online U2 比较 SHALL 是事务性的

当 v2 需要 U2 才能区分 PASS/claim 或多个 post-claim discard 时，所有参与比较的候选 MUST 使用共同完整层级。

- complete 与 safe partial 可按 profile 规则使用；
- Stage-A-only 与 Stage-B 结果不得直接混排；
- coverage 不足或任一必要候选缺结果时，online MUST 整层回退 v1；
- 回退 MUST 记录 `u2_fallback_reason`、coverage 和 kernel diagnostics；
- 不得使用“先算完的候选”做部分选择。

#### Scenario: 一个 claim 超预算
- **WHEN** PASS 与两个 claim 需要 U2，其中一个 claim 没有达到安全 coverage
- **THEN** online 返回冻结 v1 的窗口结果，而不是在 PASS 与已算完 claim 中继续选择

### Requirement: reaction tempo SHALL 以离散摸牌距离表达

CHOW/PONG evaluation SHALL 记录：

- `pass_draw_index=(hero-pending_owner)%4`，反应窗取 1..3；
- `claim_draw_index=4`；
- `tempo_cost=claim_draw_index-pass_draw_index`。

第一版 MUST NOT 把 tempo_cost 乘浮点权重加进综合分；它只能用于规范定义的 U2 guard 和诊断。

#### Scenario: 上家弃牌可吃
- **WHEN** hero 是 pending owner 的下一家并存在合法 CHOW
- **THEN** PASS draw index=1、claim draw index=4、tempo_cost=3

#### Scenario: 下家弃牌可碰
- **WHEN** pending owner 是 hero 的下一家并存在合法 PONG
- **THEN** PASS draw index=3、claim draw index=4、tempo_cost=1

### Requirement: shadow teacher SHALL 与线上动作解耦

系统 SHALL 提供 replay/audit 路径同时记录 v1、v2 与 shape-v2 all-root teacher 的 reaction 结果；teacher 输出 MUST NOT 参与 production 动作。

#### Scenario: teacher 不完整
- **WHEN** shape-v2 root evaluator 因 context/budget 不完整
- **THEN** audit 记录 delegated/incomplete，legacy-v2 动作仍只由 legacy profile 决定
