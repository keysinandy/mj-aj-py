## ADDED Requirements

### Requirement: legacyV2 SHALL 生成低成本、public-only 的大牌意图画像

普通 discard 决策中，legacyV2 SHALL 能为舍牌后的 standing hand 生成廉价 BigHandIntent，至少覆盖 `CHIITOI`、`LUXURY_CHIITOI`、`WHITE_RICH`。

BigHandIntent MUST：

- 只读取本家手牌、locked、公开 visible、公开 live-wall / meld 摘要；
- 不读取对手暗牌或真实墙序；
- 不执行未来 draw→discard DFS；
- 不调用 shape-v2 EV2；
- 不调用 Python baotou 全枚举；
- 对固定输入完全确定；
- 时间复杂度有固定 O(34) 上界。

#### Scenario: 隐藏信息变化不影响意图

- **GIVEN** 两个游戏状态的本家手牌、locked、visible、live-wall 与公开副露完全相同
- **AND** 仅对手暗牌或真实墙顺序不同
- **WHEN** 生成 BigHandIntent
- **THEN** 两个结果 MUST 完全一致

### Requirement: 七对距离 MUST 与现有 shanten 语义共享单一真源

BigHandIntent 使用的 `chiitoi_shanten` MUST 与 `mj.shanten` 当前七对分支同语义，不得维护第二套近似算法。

#### Scenario: 多财神七对距离一致

- **GIVEN** 包含 0～4 张财神的多组 standing hands
- **WHEN** 分别通过 shanten 七对 helper 与 BigHandIntent 计算七对距离
- **THEN** `chiitoi_shanten` MUST 一致

### Requirement: 豪华七对潜力 MUST 区分自然四张、活升级与死升级

对于非财神自然牌：

- 当前真实持有 4 张时可计 `luxury_groups`；
- 当前持有 3 张且公开 remaining>0 时可计 `luxury_upgrade_live`；
- 第四张已全部公开可见时 MUST NOT 继续计豪华升级机会；
- `3 真牌 + 财神` 补齐不得计自然豪华组。

#### Scenario: 三张同牌且第四张仍存活

- **GIVEN** standing hand 自然持有三张 3m
- **AND** visible 显示第四张 3m 尚未出现
- **WHEN** 生成 BigHandIntent
- **THEN** `luxury_upgrade_tiles` SHALL 包含 3m
- **AND** `luxury_upgrade_live` SHALL 增加对应 remaining mass

#### Scenario: 豪华升级已经死亡

- **GIVEN** standing hand 自然持有三张 3m
- **AND** 第四张 3m 已在公开牌河或副露中可见
- **WHEN** 生成 BigHandIntent
- **THEN** 3m MUST NOT 产生 `luxury_upgrade_live`
- **AND** legacyV2 MUST NOT 仅因该死豪华机会保护相关候选

### Requirement: WHITE_RICH SHALL 仅作为资源意图，不替代精确爆头/财飘判定

未听牌阶段手中有多张财神时，BigHandIntent MAY 输出 `WHITE_RICH`，用于表示未来高价值构型资源。

`WHITE_RICH` MUST NOT：

- 直接声称当前为爆头；
- 直接授权财飘；
- 复制 `hand_multiplier()` 的番数计算；
- 替代现有 `baotou_ukeire()`、`_choose_discard_baotou()`、`_should_piao()`。

#### Scenario: 两张白板未听牌

- **GIVEN** 本家有两张白板且 `best_s>0`
- **WHEN** BigHandIntent 判断该手存在强七对/对子结构
- **THEN** MAY 输出 `WHITE_RICH`
- **BUT** MUST NOT 进入财飘动作或把该状态标记为 `baotou_ready`

### Requirement: BigHandIntent MUST NOT 修改真实向听数

任何 intent、strength、豪华潜力不得通过修改 shanten 值参与排序。

#### Scenario: 豪华路线不伪造向听

- **GIVEN** 候选 A 的真实 shanten=0
- **AND** 候选 B 的真实 shanten=1 且为强豪华七对路线
- **WHEN** 生成候选特征
- **THEN** B 的 shanten MUST 仍为 1
- **AND** B 只能通过显式 big-hand challenger/override 机制挑战 A

### Requirement: Phase A SHALL 只扩围同向听大牌候选

同向听阶段，BigHandGuard MAY 将被 ordinary current-ukeire frontier 淘汰的强七对/豪华候选重新纳入 weighted frontier。

该阶段：

- 所有参与现有 comparator 的 roots MUST 具有相同 shanten；
- BigHandIntent 只决定 admission，不直接给 weighted score 加 bonus；
- online frontier 总数 MUST 不超过 profile 上限。

#### Scenario: 同向听保护活豪华路线

