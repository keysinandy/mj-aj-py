## Context

`mj/bot.py` 的 `choose_action()` 分两支：discard 阶段（已在 9f4f8de 重构，含 HU/`_should_piao` 财飘路径）与 react 阶段的 `_choose_react()`。当前 react 侧逻辑：

```python
eval_after(act, remove) = shanten(hand - remove, locked + 1)   # shanten-only
if after_s <= cur_s: 执行 best_act
```

三个缺陷：① 副露后必须立即舍牌，`hand - remove` 是 need+1 态，`shanten()` 在其上只隐式取最优子集，ukeire 与结构损失完全不可见；② 等向听时进张减半的碰照样被接受；③ PASS 基准 `cur_s = shanten(hand, locked)` 只覆盖向听数单一维度——张数口径本身正确，`bot.py` 现注释"含刚打出的候选牌"系误导（反应玩家 `g.hands[seat]` 本就不含他家 pending 牌），真正缺的是与 claim 最终站立牌面同口径的 ukeire/结构比较。

引擎侧基础设施齐备：`shanten()` = min(标准形, 七对) 且 `_chiitoi` 在 `locked>0` 时返回 9（`shanten.py:154-156`）；`ukeire(counts, locked, visible)` 按 `4 - visible[t]` 折算真实剩余进张；`Game.visible_counts(seat)` 含自己手牌+四家牌河+全部副露（杠计 4 张）。

## Goals / Non-Goals

**Goals:**

- 吃/碰决策评价"副露 + 最佳弃牌后的站立牌面"，门槛判据 `(shanten, ukeire)`，多候选择优用弃牌结构损失。
- PASS 有对称基准：反应时点站立暗牌原样评价。
- 等向听时按动作分档 ukeire 增量阈值（PONG ≥ +2、CHOW ≥ +4）约束副露成本。
- 七对分支获得"自然偏向保护"（PASS 基准保留七对分支、副露后评价自动退出），不加特判。
- 固定牌例回归 + vis 快照不变量测试锁行为。

**Non-Goals:**

- KONG 三态统一（暗杠/加杠进 choose_action、明杠补牌期望、死搭子杠守卫）——另开 change。
- 1-step lookahead、阶段调整、动态防守权重、番型期望（副露杀七对豪华番的同向听场景）。
- 管线重跑与阈值 A/B 调参。

## Decisions

### D1 评价对象 = claim + 枚举舍牌后的站立牌面（need 态）

张数推导（本设计冻结的修正口径）：反应前暗牌 `13 − 3·locked` = need；吃/碰 remove 2 张后为 `11 − 3·locked` = `need + 1`（`locked+1` 站立态要求 `13 − 3·(locked+1) = 10 − 3·locked`）。因此：

```text
post_claim = hand - remove            # need+1
for d in 合法舍牌(post_claim):
    standing = post_claim - d         # need
    eval(standing, locked + 1, vis) → (s, uke)
```

取最优 d 的评价（`s, uke, discard_shape_cost, d`）作为该 claim 的评分。`shanten()` 的 `need` 张数 ValueError 断言天然把关口径，测试覆盖 0/1/2 副露三档。

*备选（否决）*：直接在 need+1 态上调 `shanten()`（现状做法的延伸）——只覆盖 shanten 一个维度，ukeire/shape 对"必须舍哪张"不可见。

### D2 PASS 基准 = 反应时点站立暗牌原样评价，判据收口为 (shanten, ukeire)

`_eval_standing(hand, locked, vis)` → `(s, uke)`，hand 即 13−3·locked 张站立暗牌，不做任何增删。与 claim 侧门槛判据同键同源。**shape 不进入 PASS 基准**：`_discard_shape_cost(hand, t)` 的语义是"从弃牌前手牌打掉 t 的结构损失"，必须同时知道弃牌前手牌与被弃牌张；站立手牌没有"本次弃牌"可指，PASS 侧无法得到同语义的 shape，强行定义等于引入第二套结构模型。`_best_post_claim_discard` 因此返回 `(s, uke, discard_shape_cost, d)`，`discard_shape_cost` 只作多候选择优。

*备选（否决）*：① 保留现状 `cur_s`——张数口径正确但只覆盖向听数单维，副露成本不可见；② 为 PASS 伪造一个站立手 shape——与 claim 侧的弃牌结构损失语义不可比。

### D3 决策规则：向听分层 + 动作分档 ukeire 增量阈值

```text
claim.s < pass.s            → 接受
claim.s == pass.s           → uke_gain = claim.uke - pass.uke ≥ GAIN[action] 才接受
claim.s > pass.s            → PASS
GAIN: PONG = 2, CHOW = 4    # 初值，固定牌例锁定后不再动
```

多个已过门槛的 claim 在**当前 react mode 的合法候选**内按 `更低 shanten → 更高 ukeire → 更低 discard_shape_cost → 稳定 action 顺序` 选最优。react mode 事实（`game.py:169-186`）：claim 窗只产生 PONG/KONG_OPEN，吃窗只产生 CHOW——PONG 与 CHOW 不会同窗竞争，无需设计跨 mode 比较语义。

