## Context

当前 `mj/bot.py::_choose_react()` 已经正确模拟 CHOW/PONG 的“副露 + 最佳弃牌后站立牌面”，但 legacy 同向听逻辑仍为：

```text
claim.s < pass.s   -> 接受
claim.s == pass.s  -> PONG ukeire +2 / CHOW ukeire +4 即接受
claim.s > pass.s   -> PASS
```

这无法区分“小幅多 2 张进张”和“从普通形升级成爆头、财飘机会、明显多面听/大幅增加下一摸降向听概率”之间的质量差异。

KONG 当前分为两套路径：

1. `KONG_OPEN`：只要合法，`_choose_react()` 整个 claim 窗口立即退回 `_legacy_claim_react()`，PONG 也一起退回；旧逻辑只比较副露后 shanten，并在同向听时优先 KONG。
2. 暗杠/加杠：`_choose_draw_action()` 用 `_evaluate_kong_next_draw()` 计算公开未见牌上的下一张补牌胡牌期望，与 HU/财飘/普通弃牌 baseline 比较，但没有结构占用守卫。

`mj/hand_eval.py::enumerate_decompositions()` 已能返回 material-safe 的标准形分解（melds / pair / taatsu / singles）且缓存结果，足够用来判断一张杠牌是否正在承担顺子、搭子、雀头或刻子职责，无需写“旁边是否存在 1m/2m”之类局部特判。

## Goals / Non-Goals

**Goals**

- legacy 吃碰只做可解释的“确定推进”：降向听，或同向听下的显著牌型升级。
- “显著牌型升级”至少覆盖爆头、财飘、听牌宽度、下一摸降向听能力四类。
- 三种 KONG 共用一致的硬门：结构安全 → 牌效不降 → 有真实杠开机会 → 才比较补牌 EV。
- `123333m` 一类“刻子与顺子共享同种牌”的暗杠必须稳定拒绝。
- 诊断输出可以回答“为什么吃/碰/杠”或“为什么拒绝”。
- 新逻辑保持公开信息约束和可接受的线上延迟。

**Non-Goals**

- 不把 legacy 改造成全局 score-EV 搜索器；复杂取舍仍留给 shape-v2 / Search Teacher / RL。
- 不允许为了番数主动增加向听。
- 不修改规则引擎的动作合法性和结算。
- 不在本 change 内做训练和阈值 A/B 大规模调参。

## Decisions

### D1 所有推进比较都落到“站立牌面”同口径

PASS 和 CHOW/PONG 的比较对象均为补摸前的 need 态站立暗牌。

- PASS：反应时点原暗牌。
- CHOW/PONG：副露完成后枚举合法立即弃牌，取最佳站立态。
- self KONG baseline：当前摸牌态选择最佳非杠弃牌后的站立态。
- post-KONG：杠完成、replacement draw 尚未发生的站立态。

禁止拿 need+1 的摸牌态直接与 need 态比较，也禁止用不同 evaluator/profile 产生 baseline。

### D2 新增 LegacyShapeProgress

建议在 `mj/bot.py` 内新增轻量不可变结构：

```python
@dataclass(frozen=True)
class LegacyShapeProgress:
    shanten: int
    ukeire_types: int
    ukeire_live: int

    baotou_ready: bool
    baotou_ukeire_types: int | None
    baotou_ukeire_live: int | None

    piao_draw_types: int
    piao_draw_live: int
```

语义：

- `ukeire_*`：
  - `shanten > 0` 时表示“下一摸可降低向听”的牌种/公开剩余张数；
  - `shanten == 0` 时表示真实胡牌等待种类/公开剩余张数。
- `baotou_ready`：`is_baotou_wait(standing, locked)`。
- `baotou_ukeire_*`：下一摸后存在合法弃牌可进入爆头听的公开剩余质量，复用 `baotou_ukeire`。
- `piao_draw_*`：枚举公开未见的下一摸 t；若 `standing+t` 可胡，且从该 14 张状态移除 1 张财神后仍为 `is_baotou(...)`，则 t 是结构性的财飘机会。只有现有墙量/收手守卫允许继续博飘时，该信号才可授权吃碰。

