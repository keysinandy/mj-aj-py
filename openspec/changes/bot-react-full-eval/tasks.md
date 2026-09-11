## 1. 评价基础设施（mj/bot.py 内新增辅助）

- [x] 1.1 实现 `_eval_standing(hand, locked, vis)`：对站立暗牌原样计算 `(shanten, ukeire)`，作为 PASS 基准与 claim 评价的统一门槛判据入口
- [x] 1.2 实现 `_best_post_claim_discard(g, seat, hand, locked, vis)`：在 need+1 态手牌上枚举每种合法舍牌，对每个 need 态站立手牌调 `_eval_standing`，返回最优 `(shanten, ukeire, discard_shape_cost, 舍牌)`；张数口径依赖 `shanten()` 的 ValueError 断言，覆盖 0/1/2 副露三档
- [x] 1.3 验证 vis 快照复用口径：react 时点单份 `visible_counts(seat)` 贯穿 PASS 基准与全部 claim 评价，不重复计算、不中途刷新

## 2. `_choose_react()` 重写

- [x] 2.1 PASS 基准改为 `_eval_standing(hand, locked, vis)`，删除只覆盖向听数单维的 `cur_s` 口径；一并修正 `bot.py` 现"含刚打出的候选牌"的误导注释（反应玩家手牌本不含他家 pending 牌）
- [x] 2.2 PONG 评价：`KONG_OPEN ∈ acts` 时整个 claim 窗口（含 PONG）走 legacy 决策不进新 evaluator；`KONG_OPEN ∉ acts` 时 remove 2 → `_best_post_claim_discard`（locked+1）
- [x] 2.3 CHOW_LOW/MID/HIGH 评价：各 remove 2 → `_best_post_claim_discard`（locked+1）
- [x] 2.4 实现决策规则：`claim.s < pass.s` 接受；等向听需 `uke_gain ≥ GAIN[action]`（PONG=2、CHOW=4 模块级常量）；`claim.s > pass.s` 拒绝；多个过门槛选项在当前 react mode 合法候选内按 更低 shanten → 更高 ukeire → 更低 discard_shape_cost → 稳定 action 顺序 选最优
- [x] 2.5 KONG_OPEN 分支与整个 KONG 同窗路径（含手持三张 pending 时的 PONG）原样保留 legacy 决策；`choose_action()` discard 分支（HU/`_should_piao`）零改动
- [x] 2.6 更新 bot.py 模块 docstring 的决策原则描述（反应侧新口径）

## 3. 固定牌例回归（tests/test_bot.py 追加 TestReactDecision）

- [x] 3.1 张数口径三档（0/1/2 副露）：吃/碰评价不触发 `shanten()` ValueError
- [x] 3.2 vis 快照不变量：claim 前 vis == claim 后、弃牌前 vis（逐牌相等）
- [x] 3.3 可吃但吃后 ukeire 大降 → PASS
- [x] 3.4 吃后向听降低 → CHI，且两种吃法选"吃完 + 最佳弃牌"后效率更高者
- [x] 3.5 可碰但向听不变且 ukeire 大降 → PASS；等向听时 uke_gain 恰达 PONG 阈值 2 → 可接受（边界牌例）
- [x] 3.6 吃的边界：等向听 uke_gain=3（<4）→ PASS；uke_gain=4 → 可接受
- [x] 3.7 碰后向听降低 → PONG
- [x] 3.8 七对保护：本家七对分支严格更优的固定牌型，存在合法吃/碰 → PASS（测试名不写死"五对子=七对听牌"）
- [x] 3.9 KONG 窗口不漂移：手持三张 pending 牌（PONG+KONG_OPEN 同窗）固定牌例，决策与既有 legacy 实现一致
- [x] 3.10 现有弃牌侧/HU/财飘用例全部保持通过（零行为漂移回归）

## 4. 性能闸门与收尾

- [x] 4.1 吞吐压测（可判定闸门）：同机同内核环境，对变更前 commit `9f4f8de` 与变更后版本各运行 3 次 `python3 -m mj.evaluate 200`，4 bots 段中位吞吐降幅 ≤15% 才合入；ukeire 调用计数核对吃窗 ≤33、纯碰窗 ≤11，超出即查重复计算
- [x] 4.2 全量 `python3 -m pytest tests/ -q` 通过（含 tests/test_bc_pipeline.py 等下游语义回归）
- [x] 4.3 同步 PROGRESS.md：bot 反应侧决策新口径、阈值初值、测试清单
- [x] 4.4 `git diff --check` 干净，单 commit 只含 `mj/bot.py` + `tests/test_bot.py` + 文档
