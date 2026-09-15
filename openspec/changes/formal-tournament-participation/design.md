## Context

当前平台已经有按令牌工作的 `Api`、复用完整状态机的 `BotClient`、策略工厂
`runner.make_decide` 和线程安全的 `Recorder`。`Api` 已提供 `/api/me`、赛事规则、
赛事状态、register/ready 以及每个实例共享的 `StateThrottle`；`BotClient.run()`
也已经负责 active game 的线程派发和 SSE/state/action 对局循环。现有
`mj.platform.runner` 面向测试房，带固定 `--games` 语义；
`match_runner` 面向 `/api/match` 自由对战。这两种入口都不能表达正式赛的多阶段
等待、阶段出席确认、动态加赛或正式赛终态。

正式赛令牌是 scoped credential：令牌返回的 `tournament_id`、`user_id`、
`active_games` 和 `/api/tournaments/me/rules` 是该 worker 的权威上下文。实现还必须
与现有窗口确认、共享 state 限速、动作未知结果后的 `seq=0` 重锚保持同一边界，
并在正式赛结束后保留可复盘证据。

## Goals / Non-Goals

**Goals:**

- 提供一个无固定正常局数上限的 `mj.platform.tournament_runner` 入口。
- 为每个配置令牌建立独立的身份、规则、生命周期、game worker、限流器和结构化结果。
- 根据权威赛事状态跨阶段存活，按阶段幂等 ready，并动态发现属于本赛事的 game。
- 默认记录所有正式赛对局，且令牌、Authorization header 和包含令牌的错误内容均不落盘。
- 通过可控的 fake API/时钟测试覆盖终态、重试、并发、保活、恢复和 Ctrl-C。

**Non-Goals:**

- 不创建赛事、报名、登录门户或实现新的服务端 API。
- 不复制 `BotClient` 的事件、Mirror、SSE、state、窗口确认、动作提交或恢复循环。
- 不修改麻将规则、策略/EV/模型、自由匹配或既有测试房正常语义。
- 不把日志中的 `auto_played=0`、空 game 列表或客户端超时推断为赛事成功。

## Decisions

### 1. 以独立 runner 编排 token，以 BotClient 执行 game

新增 `mj/platform/tournament_runner.py` 作为 CLI、配置、token worker 编排和结果
格式化层。每个 token 创建一个 `Api`（必要时为 `DumpingApi`）、一个策略函数、一个
`BotClient` 和一个共享 `Recorder` 视图，然后调用 `BotClient.run(max_games=None,
stop=...)`。对 `BotClient` 的改动限于暴露正式赛生命周期观察/终态、阶段键、规则和
中断所需的窄接口；不在新 runner 中重写 game loop。

选择复用而不是新建 `TournamentGameClient`，因为现有 BotClient 已包含 Mirror、
StateDemand/StateScheduler、SSE wake-only 语义、窗口授权和未知 action POST 恢复。
把所有逻辑复制到 runner 会产生两套协议实现，未来修复无法一致生效。

### 2. token worker 是隔离边界，StateThrottle 是 Api 资源

`TournamentWorker` 保存 token label、Api、user/tournament identity、规则快照、阶段
状态、已见/已完成 game 集合、stop 状态和结构化结果。每个 token 只使用自己的
`Api` 和 `StateThrottle`；同一 token 的多个 BotClient game 线程共享该 Api，不能按
game 创建新的 `/state` 预算。多个 token 可以并行，但 worker 之间不共享身份、规则、
active game 或完成集合。

配置新增赛事专用加载/校验入口，接受非空 `tokens` 映射而不假设四人；保留现有
`load_config`、`load_match_config` 和旧 runner 的调用契约。令牌不作为 CLI 参数传入，
仅从 gitignored 配置读取。

### 3. 生命周期由权威状态驱动，阶段边界显式去重

worker 先通过 `/api/me` 解析非空 `tournament_id`，再获取赛事规则和状态。支持
`registering`、`stage_open`、`running`、`stage_done`、`finished`、`closed`、`void`；
`stage_done` 只表示阶段间等待。对 `stage_open` 使用服务端阶段标识（若响应没有
显式字段，则使用稳定的阶段边界字段组合）作为 ready 去重键；同一阶段只做有限、
幂等的 register/ready，新阶段重新确认。`TOURNAMENT_STARTED` 是状态竞争，
`NOT_QUALIFIED` 在权威状态确认后是正常淘汰。

选择显式阶段键而不是“每次轮询都 ready”，避免持续请求制造无界副作用；选择
权威终态而不是 game 数量或空列表，才能支持行政暂停、重赛和 tiebreak。

### 4. 轮询采用有界退避，并将“未知状态”与“终态”分开

