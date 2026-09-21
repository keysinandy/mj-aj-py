# Tasks

## 1. 差分为先

- [x] 1.1 用现有 `scripts/rust_parity.py` 在 memo 与默认 DFS 两种模式下分别对拍
  (shanten/ukeire/baotou_ukeire，含财神、locked、张数非法口径)。
- [x] 1.2 记录 Stage B 口径的对照基线：100 状态基准在 dfs/memo × workers=1/auto 下的
  complete、raw p50/p95/p99 与每决策 CPU。

## 2. 花色记忆化内核

- [x] 2.1 实现单花色分解表（base-5 计数打包 → Pareto `(m, t, p, wilds_used)`，按最大财神
  数构建、合并时按实际财神预算过滤）。
- [x] 2.2 实现 4 块合并并接回现有 `score()`/`need_melds` 口径，保留七对与张数校验分支。
- [x] 2.3 加入 opt-in 开关 `MJ_KERNELS_SHANTEN=memo`；表在调用/线程内可见，构建后只读。

## 3. 验证与基准

- [x] 3.1 通过两种模式的差分对拍与 focused 全套测试（122 passed）。
- [x] 3.2 跑 100 状态基准（dfs/memo × workers=1/auto）与每决策 CPU 对照，结果见
  `evidence/shanten-memo-benchmark.json`。
- [x] 3.3 结论：memo 的中位数与 DFS 相当（会话间方向不一致），尾部稳定更好
  （p95 48.2→37.3、p99 24.0→18.2），但达不到 3-5x 目标 → **默认保持逐牌 DFS**，
  花色表保持 opt-in；单线程进一步加速应改走"给 DFS 加贪心上界/更强剪枝"。
