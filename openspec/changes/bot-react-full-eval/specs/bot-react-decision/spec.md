## ADDED Requirements

### Requirement: 吃/碰按"副露 + 最佳弃牌后站立牌面"完整评价

启发式 bot 对他家弃牌的反应侧决策（CHOW_LOW/MID/HIGH、PONG）SHALL 以"副露动作完成 + 枚举每种合法舍牌后的站立牌面"为评价对象，评价键固定为 `(shanten, ukeire, shape)` 三元组，其中 ukeire 按可见牌折算真实剩余进张。

- 吃/碰评价流程 MUST 为：从反应时点手牌移除该动作消耗的 2 张牌得到 need+1 态手牌，显式枚举每种合法舍牌得到 need 态站立手牌，对每个站立手牌计算 `(shanten, ukeire, shape)`，取最优者作为该动作评分。
- 评价张数口径 MUST 满足 `shanten()` 的暗牌张数断言（副露数 0/1/2 三档均须正确）。
- 明杠（KONG_OPEN）MUST 保留既有独立启发式，不套用本评价框架。

#### Scenario: 吃后向听降低则执行吃
- **WHEN** 某吃选项（含其最佳舍牌后的站立牌面）的 shanten 低于 PASS 基准
- **THEN** bot 从所有过门槛的吃/碰选项中按 更低 shanten → 更高 ukeire → 更低结构损失 → 稳定 action 顺序 选出最优并执行

#### Scenario: 两种吃法选最终牌面更优者
- **WHEN** 同一张弃牌存在多种合法吃法，且吃完 + 最佳弃牌后的 (shanten, ukeire) 不同
- **THEN** bot 选择最终站立牌面评价更高的吃法

### Requirement: PASS 基准为反应时点站立暗牌原样评价

反应侧决策 SHALL 存在 PASS 基准：对反应时点的站立暗牌（13−3·locked 张，不做任何增删）以 `(shanten, ukeire, shape)` 评价，与各吃/碰选项评分同键比较。基准 MUST NOT 引入"含他家刚打出候选牌"的口径。

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

重写后的 `_choose_react()` 单次决策 SHALL 控制 ukeire 调用量（约 ≤50 次/决策，Rust 内核默认路径），MUST 在合入前通过吞吐压测（`python3 -m mj.evaluate 200` 相对基线降幅可接受）。

#### Scenario: 吞吐压测
- **WHEN** 实现完成后运行 `python3 -m mj.evaluate 200`
- **THEN** 相对弃牌侧重构后基线（~107.8 局/秒单核自博弈口径）吞吐降幅在可接受范围内
