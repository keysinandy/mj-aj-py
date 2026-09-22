# Design

## Context

Stage B 的成本由 child ukeire 决定，而 child ukeire 的绝大多数开销是 14 张手牌的
shanten 调用（每状态约 10k 次调用、约 6k 次未缓存）。并行化只分摊墙钟，不减少总量：
每 worker 私有缓存让唯一计算量再增约 33%（命中率 0.32 → 0.13）。因此单线程常数因子
仍然决定：① 低核/被占用机器上的 p95；② 跑满截止的那 1-3 个状态造成的 p99。

历史测量（本机，load 3.8-6.1，需在干净机器复核）：

```text
workers=1: raw p50 18.7ms / p95 48.7ms，每决策 CPU 约 24ms
workers=8: raw p50  7.3ms / p95 17.9ms，每决策 CPU 约 51ms
未截断单线程：p50 约 15-20ms，最坏状态约 50ms（更重负载下曾测到 150-250ms）
```

## Goals / Non-Goals

**Goals:**

- 单线程 shanten 吞吐提升 3-5x，使 workers=1/2 也能稳定满足 raw p95 < 40ms 与
  p99 < 48ms，并显著降低每决策 CPU。
- 与参考实现逐位一致（含财神、locked、七对、张数校验）。
- 表只读共享，供并行 worker 直接读；不引入跨调用可变状态。

**Non-Goals:**

- 不改变排序语义、预算、解释字段与 profile 默认值；不改变并行度解析。
- 不在本 change 内做根批量 FFI、draw 采样或近似排序。

## Decisions

### 1. 表结构

每个花色（万/条/筒）的 9 张计数打包成 base-5 整数（最多 5^9 ≈ 1.95M 组合），映射到该
花色的 Pareto 可行集合 `(melds, partials, pair, wilds_used)`，其中 `melds ≤ 4`、
`partials ≤ 4`、`pair ∈ {0,1}`。字牌块单独用 7 张计数（4^7）或直接沿用现有 DFS，
取决于对拍结果。

### 2. 合并与评分

4 个块的集合做小 DP（状态量级 `melds × partials × pair × wilds`，很小），再调用现有
`score()` 与 `need_melds` 逻辑，保证与参考实现同一评估口径；七对分支保持独立。

### 3. 表的构建与共享

表 MUST 构建后只读（启动预计算或 `OnceLock` 惰性填充），worker 通过共享引用读，不加锁。
若出现构建失败或规则变化，退回现有 DFS 路径并记录原因，结果口径不变。

### 4. 落地顺序与开关

先扩展现有 Rust/Python 差分对拍（随机手牌 + 财神 + locked + 张数边界），再让新内核
opt-in（环境变量或内核版本门槛），通过 parity 与基准后再决定是否设为默认。

## Risks / Trade-offs

- **[Risk]** 财神与 locked 组合的分解等价性容易写错。→ 差分对拍必须覆盖财神数量、
  locked 0-4、张数 13/14 与跨花色组合；不一致时禁止放默认。
- **[Risk]** 表占内存（5^9 上界）。→ 只对实际出现的花色模式惰性填充，并设容量上限。
- **[Risk]** 首表构建时间进入决策预算。→ 允许启动时预热；或在首次调用时一次性构建。
- **[Risk]** 与并行 worker 的交互。→ 表只读 + worker 私有结果缓存，保持确定性。
- **[Risk]** 维护成本：两套实现并存。→ 保留参考 DFS 作为对拍与回退路径。

## Migration Plan

1. 扩展差分对拍脚本与测试，作为新内核的验收门。
2. 实现花色表 + 合并 DP，先用开关 opt-in。
3. 跑 parity、全套测试与 100 状态基准（workers=1/2/4/8 与每决策 CPU）。
4. 达标后设为默认并记录 kernel 版本；否则保持 opt-in 并在 tasks 里记录结论。

## Open Questions

- 表是否需要覆盖字牌块，还是仅覆盖三个数牌花色后保留现有字牌 DFS。
- 默认开关切换后是否需要提升 `weighted_two_ply_kernel_version`。
