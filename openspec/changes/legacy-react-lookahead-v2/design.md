## Context

当前默认 `DEFAULT_BOT_EVALUATOR = legacyV2`，但 evaluator 实际是分层的：

- 普通弃牌：`LegacyTwoPlyProfile.weighted_online()` + Rust weighted two-ply；
- CHOW/PONG/KONG_OPEN：`mj.bot._choose_react_evaluated()` 的 `legacy-shape-progress-v1`；
- self KONG/HU：`_choose_draw_action()` + KONG hard gate + public replacement EV。

这使 discard 已经有 U2，而 reaction 仍主要停在 U1/当前站立牌面。与此同时，`mj/decision/root.py::_evaluate_reaction_context()` 已能用 public context 比较 PASS/CHOW/PONG/KONG_OPEN，并正确计算 PASS 的 `first_draws`，但它的成本与 opponent-response 近似决定了它更适合 shadow teacher，而不是直接替换 legacy 热路径。

本设计目标是把 **现有 weighted two-ply 能力复用到 reaction**，而不是再造一个 evaluator。

## Goals / Non-Goals

**Goals**

- 冻结当前 reaction v1，给 `legacyV2` 增加独立、可回退的 reaction v2。
- U2 第一版只做 veto/择优，不扩大副露授权面。
- 让 post-claim 最佳弃牌由共同 U2 层选择，避免先用 U1 过早裁剪。
- 显式建模 CHOW/PONG 的 draw tempo，但不用浮点权重拍分。
- KONG 继续保留当前结构/杠开硬门，并补齐完整 shape preservation 与非胡 replacement continuation。
- PONG/KONG_OPEN 只在双方都值得做的少数窗口进入 same-unit slow-path。
- 线上 incomplete U2 整层回 v1；离线 incomplete 必须 fail-loud。
- shape-v2 all-root 只用于审计和校准。

**Non-Goals**

- 不做完整 hidden-world/MCTS。
- 不让 U2 推翻向听硬门。
- 不用 teacher 在线选动作。
- 不改变规则层。
- 不在本 change 做训练。

## Decisions

### D1 冻结 v1，新增 LegacyReactionProfile v2

新增版本化 profile：

```text
LegacyReactionProfile.v1()
    version = legacy-shape-progress-v1
    future_enabled = false
    行为 == 5e0a405 当前 reaction/KONG

LegacyReactionProfile.v2_online()
    version = legacy-react-v2
    future_enabled = true
    future_mode = weighted
    soft budget ~= 6ms
    hard budget ~= 10ms
    allow_partial = true
    min_partial_coverage = 0.90
    tempo_guard = true

LegacyReactionProfile.v2_offline()
    version = legacy-react-v2-offline
    future_mode = weighted
    大节点/时间预算
    require_complete = true
    incomplete = error/fail-loud
```

路由：

```text
evaluator="legacy"       -> reaction v1
legacyV2 aliases         -> reaction v2_online
legacyV2-offline aliases -> reaction v2_offline
shape-v1 / shape-v2      -> 保持各自既有路由
```

预算具体数值放进 profile 指纹，可在性能门禁后仅通过后续 calibration change 修改。

### D2 模块边界：bot.py 不再继续膨胀

建议新增：

```text
mj/legacy_react.py
    LegacyReactionProfile
    LegacyShapeProgress (从 bot.py 迁移)
    LegacyFutureProgress
    evaluate_pass / evaluate_claim
    choose_reaction
    tempo metadata / U2 gate

mj/legacy_kong.py
    structure_guard
    shape_preserve_guard
    kong-kai gate
    bounded continuation
    PONG/KONG_OPEN slow-path
```

`mj/bot.py` 只负责 evaluator routing、HU/普通 discard 入口和兼容包装。

为避免循环 import，reaction/kong 模块必须使用纯输入/显式 callback（例如 shape_cost、piao_context_allowed、baseline discard chooser），或把真正无状态的公共 helper 下沉到 `legacy_eval.py`/小型 common 模块；禁止从 `legacy_react.py` 反向 import `mj.bot`。

迁移第一步必须有 v1 parity tests，证明纯重构不改变 `5e0a405` 行为。

### D3 复用 weighted two-ply：新增 standing frontier API

`legacy_eval.py` 新增公开于包内的 reusable API，概念签名：

```python
evaluate_standing_frontier(
    standings: Sequence[StandingRoot],
    locked: int,
    visible: Sequence[int],
    profile: LegacyTwoPlyProfile,
    *,
    shape_cost=None,
) -> Mapping[id, FutureEvaluation]
```

`StandingRoot` 至少包含稳定 id、standing hand、root shanten；API 直接复用现有 `weighted_two_ply_frontier` / Python fallback 与 `FutureEvaluation` 字段：