性能约束：

- 普通 `shanten/ukeire` 总是可算。
- `baotou_ready` 可直接算。
- 定量 `baotou_ukeire` 只在同向听、确有比较价值且 Rust kernel 可用时参与授权；无 Rust kernel 时记为 `None`，不能单独成为吃碰理由，禁止退化到 90~220ms 的 Python 全枚举热路径。
- `piao_draw_*` 仅在手里存在财神、同向听且墙/收手守卫允许时懒算。

### D3 吃碰第一硬门仍然是向听

```text
claim.shanten < pass.shanten  -> ACCEPT(reason=shanten_drop)
claim.shanten > pass.shanten  -> REJECT(reason=shanten_worse)
claim.shanten == pass.shanten -> 进入显著牌型推进 Gate
```

任何特殊结构奖励都不得突破“向听变差禁止副露”这一 legacy 边界。

### D4 同向听的显著推进分四类

同向听 claim 满足以下任一类即可进入 accepted set：

1. **爆头升级**
   - `PASS.baotou_ready == false && CLAIM.baotou_ready == true`；或
   - 双方都未 ready，但 `baotou_ukeire_live` 达到显著增益门槛。
2. **财飘升级**
   - PASS 没有结构性财飘下一摸机会，而 CLAIM 的 `piao_draw_live >= 2`；或
   - 已有机会时，`piao_draw_live` 达到显著增益门槛。
3. **听牌质量显著变宽（shanten == 0）**
   - live waits 达到显著增益门槛；或
   - 胡牌牌种增加至少 2 种，且 live waits 不减少。
4. **下一摸降向听能力显著增加（shanten > 0）**
   - `ukeire_live` 达到显著增益门槛。

“显著增益”第一版冻结为动作分档常量：

```text
PONG_MIN_ABS_GAIN = 4
CHOW_MIN_ABS_GAIN = 6
MIN_GAIN_RATIO    = 1.50
```

判定：

```text
delta = after_live - before_live
significant =
    delta >= MIN_ABS_GAIN[action]
    AND (before_live == 0 OR after_live >= ceil(before_live * 1.50))
```

这些值是保守初值，不声称数学最优；必须集中定义、写入诊断/版本指纹，并由固定牌例锁边界。后续只允许通过独立 calibration change 调整，不能在实现过程中随测试“拍到通过”。

### D5 多个 CHOW/PONG 候选的排序

先过滤未通过 Gate 的候选，再按以下稳定顺序：

1. 更低 shanten；
2. `baotou_ready` 为真优先；
3. 更高 `piao_draw_live`；
4. 更高 `baotou_ukeire_live`（未知视为不参与优势）；
5. 更高 `ukeire_live`；
6. 更多 `ukeire_types`；
7. 更低 `discard_shape_cost`；
8. 稳定 action id。

Gate 决定“做不做”，排序只决定“多个都值得做时选哪个”，不得让排序项反向突破 Gate。

### D6 KONG Structure Guard 使用最优 material-safe decomposition

三种杠都先读取当前暗牌的最优分解；只接受与当前最优 shanten 相容的 **standard** decomposition。七对严格领先时，任何会破坏七对资格的杠默认不通过结构门。

**暗杠 t**

至少存在一个最优 standard decomposition，使四张 t 的自然牌材料分配为：

- 3 张 t 构成同一个 natural triplet；
- 第 4 张 t 是 `single`；
- 第 4 张不得同时计入 sequence / pair / taatsu。

因此 `123333m` 中 3m 的最佳结构若为 `123m + 333m`，四张 3m 已全部被两个面子消耗，没有“triplet + redundant single”解释，暗杠 MUST 被拒绝。

**加杠 t**

已有公开 `pong(t)` 的前提下，手中的第 4 张 t 必须在至少一个最优 standard decomposition 中是 `single`；若它被 sequence / pair / taatsu 使用则拒绝。

**明杠 t**

反应前手里的 3 张 t 必须能在至少一个最优 standard decomposition 中共同构成 natural triplet。仅因为计数为 3 不足以证明可杠。

