## Why

当前平台客户端主要通过测试房间 runner 驱动固定范围的对局，尚未提供一个由报名令牌和平台权威状态驱动的正式锦标赛入口。正式赛事需要在不复制现有游戏协议实现的前提下，持续处理报名、分阶段晋级、阶段间等待和动态 game 分配，否则固定 token/game 数量或空 game 列表都可能导致过早退出或错误归因。

## What Changes

- 新增 `python3 -m mj.platform.tournament_runner` 正式锦标赛入口，支持一个或多个平台签发的报名令牌。
- 为每个令牌独立解析 `/api/me` 身份、获取赛事规则、维护生命周期和终止结果，并按阶段执行幂等 register/ready。
- 根据平台权威 tournament 状态持续保活和轮询，动态发现属于当前赛事的 active game，不以固定局数或暂时空列表作为完成条件。
- 为同一令牌的多个 game 复用一个 `Api` 及共享 `StateThrottle`，将每局交给既有 `BotClient.run()`，保留现有 SSE、state、窗口确认、动作提交和恢复行为。
- 默认启用 Recorder，输出结构化、按令牌隔离且经过秘密脱敏的运行结果；增加离线单元/集成验收覆盖身份、规则、阶段、重试、发现、并发和终止边界。
- 不改变麻将规则、策略模型、游戏协议、自由匹配流程或既有窗口确认、状态恢复、限速和安全动作传输实现。

## Capabilities

### New Capabilities

- `formal-tournament-participation`: 基于报名令牌的正式锦标赛入口、权威生命周期、阶段出席确认、动态 game 发现、按令牌资源隔离和结构化终止。

### Modified Capabilities

无。本变更把现有 `openspec/specs/formal-tournament-participation/spec.md` 的行为基线落成可运行入口，不修改既有游戏协议或其他能力的要求。

## Impact

- 新增 `mj/platform/tournament_runner.py` 及赛事 supervisor/config/result 相关的最小封装，并复用 `api.py`、`bot_client.py`、`runner.py` 和 `recorder.py`。
- 可能扩展 `Api` 的赛事查询/报名/ready 包装和配置加载，但保持现有测试房间与自由匹配调用兼容。
- 增加 tournament runner、生命周期、token 隔离、共享限流、规则注入、重试和秘密脱敏测试，以及 CLI 使用文档。
- 不需要服务端改造或第三方依赖；正式运行仍依赖平台已签发的报名令牌和服务端权威赛事接口。
