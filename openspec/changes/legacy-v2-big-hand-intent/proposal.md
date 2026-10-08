## Why

当前生产默认弃牌 evaluator 已是 `legacyV2`：

- `mj.bot.choose_action()` 默认使用 `DEFAULT_BOT_EVALUATOR = legacyV2`；
- 普通弃牌由 `LegacyTwoPlyProfile.weighted_online()` + Rust `weighted_two_ply_frontier` 处理；
- 当前在线 profile 的 frontier 上限为 3，soft/hard budget 为 40/50ms；
- `shape_guard` 已证明一种有效模式：不增加搜索深度，只在 root 层把“牌效接近但结构明显更好”的候选重新放回 frontier。

当前规则层已经完整支持：

- 七对和牌；
- 豪华七对组数与倍率；
- 爆头；
- 财飘；
- 4 白板；
- 杠/飘动作链；
- 最终 settlement score。

当前 legacy 策略也已经支持：

- 持财神、已听牌时主动推进爆头；
- 已经可以胡时，在墙量和收手条件允许下主动弃胡财飘；
- reaction/KONG 的 shape progress 与安全 gate。

但 **“会认大牌”不等于“会做大牌”**。普通弃牌仍存在两个结构性缺口：

1. `mj.bot.choose_discard()` 在进入 `legacyV2` 前先只保留 `best_s`（最小向听）候选，因此一个 `best_s+1`、但具有明显豪华七对/多财神长期价值的候选根本没有资格被 legacyV2 看见。
2. `legacyV2` 的 weighted two-ply 最终排序隐含“frontier roots 同向听”的前提；如果直接把 `+1 shanten` 候选塞入现有 comparator，会破坏语义，且 fallback 可能错误选择更高向听候选。
3. 当前多财神的主动做牌意识主要从 `best_s==0` 才进入精确 `baotou_ukeire`；2～3 张白板但尚未听牌时，没有廉价的长期价值画像。
4. 七对 shanten 已存在，但没有“豪华升级机会”“必须保护的三张/四张自然牌”“第四张是否仍存活”等 root 特征。
5. 直接切换到 shape-v2/EV2 能解决一部分 score-aware 问题，但其全候选 score horizon 成本不适合作为当前生产 legacyV2 的默认热路径。

本 change 的目标不是把 legacyV2 改造成完整 EV/MCTS 搜索，而是增加一个 **O(34)、低延迟、可解释、可回退的 BigHandIntent / BigHandGuard 层**：

> 大牌意图只负责“不要把高价值路线过早剪掉”，现有 Rust weighted two-ply 仍负责短期牌效；在线搜索宽度和 hard budget 不增加。

## What Changes

2026-10-08 用户追加批准方案 1：在显式 parallel 实验 profile 下，仅对唯一
`best_s+1` challenger 复用现有 Python full future；Rust 速度 frontier 保持
同向听比较，补跑服从原剩余 hard budget。该授权覆盖下文原版“不新增 Python
future search”的限制，不改变默认配置。先做积分与性能评估，只有确认正收益而
性能不达标才转入 Rust 跨向听 complete future 改造；详见 design 中的方案 1。

- 新增 `mj/big_hand_intent.py`（或等价小模块），只使用本家手牌、公开 visible、locked、live wall、公开副露等信息生成廉价 `BigHandIntent`。
- 第一版意图只覆盖：
  - `CHIITOI`：七对构型；
  - `LUXURY_CHIITOI`：已有自然四张组或自然三张且第四张仍存活的豪华升级路线；
  - `WHITE_RICH`：2～3 张财神导致的高价值构型资源，用于更早保护爆头/财飘/七对路线，但不替代现有精确 baotou evaluator。
- BigHandIntent 必须是 feature/guard，不得篡改真实 shanten；禁止 `luxury -> shanten - 1` 一类伪向听。
- `choose_discard()` 对 legacyV2 路径保留 root 的完整候选元数据，但：
  - 原 `best_s` 候选仍组成 **speed/fallback pool**；
  - 大牌层最多从非 speed pool 额外提名 1 个候选；
  - 非 legacyV2/显式 rollback 路径保持现有 min-shanten 行为。
