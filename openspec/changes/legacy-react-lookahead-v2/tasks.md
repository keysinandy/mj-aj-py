## 1. 冻结 v1 与模块拆分

- [x] 1.1 从 `5e0a405` 提取 reaction/KONG 固定牌例作为 `legacy-shape-progress-v1` parity suite；覆盖 CHOW、PONG、PONG+KONG_OPEN、closed/add KONG、爆头/财飘 reason
- [x] 1.2 新增 `LegacyReactionProfile.v1/v2_online/v2_offline`，profile 字段和 fingerprint 覆盖 future budget、coverage、tempo guard、版本号
- [x] 1.3 把 `LegacyShapeProgress`、significant-progress、reaction evaluator 从 `mj/bot.py` 迁到 `mj/legacy_react.py`，通过显式输入/callback 避免反向 import bot
- [x] 1.4 把 KONG structure/shape/kong-kai/evaluation helper 迁到 `mj/legacy_kong.py`（或等价独立模块）
- [x] 1.5 `mj/bot.py` 只保留 routing/兼容入口；完成纯重构后 v1 parity suite 必须零漂移再开始 v2 行为修改

## 2. reusable standing U2 API

- [x] 2.1 在 `mj/legacy_eval.py` 新增 `StandingRoot`（或等价结构）与 `evaluate_standing_frontier()`
- [x] 2.2 直接复用 `weighted_two_ply_frontier`、FutureEvaluation、预算/coverage/partial commit 语义；不得复制 Python draw→discard DFS
- [x] 2.3 支持一批 standing roots 共用 visible/locked/profile，返回 stable-id → FutureEvaluation 映射
- [x] 2.4 为 Stage-A-only、Stage-B、safe partial、coverage 不足、Rust 不可用写契约测试
- [x] 2.5 验证 public-only：构造完整本地 Game 与镜像/public context，相同 hero/visible 时 U2 结果一致，不读取隐藏墙/对手暗手

## 3. post-claim frontier 与 U2 veto

- [x] 3.1 v2 对 CHOW/PONG 继续先跑冻结 v1 Gate：shanten drop 直接过、worse 直接拒绝、same-shanten 必须先有 v1 significant reason
- [x] 3.2 对一个 claim 的所有最低 shanten立即弃牌候选保留完整 frontier，不再先用当前层 progress 只选一张
- [x] 3.3 对 `wait_expansion/ukeire_expansion` 实现 U2 veto：future_improve 不得低于 PASS；双方同 Stage-B 时 future_ukeire_mean 不得低于 PASS
- [x] 3.4 Stage-A/Stage-B 层级不一致、必要候选缺结果或 coverage 不足时 online 整层回 v1；offline fail-loud
- [x] 3.5 将 future types/mean 作为候选 tie-break，并保持 shape cost / stable id 最后排序
- [x] 3.6 细化 reason detail：`baotou_ready_upgrade/baotou_ukeire_gain/piao_ready_upgrade/piao_mass_gain/wait_expansion/ukeire_expansion`
- [x] 3.7 直接 baotou/piao ready upgrade 不被 generic U2 veto；缺 special-U2 时保留 v1 并记录 `u2_special_metric_missing`

## 4. tempo guard

- [x] 4.1 实现 `pass_draw_index=(seat-owner)%4`、`claim_draw_index=4`、`tempo_cost`
- [x] 4.2 ordinary same-shanten claim：tempo_cost=1 只要求 U2 不劣；tempo_cost>=2 要求 future_improve 或同 Stage-B future_ukeire_mean 至少一项严格优于 PASS
- [x] 4.3 shanten drop 与直接 baotou/piao ready upgrade 不被 tempo guard 否决
- [x] 4.4 固定座位牌例：同一 PONG hand 分别由上家/对家/下家弃牌，锁 `tempo_cost=3/2/1` 与 guard 差异
- [x] 4.5 diagnostics 输出 pass_draw_index、claim_draw_index、tempo_cost、strict-future-gain 是否满足

## 5. KONG v2 完整 shape preservation

- [x] 5.1 保留现有 structure guard / post-tenpai / live winning-mass hard gate，不改变 `123333m` 等现有 contract
- [x] 5.2 shape gate 增加 ukeire_types、已知 baotou_ukeire_live、piao_draw_live 保护
- [x] 5.3 修正 unknown 语义：`None` 不再通过 `or 0` 进入比较
- [x] 5.4 固定回归：普通 waits 不变但 piao progression 丢失 → reject；special metric unknown → 不假拒绝
- [x] 5.5 KONG hard gate 失败时断言 continuation/search 节点为 0