赛事 GET 的临时网络错误、超时和 5xx 使用有界退避；失败期间不沿用旧状态作淘汰、
完成或新阶段决策，恢复后先重新获取完整状态。正常轮询间隔保持远小于平台在线窗口
（目标小于 60 秒），stage_done 没有 game SSE 时仍单独保活赛事端点。

选择 bounded backoff 而不是无限快速重试，避免故障时自激；选择继续存活而不是以
`active_games=[]` 退出，避免阶段交接时误离赛。

### 5. 规则快照显式传给现有 game client

worker 在开始 game 前保存本 token 的规则快照，至少处理 `M`、`Rounds`、`BaseScore`、
`YouCaiBiKao` 和服务端支持的 timeout/window 参数；未知字段忽略。规则获取失败时
不以未验证的全局默认值启动 game，并按赛事轮询策略重试或以结构化失败终止。正式
赛 Recorder meta 使用该 token 的规则值。

选择 token-scoped immutable snapshot 而不是修改全局配置，避免多赛事/多 token
并行时规则互相覆盖；游戏协议仍由 BotClient 消费，runner 不构造第二套 Mirror。

### 6. 结构化结果和脱敏在 worker 出口统一

每个 worker 无论正常完成、淘汰、认证失败、协议错误还是中断，都生成包含 label、
user/tournament/game 摘要、最终状态/阶段、局数、动作计数和终止原因的 JSON-safe
结果。结果只允许出现 token label、user_id、tournament_id、game_id 等非秘密标识；
异常、DumpingApi 和 stdout/stderr 通过统一脱敏函数处理，避免直接打印 `str(ApiError)`
中可能包含的 bearer token。

选择结构化结果而不是仅依赖退出码，因为淘汰是正常赛事结果而认证/协议故障需要
非零退出码；选择默认 Recorder 而不是赛后再拉日志，因为正式赛证据不可可靠再生。

### 7. 中断使用共享 stop event，保持已有退出路径

主线程捕获 Ctrl-C 后设置一个共享 stop event；worker 停止创建新 game，BotClient
通过已有 stop 检查退出，最终关闭 Recorder 并标记 `INTERRUPTED`。不删除已写日志，
不在 shutdown 开始后主动提交新 action。`--max-games-debug`（若保留）只用于测试/诊断，
不能作为正式赛正常退出条件。

## Risks / Trade-offs

- [服务端赛事响应字段不稳定] → 将字段归一化集中在 worker/API adapter，保留原始安全
  摘要和明确的 `PROTOCOL_FATAL`；fixture 覆盖缺字段而不猜测身份。
- [现有 BotClient.run 的测试房兼容语义与正式赛语义耦合] → 用显式 tournament mode/
  lifecycle hook，保留 `max_games` 非空时旧路径；新增测试确保旧 runner 不变。
- [长时间 stage_done 可能消耗请求预算] → 赛事轮询不占 game `/state` throttle，使用
  有界 interval/backoff；game 仍只使用每 token 一个 StateThrottle。
- [Dump 或异常信息泄露 token] → dump 只保存安全请求摘要，所有异常/日志在输出边界
  经过字段级 redaction，并用包含 credential 的 fake error 做回归。
- [worker 线程在 Ctrl-C 时仍阻塞 HTTP] → 复用现有单次请求 timeout，stop 检查轮询/派发
  边界；join 使用有界等待并把未能立即退出的状态显式记入结果。
- [在线验收需要真实报名令牌且可能受平台状态影响] → 第一阶段只提交离线/fixture
  验收；真实赛事只在显式授权后运行，报告 transport、window、game、tournament 层
  证据，不把计划门槛写成已验证事实。

## Migration Plan

1. 先增加配置校验、赛事 runner 和 fake API 测试；现有 test-room/free-match 命令不变。
2. 接入 BotClient 的最小正式赛生命周期/结果扩展，运行完整本地测试、编译、diff 检查
   和 OpenSpec strict validation。
3. 使用单 token、`--state-rate 15`、默认 Recorder 进行受控 dry-run/线上 canary，
   核对 `/api/me`、rules、阶段 ready、动态 game、结果及脱敏日志。
4. 若线上行为异常，停止新 runner 或回滚新增模块/入口；不需要回滚现有游戏协议，
   也不删除已有 Recorder 文件。只有分层验收通过后才允许正式参赛。

## Open Questions

- 服务端正式响应中代表阶段身份的规范字段最终名称是什么；实现前需以真实响应或
  平台指南确认，不以 `stage_done` 次数猜测阶段。
- rules 中 timeout/window 参数到 BotClient 构造参数的完整映射由平台实际字段决定；
  未确认字段应保留为安全未消费状态，而不是静默改写全局默认。
- 多 token worker 是共享一个进程级 Recorder 还是每 token 一个 Recorder 实例，需在
  现有文件命名和关闭语义验证后定稿；无论选择哪种方式，日志必须按 gid/token label
  隔离。
