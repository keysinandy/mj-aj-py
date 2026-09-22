# Spec Delta

## MODIFIED Requirements

### Requirement: 决策携带可识别的评价版本与实际层级

LegacyV2 evaluation SHALL expose whether weighted search influenced the selected
action and which phase completed: `future_shanten`, `two_ply`, or null. A
singleton short-circuit, unsafe partial fallback, or legacy fallback MUST report
`search_used=false`. A safe Stage-A-only result MUST expose improvement bounds,
coverage, and an explicit missing-future-ukeire state; it MUST NOT report missing
metrics as zero or complete.

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

#### Scenario: shape-v1 在运行中回退
- **WHEN** 运行配置为 shape-v1 而本次因预算回退 legacy
- **THEN** 日志同时记录请求的 evaluator 与实际 legacy 层级及原因，不将本次标成完整前瞻

#### Scenario: 读取旧日志
- **WHEN** decision 记录没有 evaluation 对象
- **THEN** logview 和回放仍可处理，评价元信息显示缺失而不补造

### Requirement: 候选解释保留原始值与加权贡献

The weighted benchmark SHALL report complete count, safe partial count, fallback
count, and `search_used_rate` separately from raw kernel latency. It MUST also
report stage-A-only versus full two-ply usage so a lower latency result cannot be
mistaken for an actually adopted LegacyV2 decision.

#### Scenario: Fast but discarded search
- **WHEN** Rust returns partial rows but bounds overlap and legacy is selected
- **THEN** benchmark counts fallback and `search_used=false`, even though raw
  search time is recorded

#### Scenario: 直接进张相同但结构取舍不同
- **WHEN** 同向听候选的 U1 相同而 weighted frontier 选择与 legacy 不同
- **THEN** 解释显示造成差异的已完成 weighted improvement、child ukeire、coverage 和最佳后续路径

#### Scenario: 对子保护未胜出
- **WHEN** BOT 拆开一个天然对子
- **THEN** 解释可以复核剩余雀头来源、该对子公开碰牌潜力及胜出候选的 future 收益，不仅输出“对子权重较低”
