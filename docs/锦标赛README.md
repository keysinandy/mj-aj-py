# 正式锦标赛运行手册

对应 runner:`mj/platform/tournament_runner.py`(由报名令牌和平台权威状态驱动)。
规则口径、平台协议要点见 `PROGRESS.md` P4 节;本文件只记录怎么跑。

## 1. 启动前:环境初始化(裸机可从零开始)

完整启动链,**任何一台没装过任何东西的机器**都从最上层入口进:

```
裸机
 └─ scripts/setup.sh / setup.ps1          ← 保证 Python >= 3.10 存在
     └─ scripts/setup.py --install --venv ← 建隔离环境并分层安装(装后复验)
         └─ scripts/tournament.py --dry-run ← 探活预检 + 打印赛制
             └─ scripts/tournament.py       ← 上场(守护模式)
```

```bash
# macOS/Linux(没装 Python 也会引导安装,参数透传给 setup.py)
scripts/setup.sh --install --venv

# Windows
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 --install --venv
```

### 1.1 引导层(setup.sh / setup.ps1)

探测是**两段式**,带版本号的候选排前面:只有这样才能命中已装的旧版。
`py -3` / `python3` 永远指向最新版,机器上装了 3.14 就会把 3.11 顶掉,所以
它们只能垫底兜底。

每个候选都问一次 `setup.py --interpreter-check`,按退出码分三态:

| 退出码 | 含义 | 引导层动作 |
|--------|------|-----------|
| `0` | ≥ 3.10 且在 Rust 内核支持范围内(≤ 3.13) | 选它,直接跑 setup.py |
| `2` | ≥ 3.10 但超出内核区间(例如只有 3.14) | 跳过,记为降级候选 |
| `1` | 低于 3.10(macOS CLT 自带的 `python3` 常是 3.9) | 跳过 |
| 其他 | 该命令不是可用的 Python(如 Microsoft Store 占位 exe) | 跳过 |

探测顺序:
- Windows:`py -3.11` → `-3.12` → `-3.13` → `-3.10` → `python3.11` → `3.12` →
  `3.13` → `3.10` → `py -3` → `python3` → `python`;
- macOS/Linux:`python3.11` → `python3.12` → `python3.13` → `python3.10` →
  `python3` → `python`。

取第一个 `0`;一个都没有时:

1. **有降级候选(verdict 2)或完全没有 Python → 不再征求同意,直接安装**:
2. 安装路径:
   - Windows 优先 `py install 3.11`(Python installation manager,装进自己的
     目录并注册到启动器,**本会话立即可用,不用重开终端**);
   - 它不可用时回退
     `winget install --id Python.Python.3.11 -e --scope user --source winget`
     ——装完需**重开终端**(新 PATH 只在新会话生效)才能被 `python3.11` 找到,
     故脚本还会兜一下 `%LOCALAPPDATA%\Programs\Python\Python311\python.exe`;
   - mac/Linux 走 `brew install python@3.11`,装完用显式前缀路径
     `/opt/homebrew/bin/python3.11`(Intel 是 `/usr/local/bin/...`)继续执行,
     不必重开终端;
3. 装不上(无 brew / 无 winget / 安装失败)→ **降级**用那个 3.13+ 的解释器
   继续跑,并警告 Rust 内核不可用:锦标赛照跑,shanten/ukeire 走纯 Python,
   决策稍慢;
4. 连降级候选都没有 → 退出 1,打印手动路线(CLT / brew / python.org;
   Windows 提醒勾 "Add python.exe to PATH")。

想关掉自动安装:设 `MJ_SETUP_NO_AUTO_INSTALL=1`(同时会让 setup.py 不自动
安装 Node、不自动删除版本不一致的 `.venv`,只警告),CI 或他人机器上跑检查时
用得上。

**版本判据只在 `setup.py` 的 `MIN_PY` / `RUST_KERNEL_MAX_PY` / `MIN_NODE` 一处
维护**,引导层只读退出码——不要在 `setup.ps1` / `setup.sh` 里再抄一份 `3.13`
或 `22`(Node 由 setup.py 自己管,引导层完全不碰)。

### 1.2 安装层(scripts/setup.py,七步分层,先保命再锦上添花)

