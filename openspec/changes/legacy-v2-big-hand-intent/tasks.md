## 1. 基线冻结与契约测试

- [ ] 1.1 记录当前 `main` 的 `DEFAULT_BOT_EVALUATOR=legacyV2`、`LegacyTwoPlyProfile.weighted_online()` 参数、Rust weighted kernel version 与 profile fingerprint
- [ ] 1.2 冻结当前普通弃牌 fixed fixtures：普通 0/1/2 向听、财神保护、freeze 只能弃刚摸牌、frontier singleton、shape_guard 扩围、kernel unavailable、budget fallback
- [ ] 1.3 冻结现有爆头/财飘 fixtures：`best_s==0` 精确 baotou scope、X/Y/Z 收手、`PIAO_WALL_GUARD=6`、HU vs piao、freeze 下仅刚摸财神可飘
- [ ] 1.4 增加 fallback parity 测试：在未启用 BigHandIntent 时，legacyV2 selected/action/evaluation 与基线逐例一致
- [ ] 1.5 增加性能基线脚本输出 ordinary discard p50/p95/p99、frontier root count、fallback rate、4-bot elapsed/games

## 2. 七对与豪华潜力的单一真源

- [ ] 2.1 在 `mj/shanten.py` 抽取可复用 `chiitoi_shanten()`（或等价 package helper），现有 `_chiitoi` 改为复用它，禁止第二套算法
- [ ] 2.2 用现有七对规则覆盖 0～4 财神、locked、奇数自然牌、四白板边界的 parity tests
- [ ] 2.3 在 `mj/big_hand_intent.py` 新增不可变 `BigHandIntent`
- [ ] 2.4 实现 `luxury_groups`：仅自然持有 4 张真牌计数
- [ ] 2.5 实现 `luxury_upgrade_tiles/live`：仅自然持有 3 张且公开 remaining>0 才计升级机会
- [ ] 2.6 固定回归：第四张已在牌河/副露可见时 `luxury_upgrade_live=0`，不得继续保护死豪华机会
- [ ] 2.7 实现 `WHITE_RICH` 资源信号：wild_count/wild_live；不得调用 baotou_ukeire/EV2/DFS

## 3. BigHandIntent 强度与 profile

- [ ] 3.1 新增 `BigHandProfile` 或在 `LegacyTwoPlyProfile` 增加版本化字段：enabled、same_shanten_enabled、plus_one_enabled、min_live、max_opponent_melds、ukeire loss guard
- [ ] 3.2 所有字段进入 profile payload/fingerprint 和 evaluation diagnostics
- [ ] 3.3 第一版 kinds 固定为 `CHIITOI/LUXURY_CHIITOI/WHITE_RICH`，允许同时命中
- [ ] 3.4 定义 NONE/WEAK/MEDIUM/STRONG 的离散规则；禁止通过一个无单位 `intent_score` 与 weighted future 相加
- [ ] 3.5 实现纯 public-state 单测：修改 opponent hidden hands / true wall order 而保持 hero+visible 相同，intent 必须完全一致

## 4. choose_discard root plumbing

- [ ] 4.1 重构 `choose_discard()`：先枚举所有合法 distinct discard roots，再计算 `best_s`
- [ ] 4.2 显式生成 `speed_pool = roots where shanten==best_s`
- [ ] 4.3 非 legacyV2/rollback 路径继续只使用 speed_pool，确保零行为漂移
- [ ] 4.4 legacyV2 路径对所有 roots 生成 cheap intent 元数据，但只允许 selector 额外提名最多 1 个 challenger
- [ ] 4.5 freeze/合法动作仍以 `Game.legal_actions()`/drawn 限制为真源，不因重构恢复历史冻结盲
- [ ] 4.6 已进入 `hand[W]>0 && best_s==0` 精确爆头 scope 时继续走原 `_choose_discard_baotou()`；BigHandIntent 不覆盖该路径

## 5. LegacyRootCandidate 与安全 fallback

- [ ] 5.1 扩展 `LegacyRootCandidate` 的 intent/diagnostic 字段，保持旧构造调用 source-compatible
- [ ] 5.2 `_normalise_roots/_root_features/as_json` 支持新字段且不改变未启用时排序
- [ ] 5.3 在 `_weighted_evaluation()` 入口显式冻结 `legacy_speed_best`，只能从旧 speed/fallback pool 计算
- [ ] 5.4 所有 kernel unavailable/version mismatch/budget/incomplete/partial_not_acceptable path 强制返回 `legacy_speed_best`
- [ ] 5.5 写回归：人为构造一个 `_legacy_key` 很漂亮但 `best_s+1` 的 challenger，触发 kernel failure 后仍必须返回原 speed best
- [ ] 5.6 offline profile 的 fail-loud 语义保持，不得用 big-hand fallback 生成伪完整标签

## 6. Phase A：同向听 BigHandGuard

