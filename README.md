# 杭州麻将 AI(inspire)

基于 Suphx 范式(启发式 bot 自博弈 BC 冷启动 → MaskablePPO 强化学习)的杭州麻将 AI,对接内部对战平台(`https://10.240.169.190:18080/portal/`)。

- **[PROGRESS.md](PROGRESS.md)** — 权威进度与决策文档:规则口径、番型公式、训练结论、平台协议要点
- **[CLAUDE.md](CLAUDE.md)** — 代码结构、常用命令、引擎约束(给 AI 辅助工具,同样适合人读)

当前最优模型:`runs/bc0/best.pt`(BC,对启发式 bot 胜率 21.9%);引擎与平台行为已通过 replay 对账对齐(1125 动作 0 非法)。

## 环境

```bash
# Python 3.11;依赖:torch、numpy、stable-baselines3 + sb3-contrib(RL 用)、pytest
pip install torch numpy stable-baselines3 sb3-contrib pytest

# 离线全量测试(138 个,无需内网)
python3 -m pytest tests/ -q
```

## 运行测试房(实弹对弈)

### 1. 配置令牌

门户创建测试房间后会派发 4 个参赛令牌(仅显示一次,立即复制)。写入 `local/platform.json`(已 gitignore,**令牌绝不入库**):

```json
{
  "server": "https://10.240.169.190:18080",
  "tokens": {
    "青龙": "<令牌1>",
    "白虎": "<令牌2>",
    "朱雀": "<令牌3>",
    "玄武": "<令牌4>"
  }
}
```

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
- **偶发 409**:窗口竞态(如他家抢先碰)属正常,客户端自动 seq=0 重建;每轮个位数以内可忽略
- **跨轮批次重号**:每轮 batch 从 0 重号,`mj.replay` 默认只校验**最新轮**;历史轮复盘走门户 `GET /portal/api/games/{id}/events`(需登录态)
- **正式锦标赛**:同一客户端支持多阶段赛制(status 循环/stage_open 确认),`runner` 同款命令,令牌换成报名令牌即可

## 训练与评估(离线)

```bash
python3 -m mj.bc_data --games 600 --workers 8            # 生成 BC 数据
python3 -m mj.bc_train --blocks 2 --width 64             # BC 训练
python3 -m mj.train_ppo --steps 500000 --subproc --threads 8 \
    --init runs/bc0/best.pt --bc-reg 0.5 --out runs/ppo5 # PPO(务必带 --subproc)
python3 -m pytest tests/fancalc_parity.py -q             # 引擎 vs 平台番数对拍(需内网)
```

完整命令与超参见 [CLAUDE.md](CLAUDE.md),训练结论见 [PROGRESS.md](PROGRESS.md)。
