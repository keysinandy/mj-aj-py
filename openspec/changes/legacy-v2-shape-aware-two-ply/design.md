## Context

当前普通弃牌实际上先分流；本 change 必须覆盖两条可到达路径：

~~~text
legal discard roots
    ↓
root shanten / best_s
    ↓
hero 持白板财神 && best_s == 0 ?
    ├─ yes → push X/Y/Z guard
    │        ↓
    │      _choose_discard_baotou()
    │        ↓
    │      成功则直接 RETURN
    │      （不进入 weighted two-ply）
    │
    └─ no / baotou fallback
             ↓
         current ukeire
             ↓
      shape_guard / frontier cap
             ↓
      Rust weighted two-ply
             ↓
      Stage A: future shanten improvement mass
             ↓
      Stage B: child shanten + child ukeire + child ukeire types
             ↓
      root weighted comparator
~~~

因此，用户完整牌例属于 `baotou_scope` golden；普通 weighted two-ply 的 child/future shape 必须用独立 fixture 验证，不能用同一个最终动作测试假装覆盖两条路径。

这里存在两个不同概念，当前代码却部分混在一起：

A. discard damage
- 问题：现在打掉 t 会破坏多少邻接/对子/刻子关系？
- 当前实现：mj.bot._discard_shape_cost(hand, t)
- 输入：弃牌前手牌 + 被打 tile
- 适合：保留为解释字段或最后级别 tie-break

B. standing shape quality
- 问题：弃完牌后，当前 standing hand 本身的后续结构质量如何？
- 这是 24s 与 12s、13s 与 12s、45s 与 12s 的真正比较对象。
- 当前 legacyV2 缺少独立真值。

本 change 明确拆开 A 与 B。

## Goals / Non-Goals

### Goals

- 同 shanten、同 current ukeire、同 future shanten/ukeire 时能稳定偏好更好的 standing shape。
- 让 two-ply 的第二层真正认识 child standing shape。
- 让 future child shape 能回传到 root，而不是只影响一次 child discard 的稳定 tie-break。
- root frontier cap 与 shape_guard 不再依赖“弃掉哪张牌更伤”来近似“弃完后手牌更好”。
- Python/Rust 完全同语义。
- 固定搜索深度、固定 frontier 上限、固定 50ms hard budget。
- 可关闭、可回滚、可审计。

### Non-Goals

- shape quality 不得覆盖 shanten。
- shape quality 不得覆盖明显更大的 current/future ukeire。
- 第一版不建立全局番型 EV。
- 第一版不尝试证明某个 taatsu 分值是绝对麻将价值，只要求稳定、可解释、可 A/B。
- 不修改 hidden-information 边界。

## Decisions

### D1 新增 StandingShapeQuality，保留旧 discard_shape_cost

不得直接把 _discard_shape_cost 改名后继续使用，因为其语义是“当前 tile 被打掉时损失多少”。

建议新增：

~~~python
@dataclass(frozen=True)
class StandingShapeQuality:
    taatsu_count: int
    ryanmen_count: int
    central_kanchan_count: int
    edge_kanchan_count: int
    penchan_count: int
    pair_units: int
    isolated_count: int
    encoded: int
    version: str
~~~

LegacyRootCandidate 增加：

~~~text
discard_shape_cost      # 旧字段语义，兼容
standing_shape_quality  # 新字段，越高越好
shape_quality_version
~~~

旧 shape_loss JSON 若必须兼容，继续输出 discard_shape_cost，不得在同名字段下静默改语义。

### D2 taatsu quality 的最低契约

数牌两张结构第一版 MUST 满足：

~~~text
RYANMEN:
23 34 45 56 67 78

CENTRAL_KANCHAN:
24 35 46 57 68

EDGE_KANCHAN:
13 79

PENCHAN:
12 89
~~~

同其他条件下排序：

~~~text
RYANMEN > CENTRAL_KANCHAN > EDGE_KANCHAN > PENCHAN
~~~

因此至少必须成立：

~~~text
23 > 24 > 13 > 12
78 > 68 > 79 > 89
~~~

这不是通过对 tile 编号加偏置实现，而是 standing hand decomposition 的显式结构属性。

### D3 完整手牌用非重叠 decomposition，禁止重复计分

直接扫描所有相邻 pair 会把 234 同时算成 23 与 34，产生重复奖励。

第一版 SHALL 对每个 9-rank suit 做固定上界 decomposition / DP：

- 每次消费一个完整 triplet、sequence、pair、taatsu 或 isolated tile；
- 相同 tile copy 不得被两个 unit 重复使用；
- 对所有合法 decomposition 取稳定最优 signature；
- honors 只贡献 triplet/pair/isolated，不产生 taatsu class；
- 财神/locked 仍由 shanten/ukeire 处理，shape helper 不伪造 wildcard 替代后的虚拟两面。

