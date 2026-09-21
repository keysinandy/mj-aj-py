# Tasks

## 1. 离线 profile 与路由

- [x] 1.1 新增 `LegacyTwoPlyProfile.weighted_offline()` 与别名
  (`legacyV2-offline` / `legacy-v2-offline` / `legacy_v2_offline`)，预算放大但排序与前沿
  口径不变。
- [x] 1.2 `choose_action` 接受离线别名并路由到该 profile。

## 2. 训练标签生成

- [x] 2.1 `mj/bc_data.py` 默认 evaluator 改为 `legacyV2-offline`，
  `TRAINING_BOT_EVALUATOR` 作为常量导出。
- [x] 2.2 增加 `search_fallback_reason()`：`*_scope` 与 `only_legal_action` 属于设计内委托，
  其余 `level="legacy"` 视为搜索回退。
- [x] 2.3 `generate_game` 默认 fail-loud（带 seed/seat/原因），并新增
  `--allow-search-fallback` 放行开关。
- [x] 2.4 NPZ 增加逐样本 `label_level` 与 `label_fallback_reason`；分布式 job 与
  `minisuphx_cluster submit` 默认走离线 evaluator 并透传开关。

## 3. 验证

- [x] 3.1 新增测试：离线 profile 预算、作用域委托判定、回退被拒、审计字段
  （4 passed）。
- [x] 3.2 逐局实测 seed 0..9：在线 1 局被拒（`partial_not_acceptable`），离线 0 局被拒，
  吞吐 +7%，证据见 `evidence/offline-label-benchmark.json`。
- [x] 3.3 运行受影响的测试文件（`test_bc_pipeline.py`、`test_bot.py`、`test_legacy_eval.py`）。
