## 1. Baseline / reproduction

- [ ] 1.1 冻结 main commit/profile/kernel/budget/frontier cap
- [ ] 1.2 固化 899s fixture
- [ ] 1.3 固化 7899s + 5w 反例
- [ ] 1.4 固化 2w vs 4s：57/17 vs 48/15、shape_loss 5/2、feed 0/0、role 差异
- [ ] 1.5 冻结 2w vs 4s 完整 two-ply future metrics

## 2. Speed band

- [ ] 2.1 新增 speed_band_enabled/version/profile fingerprint
- [ ] 2.2 ratio thresholds=1.00/0.90/0.82/0.78
- [ ] 2.3 shanten2 57 vs 48 ratio≈0.842，同 competitive band
- [ ] 2.4 shanten2 57 vs 35 ratio≈0.614，被 speed dominance 排除
- [ ] 2.5 band 内 raw current ukeire 不得单独形成 winner certificate
- [ ] 2.6 feature off 恢复当前 main ordering

## 3. Pareto frontier

- [ ] 3.1 维度：ukeire/types/marginal loss/standing shape
- [ ] 3.2 strict Pareto dominance
- [ ] 3.3 速度快但结构差不得错误支配 challenger
- [ ] 3.4 baseline best-current root 永远保留
- [ ] 3.5 deterministic cap <=3
- [ ] 3.6 diagnostics 输出 dominated_by / before-after cap

## 4. Weighted root comparator

- [ ] 4.1 band 内 key：future improve -> future ukeire -> future types -> marginal loss -> future shape -> standing shape -> raw current ukeire -> risk/tile
- [ ] 4.2 current ukeire 仅作 band gate + late tie-break
- [ ] 4.3 synthetic equal-future fixture：更低 marginal loss 可反超较高 current ukeire
- [ ] 4.4 明显 future-speed winner 仍优先于结构

## 5. Stage A / partial

- [ ] 5.1 Stage A strict proof 使用新 comparator
- [ ] 5.2 band 内不得因 raw current ukeire 更高提前接受 partial
- [ ] 5.3 bounds overlap -> Stage B 或事务 fallback
- [ ] 5.4 Python/native certificate parity

## 6. User 899s golden

- [ ] 6.1 77/82 进入 competitive band
- [ ] 6.2 weighted_two_ply_entered=true / future_nodes>0
- [ ] 6.3 complete search selected !=9s

## 7. User 2w vs 4s golden

- [ ] 7.1 固定 57/17 vs 48/15
- [ ] 7.2 4s ratio≈0.842，speed_dominated=false
- [ ] 7.3 2w 不得 Pareto-dominate 4s
- [ ] 7.4 4s 进入 bounded frontier
- [ ] 7.5 root comparator 不得以 57>48 在 future 前结束
- [ ] 7.6 冻结 future 后，若 2w 无 earlier future-speed dominance 且 4s marginal loss 更低，则 selected=4s
- [ ] 7.7 replay 可复算完整 band/Pareto/future/role 链

## 8. Anti-overfit / diagnostics

- [ ] 8.1 7899s lost pair + completed-meld redundancy
- [ ] 8.2 pair 不形成 hard protection
- [ ] 8.3 5w connectivity 可见
- [ ] 8.4 输出 speed/Pareto/marginal diagnostics
- [ ] 8.5 旧日志兼容

## 9. Fallback / parity

- [ ] 9.1 kernel unavailable/version mismatch/deadline/work budget 事务 fallback
- [ ] 9.2 unsafe partial 不使用 missing future
- [ ] 9.3 native certificate 同步 speed-band semantics
- [ ] 9.4 targeted + random Python/native parity

## 10. Performance / score / release

- [ ] 10.1 helper microbench
- [ ] 10.2 ordinary discard >=3x1000 decisions
- [ ] 10.3 p95<=+10%，p99<=+15%，fallback<=+1pp
- [ ] 10.4 frontier<=3，hard budget=50ms
- [ ] 10.5 4-bot >=3x200，median<=+10%
- [ ] 10.6 Stage1 >=4096 paired
- [ ] 10.7 Stage2 independent >=30720 paired
- [ ] 10.8 openspec validate --strict / git diff --check
- [ ] 10.9 三门通过才默认开启
- [ ] 10.10 更新 PROGRESS.md