| 步 | 内容 | 不满足的后果 |
|----|------|--------------|
| ① | Python ≥ 3.10(`mj/platform` 用 `X \| Y` 联合类型语法) | 硬失败 |
| ② | 锦标赛核心路径 import 冒烟(tournament_runner/bot_client/bot/win/shanten/scoring) | 硬失败 |
| ③ | Rust shanten 内核(`--install` 且有 cargo 时 `pip install -e ./rust`) | 只警告:自动回退纯 Python,决策稍慢 |
| ④ | Node ≥ 22(`client/` 与 `web/replay_debugger/` 的构建工具链) | 只警告,**且不影响退出码**:锦标赛核心路径不依赖 Node;缺 Node 时 `client/` 与 `web_client.*` 起不来 |
| ⑤ | 可选组件:web(numpy+websockets,浏览器客户端 clientd 运行依赖)/ policy(torch + `runs/bc0/best.pt`)/ tests(pytest+numpy)/ onnx(onnx+onnxruntime) | 只警告,不影响 bot 策略;装后复验 import;缺 web 时 `web_client.sh` 会自检报错并给出安装命令 |
| ⑥ | `local/platform.json` 不存在则写模板(server 预填,令牌留空——空值会被明确报错,不会塞占位符) | 提示填令牌 |
| ⑦ | 引擎冒烟 `pytest tests/test_shanten.py tests/test_game.py` | 硬失败(`--skip-tests` 跳过) |

关键事实:**锦标赛默认路径(bot + legacyV2/shape-v1)不依赖 numpy/torch,也不
依赖 Node**——`api.py` 用 urllib 且 `CERT_NONE`(无需证书安装),Rust shanten/weighted
内核缺失时回退纯 Python。裸 venv 零三方包也可上场(2026-09-20 实测:新建 .venv 后
核心检查全通过,dry-run 真实探活平台成功;当时分六层,2026-09-24 加 Node 层后为七层)。

Node 层(`--install` 时自动装;判据 `node >= 22` 是**下界**——已经是 22 以上
就一律不动,不降级):

- **Windows**:`winget install --id OpenJS.NodeJS.22 -e --source winget`。
  必须写**带版本号**的包(winget 的 LTS 通道现在是 24,`OpenJS.NodeJS.LTS`
  不代表"要 22");`--source` 也必须带(存在多个源时 winget 会因歧义静默
  no-op 却返回 0)。该 MSI 装到 `C:\Program Files\nodejs`,会**替换同目录的
  现有 node**,并可能弹 UAC。
- **macOS**:`brew install node@22`。该 formula 是 **keg-only**,装完**不会进
  PATH**,脚本靠 `/opt/homebrew/opt/node@22/bin/node` 兜底探测;**脚本探得到
  不等于你终端里敲得到**,要在 shell 里直接用需
  `brew link --force --overwrite node@22` 或把该目录加进 PATH(该 formula 的
  `deprecation_date` 是 2026-10-28,届时这条命令可能失效)。
- 装完一律**重探判定成败,不看安装器退出码**(`setup.ps1` 曾因把安装器输出
  当退出码,把两次成功的安装都判成失败)。winget 改的是系统级 PATH,而本进程
  的 PATH 是启动时的快照,所以装成功也可能探不到——这时脚本会提示**重开终端
  后重跑**,而不是报"装失败"。
- node 由 nvm / fnm / volta 等版本管理器接管时,装完谁在 PATH 前面由它们决定,
  脚本只提示、不代为处理。

`--venv`:创建/复用仓库根 `.venv`、升级其 pip、把 `--install` 委托给
venv 解释器重入本脚本(所有包装进 venv,不污染系统 Python)。

`.venv` 已存在但**其解释器版本与当前解释器不一致**时(例如换成 3.11 后重跑),
会**直接删除 `.venv` 重建**——否则"改用 3.11"永远不会生效,后续都会委托给
那个旧解释器。注意其中的包(如 torch)要重新下载;不想让它动,设
`MJ_SETUP_NO_AUTO_INSTALL=1`。读不到 `.venv/pyvenv.cfg` 时视为"不确定",
保持复用不动。

装好后后续命令一律用 `.venv/bin/python`(Windows:
`.venv\Scripts\python.exe`);`scripts/web_client.sh` / `web_client.ps1`
会自动优先选用该解释器。

### 1.3 令牌配置

令牌放在 gitignored 的 `local/platform.json`,`tournament_token` 为正式赛专用
(存在时优先于 `tokens` 映射,单 worker 运行):

```bash
# 只需保证 server 与 tournament_token 两个字段正确
python3 - << 'EOF'
import json
p = "local/platform.json"
cfg = json.load(open(p, encoding="utf-8"))
cfg["tournament_token"] = "<报名令牌>"
json.dump(cfg, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
EOF
```

## 2. 启动命令

### 一键启动(推荐,macOS/Windows 通用)

下文以 `python3` 指代解释器;若 1.2 节用了 `--venv`,请替换为
`.venv/bin/python`(Windows:`.venv\Scripts\python.exe`)。

```bash
# 预检(探活 + 打印 StartAt/YCBK/赛制,不起 runner)
python3 scripts/tournament.py --dry-run

# 启动:默认 bot + legacy,守护模式(崩溃后探活房间再退避重启,上限 5 次),
# 输出实时 tee 到 local/tournament_<评价器>_<时间戳>.log
python3 scripts/tournament.py
```

