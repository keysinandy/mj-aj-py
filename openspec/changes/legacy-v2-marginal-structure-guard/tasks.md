## 1. Baseline / reproduction

- [ ] 1.1 冻结 main commit、profile fingerprint、kernel version、40/50ms budget、frontier cap
- [ ] 1.2 固化用户 899s fixture 与各候选 shanten/current ukeire
- [ ] 1.3 复现 frontier_singleton、search_used=false、future_nodes=0
- [ ] 1.4 固化 7899s + 5w 反例

## 2. MarginalStructureRole

- [ ] 2.1 新增 versioned helper
- [ ] 2.2 输出 lost_pair_option / same_tile_unseen
- [ ] 2.3 输出 lost_taatsu_option / lost_completed_meld
- [ ] 2.4 输出 completed_meld_redundancy
- [ ] 2.5 输出 alternative route counts
- [ ] 2.6 输出 singleton connectivity / live connectivity
- [ ] 2.7 导出 critical_compound_break / loss_tier
- [ ] 2.8 hidden-state independence tests

## 3. Pattern semantics

- [ ] 3.1 899s 弃 9s：lost pair + route drop + non-redundant
- [ ] 3.2 7899s 弃 9s：completed_meld_redundancy=true
- [ ] 3.3 7899s 不得仅因 lost pair 标 critical
- [ ] 3.4 5w live connectivity 高于 1w/9w
- [ ] 3.5 公开耗尽邻张时 live connectivity 正确归零

## 4. Role-aware singleton guard

- [ ] 4.1 新增 marginal_structure_guard_enabled
- [ ] 4.2 实现 shanten slack 0/2/4/6
- [ ] 4.3 无 challenger 时保留原 singleton short-circuit
- [ ] 4.4 有 slack 内 role challenger 时阻断 singleton
- [ ] 4.5 baseline speed winner 永远保留
- [ ] 4.6 frontier 稳定截断且 <=3
- [ ] 4.7 feature off action parity=100%

## 5. User 899s golden

- [ ] 5.1 真实 choose_discard 路径运行 fixture
- [ ] 5.2 至少一个 ukeire>=76 的低损失 challenger 被准入
- [ ] 5.3 frontier_singleton_blocked=true
- [ ] 5.4 weighted_two_ply_entered=true / search_used=true / future_nodes>0
- [ ] 5.5 complete search 下 selected != 9s
- [ ] 5.6 fallback 时恢复 baseline 并保留明确 reason

## 6. 7899s + 5w anti-overfit

- [ ] 6.1 9s 同时记录 lost pair 与 completed-meld redundancy
- [ ] 6.2 pair 标签不得形成 hard protection
- [ ] 6.3 5w connectivity 进入 diagnostics
- [ ] 6.4 构造合理弃 9s 与合理保 99 的两个完整牌面，防 pattern hard-code

## 7. Diagnostics / replay

- [ ] 7.1 root 输出 marginal-role 字段
- [ ] 7.2 decision 输出 singleton proven/blocked、slack、challengers、admitted_by
- [ ] 7.3 replay 可解释 82 vs 77 为什么仍进入 two-ply
- [ ] 7.4 replay 可解释 7899 为什么不等于 899

## 8. Fallback / parity

- [ ] 8.1 kernel unavailable/version mismatch/deadline/work budget 事务 fallback
- [ ] 8.2 unsafe partial 不得用 missing future 值参与 winner
- [ ] 8.3 若 helper 下沉 Rust，Python/Rust 随机 parity >=10000 hands

## 9. Performance

- [ ] 9.1 role helper microbench p50/p95/p99
- [ ] 9.2 ordinary discard baseline/candidate >=3x1000 decisions
- [ ] 9.3 p95 增幅 <=10%，p99 <=15%
- [ ] 9.4 fallback 增加 <=1 percentage point
- [ ] 9.5 frontier <=3，hard budget 仍 50ms
- [ ] 9.6 4-bot baseline/candidate >=3x200 局，median 退化 <=10%

## 10. Score Stage 1

- [ ] 10.1 >=4096 paired games
- [ ] 10.2 settlement score delta/game 为主指标
- [ ] 10.3 mean < -0.10/局或 95% CI upper <0 则停止
- [ ] 10.4 输出 marginal-guard divergence buckets

## 11. Score Stage 2

- [ ] 11.1 独立 seeds >=30720 paired games
- [ ] 11.2 overall mean >=0
- [ ] 11.3 95% CI lower >= -0.10/局
- [ ] 11.4 divergence bucket 不得稳定显著负收益

## 12. Release

- [ ] 12.1 targeted tests + 可运行全量测试
- [ ] 12.2 openspec validate --strict
- [ ] 12.3 git diff --check
- [ ] 12.4 保存 correctness/perf/A-B artifacts
- [ ] 12.5 三门通过才允许默认开启
- [ ] 12.6 更新 PROGRESS.md
