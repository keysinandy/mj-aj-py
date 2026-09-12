## 1. 基线与窗口身份契约

- [x] 1.1 以独立提交 `3ae3fd6` 为实现基线，复核 `catch_play` 修复及完整
  `python3 -m pytest tests/ -q` 结果，不将该修复与本 change 混合修改。
- [x] 1.2 在 `mj/platform/bot_client.py` 或独立平台模块中定义
  `WindowId`/`WindowAttemptKey` 数据结构，统一携带 game、round、owner、
  source discard seq、tile、phase 和 identity status；另为每个逻辑请求定义
  `logical_request_id`，为每个物理 retry 维护递增 `attempt_index`，不让
  `WindowAttemptKey` 兼任物理 attempt ID。
- [x] 1.3 收紧 source sequence 提取：协议权威字段或
  `tile_discarded` 自身 event seq 优先，其他事件缺失时标记
  `legacy/unresolved`；禁止把 snapshot watermark、牌河长度或副露数升级为
  跨 seq=0 的强身份，并保留弱 fallback 诊断字段。
- [x] 1.4 将 `peng → chi`、seq=0 重锚、换轮/新弃牌和动作防重路径统一接入
  `WindowAttemptKey`，为 authoritative 与 legacy 两种语义分别保留测试断言。

## 2. per-gid StateDemand 协调器

- [x] 2.1 实现 per-gid demand 对象，事实字段维护
  `watermark_target`、`full_snapshot_required`、`reasons`（SSE_DELTA 的
  wanted_seq、RESYNC 的 cause/requested_at、WINDOW_CONFIRM 的 window_id/phase/
  deadline/status）、`generation`、`in_flight`；`kind_priority`、
  `effective_deadline`、`reason_mask` 作为派生值。SSE queue 只作为需求输入/唤醒，
  不再承担完成语义。
- [x] 2.2 实现确定性合并与物理选择：只有增量 watermark 取 max，`seq=0` 由
  `full_snapshot_required` 选择而不参与 max；DELTA 物理请求使用 BotClient 的
  local applied cursor，不得跳到 SSE watermark；有效 deadline 取剩余窗口 reason
  的最早值，reason metadata 分别保留，优先级固定为
  `WINDOW_CONFIRM > RESYNC > SSE_DELTA`。重复/更低 watermark 不递增 generation，
  过期窗口进入 `SATISFIED/TERMINAL/PENDING` 规则，不改变 StateThrottle 的
  15/s/EDF/429 接口。
- [x] 2.3 将 `BotClient._play_loop()` 的 SSE、deadline、seq=0 重锚和窗口确认
  触发改为提交 demand；同一 gid 在途期间只更新对应 reason metadata，只有语义
  状态变化才递增 generation。每次 completion 先 reconcile 最新 demand；仅当
  仍有 PENDING reason 时最多创建一个 successor，不能把 generation 变化本身当
  成补请求条件。
- [x] 2.4 实现 reason-specific satisfier：RESYNC 只有在 snapshot 成功重建镜像后
  才完成，SSE_DELTA 判断 watermark 覆盖，WINDOW_CONFIRM 的 peng/chi 统一判断
  WindowId/phase/responding_seats/精确截止条件；固定输出
  `SATISFIED/TERMINAL/PENDING`，只保留 PENDING，不能以一次 seq=0 返回清空全部
  reason，并在 reason 结束后重算派生字段。
- [x] 2.5 在安全的结构性合法候选证明或 authoritative snapshot 证明无响应资格
  后，才对无非 pass 合法动作的弃牌窗口清除 urgent、回退普通 SSE 追赶；不得仅
  依据 local phase/`mirror.legal_actions()`/unresolved identity 降级。分别记录
  合法候选、过期截止、stale-pending/closed-terminal 和 catch_play 保留 urgent
  的原因。
- [x] 2.6 确认所有物理 state 尝试（含 retry）仍只经共享 `Api.state_throttle`，
  不新增并发请求、不改变默认 rate、sleep、release-aware EDF 或动作安全边界。

## 3. HTTP 与记录器观测