- `future_improve_weight`
- `future_improve_lower/upper`
- `future_ukeire` / `future_ukeire_mean`
- `future_ukeire_types` / mean
- `future_ukeire_skipped`
- `best_discards`
- `coverage`
- `partial_accepted`
- `complete`
- budget/search diagnostics

MUST NOT 在 reaction 模块另写一套 draw→discard DFS。

### D4 U2 是事务性比较层

U2 只对“需要它做决策”的共同候选集合运行。比较必须处于共同层级：

- PASS 与所有参与 U2 的 claim 使用同一 visible、locked 对应语义、profile、coverage 规则；
- 如果一边只有 Stage-A 指标而另一边有 Stage-B 指标，视为层级不一致，不得混合；
- online：共同层级不可得时，整个 U2 层回退到 v1 结果，并记录 `u2_fallback_reason`；
- offline：共同层级不可得直接 fail-loud，不能生成 v1 标签冒充 v2。

若 weighted partial 已满足 profile 的安全 commit 条件，可按 `FutureEvaluation.partial_accepted=true` 使用；coverage 不达标则回退。

### D5 v1 Gate 继续决定“有没有资格副露”

v2 不改变第一层：

```text
claim.shanten < pass.shanten
    -> ACCEPT，U2 不得否决

claim.shanten > pass.shanten
    -> REJECT

claim.shanten == pass.shanten
    -> 先走 v1 significant-progress Gate
```

第一版 U2 **不得**把 v1 原本 PASS 的同向听动作新授权为 CHOW/PONG。

原因：v2 首轮目标是降低 false-positive claim，而不是扩大行为面，便于和 `5e0a405` 做可归因 A/B。

### D6 同向听普通推进使用 U2 veto

对 v1 reason 为 `wait_expansion` / `ukeire_expansion` 的同向听 claim：

```text
claim.future_improve_weight >= pass.future_improve_weight
AND
（若双方同处 Stage-B）
claim.future_ukeire_mean >= pass.future_ukeire_mean
```

才保留。

`future_ukeire_types_mean` 作为 tie-break，不作为第一版硬拒绝条件。

若双方都是 Stage-A-only（`future_ukeire_skipped=true`），只比较 `future_improve_weight`；若层级不一致则按 D4 回退 v1。

### D7 特殊价值状态不能被 generic U2 误杀

当前 v1 的特殊进度需要细化 reason detail：

```text
baotou_ready_upgrade
baotou_ukeire_gain
piao_ready_upgrade
piao_mass_gain
wait_expansion
ukeire_expansion
```

规则：

- `baotou_ready_upgrade` / `piao_ready_upgrade` 是直接类别升级，generic ordinary U2 不得单独否决；
- 定量 `baotou_ukeire_gain` / `piao_mass_gain` 若尚无同口径特殊 U2 指标，则保留 v1 结论并记录 `u2_special_metric_missing`；
- 后续 change 可加入 baotou/piao U2，但本 change 不用普通 ukeire 假装特殊价值。

### D8 post-claim 最佳弃牌由完整候选 frontier 决定

当前 `_best_post_claim_state()` 先在最低 shanten 候选内用当前 progress 选一张。

v2 改为：

1. 枚举 claim 后所有合法立即弃牌；
2. 保留最低 shanten 候选；
3. 对全部候选一次性构造 standing frontier；
4. 若动作需要 U2，全部候选在同一 U2 层比较；
5. 排序：
   - 更低 shanten（已相同）；
   - 更高 `future_improve_weight`；
   - 更高 `future_ukeire_mean`（同 Stage-B）；
   - 更高 future types；
   - v1 special progress；
   - 更低 shape cost；
   - stable discard id。

如果 U2 不完整，回到 v1 对这批最低 shanten 候选的选择，不得只保留某个已算完的子集。

### D9 Tempo 只做离散 guard，不做浮点惩罚

对 CHOW/PONG：

```text
pass_draw_index = (hero_seat - pending_owner) % 4    # 1..3
claim_draw_index = 4
tempo_cost = claim_draw_index - pass_draw_index      # 1..3
```

CHOW 合法时天然 `pass_draw_index=1`，因此 `tempo_cost=3`。

第一版 tempo 规则仅作用于 D6 的 ordinary same-shanten claim：

- `tempo_cost == 1`：U2 核心指标“不劣”即可；
- `tempo_cost >= 2`：除“不劣”外，`future_improve_weight` 或（同 Stage-B 的）`future_ukeire_mean` 至少一项必须严格优于 PASS；
- 直接 shanten drop 和 D7 的直接特殊 ready upgrade 不受此 guard 否决。

diagnostics 必须记录 `pass_draw_index / claim_draw_index / tempo_cost`。

### D10 KONG shape-preserve 使用完整已知 progress

保留现有 structure guard 与 KONG-KAI gate，不放宽。

`_kong_shape_gate` 扩展为：