*备选（否决）*：浮点总分（副露成本折成标量加在总分上）——难解释难调试；分层规则与用户此前提案第 8 节理由一致。

### D4 vis 快照复用：claim 前 vis == claim 后、弃牌前 vis

已核实 `game.py:209-232`：`_do_discard` 先把 pending 牌 `append` 进 `discards[owner]` 再 `_begin_react`；claim 时 `_pop_pending_discard()` 才从牌河弹出（`game.py:323-325`）。因此反应时点取的 `visible_counts(seat)` 已含 pending 牌；claim 后它进自家副露（可见）、手里拿走的 2 张从自家手牌移到自家副露（可见总数不变）。同一 vis 快照贯穿 PASS 基准与各 claim 评价，写成测试不变量。

### D5 七对：不加特判，依赖分支自动退出

PASS 基准的 `shanten(hand, locked)` 含七对分支（locked==0 时），claim 侧 `shanten(·, locked+1)` 因 `_chiitoi` 在 locked>0 返回 9 而只剩标准形。七对为更优分支的局面 PASS 基准天然压过副露评分。表述为"自然偏向保护"而非绝对 guard：副露后标准形可能向听更低；平胡/七对同向听时也可能因 ukeire 阈值接受副露。

### D6 KONG 窗口整体沿用 legacy 决策（含同窗的 PONG）

明杠真实流程为 remove 3 → 成杠 → 立即补牌 → 才舍牌（`Game` 的 `KONG_OPEN → replacement=True → _draw(kong=True)`）。把它套进"claim + 最佳弃牌"评价的是实际不会出现的状态；严格做需按剩余张数对补牌加权期望，react 成本与性能闸门冲突。

且 KONG_OPEN 与 PONG **同窗竞争**：手持三张 pending 牌时 `_legal_reacts` 同时给出 PONG 与 KONG_OPEN（`game.py:178-180`），旧实现把二者放进同一 `best_key` 比较、同向听时 KONG 优先（key 次项 -1 < 0）。PONG 换新评价体系后无法保证与 KONG 的相对结果不变——"PONG 全部升级 + KONG 原样迁移"两个要求无法天然同时满足。因此本 change 的边界是：

```text
KONG_OPEN ∈ acts                    → 整个 claim 窗口(含 PONG)沿用既有决策,不进新 evaluator
KONG_OPEN ∉ acts 且 PONG ∈ acts     → PONG 用 v2 完整评价
```

代价是"手持三张时的 PONG"暂不升级。固定牌例锁 KONG 窗口行为不漂移；KONG 统一（含补牌期望）另开 change。

### D7 不动的东西（硬边界）

- `choose_action()` discard 分支：HU 优先、`_should_piao` 财飘判定，一行不改。
- 弃牌侧 `choose_discard()` 及其结构/喂牌辅助函数。
- 引擎/规则层（`game.py`/`shanten.py`/`scoring.py`）零改动。
- `evaluate` 的启发式 bot 随 `mj.bot.choose_action` 同步升级为 v2（`evaluate.py:14` 直接 import，无 v1 runtime 可保留）；前后对比用同一命令，对变更前 commit `9f4f8de` 与变更后版本、同机同内核环境各测 3 次取中位。

## Risks / Trade-offs

- [React 性能回退：吃窗 3 吃法 × ≤11 舍牌候选 + PASS 基准 ≈ ≤34 次 ukeire、纯碰窗 ≤12 次（legacy KONG 路径不计）] → 两阶段评价（向听扫描先行、ukeire 懒算：向听门槛外零 ukeire、PASS 基准仅在等向听时算、唯一候选且向听严格下降直接接受）；实测吃窗均 3.9 ukeire/窗（最大 30）、纯碰窗均 2.5（最大 12）；Rust 内核默认路径（40~97x）；合入闸门（可判定）：同机同内核环境，变更前后各 3 次 `python3 -m mj.evaluate 200`，4 bots 段中位吞吐（elapsed/games）下降 ≤15%（实测交错 A/B -2%~-10%，机器后台负载波动；ukeire 的 `(bytes, locked)` 记忆化对重复子手牌有效）。
- [阈值初值 (2, 4) 拍脑袋] → 第一版只靠固定牌例锁定语义（等向听时碰要明显改善、吃要更明显改善），不做 A/B；牌例断言的是边界行为不是最优性。
- [等向听 uke_gain 规则可能拒绝"结构上该吃的副露"（如为守听/防守）] → 接受：本规则集吃碰不加番（动作链只数飘/杠），副露只换向听推进，保守方向正确；漏吃候选由后续 KONG/lookahead change 处理。
- [七对同向听时豪华番潜力被 ukeire 阈值放过的副露杀死] → 已知限制，记入 Non-Goals；固定牌例只锁"七对为更优分支时 PASS"。
- [bot 行为漂移影响下游] → 教师分布变化是本 change 的目的而非事故；管线重训明确 out-of-scope，避免与本 change 混杂归因。