启动器(`scripts/tournament.py`)只做包装:护栏(`--no-recorder` 需
`--accept-no-recorder` 解锁;bot+shape-v2 需 `--accept-shape-v2` 解锁)、
Ctrl-C 转发(INTERRUPTED 收尾)、意外退出后先 `api.me()`/`api.tournament()`
探活(瞬时 404 TOURNAMENT_GONE 视为房间仍在,与 bot_client 同口径;
TOURNAMENT_NOT_FOUND/终态/鉴权失败不重启)。开赛前 registering/stage_open
的 1s 轮询仍由 runner 本体负责。

### 直接跑 runner(原方式)

```bash
# 启发式 bot + legacyV2(线上首选;legacy 是兼容 v2 别名;legacy-v1 可显式回滚)
python3 -m mj.platform.tournament_runner --strategy bot --bot-evaluator legacy \
    > local/tournament_$(date +%m%d_%H%M).log 2>&1 &

# 前台跑(Ctrl-C 触发 INTERRUPTED 收尾)
python3 -m mj.platform.tournament_runner --strategy bot --bot-evaluator legacy
```

注意:`strategy=bot` 时 shape-v2 评价器**不可用**(平台镜像上材料守恒硬失败,
见 `PROGRESS.md` shape-v2 节),线上使用默认 `legacyV2`(兼容别名 `legacy`)
或 `shape-v1`；U2/KONG continuation 单次不完整时该窗口安全回退冻结 v1。

### 参数速查

| 参数 | 默认 | 说明 |
|------|------|------|
| `--strategy` | policy | `bot` / `policy` / `random` / `policy-v3`;线上优先 `bot` |
| `--bot-evaluator` | legacyV2 | `strategy=bot` 时的评价器；`legacy` 映射到 v2，`legacy-v1` 是冻结回滚 |
| `--ckpt` | runs/bc0/best.pt | `policy` 策略 checkpoint |
| `--state-rate` | 16/s | 每 token `/state` 主动限速;429 抬升时回退 15 |
| `--dump` | 关 | state/action 原始摘要写入 local/logs/ |
| `--no-recorder` | 关 | **不要用**:正式赛对局日志是唯一可复盘数据源 |
| `--max-games-debug` | - | **仅调试**:提前离赛 |

## 3. 运行中

- **空转期(registering/stage_open)**:worker 以 ≤1s 轮询保持在线,
  开赛时刻(`config.StartAt`)由服务端分桌;期间断轮询 >90s 会被判离线剔除。
- **stdout 重定向有块缓冲**(直接跑 runner 的场景),日志可能滞后数分钟;
  一键启动 `scripts/tournament.py` 已用 `PYTHONUNBUFFERED=1` + 实时 tee 修掉。
  进程存活与对局进度优先看 `local/games/<日期>/` 是否有新 `.jsonl`
  (文件名 = `user_id_<gid>.jsonl`;一键启动还会周期打印计数)。
- **瞬时 404 `TOURNAMENT_GONE`**(v35 具名:与 `TOURNAMENT_NOT_FOUND`
  共用 404,**只能按 body 的 `code` 判型**):房仍在、只是 2s 内没等到房
  actor 回执(房忙/库慢,开赛与结算瞬间最常见)。registering 期实测会偶发
  (2026-09-17)。worker 会退避重试最多 12 次
  (`FORMAL_TOURNAMENT_GONE_RETRY_MAX`,累计 ~80s);持续 404 才按
  PROTOCOL_FATAL 退出。**register/ready 提交同样按此判型**:
  `FORMAL_ATTEND_GONE_RETRY_MAX=12` 次内释放占位、下一轮轮询(≈1s)重投
  (幂等,重复提交安全),避免开赛瞬间一次 GONE 就丢席位被服务端代打。
  worker 意外退出后先用令牌查一次
  `api.me()` / `api.tournament(tid)` 确认房间状态,房间还在就重新执行
  启动命令——报名状态在服务端,重启无副作用。

```bash
ps aux | grep tournament_runner | grep -v grep   # 进程是否存活
ls -lt local/games/$(date +%Y%m%d)/ | head       # 对局进度
```

## 4. 赛后

```bash
# 时间线复盘 / 动作窗口
python3 -m mj.logview <gid> --windows

# 自记日志对账(合法集 + 胡结算 + 积分)
python3 -m mj.log_replay <gid>

# 窗口可靠性发布门（0=通过、1=重放/合法性失败、2=可靠性失败）
python3 scripts/window_acceptance.py <tournamentId> \
  --scope fresh_acceptance --commit "$(git rev-parse HEAD)" --gate \
  --out local/acceptance/<tournamentId>_report.json
```

正式赛无免认证事件流(锦标赛 id 走 test-rooms 端点一律 404),
复盘以自记日志为准;`--replay-trace` 可在跑时启用本地 replay trace 侧车。
