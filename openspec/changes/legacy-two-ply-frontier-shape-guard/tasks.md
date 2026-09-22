# Tasks

## 1. Profile 旋钮与默认行为

- [x] 1.1 在 `LegacyTwoPlyProfile` 增加 `shape_guard_enabled`（默认 false）、`shape_guard_ukeire_slack`（默认 1）、`shape_guard_shape_delta`（默认 8），并按"非默认才进 payload"的方式纳入指纹与 `as_json()`；默认 profile 的指纹保持不变。
- [x] 1.2 护栏关闭时候选集/选择零漂移：既有套件（`test_legacy_weighted_frontier`/`test_legacy_eval`/`test_legacy_rust_kernel`/`test_bot`）未改断言全绿，`tests/test_shape_guard.py` 另断言关闭档与在线默认档选择/level 一致。

## 2. 形状护栏前沿

- [x] 2.1 在 `_weighted_evaluation` 里于前沿上限之后增加 `_apply_shape_guard`（D1 准入条件），并返回候选级 `admitted_by`。单测覆盖准入、`slack=0` 与 `delta` 不足三种情形。
- [x] 2.2 护栏合并后按声明顺序 `(-进张, 结构损失, 喂牌风险, 牌编号)` 应用 `max_frontier_candidates` 截断，主候选不会被截断；`admitted_tiles`/`dropped_tiles` 记录明细。
- [x] 2.3 短路判据改为"护栏后唯一"（D3）：样例 seed=290 关闭时为 `legacy-one-ply`，开启后进入加权比较（`level != legacy-one-ply`，选中项由比较结果给出）。
- [x] 2.4 扩围后预算不足仍走既有语义：`soft/hard=0` 时 `level=legacy`、`fallback_reason=partial_not_acceptable`、`search_used=false`，未把缺失值当零。

## 3. 审计字段

- [x] 3.1 评价 JSON 写入 `frontier_guard`（enabled/policy/slack/delta/primary/admitted/dropped/skipped_reason）与候选级 `admitted_by`（D5）；未启用时 `enabled=false` 且既有字段不变。
- [~] 3.2 当日线上记录（`a_a3de8f7235a2_r1_b9_t0`）本地不可得，改用确定性局面 `seed=290`（primary 打 3：进张 32/结构损失 12，护栏候选结构损失 2）作回归样例；真实记录复算待补齐。

## 4. 内核降级解耦

- [x] 4.1 内核不可用（`weighted_two_ply_frontier is None` / `MJ_KERNELS=python` / 版本不匹配）时跳过护栏并记录 `kernel_unavailable`，选择与护栏关闭完全一致（单测用 patch 覆盖）。
- [x] 4.2 运行侧显式报告降级：`mj.shanten.kernel_runtime_diagnostic()`/`format_kernel_diagnostic()`，并在 `clientd.Service.start` 与 `mj/platform/runner.main` 启动时打印一次单行诊断（实际内核、版本、降级原因、影响面）；决策记录沿用 `actual_kernel`/`kernel_fallback_reason`。

## 5. 证据与发布闸门

- [x] 5.1 离线成对 A/B 脚本 `scripts/shape_guard_ab.py`：同一预算下逐状态比较开关两侧，输出触发率、决策变化数（区分"护栏导致"与"预算边界噪声"）、逐条差异与延迟分位，JSON 写入 `evidence/shape-guard-ab.json`。
- [x] 5.2 按事前声明网格扫描 `slack ∈ {0,1,2} × delta ∈ {6,8,10}`（800 状态）：触发率 0–1.4%，基线 `slack=1/delta=8` 触发 5/800（0.63%）、护栏导致的变化 5 条、预算噪声 32 条。
- [x] 5.2b delta 放宽复核（2026-09-22，800 状态）：`delta=4/3/2` 触发 6/800（0.75%）、护栏导致的变化 6 条，对比 `delta=8` 的 5/5；即放宽只多覆盖 1 个状态。
- [~] 5.3 延迟验收：本机（load 4–6）两侧 p95 都超 50ms（on 57.0 / off 58.3），护栏边际影响 ≈ 0，属"基线已超预算、无法判定"；需在安静机器复跑 `scripts/shape_guard_ab.py`。
- [x] 5.4 默认切换评审：离线决策级 A/B 无收益指标；**2026-09-21 按决定默认开启**
  （`weighted_online()` / `weighted_offline()` 置 true，精确/legacy V1 保持关闭），
  触发率 0.63%、护栏边际延迟 ≈ 0（本机基线已超预算、绝对验收待安静机器复跑），
  并保留开关可回滚。

## 6. 文档与校验

- [ ] 6.1 更新 `bot-decision-explanations` 相关文档/前端提示（降级与护栏字段的展示）。验证：前端 typecheck/测试通过。← 未做（本轮只落 Python/记录侧）。
- [x] 6.2 运行 `OPENSPEC_TELEMETRY=0 openspec validate legacy-two-ply-frontier-shape-guard --strict` 与 focused 测试（`test_shape_guard`/`test_legacy_*`/`test_bot`/`test_bc_pipeline`）。
- [ ] 6.3 归档前同步主 specs（`openspec archive`）并复核 `actual_kernel`/护栏字段在真实记录中的落地情况。验证：归档命令 + 记录抽样。
