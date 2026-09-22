## ADDED Requirements

### Requirement: legacy KONG SHALL 保持现有 hard gate

暗杠、加杠、明杠 MUST 继续依次通过：

1. legal action；
2. material-safe optimal decomposition structure guard；
3. post-KONG shape-preserve gate；
4. post-KONG `shanten==0`；
5. 活墙可 replacement draw；
6. public remaining 中 `winning_mass>0`；
7. 之后才允许 score EV/continuation 参与选择。

高 EV、chain 或倍率 MUST NOT 绕过前置硬门。

#### Scenario: 123333m 仍禁止暗杠
- **WHEN** 当前最优结构把 3m 同时用于 `123m` 与 `333m`，不存在 triplet + redundant single
- **THEN** KONG_CLOSED(3m) 在 structure gate 被拒绝，后续 continuation 不执行

#### Scenario: post-KONG 未听仍拒绝
- **WHEN** structure/shape 安全但 post-KONG shanten>0
- **THEN** legacy v1/v2 都拒绝 KONG

### Requirement: v2 KONG shape-preserve SHALL 比较完整已知 progress

legacy v2 的 KONG shape-preserve 除 shanten/ukeire_live/baotou_ready 外，还 MUST：

- 同 shanten 时保护 `ukeire_types`；
- baseline/post 的 `baotou_ukeire_live` 都已知时，post 不得降低；
- baseline `piao_draw_live>0` 且 post 同口径可知时，post 不得降低；
- unknown MUST 与真实 0 区分，unknown 不得被当成 0 造成假拒绝或假优势。

#### Scenario: 杠后丢失财飘推进
- **WHEN** baseline/post shanten 和普通 live waits 相同，但 baseline.piao_draw_live>0、post.piao_draw_live=0
- **THEN** v2 拒绝 KONG，reason 为等价的 `shape_piao_worse`

#### Scenario: 特殊 metric unknown
- **WHEN** 无 Rust special metric 导致 baseline.baotou_ukeire_live=None
- **THEN** KONG gate 不得把 None 当 0 与 post 比较；该维度只是不提供优势

### Requirement: v2 KONG score EV SHALL 包含 bounded continuation

KONG 通过全部 hard gate 后，v2 SHALL 以 public unseen mass 评价 replacement draw：

- replacement 立即合法 HU：计入 immediate reward；
- replacement 不能胡：枚举/选择最佳合法弃牌，得到 standing hand，再计入下一次本家摸牌的公开 expected HU reward；
- non-KONG baseline MUST 使用相同 hero-draw horizon：下一次本家摸牌不胡时也允许最佳弃牌后再评价一次本家摸牌；
- 两条路径的 reward MUST 使用相同 settlement score units；
- continuation 受独立预算约束，online incomplete 时回 v1 KONG 选择，offline fail-loud。

#### Scenario: 非胡补牌能保持宽听
- **WHEN** KONG replacement 大多数牌不能立即胡，但最佳弃牌后仍保留高质量听牌并在下一次本家 draw 有非零胡牌期望
- **THEN** `continuation_reward_ev>0` 并进入 KONG total reward；不得把这些 replacement 统一按 0 处理

#### Scenario: baseline horizon 对称
- **WHEN** 比较 self-KONG 与普通弃牌
- **THEN** 两者都按规范的两个本家 draw opportunity horizon 计算，不得 KONG 看两层而 baseline 只看一层

### Requirement: PONG 与 KONG_OPEN 同窗 SHALL 在必要时使用 same-unit slow-path

当 PONG 和 KONG_OPEN 都通过各自硬门：

- post-action shanten 更低者优先；
- 同 shanten 时，KONG progress MUST 不劣于 PONG；
- 然后计算 `Q_pong` 与 `Q_kong` 的 bounded public score continuation；
- 仅 `Q_kong > Q_pong + 1e-9` 时选择 KONG_OPEN；
- exact tie、score layer incomplete 或 KONG progress 不劣条件失败时选择 PONG。

该 slow-path MUST 只在双方都有效的少数 claim 窗触发。

#### Scenario: PONG 与 KONG 同形但 KONG score continuation 更高
- **WHEN** 两者同 shanten、KONG progress 不劣，且完整 `Q_kong > Q_pong`
- **THEN** 选择 KONG_OPEN，并输出两者 Q 与 delta

#### Scenario: same-unit incomplete
- **WHEN** PONG/KONG slow-path 的任一 Q 因预算或 coverage 不完整
- **THEN** 选择 PONG 的保守稳定次序，不得用只有 KONG 才有的 EV 单边比较

### Requirement: KONG diagnostics SHALL 区分 hard gate 与 continuation

v2 KONG 诊断至少 SHALL 包含：

- structure/shape/kong-kai gate 结果；
- baseline/post完整 progress；
- winning tiles/mass/probability；
- immediate_reward_ev；
- continuation_reward_ev；
- total_reward_ev；
- continuation budget/coverage/fallback；
- PONG/KONG 同窗时的 `Q_pong/Q_kong/delta`。

#### Scenario: hard gate 拒绝不消耗 continuation
- **WHEN** KONG 在 structure 或 KONG-KAI gate 已失败
- **THEN** continuation_nodes=0，诊断明确显示拒绝层级
