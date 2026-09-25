## Why

当前生产普通弃牌已经由 legacyV2 的 weighted two-ply 处理，但这次牌例暴露出一个明确的评价缺口：

- 决策前暗手为 23455m、124s、东东、白，副露 789p；
- 弃 1s 与弃 4s 后均为 0 向听；
- 两者当前普通进张完全相同，均为 11 张：5m×2、3s×4、东×2、白×3；
- 弃 1s 后保留 24s，弃 4s 后保留 12s；
- 24s 的后续延展显著优于 12s，例如再摸 5s 时可以弃 2s 留 45s 两面；
- 当前实现却可能因 shape_loss / feed_risk 的旧 tie-break 偏向弃 4s。

根因不是 two-ply 没有执行，而是 two-ply 当前能看到未来 draw→discard，却没有完整评价未来 standing hand 的结构质量：

1. mj.bot._discard_shape_cost() 衡量的是“打掉当前这张牌有多伤”，不是“弃牌后整手牌形态有多好”。它对 124s 会把弃 1s 的局部损失估得比弃 4s 更高，因此不能作为 24s 与 12s 的 standing shape 真值。
2. Rust weighted Stage B 当前对子节点的比较主要是 child shanten、child ukeire、ukeire tile types；当这些指标相同，45s 与 12s 仍可能被视为等价。
3. root 聚合目前没有 future shape 指标，即使某个 draw 分支选出了更好的结构，也无法稳定地把该优势反馈到 root 决策。
4. max_frontier_candidates 截断与 shape_guard 仍依赖现有 shape_loss；在候选很多时，结构更优的 standing hand 甚至可能在 two-ply 之前被错误裁掉。

本 change 的目标是让 legacyV2 在不增加搜索深度的前提下真正理解“同向听、同进张时的结构质量”，修复至少以下稳定关系：

- 中张两面 > 中张坎张 > 边缘坎张 > 边张；
- 23s > 24s > 13s > 12s；
- 对称关系 78s > 68s > 79s > 89s；
- 在给定专项牌例中，当前进张与 future shanten/ukeire 不劣时，应优先保留 24s 而不是 12s。

## What Changes

- 新增独立的 standing hand shape evaluator，禁止继续把 _discard_shape_cost 当作 post-discard hand quality。
- standing shape evaluator 第一版使用版本化的 taatsu quality 语义，并至少区分：
  - RYANMEN：23～78；
  - CENTRAL_KANCHAN：24～68；
  - EDGE_KANCHAN：13 / 79；
  - PENCHAN：12 / 89。
- 对完整手牌使用固定上界的 suit decomposition / DP，避免一个 tile 同时被多个局部 pattern 重复计分。
- 保留现有 _discard_shape_cost 作为兼容/诊断字段，但新增 standing_shape_quality，并逐步把以下 tie-break 改为 standing 语义：
  - root frontier cap；
  - shape_guard；
  - final root tie-break。
- 扩展 weighted two-ply Stage B：
  - child shanten 最优；
  - child ukeire 最大；
  - child ukeire types 最大；
  - child standing shape quality 最大；
  - 最后才稳定 tile tie-break。
- 新增 future_shape_quality 聚合，使每个 root 能看到一摸一弃后的平均结构质量，而不是只看 future ukeire。
- root comparator 在 future ukeire / types 之后、legacy discard cost 与 feed risk 之前比较 future shape quality。
- Python reference 与 Rust kernel 必须同语义；Rust kernel version 必须升级，旧 wheel 必须安全 fallback。
- 新能力先以 profile 开关 / 实验 evaluator 启用，完成专项、全量、积分与性能门禁后才允许提升为 legacyV2 默认。
- 不增加搜索 horizon，不增加 max_frontier_candidates，不提高 online 50ms hard budget。

## Validation Summary

验收分四层：

1. 结构语义专项：固定 pattern、镜像、重叠 pattern 与用户牌例。
2. 搜索正确性：Python/Rust parity、Stage B child 选择、root future shape 聚合、frontier cap/shape_guard 与 fallback。
3. 积分验证：paired/fair A/B，先 4096 局筛查，再至少 30720 局独立种子精跑。
4. 时间性能验证：同机交错 baseline/candidate，报告 ordinary discard p50/p95/p99/max、Rust raw、Stage B 进入率、complete/partial/fallback、4-bot elapsed/game。

默认切换必须同时通过正确性、积分非退化和性能门；任一失败保持 feature flag 关闭。

## Out of Scope

- 不做 three-ply 或更深搜索。
- 不引入 MCTS、hidden-world rollout 或对手暗牌推断。
- 不改变 shanten、ukeire、胡牌、爆头、财飘、七对与计番规则。
- 不用一个无单位大 magic score 覆盖 shanten / ukeire。
- 不把防守风险删除；feed risk 仍保留，但只在更高优先级牌效与结构指标相同后生效。
- 不在本 change 训练 BC/PPO/RL。
- 不修改 reaction/KONG 的独立 shape progress 语义，除非复用同一纯 shape helper 不改变其行为。

## Capabilities

### New Capabilities

- legacy-two-ply-shape-quality：定义 post-discard standing shape、taatsu quality、未来 shape 聚合与专项验收语义。

### Modified Capabilities

- legacy-two-ply-weighted-frontier：child/root comparator 接入 shape quality，frontier cap 使用 standing shape。
- legacy-two-ply-frontier-shape-guard：shape_guard 的“结构明显更优”改为 post-discard standing hand 语义。
- legacy-two-ply-rust-kernel：原生 Stage B 增加 child shape 比较与 future shape 聚合，并升级 kernel contract/version。

## Impact

主要代码预计涉及：

- mj/bot.py
- mj/legacy_eval.py
- mj/shanten.py 或新增 mj/shape_quality.py
- rust/src/lib.rs
- tests/test_bot.py
- tests/test_shanten.py / 新增 tests/test_shape_quality.py
- legacyV2 benchmark / paired A/B 脚本
- replay / evaluation diagnostics

历史字段 shape_loss 不应静默改变含义；建议保留原值并新增 standing_shape_quality / future_shape_quality，等 A/B 完成后再决定是否废弃旧字段。
