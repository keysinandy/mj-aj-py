# Design

## Context

见 `proposal.md` 的动机和 `platform-public-material-context` delta spec 的行为契约。

当前平台快照已公开 `hand_counts`，但 `Mirror.apply_snapshot()` 没有保存它；`Mirror.build_game()` 为合法性而构造的占位 `Game` 只含本家手牌，`PublicDecisionContext.from_game()` 因而用 `13 - 3*len(melds)` 估算每座暗手。这个估算对普通站立手牌有效，却无法表达快照时某家尚持摸牌的 14 张状态。shape-v2 Fast EV 调用 `validate_for("fast")`，使该少一张的估算表现为未捕获的 `material_conservation`。

约束：平台只公开张数，不公开对手牌面；`legal_actions()` 仍是唯一动作合法性真源；镜像状态会在 gap/seq=0 处重建；不新增请求、不改变窗口时序；完整 rollout/teacher 与只需本家状态的 Fast EV 必须保持不同的完整性门槛。

## Goals / Non-Goals

**Goals:**

- 使可信的快照 `hand_counts` 成为仅含张数、带来源和验证状态的公开输入。
- 让平台镜像构造的上下文能在第 14 张牌等真实快照形态下守恒，并仍不读取隐藏牌面。
- 将“公共物料未知”与“确定畸形物料”分开，使 shape-v2 在前者受控委托 legacy，在后者不产生 EV/teacher 输入且不泄漏异常杀死场次。
- 提供可复现、脱敏、覆盖 snapshot → event → mirror → context → Fast EV 的测试与证据。

**Non-Goals:**

- 不将 shape-v2 改为默认策略，也不因本修复宣称通过既有收益、校准、性能或发布闸门。
- 不重建对手暗牌身份、真实墙顺序、四家完整 chain/chain_piao，或放宽 rollout/teacher 的信息边界。
- 不修改规则、计分、合法动作、状态调度、窗口确认、网络请求、日志保留政策或历史日志。
- 不以“缺少计数时按阶段猜测”覆盖已收到但不可验证的快照计数。

## Decisions

### 1. 区分“权威快照计数”和“阶段推导计数”

为 Mirror 增加私有的公共计数状态，至少包括四座计数、来源（`snapshot`、`event_advanced`、`derived`、`unknown`）和验证状态。全量快照中 `hand_counts` 只有在结构、范围、与 `my_hand`/公开副露相容时才更新该状态；无字段的旧协议快照走显式 `unknown`/兼容分支，不把此前状态错误延续为当前权威状态。

`build_game()` 将该状态以纯值的公开投影（例如 `public_hand_counts` 和来源/状态）附到占位 Game；决策上下文仅读这个投影，不读取 `hands[other]`。`from_game()` 优先使用已验证投影，原生离线 Game 不带该属性时保留当前阶段推导，从而保持现有离线 context hash 和测试基线。

**备选方案：**
- *仅在 `from_mirror()` 使用 `hand_counts`*：线上 `choose_game_action()` 现经 `from_game(build_game())`，会形成两套上下文和遗漏调用点。
- *给 `Game` 加四家真实手牌*：违反 Mirror 的隐藏信息边界，且会让未来调用者误读牌面。
- *总是用阶段公式并允许差 1*：掩盖真失步，且在副露/杠/认领边界不能通用。

### 2. 用事件转换表推进，遇到不可证实即失效

从可信快照开始，Mirror 仅在事件能唯一决定某座暗手张数变化时更新：公开弃牌、吃/碰/杠分别按已确认的从手牌移除量改变计数；自家摸牌按已知牌改变；对手未公开摸牌的计数变化不得靠墙或身份猜测。实现前必须根据 `parse_event()` 的事件语义列出逐事件增量表，并用合成全量 Game 和录制流对拍每一类事件。

任何重复、缺事件、负值、与本家/副露矛盾、或无法计算的转换都会把受影响座位标记为 unknown（或 malformed）；直到下一份有效 full snapshot 才恢复。不可把 unknown 填成零或重新套阶段公式伪装为 snapshot 真相。

**备选方案：**每个普通事件后直接按阶段+副露重新推导。它会重新引入第 14 张问题，并抹掉“快照权威计数已经失效”的审计信息。

### 3. 验证分层，而非放宽守恒

保留 `visible_count`、负计数、同一牌超过四张、本家 `hand_counts` 不等于公开本家手牌数等确定矛盾为 malformed。Fast EV 对 `unknown` 和“不完整但非矛盾”的公共物料返回一个结构化、稳定的 legacy 委托结果；对 malformed 也必须通过调用层的明确受控路径完成一次合法策略选择/重锚，而非让 `ContextError` 穿透 `BotClient`。

