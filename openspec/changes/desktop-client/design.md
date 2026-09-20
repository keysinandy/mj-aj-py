# Design

## Context

现有资产(均经线上验证,只读复用):本地引擎 `Game(seed)` 与 `legal_actions()` 单一真源;启发式 BOT(`bot.py choose_action`/`choose_shape_v2_action(profile=...)` 可注入预算);`PolicySpec.time_budget_ms`(默认 36ms)与 `PolicyV3Runtime`(置信度回退链);平台层 `runner`/`match_runner`/`tournament_runner` + `BotClient`/`Recorder`/`Mirror`;离线工具 `logview`/`log_replay`(证明 snapshot+events 足以重建公共状态)。约束:平台对局无服务端种子,线上事件流只含自家摸牌;`local/games/` jsonl 是唯一可复盘数据源;`local/` 已 gitignore;torch 依赖面仅限 `model.py`(policy 推理)。

## Goals / Non-Goals

**Goals:**

- Tauri2+React 壳 + Python sidecar 服务(`mj/clientd/`),本地 WS/HTTP 通信,一条服务协议承载四种能力域。
- 零侵入平台层:线上对弈直接进程内调用既有 runner 体系;观战走"tail jsonl → Mirror-walk"只读路径。
- 确定性本地竞技场:多进程批量、seed0+i 分配、记录 = seed+动作序列(不依赖重跑决策)。
- 无 torch 制品:ONNX 导出器 + 对拍门禁,三平台 PyInstaller 矩阵。

**Non-Goals:**

- 不改引擎、平台协议、决策层的任何行为;不引入第二套合法性判定(回放合法集断言复用 Mirror/log_replay 语义)。
- 不做线上"人类下场"操作(平台是 bot 令牌制,时序窗口为 bot 设计);观战是只读的。
- 不做模型训练/微调 GUI;不做跨设备同步;本地对战不做实时观战(全速跑完 → 回放)。
- 不做公证(Apple Developer ID)与自动更新;内部分发手动升级。

## Decisions

### D1 架构:Tauri 壳 + Python sidecar,而非移植引擎

引擎/协议/策略全在 Python 且 battle-tested(4000 行窗口时序逻辑),移植 TS 会摧毁 `legal_actions()` 单一真源与对拍背书。Tauri 用 sidecar 机制 spawn 打包后的服务进程(`tauri-plugin-shell`),前端仅做 UI。

- 服务层框架:标准库 `http.server` + `websockets` 起点即可(平台层本身零第三方 HTTP 依赖),若前端需要再评估 FastAPI——**决定:先用纯标准库 + `websockets` 单依赖**,避免为 GUI 引入重框架。
- 协议形状:REST(控制面:启动会话/查询列表/种子 CRUD/模型导入)+ WS(数据面:房间流、批次进度流、日志流)。WS 消息为 JSON 记录增量,复用 Recorder 的记录类型做前端时间线模型。

### D2 线上观战:tail jsonl,而非进程内 hook

Recorder 逐行 flush,`log_replay` 已证明 jsonl 足以重建状态。服务层用目录轮询(100~200ms)发现新文件/新行,增量喂给"记录→帧"管线。理由:对 `BotClient` 写路径零侵入(时序逻辑不可冒险);观战与离线回放共用同一条 `记录 → Mirror → 帧` 代码路径,一套渲染两用。备选(进程内回调发布)被否:侵入对弈线程,观测者故障可能拖慢决策。

### D3 本地竞技场:多进程 + seed0+i + 记录动作序列

- 并发 = `multiprocessing.Pool`(spawn),沿用 `bc_data --workers` 成熟模式;worker 配置可序列化(策略名+参数+模型路径),onnx session 懒加载。
- seed 分配:第 i 局 `seed_i = seed0 + i`,与调度无关 → 同配置重跑逐局一致(可测)。
- 记录含动作序列而非仅 seed:shape-v2 带时间预算时决策依赖机器速度,仅存 seed 跨机器重放会分叉;存动作序列则重放 = 纯 `step()` 回放,无决策重跑、跨机器确定、毫秒级。
- 座位轮转:第 i 局主位 `i%4`、庄家 `(i//4)%4`,对手相对顺序不变(fair_match 口径)。
- 随机 BOT:新实现 `random_claim_bot`,优先级 HU > 碰 > 明杠/加杠 > 吃(随机取一)> 过,弃牌随机;用 `Game` 派生 rng 种子(局种子+座位)保证可复现。落在 `mj/clientd/` 内,不改 `bot.py`。

### D4 回退窗口 → ProfileSpec 注入

`choose_shape_v2_action(g, seat, profile=None)` 已有注入口,服务层构造 `ProfileSpec(time_budget_ms=X, node_budget=N)`。**修正(apply 期实证)**:"永不回退"不能直接用 `float('inf')`——`DecisionBudget` 会对 node 预算 `int()`(inf 溢出),`ProfileSpec.fingerprint()` 的 `allow_nan=False` JSON(inf 被拒)。故用**大型有限哨兵**表示不设限:`time_budget_ms=10**9`(约 11.6 天)、`node_budget=10**12`,JSON/int 均安全且等效"永不回退"。预设 {10,50,36}+自由输入;不修改 `bot.py` 与 `profile.py`。

### D5 推理:ONNX 导出器 + 对拍门禁(方案 B)