推荐 signature 为稳定的离散 tuple，而不是任意浮点 magic score。至少包含：

~~~text
taatsu_count
ryanmen_count
central_kanchan_count
edge_kanchan_count
penchan_count
pair_units
isolated_count
~~~

encoded 只用于跨 FFI / 聚合，必须由 versioned tuple 进行无碰撞或有界可解释编码，不能成为新的独立语义来源。

### D4 用户专项牌例必须成为 golden fixture

固定 fixture：

~~~text
concealed before discard:
23455m 124s EE w

open meld:
789p
~~~

候选：

~~~text
discard 1s -> 23455m 24s EE w
discard 4s -> 23455m 12s EE w
~~~

已知条件：

~~~text
both shanten = 0
both current ukeire = 11
waits:
5m x2
3s x4
E  x2
w  x3
~~~

当其他更高优先级指标相同，且 feed_risk 差异只处于当前低风险 tie-break 层时：

~~~text
standing_shape(24s) > standing_shape(12s)
selected discard = 1s
~~~

该完整牌例 MUST 通过真实 `baotou_scope` 路径验收，而不是直接调用 weighted root evaluator 绕开早退。其最终动作修复依赖 baotou tie-break 的 standing shape。

另外必须建立一个**独立的 weighted Stage B fixture**（可直接构造 speed-equivalent roots）覆盖未来结构：

~~~text
root retains 24s
simulate draw 5s
    -> best child discard 2s
    -> retains 45s
    -> child shape superior to a speed-equivalent penchan continuation
~~~

两个 fixture 都不允许通过写死 1s/4s 或特定牌号特判。

### D5 baotou_scope 必须使用 shape-aware tie-break，且不得假装进入 two-ply

当前 `_choose_discard_baotou()` 的稳定 key 为：

~~~text
baotou tier
不主动弃白板财神
1.5 * baotou_ukeire + current legal self-draw HU ukeire descending
legacy discard shape cost ascending
feed risk ascending
stable tile
~~~

shape-aware profile 开启后改为：

~~~text
baotou tier
不主动弃白板财神
1.5 * baotou_ukeire + current legal self-draw HU ukeire descending
standing shape quality descending    # new
legacy discard shape cost ascending
feed risk ascending
stable tile
~~~

约束：

- baotou tier 与财神保护仍先于加权进度；爆头进张与当前合法自摸胡牌进张不再分先后级，
  统一比较 `1.5 * baotou_ukeire + current_selfdraw_hu_ukeire`；
- standing shape 只在加权进度分打平后生效；
- 本阶段不调用 generic weighted two-ply，也不新增 future-shape DFS；
- shape-aware 关闭时仍使用相同加权进度分，只省略 standing-shape tie-break；
- Rust baotou kernel 不可用、节点预算超限或 X/Y/Z 收手时，继续沿用既有整档 fallback，不允许 shape 部分结果污染 fallback；
- 用户完整牌例的修复 MUST 来自此路径。

若未来需要在 baotou_scope 内比较“下一摸后的 shape”，必须新增单独的、有显式节点/延迟预算的 baotou future metric；不得复用普通 weighted two-ply 的完成状态或假称 Stage B 已执行。

### D6 Root frontier 使用 post-discard standing shape

当前 _limit_weighted_frontier 在候选超过 max_frontier_candidates 时使用旧 shape_loss。

新 profile 启用时排序改为：

~~~text
current ukeire descending
standing shape quality descending
discard shape cost ascending
feed risk ascending
stable tile
~~~

实际函数可以因调用点已提前保证 current ukeire 相同而省略第一项，但语义必须一致。

shape_guard 的 shape delta 也必须迁移到 standing shape 语义。为避免旧数值阈值 8 与新 encoded 单位混用：

- 新增 shape_quality_guard_version；
- 新增与新 signature 对应的 gate；
- 禁止直接复用旧 shape_guard_shape_delta=8 当作新单位。

建议先以离散 admission rule 实现，再做参数扫描。**口径修正（2026-09-30）：已实现并冻结的 admission 不是“至少提升一个 taatsu class”，而是 taatsu-class 向量严格字典序改善**（`mj/legacy_eval._taatsu_class_improved`，比较 `standing_shape_signature[2:6]` 的 `left > right`，要求首个差异类别必须严格更优）。这两者不同义：严格字典序会拒绝“后位类别改善但前位类别相平/劣化”的候选，比任何单类改善都更窄。

