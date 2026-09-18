# Proposal

## Why

当前对弈、评估、复盘全部依赖 CLI(`mj.platform.*` runners、`logview`、`log_replay`):线上 10 场并发对局只有 stdout 文本可看,本地评估只有 `fair_match` 的一次性统计输出,事后复盘要手工按 gid 找文件。需要一个桌面客户端把「启动对弈 → 实时观战 → 事后回放」整条链路 GUI 化,并打包分发到队内 Windows/macOS 机器,降低非开发同学的使用门槛。

## What Changes

- 新增 Tauri2 + React 桌面客户端(`client/`)与 Python sidecar 服务层(`mj/clientd/`,localhost WS/HTTP,由壳进程 spawn)。
- **本地竞技场**:1 个主位策略 vs 3 个可配置对手位(随机 BOT / legacy BOT / policy),支持批量对局(总局数 N × 并发 X),主位座位与庄家按 fair_match 口径轮转,对局记录为 `seed + 动作序列`(跨机器确定性重放);种子库支持保存/选用(随机或已存种子)。
- **新增随机 BOT 策略**:有胡必胡,反应窗优先级 碰 > 杠 > 吃 > 过,多种吃法随机取一,弃牌随机。
- **线上对战控制**:锦标赛 / 匹配房(match)/ 测试房三模式启动与停止,策略(policy / BOT / policy-v3)与参数(BOT 回退窗口 {永不回退, 10ms, 50ms, 36ms 默认, 任意 ms 手填}、policy-v3 置信度阈值、state-rate 默认 16/s)配置;房间列表(N 场并发)点入单局观战:自家手牌、四家牌河、四家副露、墙长、决策/动作时间线。
- **回放查看器**:本地记录(全知视角,可看四家暗手)与线上 jsonl 记录(自家视角)统一为一套查看器组件;时间线、逐步前进/后退、进度条拖动定位。
- **打包分发**:模型导出 ONNX(BC/PPO/policy-v3 三种 checkpoint)+ 逐动作对拍验证;PyInstaller 三平台制品(win-x64 / macos-arm64 / macos-x64),torch 不进包(onnxruntime 替代),mj_kernels 按平台预编译打进制品并做加载断言;CI 三平台构建矩阵。
- **不改动**既有引擎与平台层行为:clientd 只读复用 `mj.platform` 的 runner/BotClient/Recorder/Mirror 与 `mj.evaluate`、`mj.log_replay`、`mj.decision.profile`。

## Capabilities

### New Capabilities
- `client-app-shell`: 壳与服务生命周期(启动/健康/崩溃恢复/连接状态)、设置页(服务器地址、三模式令牌)、模型管理(.onnx 导入/校验/热切换)。
- `client-local-arena`: 本地批量对局(配置、进度、取消、聚合统计)、主/对手策略与回退预算、座位与庄家轮转、种子库、对局记录格式。
- `client-online-control`: 三模式线上对弈的启动/停止/参数面,房间列表与单局实时观战数据流。
- `client-replay-viewer`: 统一回放查看器(双信息完备度视角、时间线、步进、进度条拖动)。
- `client-packaging`: ONNX 导出与对拍、三平台 PyInstaller 制品、内核加载断言、签名与 CI。

### Modified Capabilities

(无 —— 本变更全部为新增能力;引擎、平台协议、决策层的既有规格与行为不变,clientd 以只读方式消费。)

## Impact

- **新增代码**:`client/`(Tauri 壳 + React 前端)、`mj/clientd/`(sidecar 服务层)、ONNX 导出器与对拍脚本、随机 BOT 策略实现。
- **只读复用**:`mj/platform/{runner,match_runner,tournament_runner,bot_client,recorder,mirror,api}.py`、`mj/evaluate.py`、`mj/log_replay.py`、`mj/decision/profile.py`(经 `choose_shape_v2_action(profile=...)` 注入预算)。
- **依赖**:制品侧新增 onnxruntime;开发侧新增 Node/Tauri 构建链;torch 仅开发机导出时需要,不进分发制品。
- **新产物目录**:`local/arena/<batch_id>/`(批次与单局记录)、`local/seeds/`(种子库);`local/games/` 与 `local/platform.json` 沿用现状,客户端只读消费。
- **CI**:新增三平台打包矩阵;现有 pytest 体系承接服务层与策略层单测。