- 新增开发机工具 `mj/tools/export_onnx.py`:三种来源(BC best.pt 75 平面 / PPO 91 平面 oracle 置零 / policy-v3 value model)→ 单文件 `.onnx`,契约元数据(平面数、动作维 109)写进 ONNX metadata。
- 掩码与 argmax 留 numpy:导出的只是纯张量前向 → 对拍标准干净。
- 对拍脚本:固定观测样本集 + 随机局自博弈生成的真实决策点,断言 torch 与 onnx 逐动作一致;CI 门禁。
- 服务层 `onnx_policy_player` 替代 `policy_player` 用于 policy/policy-v3 策略;`PolicyV3Runtime` 以鸭子类型注入 onnx 模型(它只要求 `predict_game`/`distribution` 接口)。
- 备选(PyInstaller 打包 torch)被否:三平台 ×600MB+ 制品,分发体验差。

### D6 打包:PyInstaller onedir sidecar + 三平台矩阵

- 制品 = Tauri 安装包/目录,内嵌 sidecar(服务 exe/app + onnxruntime + numpy + mj_kernels 预编译 + 默认 .onnx 模型)。
- mj_kernels:按平台各编一份(win .pyd / macos .so×2);服务启动断言内核已加载(`MJ_KERNELS` 探测),缺失显式失败——防静默纯 Python 降级拖慢批量与回放预计算。
- Python 版本三平台统一(建议 3.11);macOS ad-hoc 签名 + README 写 Gatekeeper 首开操作。
- CI:三平台矩阵,统一冒烟(启动自检→跑 2 局本地→回放打开)。
- 模型与制品解耦:模型目录在制品旁,支持导入新 .onnx(不重启服务热切换按"新会话生效"语义实现)。

### D7 回放帧模型:预计算 + 索引

- 本地:打开记录 → `Game(seed)` 按序 `step()` 全程跑一遍,逐步快照成帧数组(手牌/牌河/副露/墙/积分);步进/拖动 = 数组索引,零重算。一局毫秒级。
- 线上:jsonl 记录 → 复用 Mirror 语义增量推进,同样产出帧数组;跳段(v10 协议固有)以最近快照锚点重建并标注缺口。打开一条记录预计算一次,观战模式则是同一管线的"流式版"(帧持续追加)。
- 视角:本地帧保留四家暗手作为全知能力的原始数据,但 UI 默认只渲染当前观察座位手牌,通过本地专用全知开关才渲染其他三家;线上帧只含自家手牌+公共信息,Mirror 结构天然如此,无泄漏路径。

### D8 前端结构与状态

React + TS,Tauri webview。组件树:控制台(对战配置)/ 房间墙 / 牌桌(手牌/牌河/副露渲染,观战与回放共用)/ 时间线(记录类型条目,复用 logview 的渲染语义)/ 记录浏览器 / 设置与模型管理。牌桌采用 DOM + SVG 牌图资源:四方相对座次由 CSS grid 定位,牌面使用本地打包的 `mahjong_graphic` 34 张 SVG 资源缩放/旋转,牌背用轻量 SVG 占位;不引入 Canvas 场景图,保留 DOM 的响应式、无障碍和组件测试能力。全知视角的侧家手牌允许自然换行而不固定裁剪;副露横牌按来源家相对拥有者映射到左/中/右位置,来源缺失时保持无方向猜测。状态管理用轻量方案(zustand 或 redux-toolkit,实现期定);WS 推送进 store,回放帧数组按记录懒加载。UI 文案中文。

## Risks / Trade-offs

- [PyInstaller 三平台 torch 缺失导致的隐蔽 import 路径问题] → 服务层 import 纪律:torch 相关 import 只存在于开发机工具;制品冒烟测试含 policy 策略对局。
- [onnxruntime 与 torch 数值漂移(不同后端浮点)] → 对拍门禁按"逐动作一致"而非 logits 数值一致;若发现边缘漂移,在导出器固化推理精度(如 double 输入)并重新对拍。
- [观战 tail 在 match 会话 10 文件并发追加时丢行/乱序] → tailer 按 (文件 inode/句柄, 行号) 增量读,单文件顺序保证;测试覆盖 10 并发文件的增量解析。
- [多进程批量在 Windows spawn 下的启动开销] → worker 池跨局复用(非每局一进程);进度按局边界上报。
- [policy-v3 的 onnx 注入接口不匹配] → 早期做注入原型验证(`predict_game` 鸭子接口),P0 内完成,失败则降级为"policy-v3 仅限开发机"并更新 spec(显式而非静默)。
- [macOS x64 制品在 M 系上被误用] → 制品命名带架构,README 说明;不做 universal2(torch 已不在,但 onnxruntime 也无 universal2 轮)。
- [客户端与 runner 行为漂移(两套默认值)] → 会话参数默认值从 runner 代码单一来源取数(共享常量模块),测试断言 UI 默认 == CLI 默认。

## Migration Plan

纯新增,无迁移。落地顺序:P0(服务层骨架+本地竞技场+回放)→ P1(线上三模式+房间观战)→ P2(ONNX 导出+打包 CI)。P0/P1 可在开发机直接 `python -m mj.clientd` + `npm run dev` 联调,不依赖打包链路先行。回滚 = 不启动客户端,CLI 工作流完全不受影响。

## Open Questions

- 前端状态管理库(zustand vs redux-toolkit)——实现期定,不影响契约。
- 种子库存储格式(单 JSON vs 每种子一文件)——实现期定,容量小无架构影响。
- 测试房模式是否暴露 4 令牌并发(现 CLI 语义)还是先做单令牌——P1 实现时按工作量定,spec 已覆盖"N 场并发房间列表"两种形态。
