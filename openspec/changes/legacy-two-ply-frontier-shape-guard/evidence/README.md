# Evidence — 2026-09-21 线上记录

## 1. 短路与降级的规模（当日三批，`local/games/20260921`）

| 指标 | 计数 |
| --- | --- |
| 决策总数 | 2175 |
| `level=legacy-one-ply`（`frontier_singleton` 短路） | 809 |
| `fallback_reason=native_weighted_kernel_unavailable` | 606 |
| `actual_kernel=legacy` / 未标 | 1415 / 760 |
| `actual_kernel=rust` | **0** |

结论：当日没有任何一次决策使用过加权前瞻；短路（37%）与内核缺失回退（28%）合计覆盖了六成以上决策。

## 2. 样本局面（`u_9812ba08fe2f_a_a3de8f7235a2_r1_b9_t0`，局1）

用 `mj.bot.choose_discard` + `LegacyTwoPlyProfile.weighted_online()`（指纹 `90e7b267d64ab861`，与记录一致）在 `.venv` 内离线复算，数字与记录逐项一致：

| seq | 记录选择 | 手牌 | 直接进张/结构损失（选中 vs 次优） | 事后层级 |
| --- | --- | --- | --- | --- |
| 107 | 4s（拆刻子） | `7w 9w 4p4p 5p5p 4s4s4s 5s 7s7s 东东` | 4s 12/13 vs 9w 11/3 | `frontier_singleton` |
| 154 | 5s（拆结构） | `5w 7w 4p4p 5p5p 2s 4s4s 5s 7s7s 东东` | 5s 13/6 vs 5w/7w/2s 11/3 | `frontier_singleton` |
| 68 | 3w（并列） | `3w 7w 9w 1p 4p4p 5p5p 4s4s 5s 7s 东东` | 3w 19/2 与 1p 19/2 完全相同 | `native_weighted_kernel_unavailable` → 按牌编号 |
| 178 | 5w（并列） | `5w 7w 4p4p 5p5p 1s 2s 4s4s 7s7s 东东` | 四候选均 13 张，结构 5w=7w=3 < 1s=5 < 2s=6 | 同上 |

## 3. 内核安装前后（同一复算脚本）

```
# 安装前 / MJ_KERNELS=python：等价于记录里的降级态
seq=68  level=legacy            kernel=legacy  fallback=native_weighted_kernel_unavailable
seq=107 level=legacy-one-ply    kernel=legacy  short=frontier_singleton
seq=178 level=legacy            kernel=legacy  fallback=native_weighted_kernel_unavailable

# 安装 rust/target/wheels/mj_kernels-0.1.0-cp311-cp311-macosx_15_0_x86_64.whl 到 .venv 后
seq=68  level=weighted-two-ply-v1 kernel=rust  （3w 与 1p 的 future 指标完全相同：ukeire_mean 23.20、types_mean 7.41）
seq=107 level=legacy-one-ply      kernel=legacy short=frontier_singleton（设计短路，护栏未开时不受内核影响）
seq=178 level=weighted-two-ply-v1 kernel=rust
```

复算脚本：`evidence/replay_b9_check.py`（重建 seq=68/107/178 的公开状态 → `choose_discard(..., profile=weighted_online())`；`MJ_KERNELS=python .venv/bin/python evidence/replay_b9_check.py` 可复现降级态）；`tests/test_legacy_rust_kernel.py` + `tests/test_legacy_weighted_frontier.py` 共 21 项在 `.venv`（装内核后）全部通过。

## 4. 待补证据（本变更的闸门）

- 冻结回放的成对 A/B（护栏开关）决策变化数与加权指标分布 —— 任务 5.1/5.2。
- 40/50ms 预算下的延迟分位与窗口损失 —— 任务 5.3。
- 参数网格 `slack ∈ {0,1,2} × delta ∈ {6,8,10}` 的扫描结果 —— 任务 5.2。

注：3 节的内核降级是**环境事实**（`.venv` 缺少 `mj_kernels`），与护栏设计相互独立；缺内核时护栏按设计跳过（需求 4），因此本变更不把该降级算作护栏收益。
