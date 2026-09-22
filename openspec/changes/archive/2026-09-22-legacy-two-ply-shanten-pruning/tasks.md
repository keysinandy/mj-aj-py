# Tasks

## 1. 实验实现

- [x] 1.1 在 `std_shanten` 中按三种确定性优先序生成贪心分解路径，并以其 `score()`
  作为 `std_dfs` 的初始上界（每条路径都落在既有合法分支内）。
- [x] 1.2 保持分支集合、剪枝界数学与 `score()` 口径不变。

## 2. 验证

- [x] 2.1 `scripts/rust_parity.py --n 300`：shanten / ukeire / baotou_ukeire 全部通过。
- [x] 2.2 逐调用微基准（4000 随机手 + 4 个病理手）：p50 4.4→5.6µs，尾部无改善。
- [x] 2.3 同会话两个 wheel 交替跑 100 状态基准（workers=1/auto，各 2 轮）：
  workers=1 complete 66/57→51/43，p50 +12~18ms。

## 3. 结论

- [x] 3.1 否决该方向并回退代码：`rust/src/lib.rs` 保持与 `858e594` 一致，默认仍是
  逐牌 DFS，`MJ_KERNELS_SHANTEN=memo` 仍是唯一 opt-in 分支。
- [x] 3.2 记录结论与下一步候选（降低调用次数、跨调用只读表、或接受现状），证据见
  `evidence/shanten-pruning-benchmark.json`。
