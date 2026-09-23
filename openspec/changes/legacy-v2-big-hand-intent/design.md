## Context

当前普通弃牌链路可以抽象为：

```text
legal discards
    ↓
mj.bot.choose_discard
    ↓
只保留 min shanten roots
    ↓
legacy_eval._root_features
    ↓
min shanten
    ↓
非财神优先
    ↓
max current ukeire
    ↓
shape_guard（必要时扩围）
    ↓
max_frontier_candidates = 3
    ↓
Rust weighted_two_ply_frontier
    ↓
legacyV2 comparator
```

这条路径非常适合低延迟牌效决策，但它不会主动保留一个已经在 root 层被 `best_s` 删除的长期高番路线。

已有 `shape_guard` 提供了本 change 的核心架构参考：**guard 只改变谁有资格进入固定大小的搜索 frontier，不改变搜索深度。**

同时，当前 weighted comparator 的排序键没有 root shanten：

```text
wildcard protection
current ukeire
future improve
future ukeire mean
future types
shape loss
feed risk
stable tile
```

原因是历史 contract 保证 frontier roots 的 shanten 相同。因此 `best_s+1` 不能直接混入该 comparator。

## Goals / Non-Goals

**Goals**

- 在 legacyV2 上增加“会做牌”的最低成本版本，不引入 shape-v2 的全量 score EV。
- 七对/豪华七对/多财神意图使用 public-only、O(34) 静态画像。
- 同向听先上线；`+1 shanten` 必须是独立 challenger/override，而不是破坏现有 comparator。
- frontier 搜索根数不增加，hard budget 不增加。
- 任何 incomplete/fallback 都回到变更前相同的 speed/fallback 行为。
- 所有大牌决策必须可解释、可复盘、可 A/B。

**Non-Goals**

- 不证明“豪华七对一定比普通胡更优”；第一版是候选保护与保守 override。
- 不建立统一番型 EV 总分。
- 不做 opponent race 的精确概率模型。
- 不让高番路线突破晚局/高压收手。
- 不在 reaction/KONG 路径复制本能力。

## Decisions

### D1 BigHandIntent 是纯特征，不是第二个搜索器

新增概念结构：

```python
@dataclass(frozen=True)
class BigHandIntent:
    kinds: tuple[str, ...]               # 可同时命中多个，不是互斥 target
    strength: str                        # NONE / WEAK / MEDIUM / STRONG
    chiitoi_shanten: int
    pair_units: int
    natural_pairs: int
    luxury_groups: int                   # 当前已经自然持有 4 张的组数
    luxury_upgrade_tiles: tuple[int, ...]# 当前 3 张且第四张公开仍存活
    luxury_upgrade_live: int
    wild_count: int
    wild_live: int
    protected_tiles: tuple[int, ...]
    reasons: tuple[str, ...]
```

实现要求：

- 仅遍历 34 种牌；
- 不递归；
- 不调用 `baotou_ukeire()`；
- 不调用 `future_values()`；
- 不调用 shape-v2；
- 不读取 opponent concealed / wall order；
- 输入相同时结果确定。

`kinds` 可同时包含 `CHIITOI/LUXURY_CHIITOI/WHITE_RICH`。禁止强制只选一个目标，因为同一牌面可能同时具有七对与多财神潜力。

### D2 七对向听只有一个真源

当前 `mj.shanten` 已有 `_chiitoi()` 逻辑。不得在 `big_hand_intent.py` 复制一份稍有不同的算法。

建议改为：

```python
def chiitoi_shanten(counts, locked=0):
    ...
```

然后：

```text
shanten._chiitoi -> delegate / alias
BigHandIntent    -> same helper
```

必须有 parity test 锁住：

- 无财神；
- 1/2/3/4 财神；
- locked > 0；
- 3 张自然牌 + 财神；
- 4 白板特殊组合。

该 helper 只返回七对距离，不改变总 shanten 选择。

### D3 豪华潜力必须区分“已经拥有”和“仍可升级”

对于自然牌 `t < W`：

```text
hand[t] == 4
    -> luxury_groups += 1

hand[t] == 3
AND remaining[t] > 0
    -> luxury_upgrade_tiles += t
    -> luxury_upgrade_live += remaining[t]
```

