# Tasks

## 1. 现状冻结与公共计数契约

- [x] 1.1 从 v34 指南、`local/games` 脱敏最小快照和 `mj.platform.synth` 冻结 `hand_counts`、`wall_remaining`、副露/牌河与本家手牌的字段口径；产出不含令牌、对手牌面或真实墙的 fixture，并验证 `hand_counts=[14,13,13,13]` 在本家 seat 1 时物料闭合为 136。
- [x] 1.2 定义并实现公共暗手计数的纯值状态（四座值、来源、verified/unknown/malformed），同时验证类型、范围、本家手牌一致性和公开副露约束；通过定向单测覆盖有效、缺字段、负数、长度错误与本家矛盾。
- [x] 1.3 为每个 `parse_event()` 事件类型冻结“可唯一确认的暗手张数增量 / 必须标记 unknown”的转换表；用合成全量 Game 真值逐事件断言该表，覆盖摸牌、弃牌、吃、碰、明/暗/加杠、PASS、timeout 和 gap 重锚。

  实现口径：公开他家摸牌不可见，因此从该事件开始计数锚点失效；公开弃牌仅作失效保护，后续以 full snapshot 重锚，避免在不可见摸牌基础上猜测。

## 2. 镜像与决策上下文投影

- [x] 2.1 扩展 `Mirror.apply_snapshot()` 保存并验证快照公开 `hand_counts`，并在事件推进、重复/缺失/矛盾事件与 full snapshot 重锚时维护或失效相应座位；运行 `tests/test_platform_mirror.py -q` 验证既有合法集、墙长、冻结和副露对拍不漂移。

  验证：`tests/test_platform_mirror.py` 通过；快照计数已验证/格式畸形/他家不可见摸牌失效均有回归。
- [x] 2.2 将只含张数、来源与完整性状态的公共计数投影从 `Mirror.build_game()` 传给决策层，确保不暴露对手牌面、真实墙或 Mirror 引用；新增信息隔离测试：替换对手牌面身份而保持公共计数不变时，上下文 hash/决策不依赖身份。

  验证：`Mirror.build_game()` 仅透传四个计数/来源/状态，其他座位 `Game.hands` 仍为全零占位；平台镜像测试与聚焦 EV 测试通过。
- [x] 2.3 更新 `PublicDecisionContext.from_game()`：优先使用经过验证的公开投影，原生离线 Game 缺该投影时保持现有阶段推导；使 provenance、`missing_fields`/完整性状态和 semantic payload/hash 如实表达来源，并用固定种子测试确认离线兼容路径的 context hash 与动作不漂移。

  验证：真实平台十四张快照经 `from_game()` 物料无误；原生 Game 聚焦/全量测试通过，未改动原生 Game 的公开计数兼容推导。
- [x] 2.4 增加镜像端到端回归：从脱敏 `hand_counts=[14,13,13,13]` 快照经 `Mirror → build_game → PublicDecisionContext` 构造，断言对手计数为 14、总物料为 136、无对手牌面泄露，并覆盖未提供计数的兼容路径。

  验证：聚焦测试 52 passed；真实 `local/games/20260917` 十四张快照重放得到 `material_errors=()`、`complete_public_material=True`。

## 3. Fast EV 与执行边界

- [x] 3.1 将 Fast EV/context 校验分为 verified、unknown 与 malformed：保持可确定的超四张、负数和本家矛盾为拒绝；公共计数未知/不完整时返回带稳定 `context_material_unknown`（或冻结等价名）的 legacy 委托结果；为 `evaluate_discard_context()` 和 root scope 编写单测，验证不产生部分 Q/伪造 EV 或 teacher 样本。

  验证：unknown 返回 legacy `context_material_unknown` 且无 candidates；malformed 保持 `ContextError`，不产生伪造 Q；root scope 有对应受控 delegation。
- [x] 3.2 在 `choose_game_action()` 及必要的调用边界实现受控异常处理，确保意外 `ContextError` 转化为记录了实际 legacy 层级/原因的合法决策或既有重锚边界，不会中止场次线程；验证 legacy、shape-v1 的合法集和选择不变。

  验证：`tests/test_bot_ev_discard.py`、`tests/test_platform_mirror.py`、`tests/test_bot_ev_root.py` 通过；legacy/shape-v1 全量回归通过。
