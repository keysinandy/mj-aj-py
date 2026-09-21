# Design

## Context

当前普通摸后弃牌由 `mj.bot` 直接枚举手牌、保留最低向听候选，再按当前 ukeire、静态牌型损失、下家喂牌风险和稳定 tile 顺序选择。`mj.shanten.ukeire` 已支持以本家手牌、四家牌河和副露组成的 visible 计算 `4-visible` 未见权重；Rust/纯 Python 调度也已经为向听和进张提供一致语义。

仓库另有 `mj.hand_eval`/`mj.decision` 的 shape-v1/shape-v2 前瞻和预算体系，但它们带有独立的 profile、Q/H2/EV 语义，当前 legacy 路径不会调用。V1 要解决的是普通 legacy 候选的“当前进张相同但后续改良不同”，因此必须保持轻量、非递归和可回退，不能把 shape-v1 的完整评价层或 EV2 假设悄悄混入 legacy。

## Goals / Non-Goals

**Goals:**

- 对当前最低向听、当前 ukeire 前沿的候选增加一次未来摸牌和一次最佳摸切。
- 用公开信息和统一 `4-visible` 权重产生 `future_improve_weight`、`future_ukeire`，并保留可解释的最佳后续弃牌。
- 让未来特征先于静态牌型损失参与同当前效率候选的排序，同时保持合法性、最低向听和财神保护硬约束。
- 通过固定 profile、确定性 tie-break、有限缓存和整次决策回退控制性能与可复现性。
- 为 legacy 基线、V1、回放反事实重算和后续 BC 数据保留可区分的解释字段。

**Non-Goals:**

- 不模拟对手暗手、真实墙序、对手动作、吃碰杠或完整终局得分 EV。
- 不递归搜索到第二次未来摸牌，不实现 MCTS、rollout、belief sampling 或完整 shape-v2。
- 不改变杭州麻将规则、财神/抓打圈/合法动作入口，也不在本变更中修改反应侧策略。
- 不因单个 `3t`/`9b` 牌例直接切换线上默认；默认切换必须遵守独立收益和性能闸门。

## Decisions

### 1. 把 V1 放在独立的 legacy evaluator 层

新增一个纯值的 legacy 前瞻评价模块（建议命名为 `mj/legacy_eval.py`，而不是继续扩张 `mj.bot`），由 `choose_discard` 负责提供当前合法候选和游戏上下文，评价模块返回候选特征与选择结果。原因是：

- `mj.bot` 继续保留 HU、KONG、抓打圈和反应动作的规则入口，只负责路由和安全回退。
- `mj.hand_eval` 已承载 shape-v1/shape-v2 的 Q/H2 语义，直接复用会把 legacy 的二阶进张误标成 I/H2 或改变其候选范围。
- 纯函数输入可以用 tuple hand、locked、visible、规则/阶段和 profile 组成稳定缓存键，也更容易做 Python 参考测试。

考虑过直接在 `choose_discard` 中嵌套两层循环，但这会继续扩大已有排序函数、难以记录部分完成和回退原因，予以否决。考虑过把 shape-v1 的 `_future_lookahead` 作为实现，因其对子状态、分母和 Q 层有不同契约，也不采用。

### 2. 根候选和未来子状态的确定顺序

根决策按以下事务顺序运行：

1. 从规则合法弃牌集生成所有不同根候选，遵守冻结/抓打圈和当前阶段合法性。
2. 计算每个候选的弃后向听，保留最低向听；如果存在同向听的非财神候选，继续保留既有财神保护。
3. 用当前根 visible 计算当前 ukeire，得到最大当前 ukeire 的前沿集合。只有该集合进入 V1；较低当前 ukeire 的候选仍保留诊断淘汰原因，但不能靠未来特征反超。
4. 对前沿集合逐候选执行一次未来摸牌评估。所有完全计算的候选使用统一的未来结果比较；V1 未完成则整次回退到步骤 1–3 的完整 legacy 排序。

这样可以把本手的 `3b`（当前加权进张 15）排除，只比较 `6b/9b/3t`，同时不会用一个未来分数掩盖当前一巡真实进张差异。

### 3. 未来摸牌的公开物料和子状态

对根弃牌后的站立牌面 `H`，定义 `r[t] = max(0, 4-visible[t])`。对每个 `r[t] > 0` 的牌种类：

1. 将 t 加入手牌，构造 `visible_after_draw`，使 t 的未知数减少一张。
2. 在该摸后状态中枚举规则允许的弃牌 d；不产生吃、碰、杠或 HU 以外的新根动作，也不递归未来。
3. 对每个 `H+t-d` 计算向听和以 `visible_after_draw` 为输入的 ukeire。
4. 以 `(shanten 最小, ukeire 最大, 现有 child shape/feed/stable tie-break)` 选出该摸牌的最佳子状态。

候选未来特征定义为：

```text
future_improve_weight = Σ r[t]                      if child_shanten(t) < root_shanten
future_ukeire         = Σ r[t] * child_ukeire(t)   over all usable t
future_ukeire_mean    = future_ukeire / Σr[t]      when Σr[t] > 0, else null
```

`future_ukeire` 使用加权总量作为排序主值，和当前 `ukeire = Σ(4-visible)` 同单位；平均值只用于展示，避免把一个理论牌数伪装成概率。若子状态已降低向听，仍可记录其子状态 ukeire，但排序首先由 `future_improve_weight` 决定。根弃牌和未来弃牌都只是可见牌河移动，不能把已见牌放回未知池。

### 4. 排序、财神和稳定性

V1 完成时使用如下字典序最小键（白板/财神保护仍置于效率比较之前）：

