## 1. LegacyShapeProgress 基础设施

- [ ] 1.1 在 `mj/bot.py` 新增 `LegacyShapeProgress`（或等价不可变结构），字段至少包含 shanten、ukeire types/live、baotou_ready、baotou ukeire types/live、piao draw types/live
- [ ] 1.2 实现统一站立态评价 helper：输入 standing/locked/visible/context，输出 `LegacyShapeProgress`；普通 ukeire 与当前公开信息口径保持一致
- [ ] 1.3 实现显著增益 helper，冻结第一版阈值：PONG absolute +4、CHOW absolute +6、ratio 1.50；before=0 时只用 absolute；听牌牌种 +2 且 live 不降作为独立 wait-expansion 条件
- [ ] 1.4 爆头信号：`baotou_ready` 直接用 `is_baotou_wait`；定量 `baotou_ukeire` 仅在同向听且 Rust kernel 可用时懒算，无 Rust 时返回 unknown 且不得单独授权 claim
- [ ] 1.5 财飘推进：抽取纯结构的“下一摸可财飘”枚举 helper，复用 `is_win/is_baotou` 和现有墙量/收手约束；只在手里有财神、同向听且需要比较时懒算
- [ ] 1.6 为 progress helper 增加缓存/计数诊断，确认没有把 decomposition、baotou 全枚举放进普通 discard 热路径

## 2. 重写 legacy CHOW/PONG 推进 Gate

- [ ] 2.1 保留“副露 + 最佳弃牌后 standing”框架，PASS/claim 都改为读取 `LegacyShapeProgress`
- [ ] 2.2 固化硬门：claim shanten 下降直接接受；shanten 上升直接拒绝；同 shanten 才进入 shape-progress Gate
- [ ] 2.3 实现同向听四类推进 reason：`baotou_progress`、`piao_progress`、`wait_expansion`、`ukeire_expansion`
- [ ] 2.4 删除 legacy 仅凭 `PONG_UKE_GAIN=2` / `CHOW_UKE_GAIN=4` 即接受的行为；旧常量移除或只保留迁移注释，不能再参与决策
- [ ] 2.5 多候选排序按 design D5：shanten → baotou → piao → baotou-ukeire → ordinary ukeire → types → shape cost → stable action
- [ ] 2.6 扩展 `return_evaluation` / replay 诊断，输出 before/after progress、thresholds、accepted/rejected reason

## 3. KONG Structure Guard

- [ ] 3.1 基于 `mj.hand_eval.enumerate_decompositions(..., optimal=True)` 实现只读取当前最优 standard decomposition 的材料占用检查，不写邻牌启发式特判
- [ ] 3.2 暗杠：要求“natural triplet(3) + redundant single(1)”；目标牌若还参与 sequence/pair/taatsu 则拒绝
- [ ] 3.3 加杠：已有 PONG 前提下要求手中第 4 张是最优 decomposition 的 single；用于 sequence/pair/taatsu 则拒绝
- [ ] 3.4 明杠：手中 3 张目标牌必须能在至少一个当前最优 standard decomposition 中共同作为 natural triplet
- [ ] 3.5 七对严格领先时验证 KONG 不会通过更差标准形 decomposition 绕过最优分支
- [ ] 3.6 Guard 返回结构化 reason，至少区分 `no_optimal_standard`、`tile_used_by_sequence`、`tile_used_by_pair_or_taatsu`、`no_redundant_single`、`safe_triplet_plus_single`

## 4. KONG 牌效保持与杠开 Gate

