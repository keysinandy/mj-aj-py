# 杭州麻将 AI(inspire)

基于 Suphx 范式(启发式 bot 自博弈 BC 冷启动 → MaskablePPO 强化学习)的杭州麻将 AI,对接内部对战平台(`https://10.240.169.190:18080/portal/`)。

- **[PROGRESS.md](PROGRESS.md)** — 权威进度与决策文档:规则口径、番型公式、训练结论、平台协议要点
- **[CLAUDE.md](CLAUDE.md)** — 代码结构、常用命令、引擎约束(给 AI 辅助工具,同样适合人读)

状态请求生命周期已按 gid 分成可升级候选、冻结 logical request、物理
transport attempt 和响应应用阶段。`StateScheduler` 与
`StateFetchCoordinator` 复用同一个 `StateThrottle` 队列；HTTP 重试保留
logical ID、为每个物理 attempt 记录独立 ID，并在 JSONL/验收报告中区分
logical_state_requests、physical_state_attempts、候选替代和发送前取消。
旧日志缺字段时报告保持显式 missing/legacy，不倒造新指标。

当前最优模型:`runs/bc0/best.pt`(BC,对启发式 bot 胜率 21.9%);引擎与平台行为已通过 replay 对账对齐(1125 动作 0 非法)。

离线麻将复盘调试器见 [docs/replay-debugger.md](docs/replay-debugger.md)。它将本地日志、独立服务端时间线和可选执行 trace 编译为只读 JSON 与单文件 React + shadcn/ui 页面；trace 默认关闭，使用 `python3 -m mj.replay_debugger <gid-or-jsonl> --out replay-output/<gid>` 导出。

shape-v2 的公开上下文、积分 EV2、离线 rollout teacher 和 BC provenance 见
[docs/bot-ev-discard.md](docs/bot-ev-discard.md)；在离线/线上闸门全部通过前保持
opt-in，legacy 仍是默认策略。

## 环境

```bash
# Python 3.11;依赖:torch、numpy、stable-baselines3 + sb3-contrib(RL 用)、pytest
pip install torch numpy stable-baselines3 sb3-contrib pytest

# 离线全量测试(无需内网)
python3 -m pytest tests/ -q -p no:cacheprovider
```

## 运行测试房(实弹对弈)

### 1. 配置令牌

门户创建测试房间后会派发 4 个参赛令牌(仅显示一次,立即复制)。写入 `local/platform.json`(已 gitignore,**令牌绝不入库**):

```json
{
  "server": "https://10.240.169.190:18080",
  "match_token": "<门户『我的 AI 身份』签发的绑定全局令牌>",
  "tournament_token": "<门户签发的当前正式锦标赛报名令牌>",
  "tokens": {
    "青龙": "<令牌1>",
    "白虎": "<令牌2>",
    "朱雀": "<令牌3>",
    "玄武": "<令牌4>"
  }
}
```

`match_token` 供自由对战用(门户测试房间 Tab 顶部「我的 AI 身份」区块领取,明文仅显示一次,可轮换);v24 后旧匿名全局令牌调 `/api/match` 会被永久 403,测试房的 4 个 scoped 令牌也不行(400 TOKEN_NOT_SCOPED)。

`tournament_token` 仅供正式锦标赛入口使用；正式锦标赛 runner 会优先使用它，不会把测试房的 `tokens` 当作报名令牌。

### 2. 一键脚本(推荐)

```bash
scripts/run_room.sh          # BC 模型打 1 轮(10 场,~4 分钟)并自动对账
scripts/run_room.sh 5        # 连打 5 轮(50 场,攒平台真实牌谱)
scripts/run_room.sh 5 bot    # 换启发式 bot 策略
```

脚本流程:发现房间 → 4 令牌并发对弈 → 每轮结束自动 `mj.replay` 对账(引擎重放全部动作 + 结算比对,非法即报错)。

### 3. 分步命令

