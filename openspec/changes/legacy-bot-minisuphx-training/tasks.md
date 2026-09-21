## 1. P0 冻结训练契约

- [x] 1.1 冻结 `main@08d33cff`、规则/profile/feature/action encoding 作为本 change 基线。
- [x] 1.2 定义 `MiniSuphxRunManifest`、`PolicyManifest`、`RolloutManifest`、`OpponentPoolManifest` 与 fingerprint。
- [x] 1.3 定义并实现 `round-score-v2-normalized` value contract，BC target 与 RL terminal reward 均为 `clip(score/24,-4,4)/4`。
- [x] 1.4 冻结 train/validation/final-test seed domains，验证所有训练/DAgger/RL job 不得使用 final-test。
- [x] 1.5 定义 action scope：`discard-only-v1`、未来 reaction scope 与 legacy safeguard 清单。

## 2. P1 BC 数据与训练底座

- [ ] 2.1 重构 `bc_data.py` 支持 deterministic distributed jobs、独立 shard + manifest、无文件名冲突。
- [ ] 2.2 将 `bc_train.py` 改为 shard-streaming Dataset/DataLoader；训练前不得全量 concat。
- [ ] 2.3 增加 AMP、pin_memory、num_workers、gradient accumulation（可选）与吞吐日志。
- [ ] 2.4 checkpoint 保存 model/optimizer/scheduler/epoch/global_step/RNG/manifest；支持严格 resume。
- [ ] 2.5 训练/验证报告增加 action-scope 分项准确率、illegal rate、value MAE/MSE。
- [ ] 2.6 benchmark 4x128/6x128；若无明确收益证据，正式 v1 使用 6x128。

## 3. P2 HybridPolicy 与 discard env

- [x] 3.1 新增 `HybridPolicy`，明确 legacy gate 与 learned discard 的动作路由。
- [x] 3.2 新增 `MahjongDiscardEnv`，只有普通弃牌暴露为 RL step，其余动作自动推进。
- [x] 3.3 加入审计日志：每个自动 legacy action 的原因、phase、legal set、selected action。
- [x] 3.4 单测证明 learned policy 永远不会覆盖 HU/KONG/CHOW/PONG/PASS/safeguard。
- [x] 3.5 同 seed 下 HybridPolicy(learned=legacy) 与纯 legacy trajectory 完全一致。

## 4. P3 Legacy BC + DAgger

- [ ] 4.1 两机生成首批 30k legacy self-play games，冻结 campaign fingerprint。
- [ ] 4.2 训练 BC0，并运行 validation/final-test（final 只评估不调参）。
- [ ] 4.3 实现 DAgger generator，保存 executor/teacher/disagreement provenance。
- [ ] 4.4 完成 D1 3k（70/30）、D2 3k（40/60）、D3 4k（10/90）。
- [ ] 4.5 训练并冻结 `BC-v1`；记录 checkpoint hash、hard-set baseline、paired baseline。
- [ ] 4.6 建立永久 BC anchor，后续 run 不得覆盖该 artifact identity。

## 5. P4 Custom PPO Learner

- [x] 5.1 新增 PPO rollout schema 与 GAE 计算，覆盖 action mask。
- [x] 5.2 新增 learner，完整加载 BC backbone/policy/value head；禁止随机替换 critic。
- [x] 5.3 实现 actor/value 独立 LR 参数组。
- [x] 5.4 实现 BC prior KL schedule，并记录 live-vs-anchor KL。
- [x] 5.5 实现 normalized target entropy controller、coefficient clamp 与 resume state。
- [x] 5.6 实现 shaping schedule，并确保 promotion eval 使用 unshaped terminal score。
- [ ] 5.7 在 PC-A 单机完成 50k~100k discard decisions 冒烟，确认无 NaN/非法动作/policy collapse。

## 6. P5 双机 synchronous Actor/Learner

> 本会话已落地通用分布式运行时基底(P0 + 最小闭环):`mj/training/`
> `distributed_jobs.py` / `job_store.py` / `coordinator.py` / `artifact_store.py`
> / `worker_runtime.py` / `distributed_bc.py` + `scripts/minisuphx_cluster.py`,
> 以及 A-only == A+B 语义等价测试(`tests/test_minisuphx_cluster.py`)。
> rl_rollout/policy-version merge 守卫/基准 benchmark 属后续子任务。

