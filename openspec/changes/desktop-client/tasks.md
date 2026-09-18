# Tasks

> 约定:每个任务的验证方式写在任务描述内;`tests/` 下新增测试统一纳入 `python3 -m pytest tests/ -q`;涉及前端的任务以组件测试 + 手动验收清单双轨验证。分期:P0 = 服务层 + 本地竞技场 + 回放;P1 = 线上对战与观战;P2 = ONNX 导出 + 打包分发。

## 1. P0 · 服务层骨架(mj/clientd)

- [x] 1.1 建立 `mj/clientd/` 包骨架与本地服务(标准库 HTTP + websockets WS):健康检查端点、静态错误结构、优雅停机。验证:`tests/test_clientd_service.py` 起真实服务线程,断言健康端点 200、未知路由返回结构化 404、停机后端口释放。
- [x] 1.2 会话管理器:创建/查询/停止三类会话(本地批次、线上模式),会话状态机(running/finished/cancelled/error)与并发会话上限。验证:`tests/test_clientd_sessions.py` 覆盖状态转换、重复停止幂等、异常会话错误信息透出。
- [x] 1.3 jsonl 增量 tail 读取器:按(文件, 行偏移)增量读、新文件发现、文件结束句柄处理。验证:`tests/test_clientd_tail.py` 用 10 个并发追加中的临时文件断言:无丢行、无重复行、乱序完成容忍(先见 events 后见其 req 之类不崩溃)。

## 2. P0 · 本地竞技场