```bash
# ① 探针:验证令牌/房间 config(新房间第一次接入时跑)
python3 -m mj.platform.probe --skip-game

# ② 实弹:BC 策略打满 10 场(跨轮复用;--strategy 可选 policy|bot|random)
python3 -m mj.platform.runner --strategy policy --ckpt runs/bc0/best.pt --games 10

# ③ 对账:平台事件流 → 引擎逐步重放,结算逐项比对
python3 -m mj.replay --room <房间id> --all          # 房间 id 见 ① 的输出

# ④ (可选)原始 /state、/action 交互 dump 到 local/logs/(排查协议问题)
python3 -m mj.platform.runner --strategy random --games 10 --dump
```

### 4. 注意事项(实测口径)

- **房间生命周期**:finished 后空闲 30 分钟即自动回收(令牌失效)——打完一轮想继续就立刻跑下一轮;脚本连打无此问题
- **M=10 并发**:测试房按 config.M 开 10 场并发(同 4 人),客户端每场次独立线程;轮询预算 16 次/秒/用户
- **窗口时序**:碰/吃窗固定走满 1s,不响应=隐式过(无惩罚);吃窗在碰窗结束后开启,客户端自动处理
- **动作 409**:用 seq=0 快照重建后继续；需结合窗口与服务端错误信息归因，不能仅按次数少就忽略。响应丢失时也先重建，不自动重发旧动作
- **跨轮批次重号**:每轮 batch 从 0 重号,`mj.replay` 默认只校验**最新轮**;历史轮复盘走门户 `GET /portal/api/games/{id}/events`(需登录态)
- **正式锦标赛**:使用专用 `mj.platform.tournament_runner` 读取
  `tournament_token`（无正常局数上限、默认 Recorder）；线上验收和回滚门槛见
  [正式锦标赛参与与线上验收](docs/formal-tournament-participation.md)

正式锦标赛启动命令：

```bash
python3 -m mj.platform.tournament_runner \
    --config local/platform.json \
    --strategy bot \
    --bot-evaluator legacyV2 \
    --state-rate 15
```

## 自由对战(自动匹配,攒真实对手数据)

用单个全局令牌经 `POST /api/match` 入席自动匹配房:服务端凑满 4 人(通常是其他队伍的 bot)开局,满员会话 = M=10 场并发 × 每场 8 局(座次逐场重洗,单房 80 手/人)。打完一房自动 re-match 挂机,直至打满 `--games` 场。

### 1. 配置与运行

`local/platform.json` 的 `match_token` 就位后(见上文配置说明):

```bash
# 线上窗口/调度验收：优先显式使用启发式 BOT
python3 -m mj.platform.match_runner --games 10 --strategy bot --state-rate 15

# policy 仅用于专项模型对照（需明确记录 checkpoint）
python3 -m mj.platform.match_runner --games 100 \
    --strategy policy --ckpt runs/ppo4/ckpt_400000.pt --state-rate 15
python3 -m mj.platform.match_runner --strategy policy \
    --ckpt runs/bc0/best.pt --games 10 --state-rate 15       # BC 对照
```

可配置项:

| 参数 | 默认 | 说明 |
|------|------|------|
| `--strategy` | `bot` | 上场策略:`bot`(线上默认启发式)/ `policy`(神经网络)/ `random`(随机合法,仅规则覆盖用) |
| `--ckpt` | `runs/ppo4/ckpt_350000.pt` | policy 策略的 checkpoint,BC(`best.pt`)与 PPO(`ckpt_*.pt`)双格式均可;备选 `runs/bc0/best.pt`、`runs/ppo4/ckpt_400000.pt` |
| `--games` | `10` | 打满场数,**以整房为退出粒度**(不中途弃房——弃房后剩余场次会被服务端代打,污染他人对局) |
| `--config` | `local/platform.json` | 配置文件路径 |
| `--state-rate` | `15` | 每令牌共享的 /state 请求预算（次/秒） |
| `--no-long-poll` | 关 | 兼容旧参数；当前默认即为 SSE + `/state?seq=N` |
| `--no-notify` | 关 | 禁用 SSE，退回普通主动 `/state?seq=N` 轮询 |
| `--dump` | 关 | 原始 /state、/action 报文 dump 到 `local/logs/`(协议排查用) |

> 线上默认策略是 `bot + legacyV2`；`policy` 只用于明确的模型对照或专项实验，`random` 仅用于规则覆盖/故障排查，不作为性能基线。

