# Proposal

## Why

Stage B 并行化（`1ca8465`）后延迟已达标，但单线程仍没有加速；花色记忆化
（`858e594`）实测中位数打平、仅尾部略好。剩下的单线程假设是"少数状态剪枝失效导致
离群调用"，对应手段是给 `std_dfs` 一个贪心初始上界或更强剪枝。本条记录该实验的
实现口径、对拍结果与 A/B 结论，避免重复投入同一条路线。

## What Changes

- **不修改内核行为**：实验结果不达标，`rust/src/lib.rs` 保持与 `858e594` 逐字一致，
  `MJ_KERNELS_SHANTEN=memo` 仍是唯一 opt-in 分支。
- 记录实验内容：三种确定性优先序的贪心分解路径（刻子/顺子/两枚+财神成刻/对子/两面/
  坎张），用其 `score()` 作为 `std_dfs` 的初始上界；每条路径都落在既有合法分支内，
  因此不会剪掉最优解。
- 记录结论与下一步可选项（降低调用次数、跨调用只读表、收紧 ukeire 候选集）及其
  前置验证要求。
- 无规格行为变化：动作、future 指标、解释字段、预算口径均未改动，因此本 change
  `skip_specs: true`。

## Capabilities

（无新增或修改的能力；本 change 只记录实验与度量结论）

## Impact

- 仅 `openspec/changes/legacy-two-ply-shanten-pruning/`（proposal/design/tasks/evidence）。
- 代码无改动；轮子无需重建。