- [x] 2.1 实现随机 BOT(`random_claim_bot`):HU 合法必胡;反应窗优先级 碰 > 明杠/加杠 > 吃(多种吃法随机取一)> 过;弃牌随机;以(局种子, 座位)派生 rng 保证可复现。验证:`tests/test_random_claim_bot.py` 至少覆盖:碰吃并存选碰、必胡、三种吃法随机分布(seed 复现)、全流程一局自博弈跑通无非法动作。
- [x] 2.2 clientd 策略工厂:policy(ckpt)/BOT(评价器 + 回退窗口)/policy-v3(置信度阈值),经 `ProfileSpec` 注入预算;"永不回退" = `time_budget_ms=node_budget=inf`;非法参数(负数/非数值)在入口拒绝。验证:`tests/test_clientd_strategy.py` 断言:预算注入后 decision 记录含 budget 摘要、永不回退配置下无预算回退、非法参数报错且不落会话。
- [x] 2.3 批量执行器:`multiprocessing.Pool`(spawn, worker 跨局复用),第 i 局 seed=seed0+i、主位座位 i%4、庄家 (i//4)%4、对手相对顺序不变;进度事件流(完成数/速率)与取消(局边界停止、已完成局保留)。验证:`tests/test_local_arena.py` 覆盖:X=1 与 X=4 同配置逐动作一致(读两批记录比对动作序列)、N=16 时 (座位,庄家) 16 组合各一局、取消后已完成局可读且统计只计已完成。
- [x] 2.4 对局记录读写:单局 `{seed, dealer, ycbk, base, seats[], actions[], result}` 落 `local/arena/<batch_id>/game_*.json`,批次汇总 `batch.json`(配置、索引、统计)。验证:`tests/test_arena_records.py` 断言:记录在无 torch 环境(monkeypatch 禁 torch import)可加载并逐动作重放一致;汇总统计可由逐局精确重算。
- [x] 2.5 种子库:命名保存/列表/删除/选用,批次种子一键入库;存储于 `local/seeds/`。验证:`tests/test_seed_library.py` 覆盖 CRUD 幂等、重名处理、批次入库后逐条可选用。
- [x] 2.6 聚合统计模块:主位视角胜率/均分/流局/倍率分布,兼容按对手配置分组。验证:`tests/test_arena_stats.py` 用固定小批次(N=16)断言统计 == 逐局求和,与 `mj.evaluate.fair_match` 同口径手工对账一例。

## 3. P0 · 回放帧管线与前端

- [x] 3.1 本地记录 → 帧数组预计算:`Game(seed)` 按记录动作序列 `step()`,逐步快照(四家手牌/牌河/副露/墙/积分/当前行动者)。验证:`tests/test_replay_frames.py` 断言:预计算不触发任何决策重跑、任取三步帧字段与手工推进的 Game 状态一致、记录中非法动作导致显式报错。
- [x] 3.2 线上 jsonl → 帧管线:复用 Mirror 语义(snapshot 锚点 + events 增量),产出自家视角帧;跳段以最近锚点重建并输出缺口标注。验证:`tests/test_online_replay_frames.py` 复用 `tests/fixtures/` 与 `local/games` 形状的样例日志:决策点重建合法集 == decision 记录合法集、他家暗手不出现在帧中、跳段场景产生标注而非报错。
- [x] 3.3 Tauri2 + React 壳骨架(`client/`):sidecar spawn/崩溃重启/连接状态、路由骨架(控制台/房间/回放/设置)。验证:`client/` 组件测试(连接状态机)+ 手动验收:杀掉服务进程后 UI 显示断开并自动恢复。
- [x] 3.4 牌桌组件:四家手牌/牌河/副露/墙长/积分渲染,支持全知(本地)与自家视角(线上)两种数据形状,观战与回放共用。验证:组件测试用固定帧数组快照对比;手动验收:本地回放切换观察座位、线上回放他家暗手区域为未知占位。
- [x] 3.5 时间线组件 + 步进/进度条:记录类型条目(事件/决策/动作/claim_miss)、上一步/下一步、拖动定位。验证:组件测试(帧索引跳转正确性)+ 手动验收:拖动到任意步的牌面 == 顺序步进到该步。
- [x] 3.6 记录浏览器:本地批次(批次→单局)与线上日志(日期→gid)两级浏览与打开。验证:`tests/test_clientd_records_index.py` 覆盖索引与既有目录结构兼容(不迁移不改名)、空目录/缺 meta 容错。
- [ ] 3.7 **P0 端到端验收**:开发机 `python -m mj.clientd` + `npm run dev`,按清单执行:配置主位 BOT(shape-v2, 10ms)+ 对手 随机/legacy/policy,跑 N=16 X=4 批次 → 统计正确 → 任选一局回放(步进/拖动/切座位) → 种子入库并选用重开一局。验收清单落 `openspec/changes/desktop-client/artifacts/p0_acceptance.md`,全部通过后勾选。

## 4. P1 · 线上对战控制与观战

- [ ] 4.1 三模式会话桥:进程内驱动 tournament/match/test runner(复用 `run_tournament`/`run_match`/`run_room`),参数面(策略/ckpt/评价器/回退窗口/置信度阈值/state-rate 默认 16/s);参数默认值与 CLI runner 共享同一常量来源。验证:`tests/test_online_sessions.py` 断言 UI 可达默认值 == CLI argparse 默认值、未显式选策略时拒绝启动、停止走既有中断语义(会话内 runner 的 stop 事件被置位)。
- [ ] 4.2 房间列表流:tail 管线 → 房间摘要(gid/轮次/墙长/状态/最近动作),动态增删。验证:`tests/test_room_feed.py` 用合成 jsonl 流(含新文件出现、终局标记)断言房间集合演进与摘要字段;前端组件测试覆盖列表增删不闪烁(key 稳定)。
- [ ] 4.3 单局观战流:按 gid 订阅帧增量(复用 3.2 管线的流式形态),WS 推送。验证:`tests/test_spectate_stream.py` 断言:订阅/退订不影响源记录写入(对拍运行带/不带订阅的 jsonl 逐行一致)、帧延迟事件时间戳有界(测试用注入时钟)。
- [ ] 4.4 前端对战控制台与房间墙:三模式启动页(参数表单 + 显式策略选择)、房间卡片墙、观战页(牌桌 + 实时时间线)。验证:手动验收清单(测试房真实对局):10 场并发显示 10 卡片、点入观战与记录重建一致、停止会话安全收尾。
- [ ] 4.5 会话 → 回放衔接:房间/会话详情直接打开已结束对局进入回放查看器。验证:`tests/test_clientd_records_index.py` 补充 gid 直开场景;手动验收一键直达。
- [ ] 4.6 **P1 端到端验收**:测试房 4 令牌真实对弈(BOT legacy)全程观战 + 会话后回放;match 单令牌冒烟。验收记录落 `artifacts/p1_acceptance.md`。

## 5. P2 · ONNX 导出与对拍

- [x] 5.1 导出器 `mj/tools/export_onnx.py`:BC(75 平面)/PPO(91 平面, oracle 置零)/policy-v3 value model 三源 → 单文件 onnx + 契约元数据(平面数/动作维 109)。验证:`tests/test_export_onnx.py` 三源各导出一份并断言元数据;缺 checkpoint/坏 checkpoint 明确报错。
- [x] 5.2 对拍门禁:固定观测样本 + 随机自博弈真实决策点,断言 torch 与 onnx 逐动作一致;产出对拍报告。验证:`tests/test_onnx_parity.py` 通过;人为注入一个坏权重文件断言对拍能拦截(负向用例)。
- [x] 5.3 `onnx_policy_player` + `PolicyV3Runtime` onnx 模型注入:掩码/argmax 在 numpy 侧,契约校验拒载平面数不符模型。验证:`tests/test_onnx_player.py` 断言:onnx 路径与 torch `policy_player` 在同一批局上逐动作一致(开发机双跑)、契约不符模型加载被拒、torch 不可用环境下策略工厂正常工作。

## 6. P2 · 打包分发

- [ ] 6.1 PyInstaller 打包规格:onedir sidecar(服务 + onnxruntime + numpy + 预编译 mj_kernels + 默认 onnx 模型),torch 排除;启动断言 Rust 内核已加载,缺失显式失败。验证:`tests/test_kernel_assert.py` 模拟内核缺失断言报错而非静默降级;`MJ_KERNELS=python` 下服务拒绝启动(打包形态)。
- [ ] 6.2 Tauri 集成:sidecar spawn 配置、设置页(服务器/三模式令牌,gitignored 路径)、模型管理页(.onnx 导入/校验/切换,新会话生效)。验证:组件测试(设置持久化、模型拒载提示)+ 手动验收:改设置不重启生效、导入坏 onnx 不影响当前选择。
- [ ] 6.3 CI 三平台矩阵(win-x64 / macos-arm64 / macos-x64):统一 Python 版本、统一构建入口、构建后自动冒烟(启动自检 → 2 局本地对局 → 打开回放)。验证:CI 全绿产出三制品;冒烟脚本 `scripts/client_smoke.py` 进仓库并本地可跑。
- [ ] 6.4 分发安全与说明:制品内容审计(无令牌/无 local/ 个人数据)、macOS ad-hoc 签名、Gatekeeper 首开说明文档。验证:审计脚本断言制品内无 `local/platform.json`、无令牌字符串;文档随制品交付。
- [ ] 6.5 **P2 端到端验收**:三平台各取一份制品在干净机器(无 Python 开发环境)执行冒烟:启动自检(内核断言)→ 本地 4 局批次 → 回放拖动 → 导入新 onnx 模型生效。验收记录落 `artifacts/p2_acceptance.md`。

## 7. 收尾

- [ ] 7.1 全量回归:`python3 -m pytest tests/ -q` 全绿(含既有 ~100 用例无回归);`openspec validate desktop-client --strict` 通过。
- [ ] 7.2 文档:README 增补客户端章节(开发机联调方式、构建入口、分发说明);PROGRESS.md 记录客户端架构结论与验收结果。
