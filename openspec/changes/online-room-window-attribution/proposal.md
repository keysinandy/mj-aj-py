## Why

线上房已经具备 SSE 唤醒、权威 `/state`、单在途 StateDemand 和安全动作恢复，但验收日志仍不能稳定回答一个窗口究竟失败在确认、决策还是提交阶段。尤其是窗口 409、`legacy_unresolved` 身份、迟到的 terminal event 与原始 `claim_miss` 之间缺少可审计关联，导致真实客户端损失、服务端终态和观测缺口可能被重复统计或错误归因。

本 change 在不改变 BOT 策略、规则、15/s 限速和重试策略的前提下，补齐窗口生命周期时间线、权威身份、409/uncertain 安全证据、canonical resolution 和冻结版本线上验收闭环。

## What Changes

- 为每个 authoritative `WindowAttemptKey` 记录 source、确认、授权快照、决策、POST、结果和 terminal 的完整时间线。
- 增加互斥的窗口终态与三段式归因：CONFIRM、DECISION、SUBMIT/POST_RESULT，并将 SUCCESS、STRATEGY_PASS、RULE_PREEMPTED 置于更高优先级。
- 修正 `response_peng -> response_chi` 的同 WindowId 生命周期解析；旧 phase 在 chi 尚未开放时保持 PENDING，反向 phase advancement 才终止旧 phase。
- 为 action 409 和 POST uncertain 记录授权快照、deadline、HTTP/队列时序、server trace（如有）及安全 RESYNC 结果，禁止旧动作重发。
- 记录 `identity_origin`、`first_seen_via` 和 identity coverage；`legacy_unresolved` 不进入强三段式归因或 strong window complete 分母。
- 扩展 acceptance 脚本生成每个 `WindowAttemptKey` 的 canonical resolution、gap 影响分类、StateDemand 收束状态和 hard-fail 检查。
- 补充 fake-clock、离线 replay、409/uncertain、identity、生命周期和 acceptance 回归测试，并为冻结版本准备 3--5 个串行独立线上房验收。

## Capabilities

### New Capabilities

- `online-window-attribution`: 定义窗口身份、确认/授权/决策/提交生命周期、失败归因、恢复证据和分层线上验收规则。

### Modified Capabilities

- None.

## Impact

- 代码：`mj/platform/bot_client.py`、`mj/platform/recorder.py`、`mj/platform/api.py`，以及必要时的 `mj/platform/state_demand.py`。
- 验收：`scripts/window_acceptance.py` 和对应窗口、恢复、状态诊断测试。
- 日志/报告：新增窗口 timeline、authorization、identity、canonical resolution、409 chain、gap impact 和分层 completeness 字段；旧日志只能按兼容 fallback 标记为弱证据。
- 运行约束：保持默认 `--state-rate 15`、BOT、SSE + incremental `/state`、现有 sleep/retry/margin；不把旧房加入冻结版本通过分母。
