# Design

## Context

- 训练入口：`scripts/minisuphx_cluster.py submit` → `legacy_bc_games` job →
  `mj/training/distributed_bc.py` → `mj/bc_data.generate_game` → `choose_action(evaluator=…)`。
- 旧默认 `DEFAULT_BOT_EVALUATOR = "legacyV2"` 走 `weighted_online()`（50ms 硬预算）。
- 实测（本机，2026-09-21，10 局）：在线 profile 有 1 局在弃牌相位回退
  `partial_not_acceptable`；离线 profile 0 回退。

## Goals / Non-Goals

**Goals:**

- 训练数据里不再出现"由 legacy 产生、却标成搜索标签"的样本。
- 回退可被发现（fail-loud）与被审计（逐样本字段）。

**Non-Goals:**

- 不改变在线 profile 与其预算（线上仍按 50ms 契约）。
- 不改变 discard-only 契约：reaction/HU/KONG 仍由既有规则决定。
- 不重写历史数据集。

## Decisions

### 1. 离线 profile

`weighted_offline()` = 在线档的 `max_frontier_candidates=3`、`allow_partial=True`、
`min_partial_coverage=0.9`，但 `hard/soft=2000ms`、`node_budget=5_000_000`、
`cache_capacity=65536`。这样搜索要么完整完成，要么（极少数）由 bounds 证明胜者后作为
安全 partial 被接受；两者都仍由评价器给出动作。

### 2. 回退判定

`search_fallback_reason(evaluation)`：仅当 `level == "legacy"` 且原因不是 `*_scope`
或 `only_legal_action` 时算回退。`legacy-one-ply`（唯一前沿短路）是评价器自身结论，
不算回退。

### 3. fail-loud

`generate_game` 默认禁止搜索回退：命中即抛 `RuntimeError`（带 seed/seat/原因），由
`--allow-search-fallback` 显式放行。这样数据管线的失败是可见的，而不是静默污染标签。

### 4. 审计字段

NPZ 增加 `label_level`（`weighted-two-ply-v1` / `weighted-two-ply-partial` /
`legacy-one-ply` / `legacy`）与 `label_fallback_reason`（作用域委托为空）。训练侧可以
据此只取搜索标签，或单独分析作用域委托样本。

## Risks / Trade-offs

- **[Risk]** 离线预算放大导致单局变慢。→ 实测 10 局 4.3s→4.6s（约 +7%），因为多数决策点
  是作用域委托，弃牌相位只是从"跑满预算后回退"变成"跑完"。
- **[Risk]** 极端状态可能跑到 2s 上限。→ 触发即回退，而回退会被 fail-loud 拦下，
  不会写入脏标签。
- **[Risk]** 新字段改变 NPZ 布局。→ 只在带 provenance 的布局里新增数组，读取侧按 key
  处理;旧 shard 不带这些 key,需要按 evaluator 版本区分。

## Migration Plan

1. 代码与测试落地（本 change）。
2. 新 campaign 用默认离线 evaluator 生成，`evaluator` 字段为 `legacyV2-offline`。
3. 训练侧按 `label_level` 过滤（若要纯搜索标签）或保留作用域委托样本。
4. 历史 shard 保持原样，不与新数据混用。

## Open Questions

- 是否要为"纯搜索标签"提供 `--label-scope discard-only` 过滤开关（跳过作用域委托样本）。
