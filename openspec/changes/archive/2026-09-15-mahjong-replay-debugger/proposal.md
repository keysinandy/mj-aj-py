## Why

现有线上问题需要同时回答“服务端发生了什么、本地何时知道、本地实际如何处理、哪个 `/state` 修复了差异”，而现有回放和 HTML 时间线分别只能核对动作或查看请求。依据 `docs/reply.md` 建立独立的本地复盘能力，并补齐实际执行证据，避免将通知延迟、协议缺段、必要窗口确认误判为状态机缺陷或冗余请求。

## What Changes

- 提供只读离线命令，加载服务端 timeline、本地 JSONL 和可选 HTTP dump / 执行 trace，生成确定性的复盘数据与可直接打开的单文件 HTML。
- 独立维护 Server Ground Truth、Local Expected State、Local Observed State；旧日志只能推导的状态明确标记为重建估计，缺失服务端数据时仍可进行有限本地复盘。
- 支持局号、seq、Before/After 和本地步骤导航，展示四家手牌可见性、历史牌河、副露、请求、结构化差异、异常和恢复链。
- 依据完整输入与实际应用证据检测吃、碰、明杠、暗杠、加杠遗漏，分别定位首次可见差异与首次确认的处理故障。
- 分类 `/state` 的业务用途、状态变化、恢复原因和可避免性；保留逻辑请求、物理重试、响应接收与应用边界，不将无牌面差异直接判为冗余。
- 增加可选本地执行追踪，记录 transition/merge 前后状态、错误、连接生命周期与因果关联；兼容既有日志和离线工具，保持线上策略、请求调度及动作授权规则。
- 将 `docs/reply.md` 的验收场景落为自动化检查，补充 wake-only SSE、缺证据、跨 seq 请求、牌河表示差异和确定性跳转测试。

## Capabilities

### New Capabilities

- `mahjong-replay-debugger`: 离线证据导入、三份独立状态、确定性双维度导航、麻将状态诊断、请求归因与自包含 HTML 复盘界面。
- `local-replay-tracing`: 可选的本地执行证据采集，包括关联 ID、实际状态变更、请求应用、SSE 连接边界及可检测的追踪缺失。

### Modified Capabilities

无。本变更新增离线能力及可选观测，不改变已有窗口授权、请求生命周期、传输诊断或 BOT 决策的行为契约。

## Impact

- 新增 `mj/replay_debugger/` 数据模型、导入适配器、参考状态机、诊断和 HTML 导出模块，以及对应测试、离线夹具与使用文档。
- 复用 `mj/replay.py` 的服务端分块格式、`mj/platform/proto.py` 的牌名和事件解析、`mj/log_replay.py` 的核对入口，以及 `scripts/render_sse_state_timeline.py` 的自包含输出经验；不直接沿用其补猜牌、自动代过或按墙钟排序作为真实执行证据。
- 在 `mj/platform/recorder.py`、`bot_client.py`、必要的 `state_fetch.py` / `api.py` 边界及 `runner.py` / `match_runner.py` 增加可选追踪接入。默认对局行为不变，不增加线上请求。
- HTML 使用本地嵌入的 CSS/JavaScript，不依赖 CDN、生产登录态或运行中的 Web 服务；新能力无需生产服务端修改。
- 保持与 `state-request-lifecycle`、`bot-shape-aware-evaluation` 等已有 change 独立。第一版不进行 BOT 策略重算、实时操作、跨局统计或生产环境自动修复。
