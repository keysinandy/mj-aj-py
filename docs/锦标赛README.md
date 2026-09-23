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
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 -Install -Venv
```

### 1.1 引导层(setup.sh / setup.ps1)

- 依次探测 `python3.11`/`python3.10`/`python3`/`python`(Windows 先试
  `py -3` 启动器),取第一个版本 ≥ 3.10 的——macOS CLT 自带 `python3`
  常是 3.9,版本不符视同没有;
- 都没有:交互模式征求同意后自动安装(mac 走 `brew install python@3.11`,
  Windows 走 `winget install --id Python.Python.3.11 -e --scope user`);
  非交互/无包管理器则打印手动路线(CLT / brew / python.org,
  Windows 提醒勾 "Add python.exe to PATH");
- winget 装完需**重开终端**(新 PATH 只在新会话生效)再跑一次脚本;
  brew 装完直接用显式前缀路径继续执行。

### 1.2 安装层(scripts/setup.py,六步分层,先保命再锦上添花)

| 步 | 内容 | 不满足的后果 |
|----|------|--------------|
| ① | Python ≥ 3.10(`mj/platform` 用 `X \| Y` 联合类型语法) | 硬失败 |
| ② | 锦标赛核心路径 import 冒烟(tournament_runner/bot_client/bot/win/shanten/scoring) | 硬失败 |
| ③ | Rust shanten 内核(`--install` 且有 cargo 时 `pip install -e ./rust`) | 只警告:自动回退纯 Python,决策稍慢 |
| ④ | 可选组件:web(numpy+websockets,浏览器客户端 clientd 运行依赖)/ policy(torch + `runs/bc0/best.pt`)/ tests(pytest+numpy)/ onnx(onnx+onnxruntime) | 只警告,不影响 bot 策略;装后复验 import;缺 web 时 `web_client.sh` 会自检报错并给出安装命令 |
| ⑤ | `local/platform.json` 不存在则写模板(server 预填,令牌留空——空值会被明确报错,不会塞占位符) | 提示填令牌 |
| ⑥ | 引擎冒烟 `pytest tests/test_shanten.py tests/test_game.py` | 硬失败(`--skip-tests` 跳过) |

关键事实:**锦标赛默认路径(bot + legacyV2/shape-v1)不依赖 numpy/torch**——
`api.py` 用 urllib 且 `CERT_NONE`(无需证书安装),Rust shanten/weighted
内核缺失时回退纯 Python。裸 venv 零三方包也可上场(2026-09-20 实测:新建 .venv 后
核心检查 6/6 通过,dry-run 真实探活平台成功)。

`--venv`:创建/复用仓库根 `.venv`、升级其 pip、把 `--install` 委托给
venv 解释器重入本脚本(所有包装进 venv,不污染系统 Python)。

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
- **瞬时 404 `TOURNAMENT_GONE`**:registering 期服务端会偶发瞬时 404
  (房间实际仍在,2026-09-17 实测)。worker 会退避重试最多 12 次
  (`FORMAL_TOURNAMENT_GONE_RETRY_MAX`,累计 ~80s);持续 404 才按
  PROTOCOL_FATAL 退出。worker 意外退出后先用令牌查一次
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

# 窗口验收归因(弱键决策/确认预算/409 分布)
python3 scripts/window_acceptance.py <roomId>
```

正式赛无免认证事件流(锦标赛 id 走 test-rooms 端点一律 404),
复盘以自记日志为准;`--replay-trace` 可在跑时启用本地 replay trace 侧车。
