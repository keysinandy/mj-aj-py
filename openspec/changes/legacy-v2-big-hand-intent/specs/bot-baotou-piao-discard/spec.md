## ADDED Requirements

### Requirement: 多财神大牌意识 SHALL 在未听牌阶段提供廉价候选保护，并在听牌后切换到现有精确爆头逻辑

legacyV2 SHALL 在不替代现有精确爆头/财飘规则的前提下感知早期大牌意图。

当本家持有财神时，legacyV2 对大牌价值的感知不再只从 `best_s==0` 开始。

- 当 `best_s>0` 时，系统 MAY 使用 `legacy-big-hand-intent` 的 `WHITE_RICH` 与七对/豪华画像保护少量普通 discard 候选；
- 该阶段不得调用高成本 Python 爆头枚举，不得直接认定爆头或财飘 ready；
- 当进入现有 `hand[W] > 0 && best_s == 0` scope 后，弃牌排序 MUST 继续使用现有 `_choose_discard_baotou()` / Rust `baotou_ukeire`；
- HU 时财飘 MUST 继续由 `_should_piao()`、`PIAO_WALL_GUARD` 和现有收手规则裁决；
- cheap intent 不得覆盖精确爆头结果。

#### Scenario: 三张白板但仍为一向听

- **GIVEN** 本家有三张白板
- **AND** 当前普通弃牌后仍为正 shanten
- **WHEN** legacyV2 评价普通弃牌
- **THEN** MAY 生成 `WHITE_RICH` intent 并保护与大牌构型一致的候选
- **BUT** MUST NOT 调用财飘动作
- **AND** MUST NOT 把该状态伪装成已爆头

#### Scenario: 进入听牌后使用精确爆头排序

- **GIVEN** 本家持有财神
- **AND** 当前 `best_s==0`
- **WHEN** legacyV2 选择普通弃牌
- **THEN** MUST 使用现有爆头 tier / `baotou_ukeire` 逻辑
- **AND** cheap `WHITE_RICH` intent MUST NOT 替代该排序

### Requirement: BigHandIntent MUST NOT 改变现有财飘收手与墙量契约

本 change MUST NOT 修改财飘的既有安全条件。

至少以下行为 MUST 保持：

- 活墙低于 `PIAO_WALL_GUARD` 时直接 HU，不弃胡财飘；
- 爆头推进 X/Y/Z 收手语义保持；
- freeze 下只有刚摸的财神合法时才允许弃白财飘；
- YCBK 的既有 scope/exception 不由本 change 扩大；
- budget/fallback 不得因为 BigHandIntent 绕过上述 guard。

#### Scenario: 大牌 intent 不能绕过晚局落袋为安

- **GIVEN** 当前可以 HU
- **AND** BigHandIntent 显示强 `WHITE_RICH/LUXURY_CHIITOI`
- **AND** 活墙低于财飘墙量 guard
- **WHEN** 选择动作
- **THEN** MUST 按既有逻辑 HU
- **AND** 大牌 intent MUST NOT 授权财飘

### Requirement: 多财神早期保护与爆头推进 SHALL 共享 public-only 边界

BigHandIntent SHALL 与爆头推进共享 public-only 信息边界。

无论 cheap intent 还是精确 baotou progression，都不得读取真实墙序或对手暗牌。

#### Scenario: 隐藏墙变化不改变早期保护

- **GIVEN** 两个状态的本家手牌与所有公开 visible 信息相同
- **AND** 真实墙顺序不同
- **WHEN** 运行早期 `WHITE_RICH` intent
- **THEN** 候选保护结果 MUST 相同