- [x] 3.1 在 `mj/platform/api.py` 的 state/action 物理 attempt 中捕获
  `throttle_enter`、`throttle_granted`、`queue_wait_ms` 以及
  `http_start`、`headers_received`、`body_finished` 边界，并用 monotonic 派生
  `pre_read_ms`、`read_ms`、`total_ms`；HTTPError 在捕获时视为 headers 已到达，
  `e.read()` 完成才设置 body_finished；不经过 StateThrottle 的 action 标记
  `not_applicable`，DNS/connect/TLS/send 保持 unavailable。
- [x] 3.2 为每个 attempt 保存 `retry_after_raw`、`server_date_raw`，并在可解析
  时补充 `retry_after_seconds`（秒数或 HTTP-date）和 `server_date_epoch`；解析
  失败不丢 raw，server Date 不参与 deadline 控制。
- [x] 3.3 记录 `deadline_left_at_send` 与 `deadline_left_at_response`，严格由
  本地 monotonic deadline 计算；保留每次 retry 的独立 throttle/HTTP 诊断和逻辑
  请求的聚合 attempts/429/queue。
- [x] 3.4 扩展 `mj/platform/recorder.py` 的 req/action/window_confirm 字段，
  携带 WindowId、attempt key、`logical_request_id`、`attempt_index`、reason、
  generation、identity status 和观测字段；保持旧 JSONL 缺字段时可回放。

## 4. 离线回归与契约测试

- [x] 4.1 在 `tests/test_window_confirmation.py` 增加 authoritative WindowId、
  peng→chi 共享身份、seq=0 同窗重锚、换轮/新牌失效和 legacy unresolved
  不强去重的 fake-clock 场景。
- [x] 4.2 在 `tests/test_state_scheduling.py` 增加十场并发模拟：100 SSE 帧一条
  demand、同 gid 单在途、跨 gid 独立、watermark/full snapshot/deadline/reason
  合并，以及 generation 变化后只有最新 demand 仍 PENDING 才产生 successor。
- [x] 4.3 增加 reason 分别完成、SATISFIED/TERMINAL/PENDING、窗口确认条件不满足、
  urgent 降级、未来窗口压过普通 backlog、过期确认自动终止/降级，以及 SSE wake
  + deadline wake 单次消费场景；增加 `catch_play` 回归，证明 local phase/stale
  mirror/identity unresolved 不能单独清除 urgent。
- [x] 4.4 增加 API fake response/HTTPError/read failure 测试，断言 throttle_enter/
  granted/queue_wait_ms、正常响应与 HTTPError 的 headers_received 语义、body
  完成边界、raw header、数值/HTTP-date 解析、unavailable 字段和本地 deadline
  剩余语义。
- [x] 4.5 增加 recorder/回放兼容测试，确认旧日志读取不报错且 unknown identity
  不被当作 authoritative；确认逻辑请求与物理 retry 可关联。
- [x] 4.6 运行 focused 回归、完整 `python3 -m pytest tests/ -q` 和
  `git diff --check`，失败先定位并补齐上述契约场景。

## 5. 验收分类、指标与文档

- [x] 5.1 扩展验收脚本/报告，为每房分别输出
  `transport_status`、`window_status`、`game_status`；区分日志缺尾与协议没有
  `round_ended`，只从受影响层的主指标分母排除 `partial`，只从不可验证层排除
  `protocol_skipped`。
- [x] 5.2 按层输出 transport/window/game 指标，增加
  `WINDOW_CONFIRM seq=0 / eligible_windows`，并分别输出
  `logical_demands`、`coalesced_demands`、`successor_requests`、
  `physical_state_requests`、`suppressed_duplicates`、`coalescing_ratio` 和独立的
  `physical_state_attempts`；同时输出各层完整性数量和对应分母。
- [x] 5.3 同步 `README.md`、`HANDOFF.md`、验收/计划文档和运行说明：线上基线
  显式使用启发式 BOT、SSE+增量 `/state`、15/s；policy 只作专项对照。
- [x] 5.4 在冻结代码后以标准 BOT 命令运行 3～5 个房，记录 checkpoint、rate、
  传输模式和三层完整性分类；各层只汇总对应 status=complete 的房，
  `protocol_skipped` 仅排除 game 层，`partial` 仅排除受影响层，不在本 change
  中进行 rate/sleep/策略实验。
