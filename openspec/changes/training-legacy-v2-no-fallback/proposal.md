# Proposal

## Why

BC 数据生成默认用 `legacyV2` 作标签源，而 legacyV2 在线 profile 带 50ms 硬预算：预算
耗尽时会**事务性回退 legacy**，但样本仍然被写成 `label_source="online_evaluator"`。
实测 10 局里就有 1 局触发 `partial_not_acceptable`，等于把 legacy 启发式动作当成搜索
标签写进训练集，且下游无法区分。

## What Changes

- 新增离线训练 profile `LegacyTwoPlyProfile.weighted_offline()`（`legacyV2-offline`）：
  排序、前沿上限、partial 接受规则与在线版一致，只把时间/节点预算放大到不会因
  deadline、work budget 或不安全 partial 回退。
- `mj/bc_data.generate_game` 默认使用该 profile；一旦 legacyV2 系评价器在**弃牌相位**
  真的回退，立即抛错（fail-loud），除非显式 `--allow-search-fallback`。
- `discard-only` 契约内的作用域委托（`reaction_scope`/`baotou_scope`/`hu_kong_scope`/
  `only_legal_action`）不算回退，但 SHALL 被记录。
- NPZ 新增逐样本 `label_level` 与 `label_fallback_reason`，使"搜索标签 / 作用域委托 /
  回退"可审计、可过滤。
- 分布式 job（`legacy_bc_games`）与 `minisuphx_cluster submit` 的默认 evaluator 改为该
  离线 profile，并透传 `allow_search_fallback`。

## Capabilities

### New Capabilities

- `training-label-generation`: 训练标签的来源、回退处置与审计字段。

### Modified Capabilities

（无）

## Impact

- `mj/legacy_eval.py`（离线 profile 与别名）、`mj/bot.py`（evaluator 路由）、
  `mj/bc_data.py`（默认 evaluator、fail-loud、新字段）、`mj/training/distributed_bc.py`、
  `scripts/minisuphx_cluster.py`、`tests/test_bc_pipeline.py`。
- 已生成的历史数据集不受影响，但新数据集的 `evaluator` 字段会写作 `legacyV2-offline`，
  旧 shard 不可与新 shard 混用；训练侧读取时按 `label_level` 过滤。
