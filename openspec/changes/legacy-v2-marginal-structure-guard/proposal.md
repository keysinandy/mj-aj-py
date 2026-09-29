## Why

当前 legacyV2 已有 standing shape、marginal structure guard 与 weighted two-ply，但仍存在第二层“直接进张优先级过硬”问题：即使结构更好的候选进入 two-ply，只要 root comparator 仍把 raw current_ukeire 当作同向听后的绝对第一排序键，future 与结构仍没有反超机会。

新的用户牌例：

~~~text
actual discard 2w:
  shanten=2
  current_ukeire=57
  ukeire_types=17
  shape_loss=5
  feed_risk=0
  marginal role=丢失一组搭子

counterfactual discard 4s (log tile 4t):
  shanten=2
  current_ukeire=48
  ukeire_types=15
  shape_loss=2
  feed_risk=0
  marginal role=connected singleton，不破坏对子/搭子

discard 4s 后:
2w 3w 3w 7w 8w 8w 9w 4b 5b 5b 6b 7t 9t
~~~

48/57=84.2%。在 2 向听阶段，这仍属于有竞争力的直接速度；旧绝对 gap guard 会过早淘汰 4s，而仅扩大 guard 也不足以解决 root comparator 的绝对 current_ukeire 优先级。

本 change 增补：

- shanten-aware competitive speed band；
- speed dominance；
- bounded Pareto frontier；
- band 内 root comparator 降低 raw current_ukeire 的绝对优先级。

目标不是“结构永远优先”，而是只有当直接速度形成明显支配时才允许它一票否决；否则让 future speed 与结构角色共同裁决。

## What Changes

- 新增 legacy-two-ply-speed-band capability。
- 初版 competitive speed ratio：
  - shanten 0: 1.00
  - shanten 1: 0.90
  - shanten 2: 0.82
  - shanten >=3: 0.78
- 同最小向听 roots 先做 speed dominance，再做 bounded Pareto pruning。
- Pareto 至少考虑 current_ukeire、ukeire types、marginal loss、standing shape。
- band 内 root comparator 调整为：
  1. future shanten improvement
  2. future ukeire
  3. future ukeire types
  4. marginal structure loss
  5. future shape
  6. standing shape
  7. raw current ukeire
  8. feed risk / stable tile
- 57 vs 48 at shanten=2 必须处于同一 competitive band；4s 不得仅因少 9 张直接进张被淘汰。
- 899s golden 继续要求阻断 raw-speed singleton。
- 7899s + 5w 继续防止“对子硬保护”。
- frontier 仍 <=3；two-ply 深度与 50ms hard budget 不变。
- diagnostics 增加 speed ratio/band、speed dominated/by、Pareto dominated/by。
- feature off 恢复当前 main 行为。

## Out of Scope

- 不做 three-ply/MCTS/hidden-world rollout。
- 不读取对手暗牌或真实墙序。
- 不把结构角色折成一个 magic float score。
- 不硬编码 2w/4s、899、7899。
- 不提高 frontier cap 或 online hard budget。
- 不保证所有“84%速度+更好结构”的牌面都固定选择结构候选；最终取决于 two-ply future 指标。

## Capabilities

### New Capabilities

- legacy-two-ply-speed-band

### Modified Capabilities

- legacy-marginal-structure-role
- legacy-two-ply-frontier-shape-guard
- legacy-two-ply-weighted-frontier
- legacy-two-ply-rust-kernel
- bot-decision-explanations

## Impact

预计涉及 mj/legacy_eval.py、root frontier、weighted root comparator、Stage A safe-partial certificate、diagnostics、tests/benchmark/A-B。Rust 搜索深度与预算不增加；若 native 负责 winner certificate，必须同步新 comparator 语义。
