## ADDED Requirements

### Requirement: 吃/碰按"副露 + 最佳弃牌后站立牌面"完整评价

启发式 bot 对他家弃牌的反应侧决策（CHOW_LOW/MID/HIGH、PONG）SHALL 以"副露动作完成 + 枚举每种合法舍牌后的站立牌面"为评价对象，门槛判据为 `(shanten, ukeire)`，ukeire 按可见牌折算真实剩余进张。

- 吃/碰评价流程 MUST 为：从反应时点手牌移除该动作消耗的 2 张牌得到 need+1 态手牌，显式枚举每种合法舍牌得到 need 态站立手牌，对每个站立手牌计算 `(shanten, ukeire)`，返回最优舍牌的 `(shanten, ukeire, discard_shape_cost, 舍牌)` 作为该动作评分。`discard_shape_cost` 复用弃牌侧 `_discard_shape_cost` 语义，仅作多候选择优，MUST NOT 进入门槛判据。
- 评价张数口径 MUST 满足 `shanten()` 的暗牌张数断言（副露数 0/1/2 三档均须正确）。
- 多个通过门槛的候选 MUST 在当前 react mode 的合法候选内择优（引擎 claim 窗只产生 PONG/KONG_OPEN、吃窗只产生 CHOW，二者不同窗竞争）。
- KONG 窗口 MUST 沿用既有决策：`KONG_OPEN ∈ acts` 时整个 claim 窗口（含 PONG）不进入本评价流程；仅 `KONG_OPEN ∉ acts` 且 `PONG ∈ acts` 时 PONG 使用本评价框架。

#### Scenario: 吃后向听降低则执行吃
- **WHEN** 某吃选项（含其最佳舍牌后的站立牌面）的 shanten 低于 PASS 基准
- **THEN** bot 从当前 react mode 的所有过门槛候选中按 更低 shanten → 更高 ukeire → 更低 discard_shape_cost → 稳定 action 顺序 选出最优并执行

#### Scenario: 两种吃法选最终牌面更优者
- **WHEN** 同一张弃牌存在多种合法吃法，且吃完 + 最佳弃牌后的 (shanten, ukeire) 不同
- **THEN** bot 选择最终站立牌面评价更高的吃法

#### Scenario: KONG 与 PONG 同窗时行为不变
- **WHEN** 手持三张 pending 牌，PONG 与 KONG_OPEN 同时合法
- **THEN** 决策结果与既有实现一致（整个 claim 窗口不进入新评价流程）

### Requirement: PASS 基准为反应时点站立暗牌原样评价

反应侧决策 SHALL 存在 PASS 基准：对反应时点的站立暗牌（13−3·locked 张，不做任何增删）以 `(shanten, ukeire)` 评价，与各吃/碰选项门槛判据同键比较。基准 MUST NOT 引入"含他家刚打出候选牌"的口径（该牌本不在反应玩家手牌中）。

#### Scenario: 可碰但向听不变且进张明显变差
- **WHEN** 碰 + 最佳弃牌后与 PASS 基准 shanten 相同，且 ukeire 增量小于该动作阈值
- **THEN** bot 选择 PASS

### Requirement: 等向听时按动作分档 ukeire 增量阈值执行

吃/碰选项与 PASS 基准 shanten 相同时，MUST 满足 `ukeire 增量 ≥ GAIN[action]` 才可执行；shanten 更高时 MUST 选择 PASS。GAIN 初值为 PONG=2、CHOW=4。

#### Scenario: 碰的边界增益
- **WHEN** 碰后与 PASS 基准 shanten 相同，ukeire 增量为 2（恰好达到 PONG 阈值）
- **THEN** 碰可被接受

#### Scenario: 吃的边界增益
- **WHEN** 吃后与 PASS 基准 shanten 相同，ukeire 增量为 3（未达到 CHOW 阈值 4）
- **THEN** bot 选择 PASS

### Requirement: 可见牌快照在反应评价内复用

反应侧评价 SHALL 使用反应时点取得的单份 `visible_counts()` 快照贯穿 PASS 基准与全部吃/碰选项评价。该快照 MUST 满足不变量：claim 前 vis == claim 后、弃牌前 vis（他家 pending 牌此时已在牌河、本家手牌移入自家副露均不改变可见总数）。

#### Scenario: 快照不变量
- **WHEN** 在 react 阶段对同一局面取 `visible_counts(seat)`，并分别模拟"某吃选项成立且舍牌前"的可见牌计数
- **THEN** 两者逐牌相等

### Requirement: 七对分支获得自然偏向保护

反应侧评价 SHALL 依赖 `shanten()` 的标准形/七对双分支语义（副露后评价自动退出七对分支）实现"PASS 基准保留七对分支"的自然偏向，MUST NOT 新增七对特判规则。

#### Scenario: 七对为更优分支时拒绝副露
- **WHEN** 本家暗牌的七对分支向听数严格优于标准形分支（且优于任何吃/碰 + 最佳弃牌后的标准形向听数），存在合法吃或碰选项
- **THEN** bot 选择 PASS

### Requirement: 弃牌侧与胡/飘路径行为保持不变

本变更 MUST NOT 改变 `choose_action()` discard 分支的行为（HU 优先、`_should_piao` 财飘判定）及弃牌侧 `choose_discard()` 的排序语义；既有弃牌侧测试 SHALL 全部保持通过。

#### Scenario: 现有弃牌/HU/财飘回归
- **WHEN** 运行 `tests/test_bot.py` 既有全部用例
- **THEN** 全部通过，行为无漂移

### Requirement: 反应侧决策性能受闸门约束

重写后的 `_choose_react()` 单次决策 SHALL 控制 ukeire 调用量：吃窗 ≤33 次（3 吃法 × ≤11 舍牌候选）、纯碰窗 ≤11 次（legacy KONG 路径不计），Rust 内核默认路径。MUST 通过可判定压测：同机同内核环境，对变更前 commit `9f4f8de` 与变更后版本各运行 3 次 `python3 -m mj.evaluate 200`，4 bots 段中位吞吐（elapsed/games）下降不超过 15%。

#### Scenario: 吞吐压测
- **WHEN** 实现完成后在同一机器、同一 Python/Rust 内核环境下，对变更前 `9f4f8de` 与变更后版本各运行 3 次 `python3 -m mj.evaluate 200`
- **THEN** 4 bots 段中位吞吐下降 ≤15%
