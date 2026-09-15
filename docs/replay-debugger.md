# Mahjong Replay Debugger

复盘器把本地 JSONL、独立服务端牌局时间线、DumpingApi HTTP dump 和可选执行 trace 编译成 `replay.json` 与单文件 `index.html`。编译过程只读输入，不访问生产接口、不提交动作，也不启动 Web 服务。导出文件内嵌 React + shadcn/ui 风格组件和样式；Vite 产物必须先构建，HTML 打开时不依赖外部资产。

## 使用

```bash
# 首次或前端源码更新后构建离线 React viewer
cd web/replay_debugger
npm install
npm run build
cd ../..

# target 可以是本地 JSONL 路径，也可以是 local/games 下的 gid
python3 -m mj.replay_debugger <path-or-gid> \
  --root local/games \
  --server /path/to/server-timeline.json \
  --trace /path/to/game.trace.jsonl \
  --http-dump /path/to/dumps \
  --round 1 \
  --out replay-output/<gid>
```

已有输出需要显式加 `--overwrite`。输入和输出解析为绝对路径后仍相同、gid 对应多个本地日志、或各来源的 game identity 冲突时，命令会报错或把冲突保留为数据质量诊断；不会静默拼接不同对局。`--print-json` 可在导出摘要后输出完整机器结果。

页面由一个共享 cursor 驱动服务端 seq 帧、本地 local step、Before/After 边界、请求/响应/merge、业务 diff 和诊断。`PLAYER_VIEW`、`LOCAL_KNOWLEDGE`、`OMNISCIENT` 只改变信息投影，保留的 `rawRecords` 仍是完整证据，因此这些模式不是访问控制。unknown、hidden、not-applicable 和 known-empty 会分别显示。

服务端状态只由独立服务端证据和可用起手牌重放；只有本地快照时，页面会显示本地锚点而不会把它升级成服务端真相。Expected 只使用截至该 local step 已到达的完整输入，SSE 水位只唤醒拉取，不制造牌局 transition。Observed 在有 trace 时只使用记录的 checkpoint/transition/merge；旧日志提供的兼容重建明确标为 `DERIVED`。因此缺少执行边界时不会报告确定的吃、碰、杠遗漏。

请求查看器按 logical request、transport request 和物理 attempt 分层，分别显示 `requestToResponseDiff`、`effectiveMergeDiff` 和 `expectedObservedDiff`。分类会保留 `VALIDATION_ONLY`、`PROGRESS_UPDATE`、恢复原因、`REDUNDANT`、`SUSPICIOUS` 和 `UNCLASSIFIED`，并带 `NECESSARY`、`AVOIDABLE` 或 `UNKNOWN`。跨 epoch/monotonic 时钟的时间差会标记为非精确。

## Trace 采集

trace 默认关闭。测试房和自由对战 runner 可以显式开启：

```bash
python3 -m mj.platform.runner --replay-trace --trace-root local/replay-traces
python3 -m mj.platform.match_runner --replay-trace --trace-root local/replay-traces
```

每个 gid 生成版本化侧车，包含 session/gid/recordId/localOrdinal、双时钟字段、因果父项、SSE、输入、请求、响应、merge、decision、action、reset 和局边界。后台写入使用有界非阻塞队列；满队列、写失败或没有 footer 会在导入后的 quality/coverage 中可见。采集发生在 enabled 判断之后才复制 payload，认证字段会替换为 `[REDACTED]`，所以关闭 trace 不复制状态，也不生成侧车。

## 固定离线基准

```bash
python3 -m mj.replay_debugger.benchmark \
  --seqs 2000 --local-steps 20000 --iterations 2000
```

命令输出 seq 数、本地步骤数、导出字节数、seek p95、峰值内存和无效 seq 保留游标的次数。验收阈值为 seek p95 `<= 200 ms`；结果依赖运行机器，应把命令输出连同 Python/Node 版本保存到验收记录。

## 20 项验收映射

下表把原有复盘场景映射到实现和测试入口。涉及线上历史事实的结论仍取决于实际输入覆盖；旧日志兼容性不等于 trace 事实。