完整 world/teacher 不接受 unknown 或 malformed，也不能复用 Fast EV 的 legacy 动作作为 teacher 样本。Fast EV 的候选 Q 不得在 unknown 状态部分计算后混合 legacy 结果。

**备选方案：**在 `validate_for()` 里忽略 `material_conservation`。这会同时放过 teacher/world 的错误输入，违反现有“非法计数不能静默修复”约束。

### 4. 选择两层回退责任

`evaluate_discard_context()`/root evaluator 应将可降级的 context 状态转换成带 `context_material_unknown`（或冻结的等价常量）的 `FastEvaluation`，保留 caller 预先计算的 legacy action。调用层还须防御任何意外 `ContextError`：将其转换为可记录的 legacy 决策或镜像重锚边界，不能让决策线程中止。根 scope 和 discard scope 采用同一原因分类，反应 scope 维持既有委托行为。

这使异常分类靠近语义验证，线上线程保护靠近执行边界：前者可离线单测，后者防未知未来协议变化。

### 5. 证据和 hash 处理

新增/更新的上下文 payload 包含计数值、来源和验证状态，故**线上** hash 变化是有意行为。原生 `Game` 不携带公共计数投影，必须证明其 hash 及固定决策在兼容路径不变；若任何现有冻结 artifact 依赖 context hash，实施后以 artifact manifest 明确比对和记录结果，不能凭推断宣布有效。

生成脱敏最小 fixture：包含 `hand_counts=[14,13,13,13]`、公开牌河/副露/墙数/本家手牌，且不包含令牌、完整对手手牌、真实墙或完整线上日志。证据清单记录：代码 revision、profile fingerprint、规则版本、平台指南版本、fixture SHA-256、Python/Rust 内核、测试/对账命令、线上房间匿名标识或非秘密汇总。

## Risks / Trade-offs

- **事件语义在杠或代打边界不能唯一推出计数** → 优先把受影响座位标为 unknown，等待 full snapshot；不为提高 shape-v2 覆盖率猜数。
- **未知状态出现频繁，shape-v2 大量回退 legacy** → 报告已验证/unknown/malformed 分母、回退率和逐 phase 原因；不得用回退路径吞吐声称 EV2 在线可用。
- **公共计数源发生平台协议变化** → 采用严格校验和受控降级；首次新版本平台运行先记录协议探针和 fixture，再更新 accepted schema。
- **新增字段被误用为隐藏信息通道** → 使用只含整数的不可变 tuple，明确禁止牌面数组/对象引用；测试通过替换对手牌面而保持计数不变来验证决策/hash 不依赖身份。
- **兼容路径无意改变离线证据** → 固定种子 hash/动作回归，比较 revision 前后的离线 context payload，任何变化纳入证据失效评估。
- **线上 abort 归因被掩盖** → 日志分别记录 `unknown`、`malformed`、legacy 委托与镜像重锚；不能把任意 `ContextError` 标成成功 EV 决策。

## Migration Plan

1. 先实现纯值公共计数投影、验证和合成/真实脱敏 fixture 测试，保持 shape-v2 默认关闭。
2. 在全量离线回归、Python/Rust parity 和 context hash 兼容检查通过后，运行只读/受控的镜像日志重放，确认既有线上录制中不会因该问题中止决策。
3. 运行新的 shape-v2 小规模线上 canary：独立新房、显式策略/profile、SSE + 增量状态、15/s；每房完整打完后执行 `mj.replay` 对账并导出无秘密 manifest。任何 `decide_errors`、未解释的物料 malformed、非法动作、NaN 或 evaluator 引起的窗口损失均阻止继续扩量。
4. 仅在 canary、性能、收益/校准及原 `bot-ev-discard` 发布闸门全部通过后，另行提案决定是否允许线上 profile/默认切换；本变更结束时仍保持 opt-in。
5. 回滚为禁用 shape-v2 线上 profile（legacy/shape-v1 不动）；不要删除本地日志或修改历史 artifact。若发现协议字段失效，禁用该计数源并回到受控 legacy 委托，而非恢复旧的未捕获异常路径。

## Open Questions

无。平台快照 `hand_counts` 的存在及 `wall_remaining` 含死墙的口径已由 v34 指南、真实录制和 `synth.py` 确认；具体事件转换表由实现任务先以现有 `parse_event()` schema 和合成真值锁定，任何不能证明的转换按本设计的 unknown 边界处理。
