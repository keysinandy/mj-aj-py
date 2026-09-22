# Proposal

## Why

`legacy-two-ply-parallel-stage-b` 已经让延迟达标，但代价是 CPU：同一批状态下每决策
CPU 从约 24ms（1 worker）升到约 51ms（8 worker，含每 worker 私有缓存带来的重复
shanten 计算），且 p99 完全由极少数跑满 48ms 内部截止的状态决定。在 ≤2 核、或与
客户端/自博弈训练同机的场景里，并行收益会明显缩水（实测 workers=2/4 时 p95 仍是
37ms）。单线程常数因子是唯一还没动过的杠杆。

## What Changes

- 用按花色的记忆化分解表替换 `std_shanten` 的逐 tile DFS：每个花色的计数向量映射到
  该花色可行的 (面子, 搭子, 对子, 财神消耗) Pareto 集合，再由 4 个块（3 花色 + 字牌）
  合并后交给现有 `score()`。
- 表 MUST 构建后只读并可被并行 worker 共享；worker 私有的 shanten/ukeire 缓存继续保留，
  且只作为"手牌 → 值"的二级缓存。
- 结果 MUST 与 `mj/shanten.py` 参考实现逐位一致：财神配刻/配对/成对雀头、locked 副露
  剩余面子、七对分支与张数校验全部保持。
- 不改排序语义、预算参数、解释字段、profile 默认值或并行度解析。

## Capabilities

### New Capabilities

（无新增能力）

### Modified Capabilities

- `bot-hand-evaluation`: 向听内核允许记忆化，但 MUST 与参考实现逐位一致，且表 MUST
  只读共享、不依赖调用顺序或线程调度。

## Impact

- `rust/src/lib.rs`: `std_shanten`/`std_dfs`/`SAVE` 界与表构建。
- `tests/`: 差分对拍（含财神与 locked）、并发读表一致性与全套回归。
- Python 适配层、profile、benchmark 契约不变；达标前保持 opt-in（见 design）。