其中：

```text
remaining[t] = max(0, 4 - visible[t])
```

若本家手里已有 3 张且第四张已公开可见：

```text
remaining[t] == 0
```

则该 tile MUST NOT 产生豪华升级保护。

3 真牌 + 财神补成 4 张不得计为 natural luxury group，与现有 `is_chiitoi()` / scoring 规则保持一致。

### D4 WHITE_RICH 只表示资源价值，不伪造番型

`WHITE_RICH` 第一版用于表示：

```text
wild_count >= 2
```

且手牌存在值得保护的七对/对子/紧凑结构。

它不是计番标签，不直接声称未来一定爆头/财飘/4 白板。

早期：

```text
Cheap WHITE_RICH intent
```

只用于候选保护。

当进入现有：

```text
hand[W] > 0 and best_s == 0
```

后，仍由 `_choose_discard_baotou()` + Rust `baotou_ukeire` 做精确爆头排序。

已经 HU 时仍由 `_should_piao()` 和现有墙量/收手规则决定财飘。

### D5 choose_discard 必须区分 all roots 与 speed pool

当前实现边枚举边删除更高 shanten roots。legacyV2 路径改为概念上的：

```python
all_roots = enumerate_legal_discards(...)
best_s = min(root.shanten for root in all_roots)

speed_pool = [
    root for root in all_roots
    if root.shanten == best_s
]
```

要求：

- `legal_actions()` / freeze 仍是合法性真源；
- 显式 legacy/rollback 路径可以继续只使用 `speed_pool`；
- legacyV2 才把 `all_roots` 元数据交给 big-hand candidate selector；
- 最多额外提名 1 个 `big_hand_challenger`；
- 不是所有 `best_s+1` roots 都进入 weighted search。

### D6 LegacyRootCandidate 增加意图元数据，但 fallback eligibility 独立

建议增加：

```python
intent_kinds: tuple[str, ...] = ()
intent_strength: str = "NONE"
chiitoi_shanten: int | None = None
luxury_groups: int = 0
luxury_upgrade_live: int = 0
wild_count: int = 0
shanten_regression: int = 0
admission_hint: str | None = None
```

不得只靠这些字段判断 fallback。

必须显式维护：

```text
speed_pool_tiles
big_hand_challenger_tiles
```

或等价 eligibility 标记。

原因：过去 `legacy = min(enriched, key=_legacy_key)` 的前提是 enriched roots 同向听。引入 `+1 shanten` 后，如果不显式分池，kernel timeout 时可能错误 fallback 到更高向听 root。

### D7 Phase A：同向听 BigHandGuard 先上线

第一阶段不允许升向听，仅在：

```text
root.shanten == best_s
```

内增加保护。

典型作用：

- 两个候选同 shanten；
- 普通 current ukeire 使某个会拆豪华机会的候选成为 primary；
- 另一个候选能保留自然四张/三张活豪华升级/强七对结构；
- `big_hand_guard` 可以把后者重新纳入固定 frontier。

Phase A 的排序仍然完全使用现有 weighted two-ply comparator。

因此 Phase A 不破坏“所有参与 comparator 的 root shanten 相同”这一不变量。

### D8 Phase B：只允许一个 `best_s+1` 强意图 challenger

Phase B 仅在后续 A/B 门禁通过后启用。

候选必须同时满足：

```text
root.shanten == best_s + 1
locked == 0
intent_strength == STRONG
```

并满足至少一种强路线：

**LUXURY_CHIITOI**

```text
chiitoi_shanten <= 1
AND (
    luxury_groups > speed_winner.luxury_groups
    OR luxury_upgrade_live > 0
)
```

**WHITE_RICH + CHIITOI**

```text
wild_count >= 2
AND chiitoi_shanten <= 1
AND pair_units >= configured minimum
```

第一版 MUST NOT 接受 `best_s+2`。

### D9 `+1 shanten` 必须有局势收手 Gate

强意图也不能无条件做大。

Phase B challenger 还必须通过：

```text
live_wall_left >= BIG_HAND_MIN_LIVE
max_opponent_melds <= BIG_HAND_MAX_OPP_MELDS
```

建议初始 calibration defaults：