- post shanten 不得更差；
- 同 shanten 时 `ukeire_live` 不得下降；
- `ukeire_types` 不得下降（除非 shanten 严格改善）；
- baseline 已 `baotou_ready` 时不得丢失；
- baseline/after 的 `baotou_ukeire_live` 都已知时不得下降；
- baseline `piao_draw_live>0` 时 post 不得下降；
- 未知特殊字段不得按 0 比较；unknown 只能“不提供优势”，不能误判为损失。

这比当前 `None -> 0` 的通用比较更严格地区分 unknown 与真实 0。

### D11 KONG continuation 与非杠 baseline 使用相同 hero-draw horizon

当前 KONG EV 主要是 replacement draw 立即胡；v2 使用两次本家 draw opportunity 的共同 horizon：

**普通非杠 baseline**

```text
当前弃牌
-> 下一次本家 draw
   -> 若胡：结算 reward
   -> 若不胡：选最佳合法弃牌
      -> 再下一次本家 draw 的期望胡牌 reward
```

**KONG**

```text
杠
-> replacement draw
   -> 若胡：结算 reward
   -> 若不胡：选最佳合法弃牌
      -> 下一次本家 draw 的期望胡牌 reward
```

两边都只用 public unseen mass，不读取墙序/对手暗牌；后续本家摸牌按现有 `draw_delay=4` 语义。

返回：

- `immediate_reward_ev`
- `continuation_reward_ev`
- `total_reward_ev`
- `winning_mass`
- `continuation_nodes`

KONG 仍必须先通过 structure/shape/KONG-KAI 硬门，continuation EV 不能绕过硬门。

### D12 PONG vs KONG_OPEN 只在双方都通过 Gate 时走 same-unit slow-path

若只有一方过 Gate，直接选该方/按 PASS 规则处理。

若双方都过 Gate：

1. post-action shanten 更低者优先；
2. 若同 shanten，KONG 必须通过完整 `progress_not_worse(PONG)`；
3. 计算：
   - `Q_pong`：PONG + v2 best discard 后的 bounded score continuation；
   - `Q_kong`：KONG replacement + bounded continuation；
4. 只有 `Q_kong > Q_pong + 1e-9` 时选 KONG_OPEN；
5. exact tie 或任一 same-unit evaluation incomplete 时选择 PONG（保守稳定次序），并记录 fallback reason。

该 slow-path 只覆盖 PONG/KONG_OPEN 同窗，不扩展到普通 CHOW。

### D13 Shadow teacher 只用于校准

本地 replay/audit 工具对每个 reaction decision 记录：

```text
legacy_v1_action
legacy_v2_action
teacher_action (shape-v2 all-root，仅离线)

PASS/claim:
  shanten
  U1
  U2 future_improve
  future_ukeire_mean
  special progress

tempo_cost
Q_pong / Q_kong（若有）
v1 reason
v2 veto/selection reason
teacher Q / delta（若完整）
```

teacher 不得进入 production action path。

首轮至少扫描 1,000 个 reaction 决策（若现有本地 replay 不足，则全量扫描可用样本并明确数量），按 CHOW/PONG/KONG_OPEN、tempo_cost、reason 分桶报告分歧。

### D14 性能与启用门禁

冻结 `5e0a405` 为行为/性能基线。

默认开启 `legacy-react-v2` 前必须同时满足：

- 同机同 Rust 内核，基线/候选交错各至少 3×200 局，4-bots elapsed/games 中位退化 ≤15%；
- online U2 eligible reaction 的 complete + safe-partial coverage ≥90%；
- reaction U2 额外评价 p95 ≤10ms；
- KONG bounded continuation 额外评价 p95 ≤15ms；
- 不允许 Python baotou 全枚举或新 Python DFS 进入热路径；
- 全量测试通过，v1 parity fixtures 零漂移。

若未过门禁，代码可以合入但 `legacyV2` 默认仍保持 reaction v1，并通过 profile 开关 opt-in v2；不得静默牺牲 deadline。

## Risks / Trade-offs

- **U2 更保守**：第一版只 veto，不新增 claim，可能暂时漏掉 teacher 认为好的进攻副露；这是为了可归因上线。
- **tempo 近似**：只建模本家下一摸位置，不直接估计中间三家的自摸/认领概率；hidden-world race 留给 shadow teacher。
- **special progress 与 ordinary U2 不同单位**：本 change 明确保护直接爆头/财飘 upgrade，不强行折成一个总分。
- **Rust kernel API 抽象成本**：需要把 discard-root 专用适配器抽成 standing frontier，但可消除 reaction 重复搜索，长期收益更高。
- **KONG horizon 增长**：必须严格预算；只有 hard gate 后才进入 continuation，并保留 online fallback。
- **模块拆分有重构风险**：先做 v1 parity 再启用 v2，避免行为变更与搬代码混在一起难归因。
