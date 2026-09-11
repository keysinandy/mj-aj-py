## Why

启发式 bot 的弃牌侧已在 9f4f8de 完成重构（向听硬约束→财神保护→进张→结构→喂牌→编号），但吃/碰反应侧仍停留在只比较副露后的 shanten（`after_s <= cur_s`）：看不见"副露后必须立即舍牌"的最终牌面，等向听时进张数减半的碰也会被接受，且 `cur_s` 含"刚打出的候选牌"的不精确口径。这是当前 bot 决策质量的最大洼地，也是 BC 教师（`bc_data`）和自博弈对手的行为质量瓶颈。

## What Changes

- 重写 `mj/bot.py` 的 `_choose_react()`：吃（CHOW_LOW/MID/HIGH）与碰（PONG）统一按"**副露 + 最佳弃牌**后的站立牌面"完整评价，评价键固定 `(shanten, ukeire, shape)`。
- 引入 PASS 基准 `_eval_standing(hand, locked, vis)`：对反应时点的站立暗牌原样评价，替代不精确的 `cur_s`。
- 吃/碰评价在 remove 2 张后的 need+1 态手牌上显式枚举每种合法舍牌，取最终站立牌面 (s, uke, shape) 最优的舍牌作为该动作的评分。
- 决策规则：`claim.s < pass.s` → 接受；`claim.s == pass.s` → 需 `uke_gain ≥ GAIN[action]`（PONG=+2、CHOW=+4 初值）；`claim.s > pass.s` → PASS。多个过门槛的 claim 按 更低 shanten → 更高 ukeire → 更低结构损失 → 稳定 action 顺序 选最优。
- 明杠（KONG_OPEN）保留现有独立启发式不动；`choose_action()` 的 discard 分支（HU / `_should_piao` 财飘路径）一行不改。
- `tests/test_bot.py` 追加 `TestReactDecision` 固定牌例回归；vis 快照不变量写成测试。

### Out of Scope（明确排除，另开 change）

- KONG 三态统一：暗杠/加杠补进 `choose_action()` 的统一评估流程、明杠补牌期望模型、死搭子杠（`2w3w3w3w3w4w`）守卫。
- 1-step lookahead（进张质量）、牌墙剩余阶段调整、动态防守权重。
- 训练管线重跑（bc_data 教师分布变化后的 BC/PPO 重训不在本 change 内）。
- 阈值 A/B 对局调参：第一版阈值只靠固定牌例锁定。

## Capabilities

### New Capabilities

- `bot-react-decision`: 启发式 bot 对他家弃牌的反应侧决策——吃/碰采用"副露 + 最佳弃牌后站立牌面"的完整评价，与 PASS 基准按 (shanten, ukeire, shape) 及动作分档 ukeire 增量阈值比较；明杠保留独立启发式。

### Modified Capabilities

（无——`openspec/specs/` 当前为空，无既有能力的需求变更。）

## Impact

- **代码**：`mj/bot.py`（`_choose_react()` 重写，新增评价辅助函数）；`tests/test_bot.py`（追加 `TestReactDecision`）。不触碰 `game.py`/`shanten.py`/`scoring.py` 等引擎与规则层。
- **下游行为**：`bc_data` 教师分布、`train_ppo` 自博弈对手、平台对局（经策略模型间接）——本 change 只改教师/对手行为，不重训管线。
- **评估口径**：v1 bot 保留为 `evaluate` 对手基线不动；本 change 的 bot 作为教师与自博弈对手。
- **性能**：react 决策新增 ≤ ~50 次 ukeire（Rust 内核默认路径）；合入前须压测 `python3 -m mj.evaluate 200` 吞吐降幅可接受。
- **文档**：按仓库约定同步 PROGRESS.md 对应结论与测试清单。
