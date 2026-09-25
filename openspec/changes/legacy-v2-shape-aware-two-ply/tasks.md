## 1. 冻结 baseline 与复现问题

- [ ] 1.1 记录当前 main commit、legacyV2 profile fingerprint、weighted kernel version、40/50ms soft/hard budget、max_frontier_candidates=3
- [ ] 1.2 固化用户专项牌例：`23455m 124s EE w` + 副露 `789p`，验证弃 1s / 4s 都为 0 向听且当前 ukeire 都为 11
- [ ] 1.3 把当前错误选择保存为 regression reproduction：确认真实路径为 `baotou_scope` 提前返回，记录 1s/4s 的 baotou tier、baotou_ukeire、旧 shape_loss（5/3）、feed_risk、`stage_b_entered=false`、selected=4s
- [ ] 1.4 冻结 ordinary discard 性能 baseline：p50/p95/p99/max、Rust raw、complete/partial/fallback、4-bot elapsed/game
- [ ] 1.5 冻结积分 baseline 脚本、seed 集合、seat rotation 与 settlement score 口径

## 2. 建立 StandingShapeQuality 单一真源

- [ ] 2.1 新增 mj/shape_quality.py 或等价模块，定义 versioned StandingShapeQuality
- [ ] 2.2 明确区分 discard_shape_cost 与 standing_shape_quality，禁止复用旧字段名掩盖语义变化
- [ ] 2.3 实现 suited taatsu class：RYANMEN / CENTRAL_KANCHAN / EDGE_KANCHAN / PENCHAN
- [ ] 2.4 固化最小排序：23>24>13>12，78>68>79>89
- [ ] 2.5 实现 9-rank 非重叠 decomposition / DP，避免 234 同时重复计 23 与 34
- [ ] 2.6 honors、pair、triplet、isolated 边界测试
- [ ] 2.7 0～4 财神与 locked 输入测试；shape helper 不得伪造 wildcard 虚拟 taatsu
- [ ] 2.8 为 signature/encoded 增加 shape_quality_version 并写入 profile fingerprint

## 3. Root standing shape 接入

- [ ] 3.1 LegacyRootCandidate 增加 standing_shape_quality/signature/version，保留原 shape_loss/discard_shape_cost
- [ ] 3.2 root 枚举在 post-discard hand 上计算 standing shape
- [ ] 3.3 _limit_weighted_frontier 在实验 profile 下改用 standing shape，而不是仅用 discard shape cost
- [ ] 3.4 为 shape_guard 增加新 standing-shape admission 语义，禁止复用旧 delta=8 的单位
- [ ] 3.5 shape guard 与 big-hand guard 同时存在时保持 frontier <=3、稳定 slot policy
- [ ] 3.6 feature flag 关闭时 root candidate set、selected、evaluation 与 baseline parity

## 4. baotou_scope shape tie-break 接入

- [ ] 4.1 冻结当前 `_choose_discard_baotou()` key：tier → 财神保护 → -baotou_ukeire → legacy shape_loss → feed → tile
- [ ] 4.2 为每个 baotou speed candidate 在 post-discard standing hand 上计算 standing_shape_quality
- [ ] 4.3 shape-aware key 改为：tier → 财神保护 → -baotou_ukeire → -standing_shape → legacy shape_loss → feed → tile
- [ ] 4.4 tier 或 baotou_ukeire 不相等时，standing shape MUST NOT 覆盖更高优先级结果
- [ ] 4.5 第一版 baotou_scope 不调用 generic weighted two-ply / Stage B，不新增 future-shape DFS
- [ ] 4.6 feature flag off 时 baotou_scope action/evaluation 与 baseline 逐 fixture 100% parity
- [ ] 4.7 golden 完整牌例必须走真实 baotou_scope：旧 key 选 4s，shape-aware key 选 1s
- [ ] 4.8 Rust baotou kernel unavailable / BAOTOU_UKE_BUDGET_NODES 超限 / X-Y-Z 收手时保持既有整档 fallback，shape 不得污染 fallback
- [ ] 4.9 diagnostics 记录 decision_scope=baotou_scope、每候选 tier/u1/standing shape/legacy shape/feed 与 baotou_shape_used
- [ ] 4.10 若后续需要 baotou future shape，另立有节点预算的 metric/change；本 change 不隐式扩搜索

