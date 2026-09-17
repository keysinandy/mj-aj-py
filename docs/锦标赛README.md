# 正式锦标赛运行手册

对应 runner:`mj/platform/tournament_runner.py`(由报名令牌和平台权威状态驱动)。
规则口径、平台协议要点见 `PROGRESS.md` P4 节;本文件只记录怎么跑。

## 1. 启动前:配置令牌

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

```bash
# 启发式 bot + legacy 评价器(线上首选;runner 默认 strategy=policy,必须显式指定)
python3 -m mj.platform.tournament_runner --strategy bot --bot-evaluator legacy \
    > local/tournament_$(date +%m%d_%H%M).log 2>&1 &

# 前台跑(Ctrl-C 触发 INTERRUPTED 收尾)
python3 -m mj.platform.tournament_runner --strategy bot --bot-evaluator legacy
```

注意:`strategy=bot` 时 shape-v2 评价器**不可用**(平台镜像上材料守恒硬失败,
见 `PROGRESS.md` shape-v2 节),线上只用 `legacy` / `shape-v1`。

### 参数速查

| 参数 | 默认 | 说明 |
|------|------|------|
| `--strategy` | policy | `bot` / `policy` / `random` / `policy-v3`;线上优先 `bot` |
| `--bot-evaluator` | legacy | `strategy=bot` 时的评价器;线上限 legacy/shape-v1 |
| `--ckpt` | runs/bc0/best.pt | `policy` 策略 checkpoint |
| `--state-rate` | 16/s | 每 token `/state` 主动限速;429 抬升时回退 15 |
| `--dump` | 关 | state/action 原始摘要写入 local/logs/ |
| `--no-recorder` | 关 | **不要用**:正式赛对局日志是唯一可复盘数据源 |
| `--max-games-debug` | - | **仅调试**:提前离赛 |

## 3. 运行中

- **空转期(registering/stage_open)**:worker 以 ≤1s 轮询保持在线,
  开赛时刻(`config.StartAt`)由服务端分桌;期间断轮询 >90s 会被判离线剔除。
- **stdout 重定向有块缓冲**,日志可能滞后数分钟;进程存活与对局进度优先看
  `local/games/<日期>/` 是否有新 `.jsonl`(文件名 = `user_id_<gid>.jsonl`)。
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