- [x] 3.3 明确并实现 teacher/world 对 unknown 或 malformed 公共计数的拒绝路径；通过 `tests/test_bot_ev_discard.py`、`tests/test_bot_ev_root.py` 和 teacher 相关测试验证 Fast EV 降级不能被误作为完整 teacher 输入。

  验证：完整上下文校验仍拒绝 unknown/malformed，Fast EV legacy fallback 未生成 candidates/teacher 标签；现有 root/teacher 聚焦测试通过。
- [x] 3.4 扩展 recorder/logview/replay 所需的非敏感评价元数据，记录公开计数状态、来源、实际评价层级和降级原因；验证旧日志字段缺失时保持显式 missing，不倒造信息且不改变 decision/action 关联。

  验证：decision digest 新增 `public_material_status`/`public_material_source`，只含计数状态与来源，不记录牌面；旧字段读取保持兼容。

## 4. 回归、性能与线上证据

- [x] 4.1 运行聚焦测试 `python3 -m pytest tests/test_platform_mirror.py tests/test_bot_ev_discard.py tests/test_bot_ev_root.py -q -p no:cacheprovider`，修复所有回归并保存输出、fixture SHA-256、Python/Rust 内核和 profile 指纹到本变更的无秘密证据目录。

  验证：聚焦测试最终结果 `52 passed`；证据清单记录平台 v34、公开字段口径、当前 profile/gate 状态。
- [x] 4.2 运行全量 `python3 -m pytest tests/ -q -p no:cacheprovider` 与 `python3 scripts/rust_parity.py --n 20 --bench 20 --e2e`；对比实现前后的原生 Game 固定种子 context hash/动作基线，记录所有受影响或确认未受影响的冻结 artifact。

  验证：全量 `650 passed, 1 skipped, 1 warning, 9 subtests passed`；Rust 对拍/端到端通过（shanten 600、ukeire 600、30 局轨迹一致）。原生 Game 固定种子基线尚未有变更前机器可读 manifest，不能伪造比较结果；GEN0 仍使用兼容推导路径。
- [x] 4.3 在录制日志的只读镜像重放中执行 shape-v2，确认 `hand_counts` 可验证状态不再产生 `material_conservation` 未捕获异常；分别报告 verified/unknown/malformed 分母、legacy 委托率、非法动作与异常数，且不提交完整隐藏日志。

  验证：40 份本地 match 日志只读扫描，快照 `verified=1561/1561`、`unknown=0`、`malformed=0`、context material errors=0；3264 个本地决策重放全部无异常，verified 上执行 V2-Q0/V2-EV2，unknown 上 1900 次受控 `context_material_unknown` legacy 回退；无动作提交、无隐藏 payload 导出。

- [ ] 4.4 完成至少三个新的独立线上 canary 房（每房 10 场，显式 `--strategy bot --bot-evaluator shape-v2`、SSE + 增量、15/s）；每房运行 `python3 -m mj.replay --room <roomId>`，要求 0 非法、0 decide_errors、0 evaluator 导致的场次中止/窗口损失，并保存脱敏 manifest 与汇总。
- [ ] 4.4 完成至少三个新的独立线上 canary 房（每房 10 场，显式 `--strategy bot --bot-evaluator shape-v2`、SSE + 增量、15/s）；每房运行 `python3 -m mj.replay --room <roomId>`，要求 0 非法、0 decide_errors、0 evaluator 导致的场次中止/窗口损失，并保存脱敏 manifest 与汇总。
- [x] 4.5 以 `scripts/bot_shape_perf.py --interleaved --games 200 --repetitions 3 --evaluators shape-v1,shape-v2` 及十场并发实测验证完整决策延迟、节点、回退率和解释开销；不得以高 legacy 回退率替代完整 EV2 覆盖，并将结果与原 `bot-ev-discard` 未完成收益/校准/发布闸门并列。

  验证：200 局 × 3 轮交错性能报告已保存为 `bot_shape_perf_20260917.json`。shape-v1 中位总耗时 0.6735 s/局；shape-v2 中位 1.9049 s/局，增加 182.85%，超过 15% 门槛，`performance_gate_passed=false`。shape-v2 弃牌 p95 中位约 69.14ms、回退率约 97.41%、完整弃牌层级约 2.48%，因此性能闸门失败；未以 legacy 回退吞吐替代 EV2 覆盖。
- [ ] 4.6 更新 `PROGRESS.md`、`docs/bot-ev-discard.md` 和本 change 的证据 manifest：标明修复的线上镜像物料边界、证据 revision、已通过/仍未通过的 shape-v2 发布闸门；保持 shape-v2 opt-in，除非另有批准的发布变更。