## 5. Python two-ply reference 接入 child shape

- [ ] 5.1 child 最小 shanten / max ukeire / max types 后计算 standing shape
- [ ] 5.2 child comparator 增加 max child standing shape
- [ ] 5.3 draw row 增加 child_shape_quality
- [ ] 5.4 root FutureEvaluation 增加 future_shape_quality_sum/mean/denominator
- [ ] 5.5 _weighted_root_key 在 future types 后、旧 shape/feed 前加入 future shape 与 standing shape
- [ ] 5.6 Stage A strict future-improve winner shortcut 保持现有优先级，不因缺失 shape 误判
- [ ] 5.7 incomplete / partial unsafe 时缺失 future shape 保持 null，不得当 0 排序

## 6. Rust weighted kernel 接入

- [ ] 6.1 在 rust/src/lib.rs 实现与 Python 同语义的 shape decomposition/helper
- [ ] 6.2 run_stage_b_units 在 shanten/ukeire/types tie 后比较 child shape
- [ ] 6.3 StageBOutcome 增加 best_shape，draw row 暴露 child shape
- [ ] 6.4 WeightedRootAccumulator 增加 future_shape weighted accumulator
- [ ] 6.5 native output metrics/rows contract 升级并同步 Python adapter validator
- [ ] 6.6 bump WEIGHTED_TWO_PLY_KERNEL_VERSION / REQUIRED version
- [ ] 6.7 旧 wheel/version mismatch 必须事务 fallback，不猜 tuple、不把 missing shape 记 0
- [ ] 6.8 MJ_KERNELS=python 与 Rust 对拍结果完全一致

## 7. 专项修复验收

- [ ] 7.1 golden：23s > 24s > 13s > 12s
- [ ] 7.2 golden：78s > 68s > 79s > 89s
- [ ] 7.3 golden：124s decomposition 选择 24s 结构优于 12s
- [ ] 7.4 用户完整牌例中，弃 1s 与弃 4s 的 shanten/普通 ukeire 保持相同，真实 `baotou_scope` 下 shape-aware profile 必须选择弃 1s，并确认不是 weighted Stage B 产生的结果
- [ ] 7.5 独立 weighted Stage B fixture：保留 24s 的 root 在 draw=5s 后能通过 child 打 2s 留 45s，被记录为结构升级；该测试不得依赖 baotou_scope
- [ ] 7.6 构造 current ukeire 明显不同的牌例，确认 shape 不得覆盖更大的直接牌效
- [ ] 7.7 构造 child shanten 不同的牌例，确认 shape 不得覆盖更低 child shanten
- [ ] 7.8 构造高 feed-risk 牌例，确认风险仍在最终 tie 层生效且 diagnostics 可解释

## 8. 随机与差分正确性

- [ ] 8.1 Python shape helper 对至少 10000 个随机合法 standing hands 做 determinism 测试
- [ ] 8.2 Python vs Rust shape signature/encoded 10000 手 parity
- [ ] 8.3 Python vs Rust weighted two-ply targeted fixtures parity
- [ ] 8.4 Python vs Rust weighted two-ply 随机 roots parity：selected、future improve、ukeire、types、future shape、best child discard
- [ ] 8.5 freeze/grab-discard legality 回归：shape 层不得新增非法 child discard
- [ ] 8.6 hidden opponent hands / true wall order 改变但 public state 相同，shape 与 weighted result 必须一致

## 9. Fallback 与兼容回归

- [ ] 9.1 native kernel unavailable -> baseline legacy fallback
- [ ] 9.2 kernel version mismatch -> baseline legacy fallback
- [ ] 9.3 hard deadline / work budget -> baseline fallback，future shape 不得部分污染 winner
- [ ] 9.4 partial_not_acceptable -> baseline fallback
- [ ] 9.5 frontier_singleton 仍可短路，不强制为了 shape 进入 two-ply
- [ ] 9.6 big-hand intent、baotou/piao、reaction-v2、KONG、freeze 全量回归
- [ ] 9.7 feature flag off 的 paired fixture action parity 为 100%

