## Why

当前 `/state` 请求在进入共享限流队列前就被固定并标记为在途，导致排队期间的新确认需求不能升级原 DELTA 请求；确认状态、请求计数和结束清理又分散在多处，已有代码可复现跨 gid 请求 ID 重复及结束后 RESYNC 残留。参考 `docs/think.md`，先建立完整的请求生命周期和职责边界，为后续调度实验提供可验证的基础。

## What Changes

- 分离可变的排队候选、冻结的逻辑请求和每一次物理 HTTP attempt；同 gid 排队期间允许 DELTA 升级为 FULL，发送后继续保持单请求在途。
- 提取状态获取协调器和调度入口，复用唯一共享 StateThrottle 等待队列，保留当前 EDF、过期降级、普通请求排序、15/s 默认值及 429 冷却策略。
- 提取窗口确认状态与时间字段的所有权，明确最早发送时间、调度截止、观察预算、阶段精确截止；保留现有确认时机与动作授权行为。
- 按 reason 的版本和 WindowAttemptKey 核对完成结果，防止旧响应完成新窗口，也防止普通 SSE generation 更新使有效确认失效。
- 统一终止清理，确保 finished、404、错误退出和停止路径中的候选、在途状态及未完成需求得到准确记录和释放。
- 增加运行范围唯一的传输关联 ID、实际物理 attempt 计数及可重建的合并证据，保留旧日志字段的兼容解释。
- 补充针对性回归、冻结日志回放和实施后的分层线上验收；不把结构重构宣称为 C4 延迟改善。

本阶段不启用自适应提前量、minimum-slack 排序、容量预留、公平调度新规则或 deadline-aware retry/successor；不改变通用 HTTP 重试、BOT 策略、动作 POST、窗口身份协议及提交余量。

## Capabilities

### New Capabilities

- `state-request-lifecycle`: 每局状态需求的排队、升级、发送、响应应用、按原因完成和终止清理，以及统一的窗口确认时间状态和并发所有权。

### Modified Capabilities

- `window-confirm-transport-diagnostics`: 为逻辑请求与物理 attempt 增加运行范围唯一的关联标识，区分候选、逻辑请求和实际发送计数，明确调度/观察时间与权威截止的诊断来源。

## Impact

- 主要涉及 `mj/platform/state_demand.py`、`throttle.py`、`api.py`、`bot_client.py`、`recorder.py`，以及新增的 `state_fetch.py`、`state_scheduler.py`、`window_confirmation.py`。
- 影响 `scripts/window_acceptance.py` 和状态需求、调度、窗口确认、传输诊断、退出路径相关测试；同步 README/HANDOFF 中与本次实现有关的说明。
- 保留同步 HTTP、每局工作线程、SSE 仅唤醒、增量使用本地已应用 cursor，以及 409/未知 POST 后 seq=0 重锚的边界。无需新增第三方依赖或服务端部署。
- 不修改 `window-identity-protocol` 的协议选择，不把本次任务合并进诊断专用的 `window-confirm-loss-root-cause`。`docs/think.md` 的算法实验作为后续独立 change。
- 冻结 `ab371b6` 三房日志仅用于诊断和兼容回放，不进入本次实现后的新验收分母。