| # | 场景 | 实现 / 验证入口 |
|---:|---|---|
| 1 | 无网络生成 JSON/HTML | `export.write_export`; `test_three_source_compile_and_offline_export` |
| 2 | 服务端 game identity 冲突 | `import_sources`; `test_identity_conflict_is_not_merged` |
| 3 | 输出保护和显式覆盖 | `write_export`; `test_three_source_compile_and_offline_export` |
| 4 | 重复事实保留全部 raw refs | `deduplicate_events`; `test_duplicate_server_event_keeps_refs` |
| 5 | 损坏行不阻塞后续行 | `read_jsonl`; `test_malformed_jsonl_keeps_later_records` |
| 6 | 稳定 ID 不随路径/时间变化 | `stable_id`; `test_knowledge_and_stable_identity` |
| 7 | 别名与三种杠 | `normalize_event`; `test_normalize_all_kan_types` |
| 8 | 仅 SSE 水位不生成 transition | `normalize_event` / compiler; `test_sse_watermark_has_no_mahjong_transition` |
| 9 | seq 0 不生成服务端帧，空洞不等于丢包 | `timeline.attach_steps`; `test_seq_zero_and_private_gap_are_not_events` |
| 10 | 跨 seq 请求的 start/response/merge | `import_local_jsonl`; `test_request_lifecycle_has_three_boundaries` |
| 11 | 独立服务端重放和缺源覆盖 | `build_server_frames`; `test_server_world_is_independent` |
| 12 | Expected 等待完整输入 | `compile_bundle`; `test_watermark_does_not_apply_pon` |
| 13 | Observed 与旧日志 DERIVED 区分 | `compile_bundle`; `test_legacy_observed_is_derived` |
| 14 | 牌河认领与加杠谱系 | `ReferenceReducer`; `test_called_river_and_added_kan_lineage` |
| 15 | 隐藏手牌与独立计数 | `KnownValue` / `project_visibility`; `test_hidden_hand_keeps_known_count` |
| 16 | 三种 request diff 覆盖 | `diagnostics.compare_states`; `test_request_diff_boundaries` |
| 17 | 业务必要性分类 | `classify_request`; `test_request_classification_priority` |
| 18 | 只在完整执行边界报告遗漏 | `detect_missing_transitions`; `test_missing_transition_requires_completion` |
| 19 | 三种视图贯穿桌面和 inspectors | React `App.tsx`; `test_visibility_projection_is_explicit` |
| 20 | trace 有界、脱敏、footer/离线导出 | `ReplayTraceWriter`; `test_trace_is_bounded_scrubbed_and_importable` |

## 验证命令

```bash
python3 -m pytest tests/test_replay_debugger.py -q
python3 -m pytest tests/ -q
openspec validate mahjong-replay-debugger --strict
npm --prefix web/replay_debugger run typecheck
npm --prefix web/replay_debugger run build
python3 -m py_compile mj/replay_debugger/*.py mj/platform/recorder.py \
  mj/platform/runner.py mj/platform/match_runner.py
```

## 已验证结果（2026-09-15）

- 专项 replay/trace 测试：`23 passed in 0.48s`。
- 完整 Python 回归：`416 passed, 1 skipped, 2 subtests passed in 110.00s`。
- OpenSpec：`openspec validate mahjong-replay-debugger --strict` 通过。
- Python 语法和 `git diff --check` 通过。
- React + shadcn/ui 构建通过：`replay-app.js` 230.29 kB，gzip 71.72 kB，CSS 3.44 kB，gzip 1.27 kB；`npm run typecheck` 与 `node --check` 通过，bundle 中 `http://`、`https://`、`fetch(`、`modulepreload` 均为 0。
- Playwright 离线浏览器冒烟通过：初始 `Round 1 / Seq 179`，Next 到 `Seq 180`，切换 `BEFORE`/`OMNISCIENT` 和无效 seq 均正常，HTTP 请求数 0、控制台错误数 0。
- 固定离线基准通过：2000 seq、20000 localStep、2000 次 seek，p95 `0.039 ms`，导出体积 `8,439,160` bytes，峰值内存 `64,508` bytes，进程最大 RSS `78,786,560` bytes，checkpoint 检查通过；95 次无效 seq 均保留在有效游标边界内。环境为 Python 3.11.14、Node v22.20.0、npm 10.9.3。
- trace capture 测量通过：固定 10,000 条 transition、bounded queue `16,384`，capture p95 `0.180935 ms`（目标 `<= 1 ms`），队列峰值 9,821，丢失 0，trace 体积 `5,698,671` bytes；该 p95 只覆盖捕获和入队，后台写盘单独运行，未把写盘耗时计入捕获指标。
- 两份现有本地日志均完成兼容导出：`local/games/20260911/u_9812ba08fe2f_a_b85ff51c0958_r1_b0_t0.jsonl`（gid `a_b85ff51c0958_r1_b0_t0`）生成 `index.html` 324,729,337 bytes 与 `replay.json` 560,954,194 bytes，覆盖 3,118 个 local step、1,369 个 normalized event、1,716 个 raw record、774 个 request，SSE/server-independent/trace 分别为 false/false/false；`local/games/20260914/u_9812ba08fe2f_a_7b0691487195_r1_b0_t0.jsonl`（gid `a_7b0691487195_r1_b0_t0`）生成 `index.html` 451,355,481 bytes 与 `replay.json` 786,966,671 bytes，覆盖 4,479 个 local step、2,117 个 normalized event、3,203 个 raw record、714 个 request，SSE/server-independent/trace 分别为 true/false/false。两份结果的 `quality` 为空、诊断数为 0、`firstVisibleDivergence` 与 `firstConfirmedFailure` 均为 `null`，`traceSchemaVersion` 为 `null`，因此仍按旧本地日志兼容结果处理，没有把它们升级成 trace 事实。

以上验证证明导入、编译、离线导出、前端构建和固定输入性能满足当前 change 的本地验收条件；线上历史是否存在未记录的动作窗口，仍取决于输入日志是否覆盖完整的服务端与执行边界。
