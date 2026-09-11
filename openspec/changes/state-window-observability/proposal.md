## Why

窗口调度已经在 15/s 共享限流和 SSE 唤醒下基本稳定，但当前仍难以回答两个
线上关键问题：某次确认请求究竟对应哪个弃牌窗口，以及一次慢请求的时间究竟
花在客户端排队、HTTP 传输还是服务端窗口竞态。SSE 帧合并只解决了唤醒层，
`/state` 需求仍可能在同一局重复在途；同时不完整房间被混入房级统计会污染
后续参数判断。现在先把身份、需求合并和分阶段观测钉成可验收契约，再决定
是否值得做任何限流参数实验。

`catch_play` 抓打圈修复已在独立 commit `3ae3fd6` 完成并通过全量回归；本
change 不重新修改该正确性路径，也不把它与调度重构混合归因。

## What Changes

- 定义稳定的 `WindowId = (game_id, round_id, discard_owner, source_discard_seq, tile)`
  和 `WindowAttemptKey = (WindowId, phase)`；`peng → chi` 共享同一 `WindowId`。
- 收紧 legacy 窗口身份语义：生产事件缺 `source_discard_seq` 时标记
  `legacy/unresolved`，不得使用牌河长度、副露数或 `/state` 的 snapshot
  watermark `seq` 做跨 `seq=0` 重锚的强去重或动作防重。
- 在 `BotClient` 与 `Api.game_state()` 之间增加按 gid 的 `StateDemand` 协调：
  每场最多一个物理 `/state` 在途；合并 `wanted_seq`、最早有效 `deadline`、
  `reason_mask`、优先级和 `generation`，请求返回后分别判断每个逻辑 reason
  是否满足，必要时最多补一次最新请求。
- 固定需求优先级 `WINDOW_CONFIRM > RESYNC > SSE_DELTA`，并让没有非 pass
  合法反应的窗口退出 urgent；本 change 不提高默认 15/s、不调整 BOT 策略、
  sleep 或 release-aware EDF。
- 为 state/action 记录可区分排队与 HTTP 阶段的诊断字段：本地
  `throttle_enter/granted`、`http_start`、`headers_received`、`body_finished`、
  `response_status`，以及 `pre_read_ms`、`read_ms`、deadline 剩余时间；DNS、
  connect、TLS、send 等不可观测阶段保持 unavailable。
- 原样保留 `Retry-After` 和 `server Date`，同时提供可解析的秒数/epoch（若能
  解析）；它们只用于诊断，不参与基于本地 monotonic deadline 的控制逻辑。
- 建立三层线上验收口径（transport/window/game），并将房间标记为
  `complete`、`partial` 或 `protocol_skipped`；只有完整房进入 A/B 房级主指标。
  增加窗口确认比例和 state demand 合并/重复指标。

## Capabilities

### New Capabilities

- `state-window-observability`: 稳定窗口身份、按场合并 `/state` 物理需求、分
  阶段 HTTP 诊断，以及带完整性分类的窗口/传输/对局验收数据。

### Modified Capabilities

（无——当前 `openspec/specs/` 没有既有能力契约；本 change 新增独立能力。）

## Impact

- **代码**：`mj/platform/bot_client.py`（窗口身份和需求协调接入）、
  `mj/platform/api.py`（保留共享 throttle，补充 state/action 阶段诊断）、
  `mj/platform/throttle.py`（第一阶段复用现有 15/s/EDF 接口，必要的需求元数据
  传递；不改变速率）、`mj/platform/recorder.py`（窗口身份、reason、完整性和
  诊断字段）。
- **测试**：扩展 `tests/test_state_scheduling.py` 及窗口/传输记录器回归，使用
  fake clock 模拟十场并发、同 gid 单在途、SSE watermark 合并、reason 分别完成、
  generation 竞态、legacy 身份和过期确认。
- **线上验收**：继续使用默认 SSE + 增量 `/state`、15/s、启发式 BOT；至少收集
  3～5 个完整房。`partial` 房（中断、日志缺尾、缺 end）只作诊断样本，
  `protocol_skipped` 需明确标注协议无法提供的结算证据。
- **兼容性**：新增日志字段和诊断标签应向后兼容既有回放；动作 POST 单次提交、
  409/未知结果 `seq=0` 重锚且不盲重发的安全边界保持不变。
