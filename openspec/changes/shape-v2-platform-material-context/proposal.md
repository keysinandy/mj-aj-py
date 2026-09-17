# Proposal

## Why

`shape-v2` 在线上平台房间会因镜像状态未保留快照公开的 `hand_counts`，而以阶段公式猜测四家暗手数；遇到快照显示某非本家座位持有第 14 张牌时，公开物料无法闭合为 136 张，Fast EV 将其判为 `material_conservation` 畸形并抛出异常。2026-09-17 的自由对战实测中，该异常触发连续重锚和场次中止，不能把尚未满足发布门的 shape-v2 用作线上可选策略。

平台 v34 的 seq=0/gap 快照已公开四家**牌张数**（不公开牌面身份）。该字段是可合法使用的公共事实，应进入镜像和不可变决策上下文，而非由占位 `Game` 的阶段语义反推。

## What Changes

- 让 `Mirror` 校验、保存并随已确认事件推进平台快照的四家 `hand_counts`；该状态只表示每座当前暗手张数，绝不保存或推断对手牌面身份。
- 让 `Mirror.build_game()` 将带来源/完整性语义的公开张数投影给决策层；`PublicDecisionContext.from_game()` 优先采用这一显式公共投影，缺失时保持现有离线 `Game` 的阶段推导兼容路径。
- 为公开张数缺失、事件无法安全推进或与本家手牌/副露矛盾的状态定义明确边界：不得伪造为零、不得读取对手 `Game.hands`；Fast EV 必须使用已验证的兼容回退并记录原因，完整 teacher/world 构建仍须拒绝未知或不守恒的物料。
- 使 shape-v2 的线上 `material_conservation` 失败可区分为真正畸形与公共计数不可确认；前者继续拒绝，后者不得导致未捕获异常或静默制造动作。
- 补充镜像—上下文—shape-v2 端到端回归、公共信息隔离、事件计数推进和实际平台录制快照重放；实施后形成无秘密的证据清单/manifest，并同步 `PROGRESS.md` 的平台口径和发布状态。

## Capabilities

### New Capabilities
- `platform-public-material-context`: 将平台公开的四家暗手张数从快照安全投影到 Mirror、决策上下文和 Fast EV，并规定不完整/矛盾公共物料的验证与降级边界。

### Modified Capabilities
- `bot-decision-explanations`: 无。现有 requirement 已要求记录实际层级与回退原因；本变更仅为其提供新的具体原因值，不改变日志关联或解释契约。

## Impact

- 主要代码：`mj/platform/mirror.py`、`mj/decision/context.py`、`mj/decision/fast_ev.py`，以及必要的 `mj/decision/root.py`/调用侧回退衔接。
- 主要测试：`tests/test_platform_mirror.py`、`tests/test_bot_ev_discard.py`、`tests/test_bot_ev_root.py`；新增真实录制快照的脱敏回归 fixture 或等价最小化 fixture。
- 线上行为：仅影响显式 opt-in 的 shape-v2/EV 决策上下文；legacy、shape-v1、规则引擎、合法动作真源、平台请求速率/窗口授权和对手隐藏牌边界不变。
- 语义哈希：公开计数及其来源属于决策输入，线上上下文 hash 可能变化；离线原生 `Game` 仍使用既有推导路径，冻结的离线证据须逐项复核而非假定仍有效。
- 不新增平台请求、不修改 API 协议、不读取/写入令牌、对手暗牌身份、真实墙序或完整隐藏回放。