实测：真实开局 seed 0–2599 中，该护栏只准入 1 次（seed 1787，primary [18] → admitted [6]；seed 290 为准入负例，`no_candidate_admitted`）。因此：

- 不得把 shape-aware 整体实验收益归功于护栏扩围（护栏基本不触发）；
- 若产品意图是保护“局部拆牌损失大、但弃后 signature 相等或轻微劣化”的局面，那是**策略行为变更**，需另做配对积分与性能验证后再单独上线，不能借修测试直接放宽准入；
- 任何放宽都须带版本化 gate 且不与旧 shape_guard_shape_delta 单位混用。

### D7 Stage B child comparator 增加 child shape

当前 Stage B 在最小 child shanten 候选内比较 ukeire 与 tile types。

新 comparator：

~~~text
1. child shanten ascending
2. child ukeire descending
3. child ukeire tile types descending
4. child standing shape quality descending
5. stable discard tile ascending
~~~

shape MUST 只在前三项相同后生效，防止为了好看结构牺牲真实向听与直接进张。

Rust run_stage_b_units 与 Python reference 必须共用完全相同的排序。

### D8 future shape 必须聚合回 root

只在 child comparator 使用 shape 仍不够：若两个 root 的 future ukeire / types 聚合相同，root 仍会打平。

因此每个 draw row 增加：

~~~text
child_shape_quality
~~~

每个 root 增加概率质量加权：

~~~text
future_shape_quality_sum
future_shape_quality_mean
future_shape_denominator
~~~

权重继续使用 public unseen mass：

~~~text
weight[t] = max(0, 4 - visible[t])
~~~

不得读取真实墙序。

### D9 Root comparator 的新顺序

新 shape-aware profile 的 speed root key：

~~~text
wildcard protection
current ukeire
future improve weight
future ukeire mean
future ukeire types mean
future shape quality mean        # new
standing shape quality           # new
discard shape cost               # legacy diagnostic/final tie
feed risk
stable tile
~~~

解释：

- current/future shanten 与 ukeire 仍是主导；
- future shape 回答“一摸一弃后平均会留下什么结构”；
- standing shape 回答当前完全打平时谁的起点更好；
- feed risk 仍保留，但不应在所有牌效指标相同前抢先压过 24s > 12s 的结构差异。

### D10 Stage A shortcut 保持安全

Stage A 只基于 future shanten improvement mass 证明严格赢家。

因为 root comparator 中 future improve 仍位于 future ukeire / shape 之前，所以：

- 若 Stage A 能严格证明 future improve winner，允许继续跳过 Stage B；
- 若 future improve 无法严格分胜负，必须进入 Stage B 才能使用 future ukeire / shape；
- 不允许因为新增 shape 指标而把一个未完成 Stage B 的 missing shape 当 0 排序。

### D11 Rust contract 与 kernel version

Rust output contract 需要扩展：

- draw row 增加 child_shape_quality；
- root metrics 增加 future_shape_quality；
- StageBOutcome 增加 best_shape；
- Python adapter 校验新 row/metric shape；
- WEIGHTED_TWO_PLY_KERNEL_VERSION 升级；
- required version 同步升级。

旧 wheel：

- version mismatch -> transactional fallback；
- 不得尝试按旧 tuple 长度猜测 shape；
- 不得把缺失 future shape 视为 0；
- evaluation 记录 kernel version mismatch。

### D12 Feature flag 与发布路径

新增 profile 字段：

~~~text
shape_quality_enabled
shape_quality_version
shape_quality_guard_enabled
~~~

阶段：

Phase 0:
- helper + tests，仅 diagnostic，不影响选择。

Phase A:
- root standing shape + frontier cap/guard；
- two-ply child shape 仍关闭；
- 用于隔离 root 修复收益。

Phase B:
- Stage B child shape + future shape root 聚合全部开启；
- 形成最终 candidate。

Release:
- 只有 Phase B 通过第 10～12 节验收，才允许 legacyV2 weighted_online 默认开启；
- rollback 只需关闭 shape_quality_enabled，不改变历史 evaluator 名称。

### D13 Diagnostics

evaluation/replay 至少增加：

~~~text
shape_quality_version
discard_shape_cost
standing_shape_quality
standing_shape_signature
future_shape_quality_sum
future_shape_quality_mean
future_shape_denominator
shape_quality_used
shape_quality_stage                  # baotou|root|stage_b
shape_changed_winner
decision_scope                       # baotou_scope|weighted_two_ply|legacy
baotou_tier
baotou_ukeire
baotou_shape_used
stage_b_entered
~~~

专项 fixture 应能直接看出：

~~~text
discard 1s:
standing = 24s class CENTRAL_KANCHAN