- [ ] 4.1 为 self-kong 构造同 profile 的非杠 baseline standing；修复当前有 KONG 候选时 `choose_discard(g, seat)` 未透传 `discard_profile` 的问题
- [ ] 4.2 为 closed/add/open 构造 replacement draw 前的 post-KONG standing，并计算同口径 shanten/ukeire/baotou
- [ ] 4.3 固化 shape-preserve Gate：post shanten 不得变差；同 shanten 时 ukeire_live 不得减少；baseline 已爆头时 post 不得丢失爆头
- [ ] 4.4 实现 KONG-KAI Gate：post shanten 必须为 0；活墙可补；公开 remaining 中 `winning_mass>0`；沿用 `kong_draw=True` 的 YCBK 语义
- [ ] 4.5 重构 `_evaluate_kong_next_draw` 为 Guard 后的评分器：未通过 Guard 的动作不得进入 EV 比较
- [ ] 4.6 为 KONG_OPEN 增加同口径 replacement-draw 评价；删除 `if KONG_OPEN in acts: return _legacy_claim_react(...)` 的整窗短路
- [ ] 4.7 PONG + KONG_OPEN 同窗最终选择：更低 post-action shanten 优先；同 shanten 时先保证 KONG 牌型进度不劣，再允许 replacement EV/chain 作为优势
- [ ] 4.8 KONG 诊断输出 structure/shape/kong-kai/EV 各层结果和 rejection_reason

## 5. 固定牌例回归

- [ ] 5.1 CHOW/PONG：向听下降必通过；向听上升必 PASS
- [ ] 5.2 同向听小幅 ukeire（旧 +2/+4 能通过但新显著门槛不足）必须 PASS
- [ ] 5.3 同向听从普通形升级为 `baotou_ready` 必须允许副露
- [ ] 5.4 同向听 baotou-ukeire 显著增加（Rust 可用）允许；Rust 不可用时该定量信号不能单独授权
- [ ] 5.5 同向听新增至少 2 张公开剩余的结构性财飘机会允许
- [ ] 5.6 shanten=0 时 wait types +2 且 live waits 不降允许；仅增加 1 种且 live gain 不达门槛则 PASS
- [ ] 5.7 shanten>0 时下一摸降向听 live ukeire 同时满足 absolute + ratio 门槛才允许
- [ ] 5.8 暗杠回归：最优结构含 `123333m = 123m + 333m` 时 `KONG_CLOSED(3m)` 必须拒绝
- [ ] 5.9 暗杠正例：最优结构存在 `333m + single 3m` 且其余 Gate 满足时可进入 EV
- [ ] 5.10 加杠回归：公开 `PONG(3m)` + 手牌 `123m` 中唯一 3m 用于顺子时必须拒绝 KONG_ADD
- [ ] 5.11 明杠回归：手持三张目标牌但最优 decomposition 不把三张共同作为刻子时拒绝 KONG_OPEN
- [ ] 5.12 post-KONG shanten>0 时无论 EV 多高都拒绝
- [ ] 5.13 post-KONG shanten=0 但所有胡牌张 remaining=0 时拒绝
- [ ] 5.14 post-KONG 同向听但 live waits 下降时拒绝
- [ ] 5.15 PONG+KONG_OPEN 同窗：KONG 被 Guard 拒绝但 PONG 明确推进时选择 PONG；两者都不推进时 PASS
- [ ] 5.16 现有 HU、财飘、死墙 no-kong、补牌、tile conservation、freeze 相关回归全部保持通过

## 6. 性能、审计与收尾

- [ ] 6.1 单测：`python3 -m pytest tests/test_bot.py tests/test_game.py -q` 通过，再跑 `python3 -m pytest tests/ -q`
- [ ] 6.2 性能闸门：同机同内核冻结当前 main baseline 与候选版本，交错各运行至少 3 次 `python3 -m mj.evaluate 200`，4 bots elapsed/games 中位数退化 ≤15%
- [ ] 6.3 额外记录 react/KONG 决策 p50/p95/p99、decomposition 调用数、baotou 定量调用数、无 Rust fallback 率；确认无 Python baotou 全枚举进入线上热路径
- [ ] 6.4 用本地批次 replay 扫描旧版发生 CHOW/PONG/KONG 的样本，输出新旧动作差异及 reason 分布，重点抽查：旧 +2/+4 边缘副露、`123333` 类杠、无活杠开张
- [ ] 6.5 同步 PROGRESS.md：legacy 新原则“吃碰看推进，杠看无损 + 杠开”、阈值版本与主要固定牌例
- [ ] 6.6 `git diff --check` 干净；实现 commit 不混入 BC/RL 重训产物或 shape-v2 评分修改
