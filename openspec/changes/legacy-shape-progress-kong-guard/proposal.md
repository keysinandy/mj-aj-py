## Why

当前 legacy 吃/碰虽然已经从“只看副露后 shanten”升级为“副露 + 最佳弃牌后的站立牌面”，但同向听分支仍由固定的 `PONG_UKE_GAIN=2` / `CHOW_UKE_GAIN=4` 决定。这个门槛过于粗糙：少量进张增加就可能触发副露，却看不见“是否更容易爆头、是否形成财飘机会、听牌是否明显变宽、下一摸降向听的概率是否大幅增加”等真正的牌型推进。

KONG 侧存在更明显的结构风险：`KONG_OPEN` 合法时整个 claim 窗口仍退回 `_legacy_claim_react()`，同向听时甚至固定偏向明杠；暗杠/加杠虽然已经有公开信息的补牌期望，但没有先判断杠牌是否正在承担顺子、搭子、雀头等结构职责。典型反例是手牌包含 `123333m`：四张 3m 中一张属于 `123m`，另外三张属于 `333m`，暗杠 `3333m` 会直接拆掉已经成型的 `123m`，legacy 必须拒绝。

本变更把 legacy 的行为原则收口为：

- **吃/碰看推进**：向听下降直接视为推进；向听不变时，只有牌型发生可解释的显著升级才副露。
- **杠看无损 + 杠开**：先证明杠不会破坏当前最优结构，再证明杠后仍保持当前牌效，并且补牌确实存在活的杠开张；全部通过后才允许进入现有期望比较。

## What Changes

- 新增 legacy 专用 `LegacyShapeProgress` 站立牌面摘要，统一描述：
  - `shanten`
  - 普通有效进张的种类数与公开剩余张数
  - 爆头是否已成型、爆头推进的公开剩余张数
  - 下一摸形成“可财飘”结构的牌种与公开剩余张数
  - 听牌态的有效胡牌种类/张数（与 `shanten==0` 的 ukeire 同源）
- 重写 legacy CHOW/PONG 的同向听门槛：
  - `claim.shanten < pass.shanten`：直接允许；
  - `claim.shanten > pass.shanten`：直接 PASS；
  - `claim.shanten == pass.shanten`：只有满足“爆头升级 / 财飘升级 / 听牌显著变宽 / 下一摸降向听能力显著增加”之一才允许。
- 删除“仅靠 +2/+4 原始 ukeire 就允许副露”的行为；普通 ukeire 仍是重要信号，但必须达到版本化的“显著改善”门槛。
- `KONG_OPEN` 不再让整个 claim 窗口回退到 `_legacy_claim_react()`：PONG 继续走新的推进判定，KONG_OPEN 单独走 KONG Guard。
- 为暗杠、加杠、明杠统一新增 **KONG Structure Guard**：
  - 暗杠：四张牌必须能在当前最优标准形分解中解释为“自然刻子 3 张 + 1 张冗余单张”；若第四张参与顺子/搭子/雀头等结构则禁止；
  - 加杠：手中的第 4 张必须在当前最优分解中是冗余单张，不能正在参与顺子/搭子/雀头；
  - 明杠：手中的 3 张必须在当前最优分解中作为自然刻子使用。
- 杠后增加同口径牌型保护：
  - 与“不杠”的最佳站立基准比较，杠后 shanten 不得更差；
  - 同 shanten 时普通有效进张不得减少；
  - 已有爆头等高价值结构不得被杠破坏。
- 增加 **KONG-KAI Gate**：模拟杠完成但补牌尚未发生的站立手牌，必须已经 `shanten==0`，且按公开未见牌统计至少存在 1 张真实可达、可在杠后补牌立即成胡的牌；否则禁止杠。
- KONG 只有通过全部硬门后，才进入现有 replacement-draw score expectation 比较；Structure Guard / KONG-KAI Gate 不得被高 EV 绕过。
- 修正 self-kong 的非杠基准：调用 `choose_discard()` 时必须透传当前 `discard_profile`，避免 weighted legacy-v2 的 KONG 候选与错误的旧 baseline 比较。
- 增加可归因诊断：记录推进原因、before/after 指标、KONG guard 拒绝原因和杠开 live mass，便于 replay/BC 数据审计。

### Out of Scope

- shape-v1 / shape-v2 的评分公式、all-root Fast EV、Search Teacher 本身不在本变更内修改。
- 不修改 `Game` 的吃碰杠合法性、死墙、补牌、连庄/倍率、财神规则。
- 不引入隐藏牌、真实牌墙顺序或对手暗手信息；所有概率/剩余张数仍只用公开信息。
- 不在本变更内重训 BC/PPO/RL 模型；教师分布变化后的训练另行执行。
- 不把“为了高番而主动增加向听”纳入 legacy；`claim.shanten > pass.shanten` 始终拒绝。
- 不追求一次性确定最终最优阈值；第一版使用冻结、版本化的保守阈值和固定牌例，后续可用 replay/teacher 数据校准。

## Capabilities

### New Capabilities

- `bot-kong-decision`: legacy 暗杠/加杠/明杠的统一结构安全、牌效保持、杠开机会和补牌期望决策。

### Modified Capabilities

- `bot-react-decision`: legacy CHOW/PONG 从固定 +2/+4 ukeire 门槛升级为“降向听或显著牌型推进”的可解释门槛；KONG_OPEN 不再冻结整个 PONG/KONG claim 窗口。

## Impact

- **主要代码**：`mj/bot.py`（LegacyShapeProgress、CHOW/PONG gate、KONG guard、diagnostics）。
- **复用代码**：`mj/hand_eval.py::enumerate_decompositions()` 作为 KONG 结构占用依据；`mj.shanten.ukeire/baotou_ukeire`、`mj.win.is_baotou/is_win` 作为推进和杠开信号。
- **测试**：`tests/test_bot.py` 增加吃碰显著推进、`123333m` 暗杠保护、加杠/明杠结构保护、无活杠开张拒绝等固定牌例。
- **下游**：legacy teacher、自博弈对手、fallback 行为会更保守且更可解释；训练数据分布会变化，但训练流程本身不改。
- **性能**：新增结构分解与特殊推进计算必须懒算、缓存，并沿用当前 15% 吞吐回归闸门；无 Rust 爆头内核时，昂贵的定量爆头信号不得触发 Python 全枚举拖慢线上决策。