discard 4s:
standing = 12s class PENCHAN
~~~

以及最终是 `baotou_scope`、weighted root shape 还是 weighted future shape 改变了 winner。对 baotou_scope 决策不得伪造 `stage_b_entered=true` 或 future shape 字段。

## Correctness Validation

必须覆盖：

- 23 > 24 > 13 > 12；
- 78 > 68 > 79 > 89；
- 124 的非重叠 decomposition；
- 789 / 234 等完整顺子不重复奖励；
- pair / triplet / honor 不被误判成 suited taatsu；
- 0～4 财神边界；
- locked / freeze 只影响合法性与 shanten，不允许 shape helper 扩大合法动作；
- visible 改变只影响 ukeire weight，不改变同一个 standing counts 的纯 shape signature；
- 用户完整牌例必须证明实际 `decision_scope=baotou_scope`、候选诊断来自各自弃后手牌；
- baotou tier / 财神保护顺序更优或加权进度分更高时，standing shape 不得覆盖；
- 独立 weighted Stage B fixture 覆盖 `24s + 5s -> 打2s留45s`；
- Python/Rust 至少 10000 个随机合法 standing hands parity；
- targeted golden fixtures 全部 parity；
- kernel mismatch、budget fallback、partial_not_acceptable 事务回退。

## Score Validation Design

使用 paired/fair A/B：

~~~text
baseline  = 当前 main legacyV2，shape_quality_enabled=false
candidate = 相同代码 + shape_quality_enabled=true
~~~

要求：

- 相同随机 seed / 初始牌墙；
- A/B 交换座位与阵营，消除 seat/order 偏差；
- 使用最终 settlement score 作为主指标；
- 记录每局 paired score delta；
- 同时报告 win rate、胡率、平均番、shape divergence count 作为辅助，不用辅助指标替代积分。

阶段门：

Stage 1 smoke:
- 至少 4096 paired games；
- 若平均积分 delta < -0.10/局，立即停止；
- 若 95% CI 上界 < 0，立即停止；
- 必须确认 shape_changed_winner > 0，否则说明能力未真正进入生产路径。

Stage 2 confirm:
- 独立 seeds，至少 30720 paired games；
- 默认开启要求：
  - mean score delta >= 0；
  - 95% CI lower >= -0.10/局；
  - targeted divergence bucket 不呈现明确负收益；
  - 不存在某一常见 shanten / wall-left bucket 的稳定显著负桶。
- 强成功定义：95% CI lower > 0。
- 未达到默认门时保持实验开关关闭，不因专项牌例正确就直接上线。

报告分桶至少包括：

- shape_changed_winner / unchanged；
- decision_scope：baotou_scope / weighted_two_ply / legacy-fallback；
- baotou-shape-changed / root-shape-only / future-shape-changed；
- shanten 0 / 1 / 2+；
- open meld count；
- wall-left quartile；
- taatsu upgrade kind：penchan→kanchan、kanchan→ryanmen 等。

## Performance Validation Design

必须在同一机器、同一 Rust wheel、同一 power profile 下交错执行 baseline/candidate，避免先后温度与负载偏差。

报告：

~~~text
ordinary discard p50/p95/p99/max
Rust raw p50/p95/p99/max
complete rate
partial accepted rate
fallback rate + reason
stage_b_entered rate
baotou_scope count + baotou_elapsed_ms p50/p95/p99/max
baotou_shape_changed rate
shape evaluator p50/p95/p99
4-bot elapsed/game p50/p95
games/sec
~~~

硬约束：

- online hard budget 仍为 50ms，不得通过加预算换正确性；
- max_frontier_candidates 仍 <= 3；
- ordinary discard p95 相对 baseline 增幅 <= 10%；
- ordinary discard p99 相对 baseline 增幅 <= 15%；
- 4-bot elapsed/game 中位退化 <= 10%；
- fallback rate 不得增加超过 1 个百分点；
- hard-deadline / work-budget fallback 不得出现结构性新增；
- Rust/Python parity benchmark 不计入线上 latency。

若当前 baseline 自身存在 p99 > 50ms 或高 fallback，验收口径是“不进一步恶化”，不得把 baseline 历史问题伪装成本 change 通过。

## Rollback

任何一个条件触发即关闭 shape_quality_enabled：

- 积分 Stage 2 不过；
- p95/p99/4-bot 性能门不过；
- fallback 增加；
- Python/Rust parity 失败；
- targeted fixture 出现 12 > 13 或 12 > 24 的逆序；
- 线上 replay 出现 shape 指标覆盖更低 shanten / 明显更大 ukeire 的决策。

关闭后必须恢复当前 legacyV2 行为，不要求回退新 diagnostics 字段。