- [ ] 6.1 在 `mj/legacy_eval.py` 新增 `_apply_big_hand_guard()`，只从 `root.shanten==best_s` 的 roots 中扩围
- [ ] 6.2 same-shanten 强七对/豪华候选可因 intent 被重新纳入，即使 current ukeire 不是 primary max
- [ ] 6.3 same-shanten 候选进入后仍使用原 legacyV2 weighted comparator，不添加 magic bonus
- [ ] 6.4 与 `_apply_shape_guard()` 同时命中时执行稳定 slot policy，frontier 总数始终 `<= max_frontier_candidates`
- [ ] 6.5 diagnostics 记录 `admitted_by=primary|shape_guard|big_hand_guard` 与被 cap 淘汰原因
- [ ] 6.6 固定牌例：拆掉自然四张/活三张会损失豪华路线时，保留路线的同向听候选至少获得比较资格
- [ ] 6.7 固定牌例：豪华第四张已死时不得仅靠 luxury intent 扩围

## 7. Phase B：`best_s+1` challenger（默认先关闭）

- [ ] 7.1 selector 只允许 `root.shanten==best_s+1`，禁止 `+2` 或更差
- [ ] 7.2 要求 `locked==0 && intent_strength==STRONG`
- [ ] 7.3 实现强 `LUXURY_CHIITOI` gate：chiitoi_shanten<=1 且已有/活豪华升级明显优于 speed 路线
- [ ] 7.4 实现 `WHITE_RICH+CHIITOI` gate：wild_count>=2、chiitoi_shanten<=1、对子结构达到 profile 门槛
- [ ] 7.5 实现局势收手：live wall、opponent meld guard；参数进入 fingerprint
- [ ] 7.6 big-hand 最多占 1 个 frontier slot，总 root count 仍 <=3
- [ ] 7.7 Phase B profile 默认 `plus_one_enabled=false`，只有完成第 11 节 A/B 后才能改默认

## 8. speed winner + independent override

- [ ] 8.1 将现有 comparator 明确封装为 `select_speed_winner()`（或等价逻辑），仅对同向听 speed roots 使用
- [ ] 8.2 `best_s+1` challenger 可以复用同一 Rust weighted future 计算，但不得直接塞进 speed comparator
- [ ] 8.3 新增 `can_big_hand_override()`：检查 strong intent、收手、future complete/safe-partial、current ukeire absolute floor、relative ukeire loss、live luxury reason
- [ ] 8.4 不直接比较不同 shanten roots 的 `future_improve_weight` 差值作为统一 EV；只把 challenger future 指标作为自身质量 guard/diagnostics
- [ ] 8.5 override exact failure/tie 默认 speed winner
- [ ] 8.6 diagnostics 输出 `speed_winner/big_hand_challenger/override/override_reason`
- [ ] 8.7 任一 override 必要字段 unknown → speed winner，不猜值

## 9. 爆头/财飘兼容回归

- [ ] 9.1 2～3 白板且未听牌时可以产生 `WHITE_RICH` intent
- [ ] 9.2 进入 `best_s==0` 后仍切换到现有精确 baotou ranking，而不是 cheap WHITE_RICH 排序
- [ ] 9.3 `_should_piao()`、墙量 6、X/Y/Z 收手、YCBK 既有 scope 不被修改
- [ ] 9.4 财飘/HU/KONG draw window 的现有 score comparison 全部保持
- [ ] 9.5 4 白板、爆头、豪华七对叠加计分只由 scoring/win 规则决定，BigHandIntent 不复制计分逻辑

## 10. Replay / Audit

- [ ] 10.1 扩展本地 replay/decision report，逐 ordinary discard 输出 intent 字段
- [ ] 10.2 增加 root summary：speed_pool、frontier、shape-guard admitted、big-hand admitted、challenger、speed winner、final selected
- [ ] 10.3 扫描现有回放，按 `CHIITOI/LUXURY_CHIITOI/WHITE_RICH` 分桶统计触发率、扩围率、override 率
- [ ] 10.4 抽样人工检查至少 100 个大牌触发点，区分“合理保护”“过度做大”“死豪华机会”“晚局未收手”
- [ ] 10.5 把典型分歧固化为 regression fixtures

## 11. 性能与收益门禁

- [ ] 11.1 micro benchmark：BigHandIntent p50/p95/p99；p95 <=1ms，且不得出现 Python DFS/baotou 全枚举
- [ ] 11.2 runtime 断言/测试：online weighted frontier roots 永远 <=3
- [ ] 11.3 同机同 Rust 内核，baseline 与 Phase A 交错各至少 3×200 局；ordinary discard p95 增幅 <=10%，4-bot elapsed/games 中位退化 <=10%
- [ ] 11.4 全量测试通过；legacyV2、reaction-v2、KONG、baotou/piao、freeze 行为无无关漂移
- [ ] 11.5 Phase A 做至少 4096 局 paired/fair A/B，报告平均结算、95% CI、按 intent kind 分桶
- [ ] 11.6 Phase B 做独立至少 4096 局 paired/fair A/B；报告 `+1 shanten` override 的发生率、平均收益、95% CI、live-wall/opponent-meld 分桶
- [ ] 11.7 若 Phase B 95% CI 下界 <0 或存在明显晚局负桶，保持 `plus_one_enabled=false`
- [ ] 11.8 报告 kernel fallback/budget fallback 时 `challenger_selected=0`，证明事务 fallback 没有被破坏
- [ ] 11.9 `git diff --check` 干净，更新 PROGRESS.md 与 evaluator/profile fingerprint 说明；本 change 不混入 BC/RL 训练产物