```text
BIG_HAND_MIN_LIVE = 24
BIG_HAND_MAX_OPP_MELDS = 1
```

这些只是待 A/B 校准的 profile 参数，必须：

- 进入 profile fingerprint；
- 出现在 diagnostics；
- 通过后续 calibration change 调整；
- 不散落 hard-code。

若已有现成的收手 helper 可安全复用其 public-state 语义，可以复用；但不得把爆头 X/Y/Z 的“推进轮数”直接套到七对，因为平台镜像的 per-Game 轮数语义不同。

### D10 frontier 总数仍为 3，并使用保留槽策略

online `max_frontier_candidates` 继续为 3。

当存在 challenger 时，推荐：

```text
slot 1: speed primary / speed winner ancestor
slot 2: 现有 normal/shape guarded candidate
slot 3: big-hand challenger
```

实现可以使用等价的稳定算法，但必须满足：

- 至少保留 1 个 speed candidate；
- big-hand 最多占 1 个 slot；
- 总数 <= `profile.max_frontier_candidates`；
- 不因 BigHandIntent 把 3 扩成 4；
- `shape_guard` 与 `big_hand_guard` 同时存在时，diagnostics 明确记录谁被 cap 淘汰。

建议执行顺序：

```text
_root_features
-> normal weighted primary
-> shape_guard
-> big_hand_guard
-> final guarded frontier cap
```

最终 cap 不能简单沿用只按 ukeire/shape 的旧排序把刚 admitted 的 big-hand candidate 又无条件挤掉；需要显式 slot policy。

### D11 same-shanten 与 `+1 shanten` 使用不同选择语义

**same-shanten candidate**

可以进入原 comparator：

```text
current ukeire
future improve
future ukeire mean
future types
shape/feed/stable id
```

BigHandIntent 的职责主要是“让它有资格比较”，而不是向 comparator 添加魔法分。

**best_s+1 challenger**

不得进入同一 comparator 直接和 speed roots 排序。

流程：

```text
speed_frontier
    ↓ existing legacyV2 selection
speed_winner

big_hand_challenger
    ↓ same Rust weighted evaluation（只为安全/未来质量信息）
big_hand_override_gate

override ? challenger : speed_winner
```

### D12 BigHand Override 第一版必须保守

`+1 shanten` override 至少要求：

- D8 强意图；
- D9 局势收手通过；
- challenger weighted evaluation 完成，或 online partial 满足现有 safe-partial contract；
- challenger 当前有效 ukeire 不低于版本化绝对下限；
- 相对 speed winner 的 current ukeire 损失不超过版本化上限；
- challenger 的公开 live luxury upgrade 仍 >0（若依赖 luxury upgrade）；
- 若该候选的高价值理由是 `WHITE_RICH`，弃牌后不得把 `wild_count` 降到该理由失效。

第一版不要求把不同 shanten 的 `future_improve_weight` 直接相减做总分，因为两者“improve”的事件语义不同。它只能作为 challenger 自身质量 guard / diagnostics，不能伪装成统一 EV。

初始参数必须通过离线网格扫描确定；在没有扫描结果前，Phase B 可以实现但默认关闭。

### D13 不允许 `intent_score` 与 weighted future 直接相加

禁止：

```text
Q = future_improve
  + luxury_bonus * 20
  + white_bonus * 5
```

第一版用离散 guard：

```text
eligible / not eligible
strong / not strong
early enough / too late
live upgrade / dead upgrade
ukeire loss acceptable / too large
```

理由：

- 当前 legacy future 指标不是 score EV；
- 豪华七对倍率与成型概率不在同一单位；
- 直接加权会制造难以解释和难以校准的魔法分。

后续如果需要统一 score EV，应单独走 shape-v2/teacher calibration change，而不是污染 legacyV2。

### D14 fallback 必须完全保持旧 speed 语义

以下任一情况：

- Rust weighted kernel unavailable/version mismatch；
- deadline/budget exceeded；
- partial_not_acceptable；
- big-hand intent unknown；
- challenger future incomplete；
- override gate 数据缺失；
- diagnostics serialization failure（不得影响动作）。

在线最终 action MUST 来自旧 speed/fallback pool。

特别禁止：