- 新增 `big_hand_guard`，与现有 `shape_guard` 同级：
  - Phase A 先只允许同向听候选发生大牌保护/排序；
  - Phase B 经 A/B 与性能门禁后，允许 `best_s+1` 的强意图候选进入 challenger slot；
  - 任何时候 online frontier 总数仍 MUST `<= 3`。
- `best_s+1` challenger 不得直接参与当前 legacyV2 的同向听 comparator。最终选择改为：
  1. 在原 speed frontier 中按既有 legacyV2 逻辑产生 `speed_winner`；
  2. 若存在通过强 gate 的 big-hand challenger，再执行独立 `big_hand_override`；
  3. 未满足 override gate 时保持 `speed_winner`。
- BigHandGuard 不增加搜索深度，不新写 Python DFS，不调用 shape-v2 EV2，不做 Monte Carlo。
- 精确爆头/财飘逻辑仍由当前 `best_s==0` 的 `_choose_discard_baotou()` / `_should_piao()` 负责；BigHandIntent 只负责更早期的候选保护。
- 所有 fallback 必须回到 **变更前相同的 legacyV2 min-shanten fallback pool**：
  - Rust weighted kernel 不可用；
  - budget exceeded；
  - partial 不安全；
  - BigHandIntent 缺字段；
  - big-hand override 无法完成；
  - 均不得让 `best_s+1` challenger 污染 fallback。
- 增加 replay/audit 字段：intent、strength、chiitoi shanten、luxury groups、luxury upgrade live、wild count、admitted_by、shanten regression、speed winner、override reason。
- 用固定牌例 + self-play paired A/B 校准强意图门槛；第一版不训练 BC/RL。

### Out of Scope

- 不把 shape-v2/EV2 切成 production default。
- 不新增多巡搜索、MCTS、hidden-world rollout。
- 不读取真实墙序或对手暗牌。
- 不修改七对/豪华七对/爆头/财飘的规则与计番。
- 不在本 change 让 CHOW/PONG 为豪华七对主动升向听；scope 第一版只覆盖普通 discard。
- 不允许任意 `best_s+2` 或更差候选进入在线 frontier。
- 不让 `intent_score` 直接作为无量纲魔法分数与 weighted future 数值相加。
- 不修改现有 KONG/reaction v2 的 gate。
- 不在本 change 做 BC/PPO/RL 重训。

## Capabilities

### New Capabilities

- `legacy-big-hand-intent`: legacyV2 普通弃牌的廉价大牌画像、候选保护、强意图 `+1 shanten` challenger、独立 override、事务性 fallback 与审计信息。

### Modified Capabilities

- `bot-baotou-piao-discard`: 多财神意识从“已听牌后精确推进爆头”向前扩展到未听牌阶段的 cheap intent；原精确 baotou/piao 路径、X/Y/Z 收手与 `PIAO_WALL_GUARD` 保持不变。

## Impact

- **主要代码**
  - 新增 `mj/big_hand_intent.py`；
  - `mj/bot.py`：legacyV2 root candidate plumbing，保留 speed pool + 1 个 big-hand challenger；
  - `mj/legacy_eval.py`：`LegacyRootCandidate` 扩展 intent 元数据、`_apply_big_hand_guard()`、speed winner + override、fallback pool 显式化；
  - `mj/shanten.py`：建议把现有七对向听公式抽成可复用 package helper，避免 BigHandIntent 重写第二套七对语义。
- **不改**
  - Rust weighted two-ply 搜索深度；
  - `max_frontier_candidates=3`；
  - online soft/hard budget 40/50ms；
  - scoring/win/game 规则。
- **测试**
  - 七对/豪华七对固定牌例；
  - 多财神早期保护；
  - 已见死第四张不再保护；
  - same-shanten parity；
  - `+1 shanten` strong/weak gate；
  - kernel/budget fallback 不受 challenger 污染；
  - freeze 合法性；
  - 原爆头/财飘回归。
- **性能**
  - BigHandIntent 必须为固定上界的 O(34) 计数/比较；
  - online frontier 搜索根数不超过当前 3；
  - 不新增 Python future search；
  - 对 legacyV2 decision latency 和 4-bot throughput 做同机交错基线验收。
