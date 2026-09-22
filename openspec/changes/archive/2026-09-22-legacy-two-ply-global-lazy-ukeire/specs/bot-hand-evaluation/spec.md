# Spec Delta

## MODIFIED Requirements

### Requirement: 直接进张与同向听改良分别计算

weighted frontier SHALL first evaluate only the best child shanten for each
public-information draw and SHALL accumulate improvement bounds using remaining
tile weight. Child ukeire MUST NOT be evaluated while a strict improvement bound
already proves a winner. If Stage A completes without a strict winner and the
maximum improvement is tied, Stage B SHALL evaluate the existing child ukeire
metrics and preserve exact two-ply ordering. Stage-A-only results MAY be used
only as decision-safe partials; missing child ukeire MUST remain missing.

#### Scenario: Future improvement resolves the winner early
- **WHEN** one root lower bound is strictly greater than every other root upper bound
- **THEN** the kernel stops before child ukeire, returns `future_shanten_only`, and the adapter may accept that root as a safe partial

#### Scenario: Full Stage A has a unique improvement winner
- **WHEN** all draw weights are evaluated and exactly one root has the greatest future improvement weight
- **THEN** Stage B is skipped and the unique root is selected without fabricating future ukeire values

#### Scenario: Improvement tie enters Stage B
- **WHEN** Stage A cannot distinguish the maximum improvement among retained roots
- **THEN** Stage B evaluates child ukeire using Stage-A best-shanten children and produces the same action and future metrics as exact weighted two-ply

#### Scenario: Stage-A-only metrics are not zero-valued
- **WHEN** a safe partial stops before child ukeire
- **THEN** future ukeire and future tile-type means are null/missing, with a phase and skip reason recorded

#### Scenario: 13 与 89 的改良存在条件差异
- **WHEN** 合成整手中其他条件对称，13 摸 4 可转 34 且增加有效进张，而对照边张无相同改良
- **THEN** 对应可行路径贡献正改良分，并显示牌 4 的未见权重和最佳弃牌

#### Scenario: 改良所需牌耗尽
- **WHEN** 其余输入相同但上述牌 4 的 visible 增加到四张
- **THEN** 通过摸 4 的改良贡献变为零；其他牌的贡献独立计算

#### Scenario: 仅未知池缩小不算改良
- **WHEN** 摸入无用牌并原样摸切，子站立牌面与原手一致，仅 visible 和未知池分母变化
- **THEN** 该路径的 I 贡献为零，不能因 p1 分母缩小误报结构改善

### Requirement: 预算耗尽回退到全候选完成层级

The weighted hard deadline SHALL be checked before each child discard, shanten
cache miss, and ukeire candidate expansion. The kernel MAY reserve a small fixed
amount of time for result construction, but MUST report the configured budget,
internal deadline and explicit `hard_deadline` reason. Work-budget accounting by
uncached shanten remains independent from deadline accounting.

#### Scenario: Ukeire inner loop crosses the deadline
- **WHEN** a child ukeire loop reaches the internal hard deadline
- **THEN** it stops without committing that branch, preserves earlier committed rows, and reports hard-deadline fallback/coverage

#### Scenario: Cache hit before deadline
- **WHEN** a shanten or ukeire state is served from the decision-local cache
- **THEN** it is counted as a hit, does not consume uncached work budget, and still obeys the surrounding deadline checks

#### Scenario: 最后一个候选前瞻被中断
- **WHEN** 部分候选完成 weighted frontier，另一个保留候选因预算未完成，而所有候选的完整当前 ukeire 已完成
- **THEN** 只有在 decision-safe bounds 证明胜者时使用已提交的 weighted 结果，否则使用完整 legacy 结果并记录回退

#### Scenario: 基础新评价也未完整完成
- **WHEN** weighted frontier 阶段预算耗尽且保留候选没有达到安全证明条件
- **THEN** 整次决策回退到完整 legacy 结果，不因为后遍历候选缺少分数而排除它
