# Tasks

## 1. Rust Stage B 并行内核

- [x] 1.1 抽出 Stage B 工作单元与结果结构，把 Stage A 行展开为 `(root, draw)` 单元并保留
  原 draw 顺序索引。
- [x] 1.2 实现 worker 执行函数（私有 shanten/ukeire 缓存、逐候选 deadline 检查、work
  budget 上限、结果与计数器返回）。
- [x] 1.3 用 `std::thread::scope` 实现按 draw 排序 + 原子游标动态调度、join 后确定原因优先级与按
  `(root, order)` 重排聚合；`workers<=1` 或单元数<=1 时保持串行路径。
- [x] 1.4 解析并行度：`workers` 参数 > `MJ_KERNELS_THREADS` > 可用核数（上限 8），并
  裁剪到工作单元数。

## 2. Python 适配与解释

- [x] 2.1 `LegacyTwoPlyProfile` 增加 `workers` 字段（校验、`as_json`，不进入 fingerprint
  payload），并透传给原生调用。
- [x] 2.2 搜索指标/解释暴露实际并行度，保持既有字段与 row arity 不变。
- [x] 2.3 benchmark 报告并行度与 raw 延迟的对应关系。

## 3. 验证

- [x] 3.1 增加 parity（workers=1 vs >1，精确 profile）、确定性（重复执行一致）与中止语义
  （deadline/work budget 整次作废）测试。
- [x] 3.2 重建 wheel，运行 focused/legacy/bot 测试与 OpenSpec 严格校验。
- [x] 3.3 跑 100 状态基准，报告 raw/E2E p50/p95/p99、complete、safe partial、fallback、
  search_used_rate 与并行度，并给出与单线程基线的对比。

## 4. 相位标签修正

- [x] 4.1 内核返回 `stage_b_entered`，使适配器不再从哨兵行推断 Stage-A-only。
- [x] 4.2 适配器区分"证明性 Stage A-only"与"哨兵行（Stage B 中断）"，并据此设置
  `search_attempt_phase`、跳过标记与缺失字段。
- [x] 4.3 增加 Stage A 截断 / Stage B 中断的相位测试，重跑基准确认 `future_shanten`
  计数只统计 Stage A 截断。
