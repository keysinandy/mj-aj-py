## Why

当前生产普通弃牌存在两条与本 change 直接相关的路径：普通候选进入 legacyV2 weighted two-ply；而“持白板财神且最小向听为 0”的牌面会先进入 `baotou_scope`，由 `_choose_discard_baotou()` 选择后直接返回。本次牌例首先暴露的是 baotou 专用路径的 shape tie-break 缺口，同时也暴露了普通 weighted two-ply 的 child/future shape 缺口：

- 决策前牌串为 `23455m 124s EE w`，副露 `789p`；牌串记号中小写 `w`（或 `B`）表示白板财神，大写 `W` 表示西风；内部常量 `tiles.W=33` 仍指白板财神，两套记号不得混淆；
- 弃 1s 与弃 4s 后均为 0 向听；
- 两者当前普通进张完全相同，均为 11 张：5m×2、3s×4、东×2、白×3；
- 弃 1s 后保留 24s，弃 4s 后保留 12s；
- 24s 的后续延展显著优于 12s，例如再摸 5s 时可以弃 2s 留 45s 两面；
- 当前实现却可能因 shape_loss / feed_risk 的旧 tie-break 偏向弃 4s。

该完整牌例的直接根因不是 weighted two-ply leaf comparator，而是 `baotou_scope` 在 weighted 路径之前提前返回；同时普通 weighted two-ply 也存在同类 shape 缺口。两部分必须分别定义、分别验收：

1. `choose_discard()` 在 `hand[tiles.W] > 0 && best_s == 0` 时进入 `baotou_scope`；只要 Rust `baotou_ukeire` 可用且未触发收手/预算回退，`_choose_discard_baotou()` 会直接返回，后续 weighted roots、shape_guard 与 Stage B 都不会执行。
2. 当前 baotou 排序键为 `tier → 不弃财神 → baotou_ukeire → 旧 shape_loss → feed_risk → tile`。本例 1s/4s 的财神档位与爆头进张打平后，旧局部弃牌损失 `5 vs 3` 使 4s 胜出。
3. `mj.bot._discard_shape_cost()` 衡量的是“打掉当前这张牌有多伤”，不是“弃牌后整手牌形态有多好”，因此不能作为 24s 与 12s 的 standing shape 真值。
4. 对非 baotou_scope 的普通路径，Rust weighted Stage B 当前主要比较 child shanten、child ukeire、ukeire tile types；当这些指标相同，45s 与 12s 仍可能被视为等价。
5. weighted root 聚合目前没有 future shape 指标；frontier cap 与 shape_guard 也仍依赖旧 shape_loss。

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
- 保留现有 `_discard_shape_cost` 作为兼容/诊断字段，但新增 `standing_shape_quality`，并把它接入所有实际可到达的 relevant tie-break：
  - root frontier cap；
  - shape_guard；
  - final weighted root tie-break；
  - `baotou_scope` 的财神档位/爆头进张之后、旧 shape_loss 之前。
- `baotou_scope` 不强制绕回 weighted two-ply。shape-aware 模式保留既有 `tier → 财神保护 → baotou_ukeire` 优先级，只在这些指标打平后加入 standing shape，再落到旧 discard cost/feed/tile；关闭开关时必须逐决策恢复旧 key。
- 第一版不在 baotou_scope 内额外运行 generic future-shape two-ply；若 standing shape 仍不足，后续必须以独立、有预算的 baotou future metric 另立 change，不能暗中扩大当前热路径搜索。
- 扩展普通 weighted two-ply Stage B：
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

1. 结构语义专项：固定 pattern、镜像、重叠 pattern 与用户牌例；用户完整牌例必须实际命中 `baotou_scope` 并由 baotou shape tie-break 改选 1s。
2. 路径正确性：分别验证 `baotou_scope` 早退路径，以及普通 weighted 路径的 Python/Rust parity、Stage B child 选择、root future shape 聚合、frontier cap/shape_guard 与 fallback。
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
- bot-baotou-piao-discard：在既有爆头 tier / 财神保护 / baotou_ukeire 完全打平后使用 standing shape，保持旧预算与 fallback 语义。
- bot-decision-explanations：明确输出实际 `decision_scope=baotou_scope|weighted_two_ply|legacy` 及 baotou 候选级 tie-break 证据。

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