```text
kernel failed
-> 使用 big-hand guard 后的 root set 跑 _legacy_key
```

因为这可能让 `best_s+1` 候选在没有 weighted 证据时胜出。

建议在 `_weighted_evaluation()` 开始时就计算并冻结：

```text
legacy_speed_best
```

并在所有 fallback path 复用同一个值。

### D15 原爆头/财飘精确路径优先级保持

当前：

```text
hand[W] > 0 and best_s == 0
```

进入 `_choose_discard_baotou()`。

本 change 不把该逻辑替换为 BigHandIntent。

优先关系：

```text
已进入精确爆头 scope
    -> 现有 baotou logic
否则
    -> legacyV2 + BigHandIntent
```

`_should_piao()`、`PIAO_WALL_GUARD=6`、X/Y/Z 收手、freeze 下只能弃刚摸财神等既有行为必须保持回归。

### D16 reaction/KONG 第一版不接 BigHandIntent

本 change scope 是普通 discard。

原因：

- reaction 已有独立 legacy-react-v2；
- claim 会改变 locked，七对路线天然失效或大幅改变；
- KONG 对豪华七对有直接破坏关系，需要单独的 same-unit 设计。

因此第一版：

```text
ordinary discard -> BigHandIntent
CHOW/PONG/KONG    -> current legacy reaction/kong behavior
```

后续若要“为了七对拒绝碰”或“豪华七对避免杠”，单开 change。

### D17 Diagnostics / Replay Contract

每个 ordinary discard candidate 至少可输出：

```text
tile
shanten
current_ukeire
intent_kinds
intent_strength
chiitoi_shanten
pair_units
luxury_groups
luxury_upgrade_tiles
luxury_upgrade_live
wild_count
wild_live
shanten_regression
admitted_by
big_hand_gate_reason
```

根级输出：

```text
speed_pool_tiles
speed_winner
big_hand_challenger
big_hand_override
big_hand_override_reason
frontier_tiles
frontier_cap_dropped
phase = same-shanten | plus-one | disabled
```

回放 UI/报告应能回答：

> 为什么这张牌虽然眼前牌效更差仍被保护？

以及：

> 为什么已经识别出豪华路线但最终还是走速度牌？

### D18 性能与发布门禁

冻结本 change 前的当前 `main` legacyV2 为性能/行为基线。

Phase A 默认启用前必须满足：

- BigHandIntent 本身 p95 <= 1ms；
- online weighted frontier root count 始终 <= 3；
- legacyV2 ordinary discard p95 不因该层增加超过 10%；
- 4-bot `elapsed/games` 同机同 Rust 内核交错各至少 3×200 局，中位退化 <=10%；
- kernel fallback rate 不显著升高；
- 原 legacyV2、baotou/piao、freeze 固定牌例通过。

Phase B 默认启用前除以上外，还必须满足：

- 至少 4096 局 paired/fair self-play；
- 相对 Phase A 的平均结算收益 95% CI 下界 >= 0，或先保持 opt-in；
- `+1 shanten` override 发生率、按 intent kind 的收益与 regret 有独立报告；
- 晚局/对手高副露桶不得出现系统性负收益；
- 超时/fallback 时 challenger 选择率必须为 0。

若 Phase B 未通过收益门禁，代码可以合入但 profile 默认：

```text
big_hand_plus_one_enabled = false
```

同向听 Phase A 可以独立上线。

## Risks / Trade-offs

- **启发式不是最终 EV**：BigHandIntent 只能防止明显的长期路线被过早剪掉，不能证明某个大牌路线全局最优。
- **+1 shanten 风险最高**：因此必须与同向听保护分阶段上线，并使用独立 override。
- **frontier 争槽**：shape guard 与 big-hand guard 会竞争 3 个槽，需要可审计的稳定 slot policy。
- **多财神与七对耦合**：财神既能补标准形也能补七对，WHITE_RICH 只能作为资源信号，不能硬编码成“必做七对”。
- **参数校准**：live wall、opponent meld、ukeire loss 等门槛需要 self-play/teacher 数据，不应靠单牌例拍值。
- **历史 fallback 前提被打破**：这是实现中最危险的兼容点，必须先显式冻结 speed fallback，再接 challenger。