- [ ] 6.1 扩展 distributed job kinds：`legacy_bc_games`、`dagger_games`、`rl_rollout`。
- [ ] 6.2 worker 注册 rollout capability；PC-B 默认 benchmark 6/8/10 actors。
- [x] 6.3 每轮发布 immutable `policy_N` 与 manifest，actor lease 前验证 git/model/value/action scope。
- [x] 6.4 rollout shard 保存 obs/mask/action/logprob/value/reward/done/episode/opponent provenance。
- [x] 6.5 merge gate 强制同一 policy_version；stale/mixed shard 单测必须失败。
- [ ] 6.6 worker crash/lease expiry/SMB failure/retry 不产生重复 semantic rollout。
- [ ] 6.7 benchmark A-only、B-only、A+B 的 discard decisions/min 与 learner idle fraction。

## 7. P6 RL Gen0 / Gen1 / Gen2

- [ ] 7.1 Gen0：100% legacy opponents，训练到 200k discard decisions，至少每 100k checkpoint/gate。
- [ ] 7.2 Gen1：legacy 60% / BC 20% / historical RL 20%，训练到累计 600k。
- [ ] 7.3 Gen2：legacy 40% / BC 20% / RL league 40%，训练到累计约 1.2M。
- [ ] 7.4 所有 generation 记录 opponent pool fingerprint；pool 变化必须创建新 generation identity。
- [ ] 7.5 永久保持 legacy>=20%、BC>=10%，除非新 OpenSpec 修改该约束。

## 8. P7 Hard Set 与 Promotion

- [ ] 8.1 建立冻结 discard hard-set，覆盖 disagreement/high-entropy/财神/杠后/墙尾/高倍风险等。
- [ ] 8.2 实现 512 smoke / 1024 fast / 4096 full paired gates。
- [ ] 8.3 paired rows 分布式执行、中央 merge/bootstrap；同 schedule 必须与单机报告一致。
- [ ] 8.4 promotion 主指标为 hero round score delta + bootstrap CI；训练 reward/loss 不可替代。
- [ ] 8.5 candidate 必须同时通过 vs legacy 与 vs current champion robustness。
- [ ] 8.6 冻结首个通过 full gate 的 `Champion-v1` 及 rollback manifest。

## 9. P8 Oracle Guiding v2

- [ ] 9.1 从 Champion-v1 初始化 Oracle Gen；不得从随机模型重新训练。
- [ ] 9.2 支持 group-wise hidden feature dropout，而非只有整局 oracle on/off。
- [ ] 9.3 按 profile 完成 oracle 1.0 -> 0 退火；oracle=0 后 LR 降低继续训练。
- [ ] 9.4 最终候选仅在 public-only 环境中进行 full paired gate。
- [ ] 9.5 public-only gate 失败则回滚 Champion-v1，Oracle artifact 不上线。

## 10. P9 Belief / Value / Search 集成

- [ ] 10.1 依赖 `belief-search-policy-iteration` 已通过的 belief/search/value gate，不重复实现算法。
- [ ] 10.2 用 Champion-v1/v2 policy 作为 search continuation/opponent profile，生成新的 teacher fingerprints。
- [ ] 10.3 hard-state/active-sampling 选择 search contexts，不要求全状态搜索。
- [ ] 10.4 search -> distill -> RL -> search 至少完成两轮，记录每轮 paired score/root regret/latency。
- [ ] 10.5 可选增加 win/deal-in/draw/expected-score auxiliary heads，并做消融。
- [ ] 10.6 冻结通过完整 gate 的 `Champion-v2`。

## 11. P10 Reaction RL 能力阶梯

- [ ] 11.1 定义并实现 `pong-pass-v1` scope；单独 legacy anchor/hard-set/gate。
- [ ] 11.2 通过后才允许 `chow-pass-v1`。
- [ ] 11.3 其余 reaction 单独建 scope/gate，不允许一次性解锁全部 109 action。
- [ ] 11.4 HU 默认维持规则/legacy safeguard；任何学习接管需要独立 change。

## 12. P11 发布与运维

- [ ] 12.1 `scripts/train_minisuphx.py` 支持 campaign create/status/resume/gate/promote。
- [ ] 12.2 run summary 汇总 BC/DAgger/RL/cluster/paired/hard-set 证据。
- [ ] 12.3 replay debugger 展示 policy version、legacy gate、RL suggestion、value、entropy、fallback reason。
- [ ] 12.4 Windows 两机重启恢复测试：coordinator/worker/learner 任一中断均可恢复且不污染 generation。
- [ ] 12.5 README/训练手册记录 PC-A/PC-B 启动方式与 rollback 流程。
