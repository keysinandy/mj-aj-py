# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

杭州麻将 AI:基于 Suphx 范式(启发式 bot 自博弈 BC 冷启动 → MaskablePPO 强化学习)训练麻将 bot,目标接入内部对战平台(`https://10.240.169.190:18080/portal/`)参加锦标赛。

**`PROGRESS.md` 是权威进度与决策文档**——规则口径、番型公式、四轮 PPO 结论、已修复 bug 清单、平台协议要点全部记录在那里,动任何引擎/规则代码前先读对应章节。

## 常用命令

```bash
# 全部单测(约 100 个,几秒到几十秒)
python3 -m pytest tests/ -q

# 单个测试文件 / 单个用例
python3 -m pytest tests/test_shanten.py -q
python3 -m pytest tests/test_game.py -k "test_kong" -q

# BC 数据生成(bot 自博弈 → data/bc/shard_*.npz,多进程)
python3 -m mj.bc_data --games 600 --workers 8

# BC 训练(masked CE + value 头,产出 runs/bc0/best.pt)
python3 -m mj.bc_train --data "data/bc/shard_*.npz" --blocks 2 --width 64

# PPO 训练(务必带 --subproc --threads 8,否则吞吐差 10 倍)
python3 -m mj.train_ppo --steps 500000 --n-envs 8 --n-steps 256 --bs 512 \
  --blocks 2 --width 64 --threads 8 --subproc --lr 1e-4 --target-kl 0.03 \
  --oracle-anneal 0.5 --shape-k 0.02 --ent-coef 0.001 \
  --init runs/bc0/best.pt --bc-reg 0.5 --out runs/ppo4

# 对弈评估(policy vs 3 启发式 bot,座位×庄家公平轮转)
python3 -c "from mj.evaluate import policy_player, fair_match, report_fair; \
  st = fair_match(policy_player('runs/bc0/best.pt'), n=96, seed0=6000); report_fair('eval', st)"

# 启发式 bot 基线自博弈评估(内置 CLI)
python3 -m mj.evaluate 200

# 平台 fan-calc 在线对拍(需内网,手动跑,不进 CI)
python3 -m pytest tests/fancalc_parity.py -q

# 平台对弈(测试房 4 令牌,local/platform.json 为 gitignored 配置)
python3 -m mj.platform.runner --strategy policy --ckpt runs/bc0/best.pt --games 10
# (对局日志默认落 local/games/<日期>/<令牌>_<gid>.jsonl;--no-recorder 关闭)

# 自由对战(单全局令牌自动匹配,meta.mode=match 区分来源)
# 线上测试优先启发式 BOT；必须显式指定（runner 代码默认仍为 policy）
python3 -m mj.platform.match_runner --games 10 --strategy bot --state-rate 15
python3 -m mj.platform.match_runner --games 10 --strategy bot --no-notify  # 关闭 SSE，普通轮询排障

# 对局日志时间线复盘(按 gid 查 local/games/)
python3 -m mj.logview <gid> --types decision,action
python3 -m mj.logview <gid> --windows   # 动作窗口时间线:观测迟到/提交落点/结果

# 自记日志对账:重放断言合法集 + 自家胡结算 + 积分累计
python3 -m mj.log_replay <gid>

# 自记日志 → BC npz 分片(严格过滤:干净局 + 提交成功的动作;--mode match/test 按来源)
python3 -m mj.log2data --root local/games --out data/bc_platform

# 赛后对账:平台事件流 → 引擎重放,合法集断言 + 结算对账(需内网)
python3 -m mj.replay --room <roomId>

# 首跑协议探针(新房间/字段发现时手动跑)
python3 -m mj.platform.probe --skip-game
```

评估口径:96 局噪声约 ±4%,关键结论以 192 局 `fair_match(n=192)` 为准;BC 基线为胜率 21.9%/均分 -0.98。当前最优模型 `runs/bc0/best.pt`(备选 `runs/ppo4/ckpt_350000.pt`)。

## 架构

自博弈训练闭环,各层单向依赖(下游不得反向 import):

```
tiles.py (34 类牌编码,白板=33 财神)
   ↓
win.py (财神百搭回溯分解:和牌/听牌/爆头判定)
   ↓
shanten.py (向听数+进张,记忆化+保守剪枝界;shanten/ukeire 默认走
            Rust 内核 rust/mj_kernels,未装扩展回退纯 Python,MJ_KERNELS=python
            强制回退;对拍验收 scripts/rust_parity.py)
   ↓
game.py (对局状态机:legal_actions()+step(),自博弈友好)
   ↓
scoring.py (v2 番型连乘公式 + 庄家×8 结算)
   ↓
bot.py (两阶段启发式决策,teacher 与 RL 对手)
   ↓
features.py (75 平面×34 + 8 标量 + 16 oracle 平面;109 维动作空间编解码)
   ↓
bc_data.py → bc_train.py → model.py (残差 1D CNN policy/value 双头)
   ↓
rl_env.py (MahjongEnv,单 agent 视角,oracle dropout) → train_ppo.py (MaskablePPO/BCPriorPPO)
   ↓
evaluate.py (fair_match 公平对弈评估)
   ↓
platform/ (P4 平台对接:api/proto/actions/mirror/bot_client/recorder/runner/synth/probe)
   + mj/replay.py (赛后事件流→引擎重放对账)
   + mj/logview.py (对局日志时间线复盘)/ mj/log_replay.py (自记日志
     对账+训练样本重建)/ mj/log2data.py (日志→BC npz,严格过滤)
```

