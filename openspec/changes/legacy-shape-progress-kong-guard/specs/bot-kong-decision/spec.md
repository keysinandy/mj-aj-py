## ADDED Requirements

### Requirement: 三种 KONG 在 legacy 中 MUST 先通过结构安全 Gate

legacy 对暗杠、加杠、明杠不得仅凭动作合法或 tile count 决定。BOT MUST 使用当前最优 material-safe 标准形分解验证杠牌材料用途。

- 暗杠 t：至少一个当前最优 standard decomposition 必须把 3 张 t 用作同一 natural triplet，并把第 4 张 t 保留为 single；若第 4 张参与 sequence / pair / taatsu 则不安全。
- 加杠 t：已有公开 PONG(t) 时，手中的第 4 张 t 必须在至少一个当前最优 standard decomposition 中为 single；若参与 sequence / pair / taatsu 则不安全。
- 明杠 t：手中的 3 张 t 必须在至少一个当前最优 standard decomposition 中共同构成 natural triplet。
- 若七对分支严格优于所有标准形，KONG 不得以一个更差的标准形 decomposition 绕过当前最优结构。

#### Scenario: 123333m 禁止暗杠 3m
- **WHEN** 当前最优结构中 `123333m` 的四张 3m 被分配为 `123m` 的一张 3m + `333m` 的三张 3m
- **THEN** `KONG_CLOSED(3m)` 的 structure Gate MUST 失败，BOT 不得暗杠 3m

#### Scenario: 独立 3333m 可继续检查
- **WHEN** 至少一个当前最优 standard decomposition 把 `3333m` 分配为 natural `333m` + 冗余 single `3m`
- **THEN** 暗杠通过结构 Gate，但仍需继续通过牌效保持和杠开 Gate

#### Scenario: 加杠牌正在组成顺子
- **WHEN** 已有公开 `PONG(3m)`，手中唯一的 3m 在当前最优分解中用于 `123m`
- **THEN** `KONG_ADD(3m)` MUST 被拒绝

#### Scenario: 三张相同牌不是当前刻子资源
- **WHEN** KONG_OPEN 合法但手中三张目标牌在所有当前最优 standard decomposition 中都不能共同作为 natural triplet
- **THEN** KONG_OPEN MUST 被拒绝

### Requirement: KONG 后站立牌效 MUST 不劣于同决策点的非杠 baseline

Structure Gate 通过后，BOT SHALL 构造 post-KONG replacement-draw 前站立态，并与同决策点的非杠 baseline 以相同公开信息和 evaluator/profile 比较。

- post-KONG shanten 高于 baseline 时 MUST 拒绝；
- shanten 相同时，post-KONG 普通 `ukeire_live` 小于 baseline 时 MUST 拒绝；
- baseline 已是 baotou_ready 而 post-KONG 失去 baotou_ready 时 MUST 拒绝；
- self-kong baseline 的 `choose_discard` MUST 透传当前 `discard_profile`，不得用不同 legacy 版本产生基准。

#### Scenario: 杠后仍听牌但有效胡牌明显减少
- **WHEN** 非杠 baseline 为 shanten 0、live waits=8，而 post-KONG 仍 shanten 0 但 live waits=2
- **THEN** KONG MUST 被拒绝

#### Scenario: weighted legacy-v2 使用同 profile baseline
- **WHEN** 当前 draw decision 使用 weighted legacy-v2 profile
- **THEN** self-kong 的非杠 baseline MUST 使用同一个 profile 选择弃牌和站立态

### Requirement: legacy KONG MUST 在动作发生前存在真实杠开机会

通过结构和牌效 Gate 后，post-KONG standing MUST 已为 `shanten==0`，且公开未见牌中必须存在至少一张 replacement draw 能立即合法成胡。

- 活墙不足以补牌时 MUST 拒绝；
- remaining 只按本家暗牌、全部牌河、全部公开副露计算，不得读取对手暗牌或真实墙序；
- 理论胡牌张已全部可见、`winning_mass==0` 时 MUST 拒绝；
- KONG replacement draw SHALL 沿用现有 `kong_draw=True` 的有财必拷响例外语义。

#### Scenario: 杠后不是听牌
- **WHEN** Structure Gate 通过但 post-KONG standing 的 shanten 为 1 或更高
- **THEN** BOT MUST 拒绝 KONG，不得以“补一张可能继续做牌”为理由杠

#### Scenario: 理论听牌但胡牌张已见完
- **WHEN** post-KONG shanten 为 0，但所有可立即成胡的牌 `remaining==0`
- **THEN** BOT MUST 拒绝 KONG

#### Scenario: 存在活杠开张
- **WHEN** post-KONG shanten 为 0，活墙可补牌，且至少一张公开未见牌能在 replacement draw 立即合法成胡
- **THEN** KONG 通过 KONG-KAI Gate，可以继续进入补牌期望比较

### Requirement: KONG 的补牌 EV 只能在所有硬 Gate 通过后参与决策

legacy KONG 决策顺序 MUST 固定为：

```text
legal
-> structure_safe
-> shape_preserved
-> post_kong_shanten == 0
-> winning_mass > 0
-> replacement-draw EV
-> final selection
```

高 replacement-draw EV、杠倍率或 chain 收益 MUST NOT 绕过前置 Gate。

对于 PONG + KONG_OPEN 同窗：

- PONG 使用 `bot-react-decision` 推进 Gate；
- KONG_OPEN 使用本 capability；
- KONG_OPEN 不得覆盖一个 post-action shanten 严格更低的 PONG；
- 同 shanten 时 KONG 只有在共同牌型进度不劣且存在真实 replacement winning mass 后，才能利用补牌 EV/chain 作为进一步优势。

#### Scenario: 高 EV 不能绕过结构破坏
- **WHEN** 某暗杠按旧 replacement EV 计算高于普通弃牌，但 structure Gate 判定该牌正在参与顺子
- **THEN** BOT MUST 拒绝暗杠，EV 不参与最终比较

#### Scenario: 明杠与碰同窗且碰降向听
- **WHEN** PONG 后最佳站立 shanten 低于 KONG_OPEN post-standing shanten
- **THEN** BOT 选择 PONG（若 PONG 合法且通过 Gate），不得因 KONG 有补牌/倍率而覆盖更低向听

### Requirement: KONG 决策 SHALL 输出可归因 Guard 诊断

KONG 评价至少 SHALL 输出：

- `structure_safe`
- `structure_reason`
- baseline/post-KONG shanten 和 ukeire
- `kong_wait_tiles`
- `kong_winning_mass`
- `kong_win_probability`
- `rejection_reason`
- 若最终比较 EV，则输出 baseline/selected value

#### Scenario: 123333m 的拒绝原因可审计
- **WHEN** replay 命中 `123333m` 暗杠候选
- **THEN** 诊断显示 structure_safe=false，并给出“目标牌被最优 sequence + triplet 共同占用/无冗余 single”的等价原因
