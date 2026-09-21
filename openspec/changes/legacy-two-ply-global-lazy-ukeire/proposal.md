# Proposal

## Why

LegacyV2 已经通过成本收口减少了部分无效工作，但 weighted two-ply 仍在每个
`root × draw` 完成 child shanten 后立即计算 child ukeire。当前 raw Rust p99
仍在 50ms 以上，且大多数在线决策最终因前瞻不完整而回退；需要利用既有
词典序排序，让 future improvement 已经可以证明胜负时完全跳过次级指标。

## What Changes

- 将 weighted two-ply 拆成 Future Shanten 阶段和按需 Future Ukeire 阶段。
- Future Shanten 阶段按未见牌权重累计 improvement/maintain，并使用
  lower/upper bound 提前确认 winner；确认后不再计算任何 child ukeire。
- 只有 improvement 无法决定胜负时，才进入 Future Ukeire 阶段，保持现有
  完整排序和 exact parity。
- 将 hard deadline 传入 child discard 与 ukeire candidate 内层，并预留少量
  返回/转换时间，避免单次 ukeire DFS 把 50ms 硬预算拖穿。
- 解释中显式区分 `future_shanten_only`、完整 two-ply、回退和是否真正使用
  weighted 结果；缺失的 future ukeire 不得伪装为零。
- benchmark 增加 search-used rate、搜索阶段和两阶段工作量统计。

## Capabilities

### New Capabilities

<!-- No new public capability; this changes the existing LegacyV2 contract. -->

### Modified Capabilities

- `bot-hand-evaluation`: weighted two-stage frontier、bounds 早停及内层硬截止。
- `bot-decision-explanations`: 记录搜索阶段、使用状态和未计算的次级指标。

## Impact

主要代码为 `rust/src/lib.rs`、`mj/legacy_eval.py` 和
`scripts/legacy_two_ply_weighted_bench.py`；同步更新 weighted frontier 测试
及 OpenSpec。PyO3 外层 root row tuple 保持兼容，Stage-A-only draw row 使用
内部显式 sentinel，由 Python adapter 映射为缺失 future ukeire。exact/offline
V1 及 public-information 边界不改变。