平台对接层要点(实测口径,详见 PROGRESS.md P4 节):事件流只含自家摸牌,他家暗手不可见——`mirror.py` 事件源重建公共状态,决策点用 `Game.__new__` 模式构建引擎;快照含四家牌河/副露/wall_remaining,`apply_snapshot` 全量自愈;测试房 M=10 → 10 场并发,每场次独立工作线程;碰窗 (T,T+1)/吃窗 (T+1,T+2) 固定走满(T=弃牌 ts),吃窗在「响应观测齐或截止(~1.05s)」先到先触发、提交不早于观察后 1.05s;无独立 hu 事件(结算在 round_ended.data);吃碰后偶发 `timeout kind=hu_failed` 跳过弃牌且手牌 +1 漂移(客户端三层防御:`tests/test_hu_failed.py`);停摆期代打回声/窗口超时在批内作废陈旧触发、api 5xx 退避重试、场次异常 ≤3 次重派(`tests/test_stale_trigger.py`);自由对战(match_runner)默认**长轮询**(v11 语义:`/state` 服务端挂起至事件刷新,观测迟到 ~0.6s;SSE 在场会禁用挂起,故长轮询不兼容 SSE,`--no-long-poll` 回滚到 SSE 事件驱动——帧只作唤醒信号不当游标)；同一 `Api`(令牌)的 `/state` 由 16/s 主动限速器统一仲裁，临近窗口的请求 EDF 优先（过期截止仅允许一次追赶拉取），动作与 SSE 不限速；窗口截止锚定服务端事件 ts（观测迟到按真实剩余收缩），碰/吃/弃牌守卫仅在物理来不及提交时放弃；gap 快照中的抓打圈须用 `god.god_discarder_seat` + 牌河/phase/turn 重建 freezer 与剩余 freeze，不能仅用 `catch_play` 布尔值，否则会错误放宽“只弃刚摸牌”并触发 409；对局日志的 `claim_miss` 记录“规则允许吃/碰/杠但未成功”，`chosen` 非空 = 策略已选却未落地，`chosen=null` + `reason=server_timeout_*` = timeout 时策略尚未决策（口径与统计见 `docs/平台窗口修复与验收.md`）；服务端无窗口身份字段，快照首见的 `legacy_unresolved` 窗口在首个权威快照上按弱键 `(round_id, discard_owner, tile, 弃牌家牌河尾位置)` 决策提交（永不升级 authoritative、不计强完备，错误弱键由 409+同环恢复兜底；确认预算截止驱动，详见 PROGRESS.md 2026-09-17 弱键决策节与 `tests/test_window_identity_protocol.py`）。

关键设计约束(改动前必读,均有 PROGRESS.md 或测试背书):

- **`legal_actions()` 是动作合法性的唯一真源**:features 的 `legal_mask`、rl_env 的 action_masks、P4 平台客户端自研判定全部必须与它集合等价(有对拍测试)。
- **动作空间 109 维**:弃牌 0-33 / 过 34 / 吃 35-37 / 碰 38 / 明杠 39 / 暗杠 40-73 / 加杠 74-107 / 胡 108;胡是显式动作,弃胡=选弃牌。
- **oracle 平面段 `[75:91)`**:训练时 Bernoulli dropout 退火(1→0),推理/评估时置零;obs 为 99×34(91 平面 + 8 标量行),BC 与 RL 网络输入同构。
- **花色置换增广方向不对称**:平面取列用 σ⁻¹,掩码与动作用 σ 前向——方向写反会把非法动作洗成合法,`tests/test_bc_pipeline.py` 有语义回归。
- **`Game(you_cai_bi_kao=...)`** 开关贯通 rl_env/evaluate/bc_data/train_ppo(`--you-cai-bi-kao`);平台每场锦标赛配置不同,以 API 返回为准。**Search-teacher distillation 训练口径例外**:`search_teacher_generate`/reference/paired 一律 `--ycbk off`(YCBK 视为永远关闭,只作推理兼容),见 `docs/search-distillation.md` 与 change design §16。
- **checkpoint 只存张量与标量**,加载一律 `torch.load(..., weights_only=True)`;policy_player 兼容 BC(net+state_dict)与 PPO(net+action_net)双格式。
- **shanten 剪枝界必须保守**:下界基准随 locked 数变化(历史 bug #6);"已见"信息只在 `visible` 一处折算成 `4 − visible`(历史 bug #7,双重扣减)。任何 shanten 优化必须配随机手牌差分验证。
- **BN 冻结**:sb3 的 `set_training_mode(True)` 会在更新期切回 train 模式,BCPriorPPO.train() 内已显式冻结;改训练循环时勿破坏。

## 领域规则速记(完整版见 PROGRESS.md 第一节)

白板=财神(百搭,不能被吃碰杠胡);只能自摸无点炮;胡是显式动作(可弃胡,碰后未摸牌禁胡);打出财神触发抓打圈;连庄首局 ×8;最后 10 墙禁杠禁摸;总番 = 1 × 分支 × 2^动作链 × (4白板×2) × (爆头×2)。

## 约定

- 引擎/规则行为的改动必须同步更新 PROGRESS.md 的对应结论与测试;规则口径以平台 `GET /portal/api/guide/version`(当前 v27)+ fan-calc 对拍为准。
- `runs/`(checkpoint)、`data/bc/`(npz 分片)是产物目录,不手改;`local/`(平台配置、`local/games/` 对局日志、dump)已 gitignore,对局日志是正式赛唯一可复盘数据源,不手改不清理。
