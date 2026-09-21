# Spec Delta

## ADDED Requirements

### Requirement: 并行前瞻必须与串行逐位一致

weighted kernel MAY 并行执行 Stage B 的 `(root, draw)` 工作单元，但 MUST 保持可观察结果与
单线程执行一致：动作、future improvement/ukeire/类型指标与 best discard 行及其顺序
MUST NOT 依赖线程数或线程调度。预算未触发时完成判定 MUST 同样与单线程一致；预算触发时
回退按聚合后的 deadline/work budget 统一判定，且 MUST NOT 提交部分 future 指标。缓存
命中/未命中以及依赖缓存命中的调用计数 MAY 因工作分配而不同，但它们 MUST 只作为性能记录，
MUST NOT 进入排名或指标。每个 worker MUST 使用调用/线程局部缓存，内核 MUST NOT 通过
跨调用的可变全局状态共享结果或缓存。并行度 MUST 可配置，并 MUST 能回退到单线程路径。

#### Scenario: 线程数不同的两次执行
- **WHEN** 同一手牌、同一 profile、同一预算分别以 workers=1 与 workers>1 执行且都完整完成
- **THEN** 两次结果的 selected、future 指标、best discard 行顺序与 Stage A 计数完全相同，
  仅缓存命中/未命中及其派生的调用计数可能不同

#### Scenario: 同一并行度重复执行
- **WHEN** 以相同输入和相同 workers 重复执行多次
- **THEN** 每次的 selected、future 指标与 best discard 行完全相同，不出现调度相关的漂移

#### Scenario: 工作单元不足以分片
- **WHEN** Stage B 只有零个或一个工作单元，或配置 workers<=1
- **THEN** 内核走串行路径，不创建线程，并给出与并行路径相同的结果

#### Scenario: 并行不引入共享状态
- **WHEN** 并行执行期间多个 worker 写入各自的 shanten/ukeire 缓存
- **THEN** 不同 worker 之间没有可变共享缓存或全局“最近结果”，解释仍与单次调用关联

## MODIFIED Requirements

### Requirement: 预算耗尽回退到全候选完成层级

The weighted hard deadline SHALL be checked before each child discard, shanten
cache miss, and ukeire candidate expansion, including inside every parallel
worker. The kernel MAY reserve a small fixed amount of time for result
construction, but MUST report the configured budget, internal deadline and
explicit `hard_deadline` reason. Work-budget accounting by uncached shanten
remains independent from deadline accounting and MUST be aggregated across
workers before deciding whether the run exceeded the work budget.

#### Scenario: Ukeire inner loop crosses the deadline
- **WHEN** a child ukeire loop reaches the internal hard deadline
- **THEN** it stops without committing that branch, preserves earlier committed rows, and reports hard-deadline fallback/coverage

#### Scenario: 并行 worker 触发截止
- **WHEN** 任一 worker 在 Stage B 期间触达内部 hard deadline
- **THEN** 整次 Stage B 事务性作废，所有 root 记录同一 `hard_deadline` 原因，不提交任何部分 future 指标

#### Scenario: 并行下的工作预算
- **WHEN** 各 worker 完成后的未缓存 shanten 合计超过 work_budget
- **THEN** 整次搜索以 `work_budget_exceeded` 中止，且不因为某片已完成的子集而提交结果

#### Scenario: Cache hit before deadline
- **WHEN** a shanten or ukeire state is served from the decision-local cache
- **THEN** it is counted as a hit, does not consume uncached work budget, and still obeys the surrounding deadline checks

#### Scenario: 基础新评价也未完整完成
- **WHEN** weighted frontier 阶段预算耗尽且保留候选没有达到安全证明条件
- **THEN** 整次决策回退到完整 legacy 结果，不因为后遍历候选缺少分数而排除它

#### Scenario: 最后一个候选前瞻被中断
- **WHEN** 部分候选完成 weighted frontier，另一个保留候选因预算未完成，而所有候选的完整当前 ukeire 已完成
- **THEN** 只有在 decision-safe bounds 证明胜者时使用已提交的 weighted 结果，否则使用完整 legacy 结果并记录回退