## 6. bounded score continuation

- [x] 6.1 抽取 public score continuation helper：本家 draw → immediate HU，否则最佳合法弃牌 → 下一次本家 draw expected HU reward
- [x] 6.2 ordinary discard baseline 升级到相同的两个本家 draw-opportunity horizon
- [x] 6.3 closed/add/open KONG：replacement immediate reward + non-HU best-discard continuation，全部用 settlement score units
- [x] 6.4 continuation 使用 public remaining、现有 chain/chain_piao/YCBK/kong_draw 语义，不读取墙序
- [x] 6.5 online continuation 独立 hard budget；不完整回 v1 KONG 结论并记录 fallback，offline fail-loud
- [x] 6.6 固定牌例：replacement 非胡但后续宽听时 continuation_reward_ev>0；baseline/KONG horizon 对称

## 7. PONG vs KONG_OPEN same-unit slow-path

- [x] 7.1 仅双方 Gate 都通过且同 shanten 时进入 slow-path；其余窗口不增加 score search
- [x] 7.2 KONG progress 必须不劣于 PONG，且 unknown 不按 0 伪比较
- [x] 7.3 计算 `Q_pong` 与 `Q_kong`；仅 `Q_kong > Q_pong + 1e-9` 选 KONG_OPEN
- [x] 7.4 exact tie / 任一 Q incomplete → PONG；写固定测试锁稳定次序
- [x] 7.5 diagnostics 输出 Q_pong/Q_kong/delta、slow-path nodes/elapsed/fallback

## 8. evaluator routing 与 offline 语义

- [x] 8.1 默认、`evaluator="legacy"`、`legacyV2` 与 `legacy-v2` 路由到 enabled v2_online；显式 `legacy-v1` 保持 v1 rollback
- [x] 8.2 任何单次 U2/KONG continuation incomplete 均事务性回退 v1；不完整不再关闭全局 v2 默认路由
- [x] 8.3 `legacyV2-offline` reaction 走 v2_offline，任何必要 U2/continuation incomplete 都 fail-loud
- [x] 8.4 shape-v1/shape-v2/policy-v3 既有路由保持不变；不让本 change 偷改高级 evaluator

## 9. shadow teacher 审计

- [x] 9.1 新增或扩展本地 replay audit，逐 reaction 记录 v1/v2/shape-v2 all-root teacher 动作
- [x] 9.2 记录 PASS/claim 的 shanten、U1、future_improve、future_ukeire_mean、special progress、tempo、Q、reason/fallback
- [x] 9.3 至少扫描 1,000 个 reaction decisions；若现有数据不足则扫描全部并在报告中明确样本数
- [x] 9.4 输出按 CHOW/PONG/KONG_OPEN、tempo_cost、v1 reason 分桶的 v1↔v2、v2↔teacher 分歧率；teacher incomplete 单列，不当真值
- [ ] 9.5 从分歧中抽样固定 regression fixtures，至少覆盖“U1 好/U2 差”“高 tempo 无严格未来优势”“PONG/KONG same-unit 翻转”

## 10. 性能与发布门禁

- [x] 10.1 聚焦单测通过后跑 `python3 -m pytest tests/ -q`
- [x] 10.2 以 `5e0a405` 为冻结基线，同机同 Rust 内核交错各至少 3×200 局；4-bots elapsed/games 中位退化 ≤15%
- [x] 10.3 报告 v2 reaction U2 extra latency p50/p95/p99、coverage、partial accepted、fallback reason；U2 extra p95 ≤10ms
- [x] 10.4 报告 KONG continuation extra latency p50/p95/p99；p95 ≤15ms
- [x] 10.5 报告 eligible reaction 的 complete + safe-partial coverage（目标 ≥90%）；未达标的具体窗口仍事务性回退 v1
- [x] 10.6 确认无 Python baotou 全枚举或新增 Python DFS 进入线上热路径
- [x] 10.7 同步 PROGRESS.md：v1 rollback、v2 U2 veto/tempo、KONG continuation、shadow audit 结论和启用状态
- [x] 10.8 `git diff --check` 干净；本 change 不混入 BC/PPO/RL 训练产物
