# 正式锦标赛参与与线上验收

正式赛入口只接受本地 gitignored 配置中的报名令牌，不创建赛事、不代替门户报名，
也不把令牌作为命令行参数传入。没有明确的赛事授权和可用报名令牌时，只运行本地
聚焦测试，不执行下面的线上步骤。

## 启动

最小配置可以只有一个 token：

```json
{
  "server": "https://<platform>",
  "tokens": {"main": "<registration-token>"}
}
```

标准命令没有正常局数上限，正式运行默认每 token 使用 `--state-rate 15`，并默认
开启 Recorder：

```bash
python3 -m mj.platform.tournament_runner \
  --config local/platform.json \
  --strategy policy \
  --ckpt runs/bc0/best.pt \
  --state-rate 15
```

`--max-games-debug N` 只用于离线/短时诊断；帮助和运行时都会提示“仅调试；正式锦标赛
不要使用，会导致提前离赛”。正式参赛不要传此参数，也不要传测试房 runner 的
`--games`。`--no-recorder` 会明确警告并丢弃不可再生的正式赛证据。

## 凭据门控线上验收清单

每个阶段都保存命令版本、配置 token label、时间、退出码、stdout/stderr（脱敏后）和
`local/games/`、可选 trace/dump 的文件清单。报告中的 token 只能用 label 表示，不能
出现 bearer token 或 Authorization header。

- [ ] 事前确认赛事管理员已授权参赛，报名令牌仅写入 gitignored 本地文件；先运行
      `python3 -m pytest tests/test_tournament_runner.py -q`、编译和 strict validation。
- [ ] 单 token canary：核对 `/api/me` 返回非空 `tournament_id`/`user_id`，rules 中的
      `M`、`Rounds`、`BaseScore`、`YouCaiBiKao` 与 Recorder meta 一致；若 token 未绑定，
      结果必须是 `TOKEN_NOT_BOUND`，不能进入 game worker。
- [ ] 生命周期：核对 `registering → stage_open → running → stage_done`，每个新阶段
      都有一次 register/ready，`TOURNAMENT_STARTED` 只记为竞态；`stage_done` 或空
      `active_games` 期间仍持续认证轮询。
- [ ] 动态 game：核对每次成功轮询的
      `/api/me.active_games ∩ tournament.my_games`，包含迟到的加赛/重赛，不操作外部
      赛事 game；每 token 的多个 game 共用一个 Api/StateThrottle。
- [ ] 终态与安全动作：只接受权威 `finished`/`closed`/`void` 或
      `qualified=false` 淘汰；检查 409/未知 POST 后有 seq=0 重锚且没有旧动作重发，
      `/notify` 只作唤醒而不推进本地 cursor。
- [ ] 证据与脱敏：扫描 stdout/stderr、JSONL、trace、dump 和汇总，确认不含完整 token、
      Authorization 或包含 token 的错误 body；保留 `response_409`、`post_uncertain`、
      stage transitions、game/action counters 和最终 status/stage。
- [ ] 中断演练：在阶段等待或 active game 期间发送 Ctrl-C，确认共享 stop 生效、不再
      创建新 game/提交新动作、Recorder 关闭、已有日志保留，并为受影响 token 返回
      `INTERRUPTED`。
- [ ] 回滚条件：出现认证错误、外部 game、无界 ready/轮询、令牌泄露、重复动作或
      evidence 缺失时立即停止正式 runner，保留现场日志，回到既有测试房/free-match
      入口；不得用“进程退出正常”替代分层证据审查。

线上结果只有在实际请求、状态、动作和日志证据逐项审查后才可标记为通过；本清单本身
不是线上验收结论。
