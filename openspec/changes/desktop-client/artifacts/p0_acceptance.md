# P0 端到端验收清单(待人工在浏览器执行)

> 目标:验证「本地对战批次 → 统计 → 回放(步进/拖动/切座位)→ 种子入库」闭环。
> 前置:开发机装好 `.venv`(WebSocket/onnx 等)与 `client/` 的 `npm install`;无 Rust 也能跑纯 Web 形态(不 launch Tauri)。

## 环境启动

1. 终端 A —— 启动本地服务侧(sidecar):
   ```bash
   .venv\Scripts\python.exe -m mj.clientd --http-port 17320 --ws-port 17321
   ```
   预期:打印 `clientd listening http=17320 ws=17321`;`local/clientd.ports.json` 写入端口。
2. 终端 B —— 启动 Web 前端(纯开发态,直连 WS/HTTP 到 17320/17321):
   ```bash
   cd client; npm run dev
   ```
   打开 `http://localhost:5173`。右上角连接状态应在数秒内显示「已连接」。

## 清单步骤

- [ ] **1. 连接状态**
  - 页面顶部显示「已连接」。
  - 杀终端 A 的 clientd 进程(`Ctrl+C`)→ 数秒内状态变「重连中(1)」(指数退避)。重启 clientd → 自动恢复「已连接」。→ 记录结果。

- [ ] **2. 跑一个本地批次**
  改造:目前「批量跑局」入口未在 UI 暴露(由 CLI 直接驱动)。为完成闭环,先用 CLI 产出一批:
  ```bash
  # 生成 16 局:主位 shape-v2(10ms),对手 随机/legacy/policy
  .venv\Scripts\python.exe -m mj.clientd --http-port 17320 --ws-port 17321  # 若未在跑
  # 在另一终端的 Python REPL 或脚本调用 clientd arena/会话接口(见下注)
  ```
  > 注:arena 执行器(`mj/clientd/arena.py`)与批量会话入口已存在但尚未挂接到 HTTP 会话端点;批量生成可临时用
  > `python -c "from mj.clientd.arena import run_batch; ..."` 或复用 P0 单测 `tests/test_local_arena.py` 产出的 `local/arena/*`。
  - 完成后:`local/arena/<batch_id>/batch.json` 含统计(主位胜率/均分)。

- [ ] **3. 记录浏览**
  - 切到「回放」页 → 「本地批次」列出该批次(`batch_id`,seed0,局数,胜率)。
  - 展开批次 → 看到 `game_*.json` 单局 → 点击「打开」。→ 加载成功进入查看器,无报错。

- [ ] **4. 回放查看器(核心)**
  - 上方「观察座位」P0..P3:本地全知应显示各家暗手,切座位高亮框移动到对应座位。
  - 点「下一步/上一步」:牌面(手牌/牌河/副露/墙数)与右侧时间线条目同步推进/高亮。
  - 拖动进度条到中段:牌面 == 从头顺序推进到该步的结果(墙数等一致),无中间残影。
  - 时间线含缺口标注条目时显示「⚠」(线上形态来自 3.2,本地无缺口属正常)。

- [ ] **5. 种子入库(可选 CLI 侧)**
  - `POST /api/seeds` 保存一批种子,`GET /api/seeds` 可见;批次种子一键入库(2.5 模块)后可在设置/批次中选用重开。

## 判定
以上 1–4 全通过即 P0 达成;5 通过记录为附加。发现问题回填这里并修复后重跑。

## 已知缺口(不影响组件/后端单测,属 UI 面未接线)
- 「批量跑局」与「线上会话」的启动入口尚未挂接 HTTP 会话端点(分支 P1 的 4.1)。
- 线上回放帧端(point 由 3.2 产出)前端「打开」暂按 P1 占位提示。