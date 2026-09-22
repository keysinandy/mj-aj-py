# Design

## Context

当前 weighted Rust kernel 对每个 `root × draw` 先找最低 child shanten，随后
立即对所有最低 child 计算 ukeire。多数状态的 future improvement 已足以按照
现有词典序选出候选，但仍支付了完整 ukeire DFS。与此同时 hard deadline 只在
外层 draw/root 循环检查，单次 ukeire 内层可能把 50ms 预算拖穿。

## Goals / Non-Goals

**Goals:**

- 保持 current shanten/current ukeire/future improvement/future ukeire 的既有
  排序语义和 exact parity。
- 在 improvement lower bound 严格胜出时，返回安全 partial，不计算或不伪造
  child ukeire。
- 只有 improvement 无法决定时才进入第二阶段；完整 two-ply 仍输出原有全部
  future metrics。
- 在 child discard、ukeire candidate 和 shanten cache miss 前检查内部 hard
  deadline，并保留 work/deadline counters。
- 让解释和 benchmark 能区分尝试搜索、实际采用搜索及搜索阶段。

**Non-Goals:**

- 本 change 不引入 compact u128 cache key、bitmask candidate set、root batch
  FFI 或新的牌效公式。
- 不改变 exact/offline profile、legacy fallback 或默认 `legacyV2` 名称。
- 不使用对手暗手、真实牌墙顺序或其它 hidden information。

## Decisions

### 1. Stage A: Future Shanten frontier

按 remaining weight 从高到低遍历 draw。对每个 active root 只枚举 legal
discard 并计算 child shanten，保存：

```text
covered_weight
improve_weight
maintain_weight
best_child_shanten
best_child_discard_mask/list
```

每处理完一个 root branch 就计算：

```text
lower = observed_improve
upper = observed_improve + total_weight - covered_weight
```

若某 root 的 lower 严格大于所有其它 root 的 upper，立即停止整个搜索并以
`future_shanten_only` partial 返回。若 Stage A 完成且只有一个最大
`improve_weight`，同样跳过 Stage B；因为 future improvement 位于 future
ukeire 之前。

### 2. Stage B: Global lazy child ukeire

只有 Stage A 没有安全 winner 且最大 improvement 并列时进入 Stage B。使用
Stage A 保存的 best-shanten child discard，不重新计算 child shanten；随后按
现有规则计算 child ukeire、tile types 和 best discard，并保持完整结果格式。

Stage-A-only row 保持现有 PyO3 tuple arity。其 draw row 的 child ukeire/type
使用不可与合法值混淆的 `-1` sentinel，Python adapter 将其映射为
`future_ukeire=None`、`future_ukeire_skipped=true`，而不是零。

### 3. Deadline propagation

weighted kernel 从 hard budget 中预留 2ms 作为返回和 PyO3 转换余量。内部
deadline 传入 counted shanten/ukeire；每个 child discard、ukeire candidate
及 shanten cache miss 前检查。内部截止返回已有 `hard_deadline` 原因，work
budget 仍优先记录 `work_budget_exceeded`。

### 4. Explanation and usage metrics

顶层 evaluation 新增：

```text
search_used: bool
search_phase: "future_shanten" | "two_ply" | null
```

`search_used` 只有 weighted complete 或 decision-safe partial 真正影响动作
时为 true；singleton、legacy fallback 和 unsafe partial 为 false。候选缺失的
future ukeire 必须带状态字段。

## Risks / Trade-offs

- **[Risk]** improvement 差距小的状态仍需完整 Stage B。→ 保持 exact parity，
  只优化可证明安全的分支。
- **[Risk]** stage-A sentinel 被误当成真实 Ukeire。→ Python 严格校验 sentinel
  与 `future_ukeire_skipped`，负值不得进入排序。
- **[Risk]** 内部 2ms reserve 降低覆盖率。→ 记录 deadline reason 和 coverage，
  仍使用 decision-safe bounds；正确性优先于强行完成。
- **[Risk]** hard deadline 检查增加少量调用开销。→ 仅在 weighted hot path
  启用并 benchmark raw/native p50/p95/p99。

## Migration Plan

1. 先在 Rust weighted kernel 中加入 Stage A accumulator、bounds early stop 和
   Stage B lazy Ukeire。
2. 更新 Python row mapper、解释字段和 benchmark search-used 指标。
3. 运行 exact Rust/Python parity、Stage-A-only synthetic tests、deadline tests
   及 100-state benchmark。
4. 若 raw p95 仍超过 50ms，保留该 change 并将 root batch/compact key/racing
   作为后续独立 change，不在本次混入更多变量。

## Open Questions

None for this phase.