```text
(is_wild,
 -current_ukeire,
 -future_improve_weight,
 -future_ukeire,
 shape_loss,
 feed_risk,
 tile)
```

最低向听在进入该键前已经是硬过滤；`is_wild` 的含义与现有 legacy 一致。牌型损失和喂牌风险不再决定 `3t`/`9b` 这种当前进张相同且未来特征已完成的候选，只在未来特征也相同或缺少定义时作为 tie-break。未来子状态的 tie-break 不递归调用 V1，使用同一套单层 legacy child key，避免隐藏的第三层搜索。

### 5. 缓存和预算

每次根决策创建一个有容量上限的 future memo，至少以以下值为键：

```text
(hand_after_root, drawn_tile, locked, visible_after_draw,
 rule_version, phase, legal_flags, profile_version)
```

已有 `shanten` 缓存继续负责 `(hand, locked)` 的纯向听结果；未来 memo 额外包含 visible 和规则资格，避免不同牌河复用同一进张。初始 profile 使用固定节点预算 4096 和单调时间预算 8ms 作为实现基线，实际门槛以压测报告冻结；预算、缓存容量和排序版本进入 profile fingerprint。

预算检查覆盖未来牌枚举、子弃牌、缓存逻辑展开和解释序列化。若任一前沿候选未完整结束，评价模块返回 `complete=false` 和明确原因，调用方丢弃所有部分 V1 结果，使用此前已完整计算的 legacy key。禁止将未完成候选的未来字段填 0 后继续比较。

### 6. 解释数据结构和旧日志兼容

V1 的 `decision.evaluation` 继续沿用现有 gid/decision/action 关联，增加版本化字段：

```json
{
  "version": "legacy-two-ply-v1",
  "profile_fingerprint": "...",
  "level": "legacy-v1",
  "complete": true,
  "future_model": "uniform_unseen_one_draw_best_discard",
  "future_nodes": 1234,
  "future_cache_hits": 56,
  "future_fallback_reason": null,
  "candidates": [
    {
      "tile": 17,
      "shanten": 1,
      "ukeire": 19,
      "future_improve_weight": 42,
      "future_ukeire": 1234,
      "future_ukeire_mean": 21.7,
      "future_best_discards": {"16": 8, "13": 4},
      "shape_loss": 3,
      "feed_risk": 0,
      "missing": []
    }
  ]
}
```

`future_best_discards` 只保存可审计摘要，不保存隐藏墙序。预算回退、输入未知或 V1 关闭时，未来字段使用 `missing`/状态表达，`level` 显示实际 legacy；旧日志没有评价对象时继续显示 `legacy_unrecorded`。

### 7. 测试和发布决策

测试分为四层：

- 纯评价测试：visible 更新、四张上限、最佳摸切、同输入确定性、缓存隔离和 Rust/Python `shanten/ukeire` 语义一致。
- BOT 回归：固定 `20260920` `seq=100` 牌面，验证 `3t/9b` 当前进张相同但 V1 可按未来特征选择 `9b`；同时验证当前进张 15 的 `3b` 不被越级选择。
- 安全回退：节点/时间预算中断、未知公开物料、冻结合法集和财神门禁均回到完整 legacy，且解释不补零。
- 证据与性能：同种子 legacy 基线/V1 对照、候选特征快照、单局和多场 p95/p99；默认切换前执行项目已有独立收益、strict 和线上窗口闸门。

## Risks / Trade-offs

- **[重复计权]** `future_ukeire` 可能同时奖励多个相近摸牌路径。→ 固定为一次摸牌、一次最佳弃牌，使用总权重而不引入额外手工搭子奖励；后续消融单独评估。
- **[性能超预算]** 34 种摸牌乘以约 14 种子弃牌可能使普通决策超时。→ 先筛当前 ukeire 前沿、使用 Rust/纯 Python 现有内核、决策内 bounded memo，并以整层回退保护窗口。
- **[visible 错误]** 摸牌后若没有更新 visible，未来特征会高估同牌；弃牌若恢复未知数则会重复计数。→ 以 visible-after-draw 作为子状态唯一输入，加入四张守恒和定向回归。
- **[合法性漂移]** 假想子状态可能误放宽冻结、财神或阶段动作。→ 通过当前规则合法动作投影生成子弃牌，V1 不自己复制吃碰杠规则；无法构造合法集时回退。
- **[策略语义混淆]** 现有 shape-v1 的 `I/H2` 与 V1 的 future 字段容易混用。→ 独立 profile/version 和字段名，解释中声明模型假设，禁止把 V1 字段写入 shape-v1 的加权 Q。
- **[单例过拟合]** `3t/9b` 的改善不代表总体收益。→ 固定牌例只作回归，默认切换依赖冻结种子、成对收益、性能和线上证据。

## Migration Plan

1. 先落地纯评价模块、候选解释和单元/回放 fixture，默认关闭 V1，记录基线 legacy 与 counterfactual V1。
2. 开启显式 `legacy-two-ply-v1` profile，运行全量回归、Rust parity、单机交错性能和小规模同种子对照；确认未触发窗口超时后再扩大样本。
3. 运行项目既有独立收益、信息安全、性能和线上逐窗验收；profile fingerprint、规则版本、内核版本和运行 manifest 一并保存。
4. 若任一闸门失败，将配置切回基线 legacy；日志保留实际层级和失败原因，旧日志和未启用实例无需迁移。
5. 只有所有闸门满足后，才评审是否把 V1 作为 `legacy` 默认子版本；否则长期保留显式 opt-in。

## Open Questions

无。V1 的主排序量、visible 更新、预算回退和 profile 边界已在本设计与 delta spec 中固定；子实现可在不改变这些契约的前提下选择具体容器或内核调用方式。
