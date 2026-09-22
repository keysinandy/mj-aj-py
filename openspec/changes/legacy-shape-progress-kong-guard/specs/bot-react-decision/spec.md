## MODIFIED Requirements

### Requirement: 吃/碰按“副露 + 最佳弃牌后站立牌面”评价，并且只有明确推进才执行

启发式 BOT 对合法 CHOW_LOW/MID/HIGH 和 PONG SHALL 以“副露完成 + 最佳合法立即弃牌后的站立牌面”为候选状态，与 PASS 的原站立牌面按同一 legacy 进度模型比较。

legacy MUST 遵守以下硬门：

- 若 claim 最佳站立牌面的 shanten **低于** PASS，claim SHALL 被接受进入候选集；
- 若 claim shanten **高于** PASS，MUST PASS，不得以番数、结构奖励或进张奖励突破；
- 若 claim 与 PASS **同 shanten**，只有发生规范定义的“显著牌型推进”才可接受；
- 多个已接受候选仅在当前 react mode 的合法动作内按稳定进度排序择优。

shape-v1 / shape-v2 的既有评价语义不由本 requirement 改写。

#### Scenario: 吃后向听下降
- **WHEN** 某 CHOW + 最佳弃牌后的 shanten 低于 PASS
- **THEN** 该 CHOW 通过推进 Gate，并在所有已通过候选中择优

#### Scenario: 碰后向听变差
- **WHEN** PONG + 最佳弃牌后的 shanten 高于 PASS
- **THEN** BOT 必须 PASS，不能因为进张数或特殊结构奖励执行 PONG

#### Scenario: 等向听只有小幅进张增加
- **WHEN** PONG/CHOW 与 PASS 同 shanten，且只增加少量普通 ukeire，未达到显著增益门槛，也未发生爆头/财飘等类别升级
- **THEN** BOT 选择 PASS

#### Scenario: 多种吃法只选择真正推进者
- **WHEN** 同一弃牌存在多个 CHOW，部分候选仅有小幅进张变化，另一候选达到显著推进
- **THEN** 仅达到推进 Gate 的候选进入排序，未过门候选不得靠 tie-break 胜出

### Requirement: legacy 同向听显著牌型推进 SHALL 覆盖爆头、财飘、听牌宽度和降向听能力

legacy SHALL 为 PASS 和 claim 最佳站立态生成同口径 `LegacyShapeProgress`。同 shanten 时，claim 只有满足下列至少一项才可执行：

1. 从非爆头升级为 `baotou_ready`，或爆头推进 live mass 达到显著增益门槛；
2. 新形成足够的结构性财飘下一摸机会，或财飘机会 live mass 达到显著增益门槛；
3. `shanten==0` 时有效胡牌等待显著变宽；
4. `shanten>0` 时下一摸可降低向听的公开剩余张数显著增加。

第一版“显著增益” MUST 使用版本化常量：

- PONG 最小绝对 live gain = 4；
- CHOW 最小绝对 live gain = 6；
- 同时要求 after >= before × 1.50（before=0 时只检查绝对门槛）；
- 听牌态若胡牌牌种增加至少 2 种且 live waits 不减少，也视为显著变宽；
- 新财飘机会从 0 起步时，至少需要 2 张公开剩余机会才可单独授权 claim。

阈值 MUST 集中定义并进入诊断；实现不得在测试过程中隐式调整。

#### Scenario: 同向听升级为爆头
- **WHEN** PASS 与 claim shanten 相同，PASS 不是爆头态，而 claim 最佳站立态满足 `is_baotou_wait`
- **THEN** claim 以 `baotou_progress` 通过 Gate，即使普通 ukeire 没达到旧 +2/+4 口径

#### Scenario: 同向听更容易进入爆头
- **WHEN** PASS/claim 都尚未爆头，Rust baotou-ukeire 可用，且 claim 的爆头推进 live mass 达到对应动作显著增益门槛
- **THEN** claim 可按 `baotou_progress` 通过 Gate

#### Scenario: 无 Rust 爆头内核
- **WHEN** Rust baotou-ukeire 不可用，且 claim 只依赖“爆头推进数量增加”而没有直接升级为 baotou_ready
- **THEN** 该定量信号不得单独授权 claim；实现不得切到高延迟 Python 全枚举热路径

#### Scenario: 同向听新增财飘机会
- **WHEN** PASS 没有结构性下一摸财飘机会，claim 在现有墙量/收手守卫允许下产生至少 2 张公开剩余的财飘机会
- **THEN** claim 可按 `piao_progress` 通过 Gate

#### Scenario: 听牌从窄听变多面听
- **WHEN** PASS 与 claim 都为 shanten 0，claim 的有效胡牌种类增加至少 2 种且公开剩余胡牌张数不减少
- **THEN** claim 可按 `wait_expansion` 通过 Gate

#### Scenario: 更容易从二向听降到一向听
- **WHEN** PASS 与 claim 都为相同正 shanten，claim 的下一摸降向听 live ukeire 同时满足对应动作的绝对增量和 1.50 倍门槛
- **THEN** claim 可按 `ukeire_expansion` 通过 Gate

### Requirement: KONG_OPEN 不得冻结整个 PONG/KONG claim 窗口

当 PONG 与 KONG_OPEN 同时合法时，BOT MUST 分别评价：

- PONG 按本 capability 的 legacy 推进 Gate；
- KONG_OPEN 按 `bot-kong-decision` 的结构安全、牌效保持、杠开机会和补牌期望 Gate。

MUST 删除“只要 `KONG_OPEN in acts`，整个窗口直接调用旧 `_legacy_claim_react`”的行为。

#### Scenario: 明杠被拒但碰能推进
- **WHEN** KONG_OPEN 因结构破坏或没有真实杠开机会被拒，而 PONG 使向听下降或达到显著牌型推进
- **THEN** BOT 可以选择 PONG，不能因为明杠合法而把 PONG 一并退回旧逻辑

#### Scenario: 碰与明杠都不推进
- **WHEN** PONG 未通过推进 Gate，KONG_OPEN 也未通过 KONG Gate
- **THEN** BOT 选择 PASS

## ADDED Requirements

### Requirement: legacy 反应诊断 SHALL 记录推进原因和 before/after 指标

legacy CHOW/PONG 的可解释输出 SHALL 至少包含最终 reason、PASS/claim 的核心 `LegacyShapeProgress`、所用阈值和候选是否通过 Gate。

reason 至少支持：

- `shanten_drop`
- `baotou_progress`
- `piao_progress`
- `wait_expansion`
- `ukeire_expansion`
- `pass`

#### Scenario: replay 可解释一次同向听碰
- **WHEN** 某 PONG 因爆头推进而被接受
- **THEN** 诊断明确记录 `reason=baotou_progress`，并能看到 before/after 的 shanten、baotou 状态和相关 live mass
