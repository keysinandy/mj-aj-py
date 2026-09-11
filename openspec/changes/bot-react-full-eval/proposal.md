## Why

启发式 bot 的弃牌侧已在 9f4f8de 完成重构（向听硬约束→财神保护→进张→结构→喂牌→编号），但吃/碰反应侧仍停留在只比较副露后的 shanten（`after_s <= cur_s`）：看不见"副露后必须立即舍牌"的最终牌面，等向听时进张数减半的碰也会被接受；PASS 基准 `cur_s` 仅覆盖向听数单一维度（张数口径本身正确，`bot.py` 现注释"含刚打出的候选牌"系误导——反应玩家手牌本就不含他家 pending 牌），缺少与 claim 最终站立牌面一致的 ukeire/结构评价。这是当前 bot 决策质量的最大洼地，也是 BC 教师（`bc_data`）和自博弈对手的行为质量瓶颈。

## What Changes

- 重写 `mj/bot.py` 的 `_choose_react()`：吃（CHOW_LOW/MID/HIGH）与碰（PONG）统一按"**副露 + 最佳弃牌**后的站立牌面"完整评价，门槛判据 `(shanten, ukeire)`。
- 引入 PASS 基准 `_eval_standing(hand, locked, vis)` → `(shanten, ukeire)`：对反应时点的站立暗牌原样评价，替代只覆盖向听数单维的 `cur_s`（shape 不进入 PASS：`_discard_shape_cost` 语义是"弃某张的结构损失"，站立手牌无本次弃牌可指）。
- 吃/碰评价在 remove 2 张后的 need+1 态手牌上显式枚举每种合法舍牌，返回最优舍牌的 `(shanten, ukeire, discard_shape_cost, 舍牌)` 作为该动作评分（`discard_shape_cost` 复用弃牌侧 `_discard_shape_cost`，仅作多候选择优）。
- 决策规则（门槛判据只用 shanten/ukeire）：`claim.s < pass.s` → 接受；`claim.s == pass.s` → 需 `uke_gain ≥ GAIN[action]`（PONG=+2、CHOW=+4 初值）；`claim.s > pass.s` → PASS。多个过门槛的 claim 在**当前 react mode 的合法候选**内（引擎 claim 窗只产生 PONG/KONG_OPEN、吃窗只产生 CHOW，二者不同窗竞争）按 更低 shanten → 更高 ukeire → 更低弃牌结构损失 → 稳定 action 顺序 选最优。
- KONG 行为保持不变：KONG_OPEN 与 PONG 同窗竞争（手持三张 pending 牌时同时合法，旧实现同向听时 KONG 优先）——为使 KONG 真正 out-of-scope，`KONG_OPEN ∈ acts` 时整个 claim 窗口（含 PONG）沿用既有决策，仅 `KONG_OPEN ∉ acts` 的纯碰窗 PONG 走 v2 完整评价；加固定牌例锁 KONG 窗口不漂移。`choose_action()` 的 discard 分支（HU / `_should_piao` 财飘路径）一行不改。
- `tests/test_bot.py` 追加 `TestReactDecision` 固定牌例回归；vis 快照不变量写成测试。

### Out of Scope（明确排除，另开 change）

- KONG 三态统一：暗杠/加杠补进 `choose_action()` 的统一评估流程、明杠补牌期望模型、死搭子杠（`2w3w3w3w3w4w`）守卫。
- 1-step lookahead（进张质量）、牌墙剩余阶段调整、动态防守权重。
- 训练管线重跑（bc_data 教师分布变化后的 BC/PPO 重训不在本 change 内）。
- 阈值 A/B 对局调参：第一版阈值只靠固定牌例锁定。

## Capabilities

### New Capabilities

- `bot-react-decision`: 启发式 bot 对他家弃牌的反应侧决策——吃/碰采用"副露 + 最佳弃牌后站立牌面"的完整评价，门槛判据 (shanten, ukeire) 加动作分档 ukeire 增量阈值，多候选择优用弃牌结构损失；KONG 窗口（含同窗 PONG）沿用既有决策。

### Modified Capabilities

（无——`openspec/specs/` 当前为空，无既有能力的需求变更。）

## Impact

- **代码**：`mj/bot.py`（`_choose_react()` 重写，新增评价辅助函数）；`tests/test_bot.py`（追加 `TestReactDecision`）。不触碰 `game.py`/`shanten.py`/`scoring.py` 等引擎与规则层。
- **下游行为**：`bc_data` 教师分布、`train_ppo` 自博弈对手、平台对局（经策略模型间接）——本 change 只改教师/对手行为，不重训管线。
- **评估口径**：`evaluate` 直接 `from mj.bot import choose_action`（`evaluate.py:14`），启发式 bot 随本 change 升级为 v2，不维护 v1 runtime；前后性能用同一命令、同机同内核环境，对变更前 commit `9f4f8de` 与变更后版本各测 3 次取中位对比。
- **性能**：react 决策 ukeire 调用理论上限——吃窗 ≤34 次（3 吃法 × ≤11 舍牌候选 + PASS 基准 1）、纯碰窗 ≤12 次（legacy KONG 路径不计）；合入闸门：4 bots 段中位吞吐降幅 ≤15%（见 spec）。
- **文档**：按仓库约定同步 PROGRESS.md 对应结论与测试清单。
