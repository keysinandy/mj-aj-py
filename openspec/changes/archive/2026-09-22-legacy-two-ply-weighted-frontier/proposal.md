# Proposal

## Why

当前 Rust two-ply 内核已经消除了 Python 逐子节点 FFI 的主要开销，但完整 frontier 在 50ms 在线预算下仍经常超时。其主要成本来自重复的无财神向听 DFS、对不可能成为最佳子节点的 child 计算 ukeire，以及整个 frontier 完成后才提交结果。

需要把 two-ply 从“全量、一次性提交”的离线模型拆成可控的 weighted frontier：按照公开信息下的理论剩余张数加权，延迟计算 child ukeire，优先覆盖高权重分支，并在满足覆盖率和关键候选条件时安全接受 partial 结果；完整 exact 模式继续作为 replay、回归和 teacher 使用。

## What Changes

- 新增 `legacyV2` 在线评价 profile（内部内核仍为 `weighted_two_ply_frontier`），默认 soft deadline 40ms、hard deadline 50ms；`weighted-two-ply-frontier-v1` 等名称作为兼容别名，`legacy` 保留为显式回退。
- 将下一摸按 `max(0, 4-visible[tile])` 加权，记录 `total_weight`、`covered_weight` 和 coverage，而不是把 34 种牌视为等概率。
- 对 child 先批量计算 shanten，只对最低 shanten 的并列 child 计算 ukeire，并记录 ukeire 牌种数。
- Rust 统一缓存含财神和不含财神的 shanten 状态；补充真实 shanten/ukeire 调用、缓存命中和覆盖率指标。
- 增加 frontier 上限和按高剩余权重优先的 draw 顺序；支持 candidate-level commit 和满足门槛的 partial result。
- 合并 root frontier 的重复计算，保持公共信息、抓打圈、财神和反应窗口规则门禁在 Python 侧不变。
- 保留 exact two-ply 离线模式和 `MJ_KERNELS=python` 对拍路径；所有结果继续携带 profile、内核、预算和回退/partial 原因。
- 增加典型 `3t`/`9b` 回归、公开信息隔离、超时 partial、冻结合法性和 weighted 指标测试，并更新 50ms 基准证据。

## Capabilities

### New Capabilities

- `legacy-two-ply-weighted-frontier`: 面向在线 50ms 预算的概率加权、lazy child ukeire、coverage 和 partial two-ply 评价契约。

### Modified Capabilities

- `bot-hand-evaluation`: two-ply 的 weighted draw、lazy child、partial 接受条件和 exact/offline 分层发生变化。
- `bot-decision-explanations`: 解释需要输出 weighted future 指标、coverage、partial 状态和真实搜索 metrics。

## Impact

- 主要代码：`mj/legacy_eval.py`、`mj/bot.py`、`mj/shanten.py`、`mj/logview.py`、`rust/src/lib.rs`，必要时拆出 Rust 内部 cache/metrics 模块。
- API：新增 profile/config 字段和评价 JSON 字段；保留已有 legacy、Python 对拍和 Rust V1 接口兼容。
- 测试与证据：新增 Rust/Python parity、partial transactional boundary、真实 public-visible 状态基准；根据本轮发布决策将 `legacyV2` 设为默认，保留完整性能/回退遥测。
- 依赖：不新增第三方依赖；Rust wheel 仍按已有 PyO3/Maturin 构建。
