# Design

## Context

上一 change（`legacy-two-ply-global-lazy-ukeire`）已把搜索拆成 Stage A（future shanten）
与 Stage B（child ukeire）。若不做并行，剩余成本分布为：

```text
Stage A   约 6–8ms   （约 1.2k 次 child shanten，硬预算 6ms 时 80 个状态里 55 个已全覆盖）
Stage B   约 35–40ms （约 10k 次 shanten，其中 14 张手牌调用占多数）
未截断预算 p50 43ms、max 143ms，8/60 仍撞 node_budget
```

同时 100 状态样本里没有一个状态被 Stage A 的 bounds 提前判胜（前沿 improve 精确并列），
所以剪枝性价比为零，唯一可用的杠杆是常数级吞吐。

## Goals / Non-Goals

**Goals:**

- Stage B 并行化，在线 50ms 预算下 raw p50 进入 25ms 以内，且完成率显著上升。
- 与串行路径逐位一致：动作、future 指标、best discard 行、计数器求和、中止原因都不依赖
  线程数。
- 保持既有预算/事务语义：deadline 与 work budget 仍然整次 Stage B 生效。
- 不引入跨调用的可变全局状态；缓存仍为调用/线程局部。

**Non-Goals:**

- 本 change 不改排序语义、不改 `min_partial_coverage`、不做 decision-safe bounds 的新证明。
- 不并行化 Stage A（它的 bounds 提前结束依赖 draw 主序，保持串行）。
- 不做 shanten 花色记忆化（另一条独立路线），不引入 rayon 等新依赖。
- 不改变 Python 侧 root 构造、解释映射或 legacy fallback 逻辑。

## Decisions

### 1. 分片粒度：按 draw 轮转分片

Stage B 的工作单元是 `(root, draw)`。分片按 draw 轮转（`draw_index % workers`），因此
同一 draw 的所有 root 落在同一 worker 内，保留
`child(root x, draw d, discard y) == child(root y, draw d, discard x)` 的对称缓存复用；
若按 (root, draw) 均匀分片，对称对会被拆散，缓存命中率下降。

### 2. 工作单元与结果结构

Stage A 结束后，把每个 root 的 `stage_a_rows` 展开为工作单元：

```text
StageBUnit { root_index, order, draw, weight, child_s, best_discards }
StageBOutcome { root_index, order, draw, weight, child_s, best_u, best_types, tied }
```

`order` 是该行在 `stage_a_rows` 中的位置。聚合时按 `(root_index, order)` 排序，保证
`draw_rows` 的顺序与串行实现完全相同（weight 降序的 draw 顺序）。

### 3. Worker 缓存

每个 worker 持有一份空的 shanten/ukeire 缓存，容量沿用 `cache_capacity`。Stage A →
Stage B 的共享命中只覆盖每个 `(root, draw)` 的第一次 child shanten 调用（约 1% 的调用），
clone 整个缓存不划算；worker 私有缓存同时保证无跨线程共享可变状态。

### 4. 中止语义

每个 worker 在每个 child discard 与每个 ukeire candidate 前检查内部 hard deadline，
并使用 join 时的剩余 work budget 作为本片上限。join 后：

- 任一 worker 报告 hard deadline → 整次 Stage B 报 `hard_deadline`；
- 否则若任一片报告 work budget，或各片合计未缓存 shanten 超过预算 → 报
  `work_budget_exceeded`；
- 中止时沿用现有行为：清空 `future_ukeire`/`draw_rows`，reason 写入所有 root，
  Python 侧仍然 `partial_not_acceptable` 回退 legacy。

原因优先级固定（deadline 先于 work budget），避免并行下出现随调度变化的回退原因。

### 5. 并行度配置与回退

`LegacyTwoPlyProfile.workers`（默认 0）经原生调用传入；Rust 侧解析顺序为
`workers > MJ_KERNELS_THREADS > available_parallelism（上限 8）`，并裁剪到工作单元数。
`workers<=1` 或工作单元数 <= 1 时走原串行路径，保证 parity 测试可以对照。

`workers` 只进入解释 JSON，不进入 profile fingerprint 的 `_payload`：它与
`soft_budget_ms`/`hard_budget_ms` 同级，属于部署/预算旋钮，不应因为只改并行度就让既有
记录的 profile fingerprint 失效。

### 6. 计数器

`root_candidates`、`draw_nodes` 来自 Stage A，保持不变；`shanten_calls`、
`ukeire_calls`、`child_nodes`、四个 cache hit/miss 计数由各 worker 求和。整数求和与顺序
无关，因此计数与串行一致（缓存清空时机可能不同，属已知的非语义差异，见 Risks）。

## Risks / Trade-offs

- **[Risk]** 单次决策瞬间占满多核，与同机客户端/自博弈 rollout 争 CPU。→ 并行度可配置，
  默认上限 8，可设 `workers=1` 回退；benchmark 必须记录并行度与机器负载。
- **[Risk]** 线程创建开销（每片约 30µs）。→ 工作单元数 <= 1 时不建线程；必要时后续再做
  持久线程池，本 change 先用 `std::thread::scope`。
- **[Risk]** 每片缓存工作集变小，命中率、cache 清空时机与串行不同。→ 只影响性能，不影响
  结果；benchmark 记录 hit rate 供调参。
- **[Risk]** 依赖 legacyV2 标签的数据集（BC 数据默认 `--evaluator legacyV2`）在加速后
  完成率上升，标签分布变化。→ 新版本数据单独建 campaign，不与旧 shard 混用；评估需
  同时记录 kernel 版本与并行度。
- **[Risk]** 并行度进入解释字段后，旧日志缺少该字段。→ 读取侧按缺失处理，不补造。

## Migration Plan

1. Rust 侧加入工作单元结构、worker 执行函数与并行驱动，保留串行分支。
2. Python 侧加 `workers` 字段并透传，解释/benchmark 暴露并行度。
3. 运行 parity（workers=1/2/4）、确定性、中止语义与既有测试；重建 wheel 后跑 100 状态基准。
4. 若 raw p50 已达标则保持默认自动并行；否则评估线程池或 shanten 花色表作为独立 change。

## Open Questions

None for this phase.