- **GIVEN** 两个舍牌候选 shanten 相同
- **AND** 候选 A ordinary ukeire 更大
- **AND** 候选 B 保留自然三张且第四张仍存活、七对距离不差
- **WHEN** B 满足 same-shanten BigHandGuard
- **THEN** B SHALL 获得进入 weighted two-ply 比较的资格
- **AND** 最终仍由现有 legacyV2 weighted comparator 选择 A 或 B

### Requirement: Phase B SHALL 最多允许一个 `best_s+1` 强意图 challenger

当 profile 显式启用 plus-one phase 时，BigHandGuard MAY 从非 speed roots 中额外提名至多一个 challenger。

challenger MUST：

- `shanten == best_s + 1`；
- `locked == 0`；
- `intent_strength == STRONG`；
- 满足版本化的大牌强路线条件；
- 满足 live-wall / opponent-meld 收手条件；
- 不导致 online frontier 总数超过 3。

`best_s+2` 或更差 MUST NOT 被提名。

#### Scenario: 弱豪华机会不能退向听

- **GIVEN** 某候选比 best_s 差 1 向听
- **AND** 仅有弱七对倾向，没有活豪华升级或多财神强条件
- **WHEN** BigHandGuard 运行
- **THEN** 该候选 MUST NOT 成为 challenger

#### Scenario: 强豪华七对可以获得 challenger 资格

- **GIVEN** 某候选为 `best_s+1`
- **AND** `locked==0`
- **AND** 七对距离 <=1
- **AND** 保留至少一个公开仍存活的自然豪华升级
- **AND** live-wall 与 opponent-meld guard 均通过
- **WHEN** plus-one phase 已启用
- **THEN** 该候选 MAY 成为唯一 big-hand challenger

### Requirement: `best_s+1` challenger MUST 使用独立 override 而非现有同向听 comparator

legacyV2 MUST 先在 speed roots 中产生 `speed_winner`。

`best_s+1` challenger 不得仅因 current ukeire / future improve / shape key 直接与 speed roots 同键排序。

只有独立 `big_hand_override` gate 完整通过时，challenger 才可替换 `speed_winner`。

#### Scenario: challenger 被搜索但 override 不通过

- **GIVEN** 强豪华 challenger 已进入 Rust weighted frontier
- **AND** weighted future 计算完成
- **BUT** challenger 的 ukeire 损失超过 profile 上限
- **WHEN** 选择最终 action
- **THEN** MUST 返回 `speed_winner`
- **AND** diagnostics SHALL 记录 `override=false` 与拒绝原因

### Requirement: online fallback MUST 永远返回旧 speed/fallback pool 的动作

引入更高向听 challenger 后，所有 online fallback MUST 使用在 big-hand admission 前冻结的 legacy speed fallback。

包括：

- native kernel unavailable；
- kernel version mismatch；
- node/time budget；
- incomplete frontier；
- unsafe partial；
- challenger 必要字段 unknown。

#### Scenario: kernel failure 不得选更高向听 challenger

- **GIVEN** speed candidate 为 shanten 0
- **AND** challenger 为 shanten 1 且 BigHandIntent 很强
- **AND** Rust weighted kernel 在评价时失败
- **WHEN** legacyV2 fallback
- **THEN** 最终动作 MUST 来自旧 speed/fallback pool
- **AND** MUST NOT 因 challenger 的 intent/shape/ukeire 选择 shanten 1

### Requirement: BigHandGuard SHALL 保持在线搜索宽度和预算不增加

默认 online profile：

- `max_frontier_candidates` MUST 保持现有上限 3；
- BigHandGuard 最多占用一个 frontier slot；
- 不增加 weighted search horizon；
- 不增加 online hard budget；
- 不新增 Python future search。

#### Scenario: shape guard 与 big-hand guard 同时扩围

- **GIVEN** primary、shape candidate、big-hand candidate 同时存在
- **WHEN** 构造最终 online frontier
- **THEN** frontier root count MUST <=3
- **AND** diagnostics SHALL 说明任何被 cap 淘汰的候选及原因

### Requirement: BigHandIntent 决策 SHALL 可审计

普通 discard evaluation 至少 SHALL 记录：

- `intent_kinds`
- `intent_strength`
- `chiitoi_shanten`
- `pair_units`
- `luxury_groups`
- `luxury_upgrade_tiles/live`
- `wild_count/wild_live`
- `shanten_regression`
- `admitted_by`
- `speed_winner`
- `big_hand_challenger`
- `big_hand_override`
- `big_hand_override_reason`

#### Scenario: replay 能解释为什么没有做豪华七对

- **GIVEN** 某局面识别到豪华七对潜力
- **BUT** 因活墙不足或对手副露过多触发收手
- **WHEN** replay 展示该决策
- **THEN** SHALL 显示 intent 已识别
- **AND** SHALL 显示 override 被哪个 guard 拒绝