## 10. Replay / diagnostics

- [ ] 10.1 evaluation JSON 输出 shape_quality_version、discard_shape_cost、standing_shape_signature/quality
- [ ] 10.2 future JSON 输出 child_shape、future_shape_sum/mean/denominator
- [ ] 10.3 输出 `decision_scope=baotou_scope|weighted_two_ply|legacy`、`shape_quality_stage=baotou|root|stage_b`、shape_changed_winner、stage_b_entered
- [ ] 10.4 replay 能直接解释用户牌例为何从弃 4s 改为弃 1s
- [ ] 10.5 扫描历史 replay，统计 shape-aware divergence rate 与主要 pattern bucket

## 11. 时间性能验证

- [ ] 11.1 同机交错 micro benchmark：shape helper p50/p95/p99，分别测 Python reference 与 Rust
- [ ] 11.2 同机交错 ordinary discard benchmark：baseline/candidate 各至少 3×1000 decisions
- [ ] 11.3 报告 ordinary discard p50/p95/p99/max；p95 相对增幅必须 <=10%，p99 <=15%
- [ ] 11.4 报告 Rust raw p50/p95/p99/max、stage_b_entered、complete/partial/fallback，并单列 baotou_scope count、baotou_elapsed_ms 与 baotou_shape_changed rate
- [ ] 11.5 fallback rate 相对 baseline 增加不得 >1 percentage point
- [ ] 11.6 不提高 weighted_online 50ms hard budget，不提高 frontier 上限 3
- [ ] 11.7 4-bot benchmark baseline/candidate 交错至少 3×200 局；elapsed/game 中位退化 <=10%
- [ ] 11.8 若性能门失败，优先优化 shape helper/cache/FFI，不允许通过加预算过门

## 12. 积分 Stage 1：4096 paired smoke

- [ ] 12.1 使用冻结 seed 与 seat rotation，baseline=shape off，candidate=shape on
- [ ] 12.2 至少 4096 paired games，主指标为 settlement score delta / game
- [ ] 12.3 报告 mean、median、95% CI、win rate、胡率、平均番，仅 settlement score 用作主门
- [ ] 12.4 若 mean < -0.10/局，停止并保持默认关闭
- [ ] 12.5 若 95% CI upper <0，停止并保持默认关闭
- [ ] 12.6 必须 shape_changed_winner >0，并输出 changed / unchanged 两桶收益
- [ ] 12.7 分桶 decision_scope、shanten、open meld count、wall-left、baotou-shape/root-only/future-shape divergence

## 13. 积分 Stage 2：至少 30720 paired confirm

- [ ] 13.1 使用独立 seeds，不复用 Stage 1 统计量
- [ ] 13.2 至少 30720 paired games，保持同牌墙/换座公平配对
- [ ] 13.3 默认启用前要求 overall mean score delta >=0
- [ ] 13.4 默认启用前要求 95% CI lower >= -0.10/局；95% CI lower >0 记为强成功
- [ ] 13.5 targeted divergence bucket 不得出现明确稳定负收益
- [ ] 13.6 常见 shanten/wall-left/open-meld bucket 不得出现显著负桶
- [ ] 13.7 报告 shape 改变动作率、每局改动次数、decision_scope 分布、具体 taatsu upgrade 分布
- [ ] 13.8 Stage 2 不过则保留实验 evaluator，不提升 legacyV2 默认

## 14. 发布与回滚

- [ ] 14.1 全量 pytest / 项目测试通过
- [ ] 14.2 openspec validate --strict 通过
- [ ] 14.3 git diff --check 干净
- [ ] 14.4 保存专项、parity、A/B、性能报告到 change artifacts
- [ ] 14.5 只有 correctness + score + performance 三门均通过才修改 weighted_online 默认
- [ ] 14.6 发布后 replay 抽样至少 100 个 shape-changed decisions 做人工复核
- [ ] 14.7 若线上 fallback/latency/异常结构逆序回归，关闭 shape_quality_enabled 即恢复 baseline
- [ ] 14.8 更新 PROGRESS.md，记录 evaluator/profile/kernel version 与 A/B 结论