对局日志与测试房同构:`local/games/<日期>/<user_id>_<gid>.jsonl`,meta 行带 `mode: match` 标记;`mj.logview` / `mj.log_replay` 复盘对账命令不变。

### 2. 日志转训练数据

```bash
python3 -m mj.log2data --mode match --out data/bc_match   # 仅自由对战 → npz
python3 -m mj.log2data --mode test --out data/bc_test     # 仅测试房/存量日志
python3 -m mj.bc_train --data "data/bc_match/shard_*.npz" \
    --init runs/bc0/best.pt --epochs 5 --out runs/bc_ft   # 平台数据微调(--init 双格式)
```

`log2data` 沿用严格过滤(无 illegal/reset 的干净局 + 提交成功的动作);`bc_train --init` 支持从 BC 或 PPO checkpoint 初始化(架构参数随 checkpoint,老 75 平面 ckpt 自动补零迁移)。数据配比/是否按胜负过滤,等数据到手按质量再定(见 PROGRESS.md P4)。

### 3. 注意事项(实测口径)

- **退出粒度**:`--games N` 打满 N 场即止,但总会打完当前房(一房 10 场);想多攒数据把 N 给大点
- **接收模式**:默认使用 `/notify` SSE 唤醒后拉取增量 `/state`，SSE 帧不直接推进游标；`--no-long-poll` 保留为兼容参数，`--no-notify` 退回普通轮询。各场共享每令牌限速
- **状态需求协调**:每个 gid 维护一个 `StateDemand`；增量 watermark 与 `seq=0` 全量快照请求分开合并，增量物理请求始终从本地已应用 cursor 读取，RESYNC、SSE_DELTA、WINDOW_CONFIRM 各自保留完成条件；同一 gid 最多一个物理 state 在途，所有 retry 继续经过共享 StateThrottle（默认 15/s 配置保持不变）
- **错误自愈**:409 MATCH_BUSY/MATCH_LIMIT_REACHED 自动退避重试(10s);房间 finished ~60s 宽限关停后 404 属正常,自动开下一房;崩溃重启后重调 `/api/match` 幂等返回原房(v24)
- **永久错误**:403 PORTAL_BINDING_REQUIRED = 令牌非门户绑定；403 FEATURE_DISABLED = 平台关闭新自由匹配（v29），均不自动重试
- **排行榜曝光**:auto 房整场完整打完会计入门户排行榜(积分榜/胡大牌榜/单场得分榜)
- **满员等待**:入席后不满 4 人会挂等(池里没人的时段);其他队 bot 活跃时段开打效率最高
- **窗口验收**:见 [平台窗口修复与验收](docs/平台窗口修复与验收.md)。日志 `started_at` 为动作调用起点，`ts` 为响应完成后的记录时间；两者均不是服务端实际接受时间
- **分层完整性**:`scripts/window_acceptance.py` 按房输出 `transport_status`、`window_status`、`game_status`；缺少 `round_ended` 只标记 game 层 `protocol_skipped`，日志缺尾才影响对应层的 `partial` 分母。报告同时区分 logical/coalesced/successor/physical/suppressed state demand 与物理 retry
- **吃碰杠机会损失**:日志含 `claim_miss` 记录——规则允许吃/碰/杠但未成功。`chosen` 非空表示策略已选动作却没成；`chosen=null` + `reason=server_timeout_*` 表示 timeout 时策略尚未完成决策；策略已决策为过不记录。判定口径与统计命令见上述验收文档

## 训练与评估(离线)

```bash
python3 -m mj.bc_data --games 600 --workers 8            # 生成 BC 数据
python3 -m mj.bc_train --blocks 2 --width 64             # BC 训练
python3 -m mj.train_ppo --steps 500000 --subproc --threads 8 \
    --init runs/bc0/best.pt --bc-reg 0.5 --out runs/ppo5 # PPO(务必带 --subproc)
python3 -m pytest tests/fancalc_parity.py -q             # 引擎 vs 平台番数对拍(需内网)
```

完整命令与超参见 [CLAUDE.md](CLAUDE.md),训练结论见 [PROGRESS.md](PROGRESS.md)。
