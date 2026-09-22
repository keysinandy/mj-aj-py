# Proposal

## Why

LegacyV2 的完整 weighted two-ply 单线程需要 p50≈43ms、最坏 143ms，而在线预算是 50ms。
实测（100 状态基准与逐前沿打印）表明前沿候选的 future improvement 精确并列，Stage A
的 bounds 剪枝命中率≈0，因此 Stage B（child ukeire，约占 90% 的 shanten 调用）几乎总会
执行，42–66% 的状态在预算内无法完成并事务性回退 legacy。继续做剪枝或调度优化无法达标，
需要提高内核单位时间吞吐。

## What Changes

- weighted kernel 的 Stage B 按 draw 分片并行执行（`std::thread::scope`，不引入新依赖）；
  每个 worker 使用私有的 shanten/ukeire 缓存。
- 结果聚合顺序固定（root 顺序 × Stage A draw 顺序），使并行与串行的动作、future 指标、
  best discard 行和计数器求和逐位一致；线程数不得进入排名或指标口径。
- 新增并行度配置：`LegacyTwoPlyProfile.workers`（0 = 自动，按可用核数并设上限），
  `MJ_KERNELS_THREADS` 环境变量可覆盖；`workers<=1` 保持原串行路径。
- 保持预算语义：任一 worker 触发 hard deadline 或 work budget 时整次 Stage B 事务性作废，
  中止原因与覆盖度口径不变，不得提交部分 root 的 future 指标。
- 解释与 benchmark 记录实际并行度，raw 延迟必须与并行度一起报告。

## Capabilities

### New Capabilities

（无新增能力）

### Modified Capabilities

- `bot-hand-evaluation`: 前瞻预算与确定性要求补充"并行执行必须与串行逐位一致、预算中止
  语义不变、内核缓存必须是调用/线程局部"。
- `bot-decision-explanations`: 解释与基准必须暴露前瞻并行度，避免把并行加速误读为决策
  质量或搜索完成率的提升。

## Impact

- `rust/src/lib.rs`: `weighted_two_ply_frontier` 的 Stage B 工作分片、worker 执行与计数器聚合。
- `mj/legacy_eval.py`: profile `workers` 字段、原生调用透传、搜索指标与解释字段。
- `scripts/legacy_two_ply_weighted_bench.py`: 报告并行度与 raw 延迟的对应关系。
- `tests/test_legacy_weighted_frontier.py`: 并行/串行 parity、确定性与中止语义测试。
- 线上与训练接口不变；legacyV2 排序语义不变，但完成率提高会改变依赖 legacyV2 标签的
  数据集分布（BC 数据需按新版本重新生成，见 design 风险节）。
