## 1. 评价基础设施（mj/bot.py 内新增辅助）

- [ ] 1.1 实现 `_eval_standing(hand, locked, vis)`：对站立暗牌原样计算 `(shanten, ukeire, shape)`，作为 PASS 基准与 claim 评价的统一评价入口
- [ ] 1.2 实现 `_best_post_claim_discard(g, seat, hand, locked, vis)`：在 need+1 态手牌上枚举每种合法舍牌，对每个 need 态站立手牌调 `_eval_standing`，返回最优 `(评分, 舍牌)`；张数口径依赖 `shanten()` 的 ValueError 断言，覆盖 0/1/2 副露三档
- [ ] 1.3 验证 vis 快照复用口径：react 时点单份 `visible_counts(seat)` 贯穿 PASS 基准与全部 claim 评价，不重复计算、不中途刷新

## 2. `_choose_react()` 重写

- [ ] 2.1 PASS 基准改为 `_eval_standing(hand, locked, vis)`，删除含糊的 `cur_s` 口径
- [ ] 2.2 PONG 评价：remove 2 → `_best_post_claim_discard`（locked+1）
- [ ] 2.3 CHOW_LOW/MID/HIGH 评价：各 remove 2 → `_best_post_claim_discard`（locked+1）
- [ ] 2.4 实现决策规则：`claim.s < pass.s` 接受；等向听需 `uke_gain ≥ GAIN[action]`（PONG=2、CHOW=4 模块级常量）；`claim.s > pass.s` 拒绝；多个过门槛选项按 更低 shanten → 更高 ukeire → 更低结构损失 → 稳定 action 顺序 选最优
- [ ] 2.5 KONG_OPEN 分支保持现有独立启发式与动作选择原样迁移，`choose_action()` discard 分支（HU/`_should_piao`）零改动
- [ ] 2.6 更新 bot.py 模块 docstring 的决策原则描述（反应侧新口径）

## 3. 固定牌例回归（tests/test_bot.py 追加 TestReactDecision）

- [ ] 3.1 张数口径三档（0/1/2 副露）：吃/碰评价不触发 `shanten()` ValueError
- [ ] 3.2 vis 快照不变量：claim 前 vis == claim 后、弃牌前 vis（逐牌相等）
- [ ] 3.3 可吃但吃后 ukeire 大降 → PASS
- [ ] 3.4 吃后向听降低 → CHI，且两种吃法选"吃完 + 最佳弃牌"后效率更高者
- [ ] 3.5 可碰但向听不变且 ukeire 大降 → PASS；等向听时 uke_gain 恰达 PONG 阈值 2 → 可接受（边界牌例）
- [ ] 3.6 吃的边界：等向听 uke_gain=3（<4）→ PASS；uke_gain=4 → 可接受
- [ ] 3.7 碰后向听降低 → PONG
- [ ] 3.8 七对保护：本家七对分支严格更优的固定牌型，存在合法吃/碰 → PASS（测试名不写死"五对子=七对听牌"）
- [ ] 3.9 现有弃牌侧/HU/财飘用例全部保持通过（零行为漂移回归）

## 4. 性能闸门与收尾

- [ ] 4.1 压测 `python3 -m mj.evaluate 200` 吞吐，相对基线降幅可接受才合入；必要时用 ukeire 记忆化/候选剪枝收窄
- [ ] 4.2 全量 `python3 -m pytest tests/ -q` 通过（含 tests/test_bc_pipeline.py 等下游语义回归）
- [ ] 4.3 同步 PROGRESS.md：bot 反应侧决策新口径、阈值初值、测试清单
- [ ] 4.4 `git diff --check` 干净，单 commit 只含 `mj/bot.py` + `tests/test_bot.py` + 文档