### D7 KONG 必须保持非杠 baseline 的牌效

结构门通过后，构造 post-KONG standing：

- closed：移除 4 张 t，`locked+1`；
- add：移除 1 张 t，locked 不变；
- open：移除手里 3 张 t，`locked+1`。

与同决策点的非杠 baseline standing 比较：

```text
post_kong.shanten > baseline.shanten -> REJECT
post_kong.shanten == baseline.shanten
    AND post_kong.ukeire_live < baseline.ukeire_live -> REJECT
baseline.baotou_ready
    AND NOT post_kong.baotou_ready -> REJECT
```

如果 post-KONG shanten 更低则视为牌效推进，可以继续下一门；但最终仍必须满足 D8 的杠开条件。

self-kong 的 baseline 必须由当前 evaluator 对应的 `discard_profile` 生成；`choose_discard(g, seat)` 不透传 profile 的现状必须修正。

### D8 KONG-KAI Gate：当前必须真的存在补牌立即成胡的机会

legacy 不做“先杠了再慢慢做牌”的杠。Structure Guard 与牌效门通过后：

1. post-KONG standing 必须 `shanten == 0`；
2. 活墙必须允许 replacement draw；
3. 按公开 visible 构造 `remaining[t] = max(0, 4-visible[t])`；
4. 对每个 remaining>0 的 t，测试 `post_kong + t` 是否可胡；KONG replacement draw 使用现有 `kong_draw=True` 的 YCBK 例外语义；
5. `winning_mass = sum(remaining[t] for winning t)` 必须 > 0。

只“理论听牌”但所有胡牌张均已见完时，MUST 拒绝 KONG。

### D9 KONG EV 只能在硬门之后比较

完整顺序固定：

```text
legal KONG
  -> structure_safe?
  -> shape_preserved?
  -> shanten == 0?
  -> winning_mass > 0?
  -> replacement-draw EV
  -> 与非杠 baseline / 同窗 PONG 比较
```

任何高 EV 都不得绕过前四个硬门。

对于 PONG + KONG_OPEN 同窗：

- PONG 正常走 D3/D4 推进 Gate；
- KONG_OPEN 走 D6~D9；
- KONG 不得覆盖一个 standing shanten 更低的 PONG；
- 同 shanten 时先比较共同的牌型进度指标；只有形状不劣且 KONG 有真实 replacement win mass 时，KONG 才能利用其补牌 EV/连杠价值胜出；
- 删除“`KONG_OPEN in acts` → 整窗 `_legacy_claim_react`”的特殊短路。

### D10 可归因诊断是行为契约的一部分

吃碰至少输出：

```text
reason: shanten_drop | baotou_progress | piao_progress |
        wait_expansion | ukeire_expansion | pass
before_progress
after_progress
thresholds
```

KONG 至少输出：

```text
structure_safe
structure_reason
baseline_progress
post_kong_progress
kong_wait_tiles
kong_winning_mass
kong_win_probability
rejection_reason
```

固定牌例测试断言核心 reason，避免后续“结果一样但逻辑偷偷漂移”。

## Risks / Trade-offs

- **legacy 会明显更保守**：部分旧版 +2/+4 的边缘吃碰将变为 PASS。这是目标行为；复杂的微小 EV 优势留给高级 evaluator。
- **阈值初值仍需校准**：4/6 + 1.5x 是保守规则，不声称最优。通过 replay/teacher 分析另开 change 调整，禁止在本 change 里边测边改。
- **decomposition 成本**：KONG 仅在合法杠动作出现时才枚举，复用 `enumerate_decompositions` 缓存；不得把结构分解放到每个普通 discard 热路径。
- **爆头定量计算成本**：无 Rust kernel 时只保留 `baotou_ready` 的廉价类别信号，定量 baotou-ukeire 不授权副露。
- **财飘机会是近一步结构信号**：它描述“下一摸能形成可飘结构”，不是完整多轮 EV；墙量/收手守卫继续生效。
- **训练分布漂移**：教师更少做边缘副露和无意义杠，BC/RL 数据会变化；本 change 不混入训练结果以保持归因。
