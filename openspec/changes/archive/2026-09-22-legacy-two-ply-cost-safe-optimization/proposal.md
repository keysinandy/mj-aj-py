# Proposal

## Why

LegacyV2 已经能够在 50ms 窗口内执行 weighted two-ply，但当前仍会为唯一 root 重复搜索，并把 partial 是否可接受简化为覆盖率阈值；同时 native node budget 没有反映 ukeire 内部的真实 shanten DFS 工作量。需要先收紧这些边界，降低平均延迟并避免“覆盖率高但仍可能翻转选择”的 partial 决策。

## What Changes

- 当 current shanten/ukeire frontier 只剩一个候选时，LegacyV2 直接返回 one-ply 已确定的合法动作，不启动 two-ply。
- Partial weighted 结果改为 decision-safe bounds：对每个 root 记录已观测 improvement、未覆盖权重及 improvement lower/upper bound；只有存在严格证明的胜者时才允许 partial 影响选择，否则事务式回退 legacy。
- Partial 的 future ukeire mean 只使用已覆盖权重归一化，并继续暴露 covered/total，禁止把未覆盖分支隐式当成零。
- weighted Rust budget 改为按 shanten cache miss（实际 DFS work）限制；保留 child/ukeire counters 作为诊断，不再用 child 数单独代表预算消耗。
- 增加 singleton short-circuit、partial bound、work-budget 的回归和 benchmark 证据；不在本变更中引入 root batch、compact key、bitmask 或 racing。

## Capabilities

### New Capabilities

<!-- No new public capability; this is a stricter implementation of the existing LegacyV2 evaluation contract. -->

### Modified Capabilities

- `bot-hand-evaluation`: singleton frontier short-circuit, decision-safe partial acceptance, and work-based weighted budget.
- `bot-decision-explanations`: expose observed/remaining improvement bounds and the denominator used for partial future means.

## Impact

主要代码：`mj/legacy_eval.py`、`rust/src/lib.rs`，以及 `tests/test_legacy_weighted_frontier.py` 和 Rust/Python kernel tests。weighted row 仍保持向后兼容；新增 candidate explanation 字段和 search metric 字段均为可选，旧日志继续可读。默认 evaluator、公开信息边界、legacy fallback 和 exact offline profile 不改变。
