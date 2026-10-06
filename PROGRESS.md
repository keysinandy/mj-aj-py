# 杭州麻将 AI 开发进展

> 目标:基于 Suphx 范式(SL 预训练 + 自博弈强化学习)训练杭州麻将 AI,
> 接入内部对战平台(`https://10.240.169.190:18080/portal/`)参加锦标赛。
> 当前阶段:P2 贯通(BC 冷启动 93% top-1,最优 BC 基线现为
> **runs/bc0_legacy60k/best.pt**,6x128/60k legacyV2 自弈,
> fair_match(192,legacyV2)=胜率 25.5%/均分 −0.16,与 teacher 打平;
> **当前最强模型 runs/ppo_league/final.pt**(league 自博弈,vs legacyV2
> +1.00/局),见 2026-10-05 节;旧 runs/bc0 seed 保持兼容);
> P3 四轮 PPO 跑批均未显著超越 BC 基线——BC 先验正则(ppo4)已消除
> 训练崩塌但增益仍在评估噪声内,瓶颈为固定启发式 bot 对手的上限
> (详见 P3 节),下一步靠自博弈对手池或平台真实牌谱;吞吐已修
> 10.5 倍;2026-09-15 在线核验指南更新至 **v34**(服务端
> `updated_at=2026-09-14`;v26 抓打圈豁免方响应、v29 功能开关、v30
> 姓名字段收口、v31 局间 5 秒 `settled`、v32 杠爆重算、v33 杠后补牌
> 决策窗口、v34 今日榜 `last`;v27/v28/v34 为门户面变更);**P4 平台对接已
> 实弹贯通(2026-09-08)**:mj/platform/ 客户端 + replay 对账器,
> BC 模型测试房 10 场实弹全胜率正常、引擎-平台 1125 动作对账 0 非法
> (P1.5 收口);下一步攒平台真实牌谱 + 正式锦标赛实测。
> **有财必拷响**(YouCaiBiKao)引擎开关已实现。

## 2026-10-05 内核 v5 / teacher 口径 / BC0_legacy60k 基线

**内核契约（生成门禁已内置）**
- Rust 内核必须为源码要求 `rust-weighted-two-ply-v5`（`mj/shanten.py`
  `WEIGHTED_TWO_PLY_KERNEL_REQUIRED`）。旧 v3 轮子误加载时 LegacyV2 事务性回退 v1，
  `weighted_kernel_compatible=False`、`degraded=True`，**生成标签即在测回退后策略**。
- `mj/training/teacher_generate.py` 新增 `require_compatible_kernel()` 生成前 fail-loud
  （`scripts/search_teacher_generate.py` 入口强制，`--allow-degraded-kernel` 仅 smoke 逃逸）；
  `generation_runtime_fingerprint()` 在 manifest 记录 `git_commit` + 实际加载内核（版本/
  兼容/degraded/爆头飘牌算子）+ teacher 配置，不再只靠 `teacher_config_hash`。

**Teacher 口径修正（重要）**
- 训练轨迹/标签策略源 = **当前后台已配置的 legacyV2**：`--policy-source heuristic:legacy`
  （→ `choose_action("legacy")` → `DEFAULT_BOT_EVALUATOR='legacyV2'` →
  `LegacyTwoPlyProfile.weighted_online(默认)`）。**不用 `heuristic:shape-v2`**
  （`choose_shape_v2_action`，另一条策略），否则模仿错 teacher。
- 由此：search-teacher 蒸馏线 gen0_v5/gen1_v5（shape-v2 teacher）与 gen0_legacy 均作废；
  纯 BC 蒸馏启发式 teacher 打不过 teacher（reference-regret 改善≠全局强度，paired 全 regression）。
- `docs/minisuphx-training-plan-v2.md` §3 已同步：policy-source=heuristic:legacy，配对基线=heuristic:legacy，
  新旧 shard 不混用。

**Shape Guard 口径裁定（同步至 openspec/specs 主规格）**
- 准入谓词 `_taatsu_class_improved`（`mj/legacy_eval.py:1491`）为 taatsu-class 向量
  **严格字典序**（`standing_shape_signature[2:6]` 的 `left > right`，要求首差异类别严格更优），
  **不等于** design 曾写的"至少改善一个搭子类别"。真实开局 seed 0–2599 仅准入 1 次（seed 1787，
  primary[18]→admitted[6]）；seed 290 为准入负例。legacy discard-cost 路径对 290 准入门，standing-shape 不准入——
  行为口径已切换，交付契约已同步收口。放宽护栏（含保护"弃后签名相等"）属独立产品决策，须另做配对+性能验证。

**Bug 修复：scripts/search_bc_train.py**
- replay 分支引用 `train.seed` 但 `train` 在其后才构造 → `--replay-size>0` 必崩
  （`UnboundLocalError`）。改为 `seed=args.seed`（等价）。

**BC0_legacy60k 基线（新 BC anchor）**
- 数据：`data/bc_legacy60k/`（60k 局 legacyV2 自弈，2400 shard / 79.3M 样本，单 campaign manifest）。
- 训练：`streaming_bc` 6x128 public-v1，CPU 4 epoch（总 ~44h），val_top1 0.9872 / val_discard_top1 0.9417 /
  train_loss 0.042。产物 `runs/bc0_legacy60k/best.pt`。
- 评估：`fair_match(n=192, evaluator=legacyV2)`（1 座模型 vs 3 座 legacyV2 启发式）
  → **胜率 25.5%，均分 −0.16 vs opp +0.05**，基本与 legacyV2 teacher 打平（±4% 噪声内≈0）。
- 对比：v3-era BC 基线 21.9%/−0.98；旧 2x64 search-teacher gen0_legacy 被启发式碾压（−10 分）。
  6x128+60k+正确 teacher 已把模型从被击败拉平到 teacher 水平（BC prior 的上限=teacher）。
- 下一步：以 best.pt 为 BC prior 做 DAgger 分布修复 → 终端分 RL（PPO）以真正**超过** legacyV2。

**P6 终端分 PPO 首次超过 legacyV2（同一日）**
- 环境：`.venv` 补装 `stable-baselines3==2.9.0` + `sb3-contrib==2.9.0`；Windows 不支持
  `--subproc`（SubprocVecEnv 默认 fork 不可用），用 DummyVecEnv（吞吐 ~58fps）。
- run：`train_ppo --steps 50000 --blocks 6 --width 128 --init runs/bc0_legacy60k/best.pt
  --bc-reg 0.5 --shape-k 0 --oracle-anneal 0.5 --ent-coef 0.001 --lr 3e-5 --target-kl 0.03`
  （纯终端分 reward，BC prior KL 锚定）。产物 `runs/ppo_legacy60k/final.pt`。
- 评估 `fair_match(n=384, evaluator=legacyV2)`（1 座模型 vs 3 座 legacyV2）：
  - BC0_legacy60k：胜率 25.26%，均分 −0.078（≈ 与 teacher 打平）
  - **PPO_legacy60k：胜率 26.04%，均分 +0.677 vs opp −0.226（约 +0.90/局正边际）**
- 结论：**终端分 PPO（50k smoke）首次使模型显著高于 legacyV2 teacher**；BC prior 恰为打平基线，
  复现"SL prior → terminal-score RL 超越"的关键路径。下一步按计划 P7 league（更多步 + 对手池）继续提。

**P8 League 自博弈对手池（同一日，当前最强）**
- `train_ppo` 新增 league 对手池：`--opponents "legacy=0.6,bc0=0.2,ppo6=0.1,ppo7=0.1"`
  + `--opponent-ckpt name=path`（历史 PPO/BC checkpoint 即本 agent 旧版本=自博弈），
  `--league-seed` 确定性阵容；另加 `load_ppo_init` 支持从 PPO checkpoint（net+action_net）
  warm-start 续训（此前 `--init` 只认 BC `{state_dict}`）。
- run：`train_ppo --steps 300000 --init runs/ppo_legacy60k/final.pt --opponents ...`，产物
  `runs/ppo_league/final.pt`；explained_variance 0.557、bc_kl 0.003 稳定。
- 评估 `fair_match(n=384, legacyV2)`：
  - PPO6_50k margin +0.903；PPO7_200k（固定对手纯加步）+0.639（未提升）；**PPO8_league +1.000**（当前最优）。
- 结论：league（面向 harder 池）训练出最强模型（vs legacyV2 +1.00/局），相比 P6 仅小幅提高（+0.10，噪声内）
  但无 P7 纯加步的回落，收敛更稳。相对固定 legacyV2 对手的 RL 在 ~50k 已近收益上限，league 是继续提升的正确结构。

## 2026-09-28 legacyV2 marginal structure guard

- 已实现 versioned `MarginalStructureRole`、公开可见 live connectivity、
  shanten-aware slack `(0,2,4,6)` 与 bounded singleton admission；按用户
  要求，在线 `legacyV2` 默认开启 `marginal_structure_guard_enabled=true`，
  `legacyV2-offline` 与 baseline/对照 evaluator 仍显式关闭。
- 899s golden 已通过真实 `choose_discard` 路径进入 two-ply；完整搜索
  选择 3m，保留 9s 的 82 vs 77 诊断、admission、future nodes 与 fallback
  证据。7899s redundancy、隐藏暗牌隔离和 feature-off parity 已覆盖。
- 当前验证：新增 11 项、legacy weighted/profile 48 项、策略审计 17 项通过；
  `openspec validate legacy-v2-marginal-structure-guard --strict` 通过。
  当前仍缺 end-to-end p95/p99、4-bot A/B 与 Stage 1/2 积分门；本次默认切换
  是用户明确的线上发布决定，保留显式 `false` 回滚开关，未将缺失门禁误报为
  已通过。证据见该 change 的 `artifacts/`。

> **2026-09-10 窗口可靠性复审（正在修复与验收）**：下文 2026-09-09
> 的“客户端物理边界”“剩余 409 均为外因”是历史实验判断，不能作为当前
> 代码正确性的结论。独立复现发现同批碰超时会丢吃窗、等待后越过截止仍
> 提交、409 未实际重锚、快照吃窗等待态丢失。旧动作日志 ts 是 HTTP
> 响应完成时间，也不能据此断言服务端接受动作时已过期。当前验证清单与
> 结果见 [平台窗口修复与验收](docs/平台窗口修复与验收.md)。本次已在线
> 核验指南 **v34**：本次从 `GET /portal/api/guide/version` 与
> `GET /portal/api/guide?format=text` 拉取；v26-v33 的规则/协议变化与
> v34 的门户排行榜字段已记录在下方。只读查询 `match_enabled=true`，账号
> 当时无在途对局。
>
> **2026-09-24 指南自检（v35）**：`GET /portal/api/guide/version` 返回
> **v35**（2026-09-23，type=breaking），唯一 delta 是 404
> `TOURNAMENT_GONE` 具名——**契约澄清，wire 零字节变化、规则零变化**，
> 只要求客户端把「404 ⇒ 房已删」改成按 body 的 `code` 判型。本仓三处
> 判型缺口已补齐（详见 P4 节 v35 条目）；各 profile 的 `rules_version`
> 仍为 v34（v35 无规则变化，指纹不随之改写）。
>
> **吃/碰/杠机会损失日志 `claim_miss`（2026-09-10 新增）**：验证“窗口
> 是否真的丢机会”此前只能事后重放合法集推断，现在客户端直接落盘。
> 写入条件：phase ∈ {response_peng, response_chi}、镜像能算出非 pass
> 合法动作，且满足两类之一——① 策略已选动作却未落地（`chosen` 非空：
> `action_rejected`/`action_uncertain`/`decision_boundary_resync`/
> `window_already_attempted`/碰窗精确截止/吃窗等待后关闭）；② 服务端
> timeout 时策略尚未完成决策（`chosen=null` + `server_timeout_peng|chi`）。
> 无合法动作自动过、策略明确 pass、镜像张数漂移不可评估均不记录。
> 注意 `claim_miss` ≠ 策略本来一定会执行；统计漏窗损耗须按 `reason`
> 与 `client_decision` 分开看。字段/口径/统计命令见
> [平台窗口修复与验收](docs/平台窗口修复与验收.md)。
> 线上验证（房 `a_59753d3945aa`，默认 12.5/s，10/10 局）：865 动作 0 失败、
> 0 个 409、0 次 post_uncertain；`claim_miss=25`（chi/peng timeout 未决策
> 23+1、peng 决策临界重锚 1），23 条 chi 全部回放确认为“窗口内确无本地
> 决策”。验证后补入窗口决策跟踪（`_window_decisions`）：已本地决策为过
> 的窗口不再计入 claim_miss，碰窗也不计 auto_played（`tests/test_claim_miss.py`）。
> **吃窗丢窗修复（2026-09-10）**：定位到 41/106 未及决策的机制——增量
> `/state` 被服务端挂起 ~0.8s，而带窗口截止的 `seq=0` 快照请求走 EDF
> （队列 p50 42ms/端到端 63ms）；旧路径在干等期用增量轮询把 1s 吃窗耗光，
> 41/41 窗口从未发出确认请求。改为”无碰/杠可做即抓权威快照”（含提前量
> 0.25s、最小间隔 0.12s、每窗上限 8 次，截止不可评估则退回原语义）。
> 离线仿真按实测延迟复现失败并验证修复（T+1.11 提交）；线上复测待做。
>
> **2026-09-24 正式赛吃窗回归修复**：正式赛日志出现 5 条
> `chosen=null + server_timeout_chi`。共同链路为：弃牌事件 `ts` 只有整秒，
> 首次据此估算的吃窗截止最多偏早近 1 秒；随后 `response_peng` 权威快照已
> 给出精确碰窗截止，可推导真实吃窗截止，但确认状态预算合并仍取
> `min(旧估算, 新推导)`，导致在碰窗尚未结束时即
> `confirmation_retry_exhausted`，策略从未被调用。现改为给截止证据分级：
> `response_chi` 权威截止 > `response_peng` 权威截止加一个吃窗 > 整秒弃牌
> 估算；同窗重建时允许更高等级证据替换较早粗估，只有同等级才保守取最小。
> 同时跨相位确认日志不再把 `response_peng.window_deadline_ms` 伪记为吃窗
> `exact_deadline_at`。专项测试覆盖精确截止升级及跨相位日志口径。
>
> **线上可靠性发布门（2026-09-24）**：`scripts/window_acceptance.py --gate`
> 将既有归因报告升级为可自动化阻断的发布门；仅 `--scope fresh_acceptance`
> 且提供冻结 commit 时有效。默认要求未决策服务端超时、确认预算耗尽、动作
> 拒绝/不确定、镜像漂移、窗口 409、重复 POST、canonical client loss 与
> hard-fail 全为 0，重放/合法集错误返回 1、可靠性门失败返回 2。日志发现
> 同时支持自由对战与 `tournament_<tid>_b*_t*.jsonl` 正式赛命名。完整命令、
> 指标口径与失败归因见 `docs/平台窗口修复与验收.md`。

> **弱键决策（window-snapshot-identity-decision，2026-09-17）**：服务端
> 不部署窗口身份字段（guide v34 全字段普查零命中，见
> `openspec/changes/window-identity-protocol/artifacts/`）。线上快照首见
> 的 `legacy_unresolved` 窗口全部走同一条损失链：确认循环烧完 8 次拉取
> 预算后 `identity_confirmation_budget_exhausted`，策略从未被调用（一天
> 6 个合规窗、5 个真损失）。新契约把“决策授权”与“证据归因”拆开：
> ① 快照首见、无协议身份的 legacy 窗口在首个权威快照上即可用弱键
> `(round_id, discard_owner, tile, 弃牌家牌河尾位置)` 决策提交（
> `_resolve_window_confirm` UNKNOWN→weak_key_open；`_act_window` 快照授权
> 门放行 legacy key）——弱窗永不升级为 authoritative、不计强完备；错误
> 弱键提交由服务端 409 + 既有同环恢复兜底。② 确认预算改为截止驱动：
> 剩余窗口时间 < 一次确认往返 + decide+POST 余量即停止排确认拉取；连
> 决策余量都没有时仍落 `identity_confirmation_budget_exhausted` 记录。
> ③ 弱键去重 epoch 内用牌河尾位置（fallback 额外带副露计数——认领会
> 弹牌河,不带则同牌重弃会撞键）；跨重锚仅“round/owner/tile 匹配 + 牌河
> 尾位置与副露计数均一致”才保守携带。④ `window_confirm_weak_open` /
> `weak_key_decisions` 计数与 decision/action/claim_miss 上的
> `identity_status=legacy_unresolved` 使弱键结果可单独归因，验收分母不变。
> 测试：`tests/test_window_identity_protocol.py` 扩 5 例（快照首见 chi/peng
> 决策提交、409 恢复不重发、同牌重弃不撞键 + 跨重锚携带边界、近截止停
> 确认改决策/预算记录）。**线上验证（2026-09-17 测试房 `t_772639fd4c27`，
> 4 令牌×10 局 legacy/16/s）**：`identity_unknown` 确认拉取 59→0、
> `identity_confirmation_budget_exhausted` 7→0、legacy `server_timeout_*`
> miss 7→0;唯一出现的快照首见弱键窗口(青龙 worker,gid r1_b5,
> response_peng)弱键授权 → 决策 → POST 成功并获服务端 `peng` 回声。
> 既有 peng 409 关窗竞速(authoritative 身份、决策余量 100-500ms、
> 三家 timeout 同秒)两房同量(10 vs 9),与弱键无关;其余:14 条
> claim_miss 全为 response_peng 且牌均无人认领,服务器弃牌窗代打
> 9 次/40 局。证据:`openspec/changes/
> window-snapshot-identity-decision/artifacts/online_validation_20260917.md`。

> **平台共用层代码审查修复(2026-09-17)**:锦标赛/自由对战/测试房共用的
> bot_client 层 review 后修九项——① `/state` 客户端默认限速维持 **16/s**
> (用户决策;沿革 12.5/s(09-09)→15/s→16/s,此前未同步文档,**当前
> 限速结论以本条为准**。16/s 与服务端墙同值,429 级联风险与退避历史见
> 09-08/09-09 记录,可 `--state-rate` 回调);② 跨重锚弱键携带补齐副露
> 计数比对(漏看 claim 事件后陈旧 fallback 撞上同长牌河,不再把真实
> 新窗当作已尝试静默跳过——该方向无 409 兜底);③ `_act_window` 快照
> 授权门补 last_discard-vs-pending 交叉检查(已结算旧窗的快照回到确认
> 环,不再直接 POST 必 409 动作、把 miss 归因洗成“服务端拒绝”);
> ④ 弱键授权计数收口到 `_set_window_authorization` 单点(`_act_window`
> 直达腿不再漏计),`window_confirm_open` 权威桶不再混入弱开启;⑤
> recorder 弱键授权记 `outcome=weak_key_open`,不再伪装
> `authoritative_open`(对局日志是正式赛唯一复盘数据源;logview/验收
> 可区分,`scripts/window_acceptance.py` 两口径均认且弱开启不单独触发
> DECISION 归因);⑥ 弱键确认记账挂实际授权的 key(`_record_window_confirm`
> 的 `window_key` 覆盖,同环第二实例不再错账到旧 window_id);⑦ 快照
> 暴露判定统一为 `_window_snapshot_exposure` 共享梯(确认 MATCH 腿与
> 弱键观察腿,新增守卫自动双边生效),`WEAK_DECIDE_MARGIN` 改引用
> `DEADLINE_MARGIN` 锁同步;⑧ 弱键计数进 `TournamentResult.diagnostics`
> (`window_confirm_weak_open`/`weak_key_decisions`,锦标赛汇总单独可
> 归因);⑨ probe 局列表解析对未知包装响亮报错(不再静默空列表放行
> 协议漂移)。测试:`tests/test_window_identity_protocol.py` 扩 4 例
> (携带副露钉、last_discard 门、recorder 弱口径、弱键记账身份)+
> `tests/test_tournament_runner.py` 1 例(诊断计数透出)。
> ⑩ **正式赛轮询 404 容错(2026-09-17 19:23 实弹,「1024杭麻竞技二测」
> `t_65d538e905c5`)**:registering 期 `/api/tournaments/{tid}` 间歇性
> 404 `TOURNAMENT_GONE`(直连抽查 12/12 成功,但 runner 轮询流里偶发,
> 前两次运行均被单次 404 以 PROTOCOL_FATAL 错杀)。修复:轮询循环对
> 404+TOURNAMENT_GONE 有界退避重试(`FORMAL_TOURNAMENT_GONE_RETRY_MAX=12`,
> 累计 ~80s,成功即清零;持续 404 仍按真离赛致命)。
> `tests/test_tournament_runner.py` 扩 2 例(瞬时 404 恢复 FINISHED/
> 持续 404 仍 PROTOCOL_FATAL)。运行手册:`docs/锦标赛README.md`。

> **番型感知尝试(2026-09-17 正式赛复盘产物,已回退)**:正式赛
> `t_65d538e905c5` 147 局实测——胡率 22.4%(噪声内),**番型质量是主要
> 差距**:爆头 3 vs 他家 24、七对 0 vs 4、财飘 0 vs 2,总番 37 vs 149,
> 场均 -28.5;我方 36 次摸到财神全部持留,但 14 次持白胡全是 fan=1
> 平胡(白折进标准路径当百搭,六对+财神的爆头形才 ×2)。根因判断:
> 弃牌排序纯按(向听, 进张),同向听下进张略多的平胡路径系统性挤出
> 番大路径。曾实现 `chiitoi_shanten` 公开七对分支向听 +
> `bot._fan_path_weight`(同向听候选进张 ×2/持白 ×4,向听仍硬约束)+
> `_claim_gain_scale`(持白七对形等向吃碰门槛翻倍)。
> **A/B(`local/ab_fan_aware.py`,基线=git HEAD 快照,2v2 混战 24 组合
> 轮转)**:1920 局 +0.72/局、7680 局 +0.31/局(95% CI [-0.28,
> +0.88])、30720 局(独立种子) **-0.03/局**(95% CI [-0.32,
> +0.26])——合并 38400 局约 +0.04/局,**对旧 bot 净收益≈0**,早期
> 正值判为噪声。高倍率分布几乎不动(×2 2193 vs 2157、×4 85 vs
> 80/30720 局)。定性:旧 bot 同样持留财神、对手等强时番大路径的
> 进张劣势抵消番数优势,"对手更强才有收益"的假设未经验证。
> **代码已整体回退至 HEAD**(bot.py/shanten.py/测试/gitignore;A/B
> 脚本与结果 JSON 在 local/,gitignored)。差距根因仍成立,闭合需
> 另寻杠杆:向听严格下降副露 vs 持白七对路径的权衡(当前一律接受)、
> 杠 EV 的番链权重、或 search-distillation 换更强基线。

> **爆头/财飘感知弃档(2026-09-18,openspec baotou-piao-aware-discard)**:
> 与上条番型感知(全量进张番型加权)不同杠杆——只换「持财神+听牌」
> 触发态的进度信号。动机:YCBK 门禁下持财神非爆头的普通胡牌张一张
> 都提交不了(引擎 `_can_hu`),爆头听(×2 起)才是真实进展;
> **验收口径定为 YCBK 关——用户口径 2026-09-18:当前项目不考虑
> YCBK 开的场景**(YCBK 开 A/B +0.97/局 CI[+0.51,+1.45] 仅作参考
> 数据存档,不作验收依据)。变更(bot.py,`legal_actions` 门禁零
> 改动):①`choose_discard` 触发态叠加爆头档:tier0=弃后爆头听(听
> 任意)整体优先(档内财神保护不变),同档比较
> `1.5×baotou_ukeire + 当前规则允许的自摸胡牌进张`,不将爆头进张作为普通胡牌进张
> 之前的独立排序层;shape-aware 只在组合进度打平后比较结构。不持财神/未听牌排序
> 逐候选不变。回放 `u_9812ba08fe2f_a_ac8ded7821b2_r1_b9_t0` round1：弃2筒为
> `1.5×7+9=19.5`，弃9条为 `1.5×5+21=28.5`，故组合进度选择弃9条。候选诊断改为逐行
> 从对应弃后手牌计算 shanten/当前结构听口/合法自摸胡牌听口，并记录公式版本与组合分；
> 修复原诊断循环误复用末个候选手牌的问题。②**自适应收手 X/Y/Z**(初版「无条件生效」翻案,
> 无条件 30720 局 −0.39/局 CI[−0.68,−0.10]):进入推进态后 X 轮未
> 转化 / 任意对手副露 ≥ Y / 活墙可摸 < Z 任一触发即回速度线(legacy
> 键 + 不弃胡);轮/副露只增、活墙只减 → 收手为吸收态,轮数按
> Game 实例记账(镜像局 X 不累积,Y/Z 仍生效)。**网格扫描定参
> (local/ab_baotou_sweep.py,粗筛 20 配置 × 3840 + 精跑 6 配置 ×
> 30720,YCBK 关,配对结算分)**:X 是唯一有效杠杆(X=0 −0.03 /
> X=2 **+0.02 CI[−0.26,+0.30]** / X=4 −0.10 / X=99 −0.39——Y/Z 在
> X=2 下不约束,粗筛各 X 下 Y=2 一致优于 Y=99);默认
> **X=2(`BAOTOU_PUSH_MAX_ROUNDS`)、Y=2、Z=16**。补充网格
> (Z=16 × Y∈{1,2,3} × X∈{2,3,4,5},各 30720 局)确认:X=2 头名
> (Y=2/3 并列 +0.02,Y=1 −0.02 过于胆小);X≥3 时最优 Y 反而降到 1
> (早收手补偿久推进,暴露量守恒);最差格 (X=5,Y=3) −0.28
> CI[−0.57,+0.01]——推得久又收得晚两头吃亏。胜率 49.9% vs
> 49.9%、×2 局 2243 vs 2138——**积分与旧 bot 打平,收益是冻结盲
> 修复+墙守卫+高倍率转化,不是场均分**。③墙量守卫
> `PIAO_WALL_GUARD=6`:活墙可摸(live_wall_left,死墙已扣)< 6 直接胡
> (跳过飘与杠期望),`_should_piao` 门 5→6 同常量。补充 HU 窗口优先级：HU 合法时，
> 若存在合法非财神弃牌、保留财神后已听任意牌，且活墙 ≥ 6，则主动过当前 HU 进入
> 下一摸爆头胡；该强推进机会忽略 X/Y/Z 软收手和杠/财飘比较，仍受合法动作集与
> `PIAO_WALL_GUARD` 硬墙门约束，线上记录在 `legacy_detail.reason`。
> ④顺带修复两个 **新旧 bot 共有的冻结盲潜伏 bug**(抓打圈只能弃刚摸牌):choose_discard
> 候选收窄到刚摸牌、_should_piao 仅刚摸财神时才飘——A/B 实弹暴露
> (冻结+暗杠四张时 legacy 键选拆杠第 4 张;冻结弃胡打白飘),线上
> 同样会触发 409。性能:`mj_kernels.baotou_ukeire` Rust 算子,
> `is_baotou_wait` O(1) 等价刻画(持 ≥1 财神且去一财神后恰
> 4−locked 面子;或 locked=0 七对路径 wilds≥单张数+1 且奇偶匹配),
> 触发态决策冷 3.5–5.7ms/暖 0.9ms(纯 Python 定义版 90–220ms);
> 差分 `rust_parity.py` 5000 手 + 剪枝差分(W<3 远牌非爆头进张)。
> **预算只按节点数**(墙钟预算会让同种子轨迹随负载漂移——e2e
> 轨迹分歧实弹暴露,修正后逐局一致);无 Rust 内核整档跳过回退旧
> 排序,`MJ_KERNELS=python` 同口径;跑对局机器需 `pip install -e
> rust/`。测试:test_bot 扩 16 例(tier/预算/无内核/冻结×2/墙守卫×3/
> 收手×5/YCBK 无关)、test_shanten 扩 baotou_ukeire 5 例,全量 767
> 通过。BC/RL 影响:teacher 触发态行为变化 → 之后 bc_data 分布随之
> 变化,既有分片不回刷;历史 PPO run 对手版本不同,跨 run 对比需
> 注明。线上待办:match_runner 冒烟(tasks 4.4)+ 平台层打通 X 轮数
> 记账(镜像每决策重建 Game,X 目前线上不累积)。

> **Piao Search 与 Guaranteed Next-Draw HU(2026-09-29,同一 OpenSpec 变更)**：
> HU 窗口延迟候选改为 candidate-specific policy。弃后为全牌爆头听、且公开未见牌
> `winning_mass == total_unseen` 的 `piao_discard/baotou_next_draw` 候选标记
> `guaranteed_next_draw_hu=true`，即使观察到 X/Y/Z(`rounds/opp_melds/live_wall`)
> 也保留完整 raw value；`PIAO_WALL_GUARD=6` 仍是唯一硬墙门。审计同时记录
> `observed_delay_reasons`、`ignored_delay_reasons`、`raw_value/effective_value`，
> 不再把被忽略的 `opp_melds` 显示成实际归零原因。对 seq351(立即七对 20 分)的
> 三个必爆头候选，本地构造保留约 40 分的真实 next-draw EV，并由
> `hand_multiplier + settle` 自然计入豪华七对，未添加人工 bonus。
> 白板≥2的爆头站立手新增固定弃白 `piao_draw_mask`、`piao_live/piao_ratio` 与
> `floor(live_wall/4)` horizon 快门；首版只检查现有最佳爆头候选，在线
> `max_search_passes` 暂不启用（Mirror 重建尚无会话持久化）。Rust 批量
> `piao_draw_mask/is_baotou_wait` 与 Python 参考实现随机对拍通过；200 样本冷路径
> benchmark 为 p95 0.43ms、p99 0.60ms，低于 p95≤1/p99≤2ms 门。参数网格的本地
> 2 局/配置 smoke 记录了 pass/opportunity/cashout/search-lost/piao-lost、直接胡次数、
> 平均倍率与结算分；由于会话层尚未持久化 `max_search_passes`，34 个配置动作相同，
> 结果仅作校准与不晋级证据。另有 2 局 counterfactual A/B 显示 Guaranteed policy
> 的样本分差为 -12/局，样本量不足且不作线上晋级依据。

## 一、规则定稿(与需求方逐条确认)

### 平台固有规则(指南 v34,2026-09-15 在线拉取;服务端 `updated_at=2026-09-14`;本节同时保留 v26-v34 变更记录)
- 136 张牌:万/筒/条 1-9 各 4 张 + 东南西北中发白各 4 张,无花牌
- **白板 = 财神(百搭)**,可替代任意牌;不能被吃/碰/杠/胡,可主动打出
- **打出财神触发抓打圈**:其余玩家一圈内不能吃碰明杠(仅暗杠与自摸胡),
  出牌只能打刚摸到的牌;打出财神者本人不受限(保留反应权)。快照
  `god.god_discarder_seat` 标出该豁免方;本人受限判据为
  `catch_play && god_discarder_seat != seat`
- **只能自摸,不允许点炮**;禁止抢杠胡
- **胡牌是显式动作(弃胡机制)**:普通摸牌与暗杠/补杠/明杠后的补牌成胡
  都由玩家提交 hu,可弃胡继续打牌(飘/杠链前提);**碰/吃/杠后未摸牌不可胡**
  (刚摸牌门禁);可胡但超时未响应 → 服务端自动胡兜底。v33 起杠后补牌
  不再在 `gangDraw` 同步自动结算，停在 `phase="draw"` 决策窗口
- 连庄:首局即 ×8,流局庄家连庄
- 牌墙:最后 10 墩(20 张)保留不摸;最后 10 墩内禁止杠牌;摸完无人胡则流局
- 吃最多 2 摊(以本人副露 kind==chi 组数计,碰/杠不占名额,每局重置;
  v25 起服务端强校验,第 3 次 chi 409——引擎 `CHOW_LIMIT=2` 自律在先,
  mirror 从快照 melds 重建吃数贯通决策,合规零影响);碰、杠不限次数;
  碰(含明杠)窗口先于吃窗口
- 不存在过胡、不存在振听

### 平台指南 v26-v34 变更记录(2026-09-15 在线拉取)

- **v26(2026-09-08, changed)**:抓打圈内的豁免方是打出财神者本人。
  非财神弃牌时，若豁免方有对应资格，服务端仍固定走满碰窗；只有豁免方
  是出牌者下家时再开放吃窗。豁免方可吃、碰、明杠、补杠并任意出牌，
  `god.god_discarder_seat` 是快照中的公开判据；其余三家规则不变。
- **v27(2026-09-08, added/changed)**:门户积分榜新增 `prev`；`top` 不再
  产生 `rank=0` 的幽灵行。均为门户字段/展示调整。
- **v28(2026-09-09, changed)**:胡大牌榜同番同分时取消“该牌型全史次数”
  排序键，改按胡手时刻倒序再按 `user_id`；`count` 仍是注记。
- **v29(2026-09-09, breaking)**:新增功能开关。自由匹配、自建测试房或测试房
  重开被关闭时返回 `403 FEATURE_DISABLED`，属于永久条件，不应重试；已有
  在途对局和既有房间的允许操作不受影响。新增门户
  `GET /portal/api/features` 供展示层读取开关状态。
- **v30(2026-09-10, changed)**:除管理面正式锦标赛外，他人姓名字段只下发
  AI 昵称，昵称为空返回空串；消费方用 `user_id` 作身份与空值回退。
- **v31(2026-09-11, changed)**:每局结算到下一局发牌固定暂停 5 秒；期间
  快照为上一局终态，`phase="settled"`、`round_no` 仍为刚结束局、
  `waited_seat=-1`，任何动作均为 `409 INVALID_ACTION`。发牌时继续唤醒
  原有轮询并返回新局全量快照，长轮询上限和字段集不变。
- **v32(2026-09-12, changed)**:摸牌后暗杠/补杠时重新计算爆头状态；杠后手牌
  为听任意牌且补牌胡时，番数由普通杠开 `×2` 修正为杠爆 `×4`。历史落库
  分数不回溯，消费服务器 `fan/detail/scores` 的 bot 契约不变。
- **v33(2026-09-13, changed)**:杠后补牌能胡时与普通摸牌同构，客户端可提交
  `hu`、继续杠或弃胡打财神；不响应仍由服务端按出牌超时自动胡兜底，番数
  与分数口径不变。
- **v34(2026-09-14, added)**:门户 `GET /portal/api/leaderboard` 今日榜
  新增 `last` 键；仅 `period=today` 且上榜人数大于 32 时为
  `{rooms,firsts,score,is_me}`，其他情况为 `null`。这是纯门户加法，bot
  玩家 API 行为不变，消费方应忽略未知键并处理 `null`。

### 有财必拷响(YouCaiBiKao,锦标赛可配置项;2026-09-03 自检发现并实现)

指南 §1.6:创建者可配置(config.YouCaiBiKao),**每场锦标赛可能不同**,
bot 必须以 `GET /api/tournaments/me/rules` 返回的 config 为准。
- 语义:**手上有财神时不允许平胡,必须爆头/杠开才能胡**
  (爆头 = 摸前站立手听任意牌;杠开 = 杠后补牌成胡)
- 只门禁 HU 合法性,不改番型计算;爆头/杠开胡的倍率由原公式自然给出
- 引擎实现:`Game(you_cai_bi_kao=True)` 开关(默认关,向后兼容);
  `_can_hu` 加门禁 + `_draw(kong=)` 跟踪杠后补牌;贯通
  rl_env / evaluate / bc_data / train_ppo(`--you-cai-bi-kao` CLI)
- 策略影响(规则推导):财神从"万能进张"变为"需先构爆头/杠开才兑现"
  的资源——持财神非爆头时 HU 不可用,弃胡打牌节奏变化;评估口径下
  ×2 倍率占比显著上升(bot 自博弈 60 局:×2 占 38/56)
- 注意:与"爆头胡计 ×2"是两件事——计番照旧,开关只管"能不能胡"

### 番型倍率(v2 公式重构)

**总番 = 1 × 分支因子 × 2^动作链次数 × (4 白板 ×2) × (爆头 ×2)**,
计算顺序:分支 → 动作链 → 4 白板 → 爆头(最后统一加)。

| 番型 | 说明 | 倍率 |
|---|---|---|
| 平胡 | 普通自摸胡 | ×1 |
| 七对子 | 7 对胡(禁副露;财神可补对;**允许财飘**——v3 修订) | ×2 |
| 豪华七对 | 七对含 N 组手中四张(4 真白板仅两两自配、未补配落单时算 1 组;白板补单不重复计豪华——**v21 修订**) | ×4/×8/×16 |
| 杠开 | 杠后补牌自摸胡(链 1 动作) | ×2 |
| 杠爆 | 爆头状态杠,补牌胡 | ×4 |
| 财飘/双/三财飘 | 连续 1/2/3 次爆头态打出财神再爆头胡 | ×4/×8/×16 |
| 4 个白板 | 胡牌时手留 + 链内飘出的白板 = 4 | ×2 |
| 爆头 | 站立手听任意牌即胡(支持副露;恰 4 白板同样计爆头并与 4 白板叠加——**v21 修订**) | ×2 |

### 七对/爆头 白板口径修正(v21 裁定,2026-09-07;引擎已同步)

两条用户裁定,撤销 2026-09-01 的 fan-calc 旧实测口径:

- **①豪华七对**:4 张白板在其余牌存在落单(白板作百搭补配其他牌
  成对)时**不再同时计 1 组四张**;仅其余牌全自然对、白板两两自配时
  4 白板仍计豪华组。受影响牌形总番减半(如 3 对+4 单+4 白:×8 → ×4)
- **②爆头**:撤销「听牌态正好 4 张白板不视为爆头」旧裁——4 白听
  任意即胡**按爆头计**并与「4 个白板 ×2」叠加(3 对+3 单+4 白 →
  七对×爆头×4 白板 = ×8)
- 引擎改动(2026-09-08):`win.is_chiitoi` 豪华组改为「自然四张 +
  (W==4 且 singles==0)」;`win.is_baotou` 删除 W==4 排除分支
- **注意持 4 白的七对牌形总番多不变、仅归因变化**:3 对+3 单+4 白
  摸任意,旧口径 = 豪华七对×4 × 4 白板 ×2 = ×8(非爆头);新口径 =
  七对 ×2 × 爆头 ×2 × 4 白板 ×2 = ×8——同值不同明细。**对拍以
  明细+倍率双一致为准**(旧引擎在明细上即已与 v21 后的平台不符)
- fan-calc 对拍 22/22 全过(新增 4 用例:恰手留 4 白+爆头 / +杠开 /
  4 白补单七对 / 4 白自配豪华);全局最大 ×512 不受影响(其手留仅
  1 白,两裁定均不触发)

- **动作链**:每个飘/杠动作 ×2,可连续可组合(杠飘链叠加);打出
  非飘非杠的牌(含非爆头态打白板)断链清零。链内飘出白板计入 4 白板。
  链无硬上限,物理上限 杠×3+飘×3 = 6(fan-calc v6 起校验 0-6)。
- **飘的判定**:打出财神后站立手牌仍听任意牌(“继续听任意牌”)——
  自带弃胡语义(打白前的 14 张必成胡)。爆头态摸白板是典型决策点:
  直接胡(×2)vs 弃胡打白飘(×4 起)。
- 最大牌型:**七对分支** 三豪华七对+三财飘+4 白板+爆头 = ×512
  (v3 修订,全局最大);平胡分支上限 ×256(链 6+4 白+爆头)。
- **4 白板口径已定**(v6):fan-calc 工具修复为 手留+链内飘出=4,
  与对局结算同源,和引擎口径一致;`tests/fancalc_parity.py` 20 用例
  (hu/倍率/番型明细/庄闲结算)对拍全过。

### 计分公式
- 得分 = 底分 × 倍率 × (庄家 ×8 / 闲家 ×1),三家分别结算
- 庄家胡:三家闲家各付 底分×倍率×8
- 闲家胡:庄家付 底分×倍率×8,其余两家各付 底分×倍率

### 战略结论(规则推导)
- 无点炮 → 无防守维度(无危险牌概念),核心仍是单人竞速;
  玩家间交互:喂牌、打财神冻结对手一圈、杠耗牌墙
- **弃胡/财飘引入番型-速度权衡**:爆头态摸白板时博倍率 vs 落袋为安,
  爆头态下任何和牌都至少 ×2,倍率期望显著上升
- 评估指标:自摸率、平均倍率、杠开/财飘转化率、流局率(非 deal-in rate)
- 计分线性 → **RL 无需 GRP**(全局奖励预测),单局得分即干净 reward

## 二、已完成的实现

```
mj/
├── tiles.py     34 类牌编码(白板=33) + 牌串解析
├── win.py       财神百搭回溯分解:和牌判定(标准形/七对/豪华七对组数)、
│                听牌集合、爆头判定(任意牌即胡;恰 4 白板除外)
├── shanten.py   向听数(标准形+七对,财神感知)+ 进张枚举;
│                结果按手牌记忆化,剪枝界用剩余牌数材料上界(可证明保守)
├── game.py      对局状态机:吃(≤2摊)/碰/明暗加杠/抓打圈/显式 HU 动作
│                (弃胡+刚摸牌门禁)/动作链跟踪(飘/杠 ×2,断链清零)/
│                最后10墩禁杠/流局/结算;legal_actions()+step() 自博弈友好接口
├── scoring.py   v2 番型公式:分支(平胡/七对×2^豪华)→动作链→4白板→爆头
│                连乘 + 庄家×8 结算
├── bot.py       启发式决策:弃牌优先级 = 向听数(硬约束)→ 财神保护 →
│                进张数(全最小向听候选取精确 ukeire)→ 牌型结构损失 →
│                喂牌风险 → tile 编号(仅稳定排序);成胡默认 HU,
│                爆头态打白仍听任意牌且墙牌充足时弃胡打白飘
├── features.py  NN 特征(75 平面×34 + 8 标量;oracle 再加 16 平面)+
│                109 维动作空间编解码与 legal_mask;座位相对序(自家/
│                下家/对家/上家)保证平移不变;oracle 只需活墙(死墙
│                = 全信息余集,信息完备);extract ~1.3-1.6ms/决策点;
│                花色置换增广 SUIT_PERMS(精确对称 ×6,平面列 σ⁻¹ 取/
│                掩码与动作 σ 前向——方向必须区分,掩码用 σ 前向会
│                把非法动作洗成合法,已有语义回归测试)
├── bc_data.py   BC 数据生成:bot 自博弈 → (planes, mask, action,
│                value) 分片 .npz(多进程;teacher 动作合法性断言;
│                planes float16;实测 8 worker 2129 样本/秒)
├── model.py     残差 1D CNN policy/value 双头(suphx 式 15×256,
│                可调 blocks/width);masked CE / masked softmax 工具
├── bc_train.py  BC 训练:masked CE + value 辅助头(tanh,MSE)+
│                批内花色置换增广;val 集 CE/top-1;checkpoint 仅存
│                张量与标量(加载用 weights_only)
├── evaluate.py  通用对弈评估:启发式 bot / 随机 / 任意 callable 玩家
│                互对;policy_player(ckpt) → mask 内 argmax 玩家
├── replay.py    回放校验器:平台赛后事件流→引擎逐步重放,合法集断言
│                + 结算对账(P1.5 收口;CLI: python3 -m mj.replay)
└── platform/    平台对接客户端(P4,详见 P4 节)
tests/           138 个单测全部通过(含向听数性质测试:
                 shanten=-1⟺is_win / 加牌单调性 / 听牌必有进张 / locked 回归 /
                 进张可见牌折算:双扣回归、牌河副露扣除、弃牌候选精确计数 /
                 features:平面语义精确值 + bot 对局逐决策点状态对拍 +
                 动作空间编解码往返 + mask⟺legal_actions 集合等价 /
                 BC 管线:数据不变量与确定性、置换群结构(S3 封闭性、
                 Q/A 互逆)与增广语义方向、teacher 合法性、网络前向双头 /
                 平台层:牌名与动作映射往返、mirror 200 局×4 座位属性
                 测试(64k 决策点合法集零分歧)、FakeApi 全链路对弈、
                 replay 对账与污染检测)
                 另有 fancalc_parity.py:引擎 vs 平台 fan-calc 在线对拍
                 (22 用例,hu/倍率/番型明细/结算;需内网,手动跑)
```

### 验证结果

**单元测试**:95 个,覆盖每个番型正反例(含财飘/双财飘/杠飘链/连杠/
4 白板两种口径/豪华七对财神补齐不算)、财神填顺/刻/雀头、locked 组合、
结算公式守恒、状态机不变量(136 张牌守恒逐步检查、必然终局、抓打圈约束、
财神永不可吃碰杠、碰窗口先于吃窗口、弃胡、碰后禁胡、链断清零)、
向听数性质(随机手牌:-1⟺和牌、加牌向听最多降 1、听牌必有进张、
locked 手牌回归用例、缓存命中一致性)、进张可见牌折算回归、
features 平面语义与动作空间。

**shanten 优化验证**(2026-09-03):剪枝界 + 记忆化重构后,与无剪枝
暴力穷举 DFS 对 10 万随机手牌(均匀墙抽牌 + 近听结构,locked 0-4,
财神 0-4)两次全量对比零分歧;差分测试同时发现并修复了旧实现的
locked 手牌向听数虚高 bug(见下)。

**性质测试**(shanten,2000+ 随机手):
- `shanten == -1 ⟺ is_win`(与和牌判定互验)
- 单调性:加任意自然牌,向听数最多降 1
- 0 向听 ⟹ 必存在进张(含财神)

**Bot 评估**(100 局,v2 规则,ukeire 双扣修复后重测 2026-09-03):

| 对局设置 | 和牌分布 | 平均得分 | 流局率 |
|---|---|---|---|
| 1 bot vs 3 随机 | 60 : 0 : 1 : 0 | +16.5 vs -5.5 | 39% |
| 4 bots 自博弈 | 36 : 23 : 20 : 21 | +4.4/-1.2/-1.5/-1.7 | 0% |

倍率分布(1 bot vs 3 随机):×1 52 局、×2 9 局;(4 bots):×1 81、
×2 18、×4 1。随机 baseline 对局:44/1000 和牌,956 流局(符合预期,
随机打法手牌差)。

已知近似(打牌抉择的牌效模型):进张张数 = 4 − 已见(自己手牌+
牌河+副露+杠),含他家手牌与死墙 20 张,是**上界**;墙维度
(自己还能摸几张 ≈ live_wall/4)未参与弃牌抉择,仅用于财飘判定
(live_wall ≥ 5)与终局杠禁——残局按剩余摸牌折算进张价值留 P2。

### 已修复的关键 bug(记录避免重蹈)
1. 剪枝下界不安全——"已取组件外的牌"被当死牌,但分解允许重组
   (22m33m 可拆 234m+23m);改保守下界(每张未分配牌最多省 1)
2. 财神补过的搭子没从搭子池扣除 → 重复计数
3. 雀头选定后剩余对子被丢弃——对子可当搭子(摸第三张成刻)
4. 13 张手返回 -1——4 面子+财神是"摸任意即胡"的爆头听牌(0),非成牌
5. 七对公式漏算单张价值——6 对+1 单即听牌
6. **locked 手牌向听数虚高**(2026-09-03):剪枝下界的基准硬编码为
   8(满需求 4 面子),而 locked>0 时基准应为 2×(4-locked)——
   有副露的手牌被过度剪枝,向听数偏高,bot 副露后决策系统性偏差。
   差分测试发现(新旧结果在 locked≥1 手牌上分歧,新值更低且与
   暴力穷举一致),例:locked=3 手 1m1m 8m8m W 是和牌(1m1m+W 刻、
   8m8m 雀头)旧值 0 新值 -1。教训:剪枝界的"基准"必须随 need 变化;
   优化必须配 ground truth 差分验证
7. **进张张数双重扣减**(2026-09-03):ukeire 的 visible 已含自己手牌,
   _left 又减 counts[t]——对子待第三张/双碰听的进张被算成 0 张,bot
   系统性偏拆这类搭子。改为 4 − visible[t](弃牌候选传弃牌前完整手牌
   即精确:弃牌只是手→牌河转移,可见总量不变);顺带删除 _choose_react
   中未使用的 vis。同种子重测:1 bot vs 3 随机和牌 50→60,流局 47%→39%。
   教训:同一份"已见"信息在调用方与被调方各扣一次,口径必须单点定义
   (visible 含手牌,4 − visible 一处折算)
8. **弃牌排序由 tile 编号主导 —— 系统性先拆数牌、留孤张字牌**
   (2026-09-11):旧 choose_discard 的排序键是 (向听, 财神, 喂牌, t),
   同向听同风险时由 t 决定;数牌编号 0~26、字牌 27~33,于是孤张
   东南西北中发留手里,数牌两面搭子反被先拆。且 shanten>1 时直接
   return、完全不比进张(与自身 docstring 矛盾);贴近听牌时先按
   编号截 top_k=4 再算 ukeire,字牌可能连候选都进不去。随机搜索
   实锤:3m44m567m1p5p3689sEF(向听4)打东进张 95、打 3万仅 70,
   旧实现打 3万。修复:向听数仍是硬约束,新增 _discard_shape_cost
   (孤张字牌 0 < 字牌对子 6 < 字牌刻子 10;数牌基数 2、对子+4/
   刻子+8、相邻+3、嵌张+1),最小向听候选全部参与精确进张比较,
   仅在向听之前用财神保护。教训:启发式里的"稳定 tiebreak"绝不能
   用编码序——编码序会悄悄变成事实上的主排序键
9. **ukeire 是 bot 运行时的绝对瓶颈**(2026-09-11):关掉 ukeire 自博弈
   17.0 局/秒、开启 3.53 局/秒——约 85% 时间在 ukeire。全最小向听
   候选都取精确进张后为 2.28 局/秒(1.55 倍);它同时拖慢 bc_data
   与 RL 环境里每步 3 个 bot 对手。
   **候选剪枝**(同日落地):无财神且向听数 > 0 时,不在手且与任何
   手数牌同花色距离 >2 的牌不可能是进张(标准形 DFS 组件全要求
   同种或 ±1/±2;七对在 13 张奇数手必有单张,新孤张只增 singles)
   ——_ukeire_candidates 只枚举手中牌 + 数牌 ±2 同花色邻域 + 财神,
   内层 shanten 调用 30.8 → 24.1 次/ukeire(没到理论 15~22,因为带
   财神的手牌全量 fallback),200 局自博弈 2.28 → 2.94 局/秒,
   距原始排序口径仅剩 1.20 倍。**有财神时必须全量枚举**:财神可配
   任意新单张成对(七对,如 6 对 + 财神摸任意牌成胡)或补结构,
   除非有完整证明 + 大规模差分,不再细分。语义由
   tests/test_shanten_props.py::TestUkeirePruning 守住:随机差分
   (locked 0..4 × 无财神,三元组逐位相等且 acc 升序)、非候选
   不降向听性质断言(直接验证剪枝命题)、财神专项(monkeypatch
   _ukeire_candidates 为 raise,断言有财神时不被调用且结果与全量
   一致)+ 财神随机差分
10. **win.py _melds 漏"财神前置顺子"分解——合法自摸胡被拒**(2026-09-11,
    Rust 对拍脚本的差分暴露):旧版只枚举"当前最小自然牌 t 作顺子首位",
    漏掉 t 位于第 2/3 位、更小起始位用财神补的形态([财,t,t+1]/
    [财,财,t])。实锤手牌:5万5万 789万 456筒 8筒9筒 6条6条 白白
    (decompose: 789万 + 456筒 + [白7筒 8筒 9筒] + [6条6条白] +
    5万5万)——shanten=-1 而 is_win=False 的内部矛盾,waiting_tiles
    漏 9筒,game.py:137 的 HU 合法性直接拒掉合法胡(方向保守:
    只会漏胡、不会误胡,自博弈/平台对弈里表现为错过胡牌)。
    修复:_melds 顺子分支枚举 s ∈ {t, t-1, t-2}(t 所在位用真牌,
    依据"财神与同值真牌可互换不失一般性")。均匀随机 300 手的性质
    测试采不到该形态(需多财神+特定残形),新增财神加密采样
    (强制 1~3 财神 × 1500 手)的 shanten=-1 ⟺ is_win 性质测试。
    **待办**:平台 fan-calc 对拍复核(需内网手动)——修复只放宽
    is_win,与 shanten.py 既有口径对齐;平台指南 v27 无"财神不可
    补顺子前置位"的限定,但按约定规则改动应过一遍对拍
11. **吃/碰反应侧只比 shanten——等向听进张减半的碰照做**(2026-09-11,
    openspec change `bot-react-full-eval`):旧 _choose_react 的门槛
    是 `after_s <= cur_s`,副露后必须立即舍牌的最终牌面完全不可见
    (ukeire/结构损失不参与),且 `cur_s` 的"含刚打出的候选牌"注释
    系误导(反应玩家手牌本不含他家 pending 牌,真正问题是 PASS 基准
    只覆盖向听数单维)。重写为"副露 + 最佳弃牌"完整评价:PASS
    基准 `_eval_standing(hand, locked, vis)` → (s, uke) 与 claim 后
    need+1 态手牌枚举舍牌(_post_claim_min_shanten → _best_standing,
    仅最小向听舍牌算 ukeire)同键比较;等向听需进张增量
    ≥ PONG_UKE_GAIN(2)/CHOW_UKE_GAIN(4);多候选择优
    向听→进张→弃牌结构损失→动作序。财神弃牌与 choose_discard 同口径
    (参与最小向听比较、同向听候选内 (d==W, -uke, shape, d) 保护——
    评审修正:初版直接跳过 W 且注释谎称同口径;百搭语义下"打财神是
    唯一降向听舍牌"实证不可达,随机 5000 手无反例,修正为显式一致
    防语义漂移,test_bot.py 有随机性质测试)。**KONG 边界**:KONG_OPEN 与
    PONG 同窗竞争(h[tile]==3 时同窗),PONG 换评价体系后相对结果
    无法保持,故 KONG_OPEN ∈ acts 时整窗(含 PONG)走 legacy 决策
    (_legacy_claim_react,行为与旧实现逐动作一致);暗杠/加杠本就
    不在 bot 的决策集(choose_action discard 分支只处理 HU/弃牌),
    KONG 三态统一另立 change。vis 快照不变量:pending 牌已入河
    (game.py _do_discard 先 append 再 _begin_react)、claim 时移入
    自家副露,claim 前后可见总量不变,单快照贯穿评价(有测试)。
    性能:两阶段评价(向听扫描先行、ukeire 懒算)实测吃窗均 3.9
    ukeire/窗(最大 30)、纯碰窗均 2.5(最大 12,含 PASS 基准 1 次),
    KONG legacy 窗 0;交错 A/B 自博弈吞吐 -2%~-10%(机器后台负载
    波动,15% 闸门内)。七对无特判:副露后 shanten 的七对分支自动
    失效(_chiitoi locked>0 → 9),PASS 基准保留——"自然偏向保护"
    而非绝对 guard(七对同向听且副露进张大幅改善时仍可能吃碰)。
    验收:tests/test_bot.py::TestReactDecision 16 用例(向听下降吃/
    碰、两种吃法选最终牌面优者、等向听进张增量恰达/未达门槛边界、
    KONG 同窗 legacy 不漂移、七对保护、vis 不变量、副露 0/1/2 张数
    三档);全量 283 passed。教师分布变化未重训管线(BC/PPO 重跑
    另行决策)。
12. **legacy 吃碰看推进、杠看无损 + 杠开**(2026-09-22,openspec change
    `legacy-shape-progress-kong-guard`):新增
    `LegacyShapeProgress`/`legacy-shape-progress-v1`,PASS 与
    “副露 + 最佳弃牌”同口径比较。向听下降直接吃碰;同向听仅四类
    显著推进可做:爆头、财飘、听牌宽度、下一摸降向听能力。冻结第一版
    阈值:PONG 绝对 +4、CHOW 绝对 +6、比例 1.50(before=0 只看绝对),
    听牌牌种 +2 且 live 不降;旧 `PONG_UKE_GAIN=2`/
    `CHOW_UKE_GAIN=4` 不再参与决策。KONG_OPEN 与 PONG 分路评价;
    三种 KONG 先过 material-safe 最优标准形结构门(暗杠/加杠要求冗余
    single,明杠要求自然刻子),再比较同口径 post-KONG 牌效,最后
    要求 post-KONG 已听且有公开未见杠开张,才进入补牌 EV。固定牌例:
    `123333m` 暗杠 3m 拒绝、`333m + single 3m` 可继续、加杠牌在
    `123m` 中拒绝、明杠三张被顺子占用拒绝、post-KONG 未听/无活杠开张/
    live waits 下降拒绝;PONG/KONG 同窗不再整窗回退。
    验证:`tests/test_bot.py` + 新
    `tests/test_legacy_shape_progress_kong_guard.py` 锁阈值、结构、
    杠开与诊断;同机同 pyenv/Rust 内核交错 3×200 局,4-bots
    elapsed/games 中位数 60s→67s(+11.7%,≤15%);200 局诊断:
    react p50/p95/p99=0.299/2.089/13.181ms,KONG_OPEN
    1.182/5.053/17.614ms,self-KONG 1.106/9.710/29.905ms,
    decomposition 121 次、Rust baotou 4790 次、Python baotou
    fallback 0。20260922 本地批次 40 局扫旧吃/碰/杠 209 个决策:
    186 个动作不变、23 个旧 PONG/KONG_OPEN 改 PASS;KONG 拒绝
    分布 post-KONG 未听 9、牌效下降 3、结构占用 1。
13. **legacy reaction lookahead v2（2026-09-22, openspec change
    `legacy-react-lookahead-v2`）**:冻结 `legacy-shape-progress-v1` rollback，
    新增 reusable standing frontier、普通 CHOW/PONG 的 U2 veto/tempo guard、
    KONG shape-preservation 与 public 两摸 score continuation；
    `legacyV2` reaction profile 在门禁前显式 disabled，
    `legacyV2-offline` 使用 private tie-guard 强制完整 Stage-B，缺失仍
    fail-loud。1000 条真实 reaction shadow audit：v1↔v2=0、v2↔teacher=0、
    teacher incomplete=5；eligible U2=145，complete/safe-partial=0%，
    U2 p50/p95/p99=3.462/9.481/17.379ms，KONG continuation
    p50/p95/p99=12.339/13.259/13.259ms。随后将 KONG continuation 改为
    内层可中断的 bounded search（最低向听/听牌剪枝、standing cache、
    score/cache/预算诊断），online profile 固定 node=512、soft/hard
    8/15ms；200 局 online v1-first 复测（统计全部 KONG 候选）为
    p50/p90/max=5.289/12.390/15.325ms，10 次 hard-deadline 整层回 v1。
    严格 offline 无回退 200/200 完成，KONG p50/p90/max=16.023/46.157/313.894ms，
    积分总向量仍
    `[-125,266,197,-338]`，与 v1 逐局积分零差异。冻结 `5e0a4058…` 与当前候选
    同种子交错 3×200 局：baseline 中位 0.12115s/局、candidate
    0.15224s/局（+25.66%，超过 15%）；当时 coverage/吞吐门禁均未通过，
    因此默认继续 v1。全量测试 `979 passed, 1 warning, 9 subtests`（bounded
    search 修改前的首轮全量结果为 978 passed），
    strict OpenSpec validate 通过；未从无真实分歧中伪造 9.5 regression
    fixtures。

    2026-09-23 修复 U2 `0/123`：PASS 单 root 被 Rust Stage-A 比较器的空集合
    `all()` 误当成 strict winner，首个 draw 即跳过 Stage B，coverage 仅
    3%–6%。online reaction 现在对每个 standing frontier 统一使用 Stage-A
    safe-partial：所有 roots 达到 profile 的 90% coverage 后才停止并比较
    `future_improve_weight`；普通 discard 不变，offline 仍强制 Stage-B。
    增加 `rust-weighted-two-ply-v3` ABI 标识，旧 v2 wheel 显式 mismatch 回退，
    不会误调用新参数。新增 singleton、coverage 与旧 wheel 回退回归。
    真实 native 26 项、reaction/eval 相关 111 项通过。全量 suite 979 passed、
    3 failed（仅 minisuphx cluster 测试因 coordinator `127.0.0.1:7890`
    未运行而 connection refused），9 subtests 通过。

    同机 v1/v2 同 seed 配对 3×200（600 局）报告：
    `local/legacy_reaction_score_audit_stagea_fix_3x200_20260923.json`；
    U2 eligible 385/385 complete-or-safe-partial（100%，fallback 0）。
    v1/v2 每局 elapsed 中位数 110.24/89.16ms（候选快 19.1%，满足 ≤15%
    退化门槛）；一次 200 局诊断 U2 extra p50/p90/p95/p99/max 为
    3.000/4.707/5.392/6.202/7.006ms。积分方面 31/600 局动作有差异、
    16/600 局积分有差异，累计 v2-v1 各座位 `[-42,-79,+7,+114]`，积分守恒；
    该样本只能证明决策生效，不能证明策略胜率提升。KONG continuation
    3×200 的 p95=15.097ms，略高于 15ms 门槛；默认 routing 保持 v1。9.5
    分歧 regression fixtures 与 KONG p95 门槛仍未完成。

    后续 bounded continuation 优化：同一批 3×200 通过按 standing 复用精确
    per-wait score vector，并给在线 hard cutoff 留 2ms unwind reserve；末尾
    追加 deadline 检查，避免最后一个不可中断 score 调用后误报 complete。
    报告保存在
    `local/legacy_reaction_score_audit_reserve_3x200_20260923.json`：KONG
    continuation n=93，p50/p90/p95/p99/max=
    5.569/13.072/13.135/13.894/15.015ms，p95 通过 ≤15ms 门槛（max 比名义
    hard budget 高 0.015ms，按实测记录，不隐去）；U2 coverage 383/385
    （99.48%，2 次 `u2_incomplete`），积分 delta 仍为
    `[-42,-79,+7,+114]`，结论不变且默认 routing 仍为 v1。新增 1,800 局候选
    self-play（seed 190000–191799）均未找到完整 PONG/KONG same-unit 翻转；
    9.5 继续待真实 fixture，不以合成/伪造分歧代替。

    2026-09-23 用户确认生产默认切到 v2：默认、`legacy`、`legacyV2`、
    `legacy-v2` 与 weighted aliases 均使用 enabled online profile；冻结
    v1 仍可显式通过 `legacy-v1` 回滚。此前同 seed 3×200 的 U2 coverage
    99.48%、KONG continuation p95=13.135ms、elapsed/game 候选快 19.8% 为
    路由启用的观测证据；两次 U2 incomplete 与任一 KONG continuation
    incomplete 仍按当前窗口/杠整层回退 v1，记录 fallback，不提交部分结果。
    当前工作机安装的扩展报告 `rust-weighted-two-ply-v2`，代码要求 v3；
    已修复启动诊断，将旧 ABI 明确标为 degraded/version mismatch。实际在线
    alias 仍路由 enabled v2，kernel mismatch 时返回 `u2_incomplete` 并整层
    回退 v1；要在此机验证完整 v2 搜索需重建/部署 v3 扩展。9.5 的真实
    PONG/KONG same-unit 分歧 fixture 仍未找到，不伪造。

14. **legacyV2 BigHandIntent（2026-09-23, openspec change
    `legacy-v2-big-hand-intent`）基线冻结**：行为基线为实现提交
    `97d337fea6132b05066eb26bf9190bb5f82c9e42`，默认 evaluator=`legacyV2`，
    online weighted profile fingerprint=`d87ad5add4e1c0c7`，Rust weighted
    kernel=`rust-weighted-two-ply-v3`；完整参数和固定种子牌例见
    `tests/fixtures/legacy_v2_big_hand_baseline.json`。0/1/2 向听例 action
    分别为 10/19/32，多财神未听牌例 action=27；冻结单张、singleton、
    shape guard、kernel unavailable 与 budget fallback 已加基线契约测试。
    爆头/财飘、墙量 6、X/Y/Z、freeze 与四白板计番回归引用既有固定测试。
    旧版同机 v3 kernel 的 interleaved
    3×200 性能基线（seed 310000 起）共 20,191 次 ordinary discard：决策
    p50/p95/p99/max=2.650/39.706/56.408/206.790ms，frontier roots
    p50/p95/p99/max=1/3/3/3，按有 fallback_reason 计 fallback 18.47%；
    instrumented 4-bot elapsed/game 中位/p95=478.44/827.19ms。测量包含
    `return_evaluation=True` diagnostics 开销，候选比较必须使用相同脚本/口径。

    **2026-09-23/24 完成 Phase A/B 对局验收**（同机 Python 3.11.14、
    `rust-weighted-two-ply-v3`；paired source-seed percentile bootstrap 5,000
    轮，balanced 16 个 hero-seat/dealer 组合）。Phase A vs frozen
    `legacy-v2-baseline` 使用 seed 410000 起的 4,096 对局，无 error：基线
    均分 -0.1943、Phase A -0.2673；candidate-baseline = **-0.0730 分/局**，
    95% CI **[-0.1606,+0.0146]**，未证明积分改善。胜局 1,018 vs 1,006，
    两侧各 9 流局。intent game strata（描述性、互相可重叠，不能当作单决策
    因果收益）：CHIITOI -0.0719 [-0.2727,+0.1159] (n=1,001)，
    LUXURY_CHIITOI -0.4435 [-0.9289,-0.0377] (n=239)，WHITE_RICH
    -0.2532 [-0.6623,+0.1104] (n=154)。Phase A 共记录 153 次 big-hand
    root admission、0 个 `+1` challenger/override；本次报告 profile fingerprints
    为 disabled baseline `333a41e31e320a6c`、Phase A
    `39faefce3674f5eb`（baseline fixture 保存的是加字段前行为基线 fingerprint
    `d87ad5add4e1c0c7`）。积分 JSON：
    `/tmp/legacy-v2-big-hand-score-phase-a-vs-legacyv2-4096.json`。

    Phase A 同机交错性能基准 3×200 局/每 profile（共 1,200 matches，seed 310000
    起）：ordinary discard p50/p95/p99/max，baseline
    4.171/52.262/69.699/181.524ms，Phase A
    7.114/55.094/71.436/214.567ms；p95 增幅 **+5.42%**，通过 ≤10% 门槛。
    4-bot elapsed/game p50/p95/p99/max，baseline
    638.0/1145.7/1425.8/1886.9ms，Phase A
    719.6/1266.0/1558.0/2153.0ms；p50 退化 **+12.79%**，未通过 ≤10% 门槛。
    frontier roots max=3，fallback rate 19.36% vs 19.28%。intent 单次 10,000
    次 microbench p50/p95/p99/max=0.354/0.683/1.070/17.323ms；p95 通过
    ≤1ms，max 保留记录。指标含 `return_evaluation=True` diagnostics 开销。
    性能 JSON：`/tmp/legacy-v2-big-hand-perf-final-3x200.json`；microbench：
    `/tmp/legacy-v2-big-hand-intent-final.json`。

    Phase A 的 100 条真实 intent 触发样本已人工检查：18 个 root 通过同向听
    admission gate，7 个进入扩围；311 个 root 因 intent 不够强、9 个因同向听
    ukeire 损失 gate 拒绝。样本中 0 个动作偏离 speed winner；但 90/100 样本
    live wall≥41、10/100 为 21–40、无 ≤20 样本，因此这份样本不能单独证明
    晚局安全。样本按首批真实触发点保留在 score JSON。

    Phase B 首轮 4,096 paired A/B（seed 510000 起）delta=-0.0847，95% CI
    [-0.1616,-0.0151]。审计发现满 3-root frontier 时 Phase B 曾挤掉一个旧
    speed root，可能让未 override 情况下的 speed comparator 少看候选。已修复为
    满额拒绝 `+1` admission、原 Phase A frontier 不动，并新增容量 regression。
    修复后同 seeds 重跑 4,096 对局、无 errors：Phase B vs Phase A delta
    **-0.0017 分/局**，95% CI **[-0.0886,+0.0847]**；只出现 1 个 challenger
    event，seed 511038、hero seat 2、dealer 3、wall=34、opponent melds=1，因
    `big_hand_challenger_incomplete` 拒绝，speed winner/action 均为 13、challenger
    为 23，0 次 override。event 所在 game 两策略得分差=0；实际 override 样本为
    0，故 per-override 收益区间不可估计。修复版 score JSON：
    `/tmp/legacy-v2-big-hand-score-phase-b-vs-phase-a-postfix-4096.json`；修复前
    结果仅作为发现证据：`/tmp/legacy-v2-big-hand-score-phase-b-vs-phase-a-4096.json`。

    结论：BigHandIntent 保持实验 opt-in；生产 weighted online profile 和
    `legacy-v2` 兼容别名仍 `big_hand_enabled=false`，Phase B
    `plus_one_enabled=false`。Phase A 积分没有正向显著性且 elapsed/game gate
    超标；Phase B 修复版没有可估的 override 收益，修复前负向结果促成的容量
    保护已固化。目标测试套件最终 138 passed、9 subtests passed；全量测试
    曾有 997 passed、19 个 loopback bind 在 sandbox 被禁止而失败，提升权限重跑
    相应 4 个 clientd/minisuphx 模块 22 passed（17.11s）。OpenSpec strict 与
    `git diff --check` 均通过。

15. **Mini-Suphx v2 单机 PPO 三连 bug(2026-09-25 smoke 闭环抓出,已修)**:
    - **rollout 设备不匹配**:`mj/training/ppo_rollout.py::gather_rollout` 的
      `planes_t/scalars_t/mask_t` 固定建在 CPU,而 policy 在 cuda → weight 在
      cuda、输入在 cpu,`--device cuda` 必挂。修复:探测 `policy.parameters()`
      首参所在 device,输入张量同设备创建。
    - **max_episodes 硬编码 500 截断 rollout**:实测约 8.6 决策/局,500 局只够
      ~4.3k 决策,正式 rollout 16384/32768 在此必然报错(永远采不满)。
      修复:`max_episodes<=0` 时按 `n_decisions×3+300` 缩放(是 fail-loud 兜底,
      非预算);`distributed_rollout.py` 的默认 `500` 同步改 `0`。
    - **checkpoint resume 的 RNG 状态设备错乱**:`torch.load(map_location=cuda)`
      把 RNG 的 uint8 ByteTensor 也搬到 cuda,而 torch 2.x 的
      `set_rng_state`(CPU 生成器)与 `cuda.set_rng_state_all`(各设备状态实际以
      **CPU** 字节张量存储)都拒收 cuda 张量 → resume PPO 报
      `RNG state must be a torch.ByteTensor`。修复:恢复前对 torch 状态
      `.cpu()`、cuda 各状态逐个 `.cpu()`。

    **验收**(smoke campaign `runs/minisuphx/smoke`):create→legacy BC→BC anchor
    →DAgger D1→PPO 一期→smoke gate→resume 全闭环跑通;从 policy_v0 checkpoint
    续训 8192 决策成功并发布 policy_v2。相关单测 49 passed。

16. **legacyV2 ShapeQuality（2026-09-26，OpenSpec change
    `legacy-v2-shape-aware-two-ply`）**：新增版本化 `StandingShapeQuality`，用
    非重叠 rank decomposition 区分 ryanmen、中央/边坎张与边张；最小顺序固定为
    `23>24>13>12`、`78>68>79>89`。Rust/Python 对拍覆盖 10,000 手牌；weighted
    Stage B 同步比较 child shape；爆头快路径只在 tier、财神保护和爆头进张并列时
    使用 standing shape，保留原 `discard_shape_cost` 与 feed-risk tie-break。
    按用户要求，在线 `legacyV2` 默认现为 Phase B shape-aware：
    `shape_quality_enabled=true`、`shape_quality_stage=full`、
    `shape_quality_guard_enabled=true`；显式关闭 `shape_quality_enabled` 可回滚。
    weighted hard budget 50ms、frontier 上限 3 不变，内核版本为
    `rust-weighted-two-ply-v4`。baseline/Phase A/B 对照 evaluator 显式保持
    shape-off，避免默认切换污染 A/B。

    用户牌例已按公开信息复现：`local/games/20260923/`
    `tournament_t_069a55e84b26_b9_t4.jsonl` 的真实弃牌帧为 seq=707；旧 key 选
    4s，shape-aware `baotou_scope` 选 1s，stage B 未进入。回放证据在
    `openspec/changes/legacy-v2-shape-aware-two-ply/artifacts/`
    `replay_b9_seq707_20260926.json`。另从本地 1,011 份记录中按路径均匀抽样 60 份，
    以 Mirror 公开态重放取得 3,836 个可配对弃牌状态；candidate 与 baseline 有
    60 次动作不同（1.56%），其中 baotou 26、Stage B 34、root-only 0。样本中
    evaluator `shape_changed_winner` 标志为 42 次，与直接动作差异的计数口径不同；
    两次扫描结果为 59/60 次，故该抽样率只作描述性估计。4 份日志有 replay
    illegal 记录（10 条），45 份有 warning；决策 hook 只在合法集对齐后运行。
    详情及 mismatch 示例见 `historical_replay_scan_20260926.json`。

    积分验收使用独立冻结种子及换座配对：Stage 1 为 4,096 局，均值
    **+0.0598**、中位数 0、95% CI **[-0.0642,+0.1855]**；Stage 2 为 30,720 局，
    均值 **+0.0447**、中位数 0、95% CI **[-0.0204,+0.1087]**，形式门槛通过，
    但区间跨 0，不构成确定正收益。Stage 2 evaluator 的
    `shape_changed_winner` comparator flag 为 2,652 次（1.288%，影响 2,440 局）；
    changed-game 均值 +0.2873、95% CI
    [-0.1836,+0.7824]。15 个按 scope/shanten/副露/剩余牌墙分桶的 bootstrap
    cohort 没有样本充足且显著为负的桶；墙剩 20 以下、3 副露及 4 向听样本太少，
    不作推断。Stage1 另外按 scope/shanten/副露/wall-left 做 13 个收益 cohort；
    root profile 下 flagged divergence 为 baotou 138、weighted/root-only 71、
    future-shape 0；没有样本充足且显著为负的桶。详见
    `score_summary_20260926.json`、`score_stage1_buckets_20260926.json`、
    `score_stage2_buckets_20260926.json`。历史 replay 样本的直接动作差异为 60/3836
    （1.56%），`shape_changed_winner` flag 42 次；两者口径有 18 次不一致，故
    Stage2 正式 action-rate 口径仍待补齐，不能把 comparator flag 当成真实动作差异。

    性能门通过：交错 4-bot 共 1,800 局（每 evaluator 600 局），Phase A p50
    退化 **+0.44%**，Phase B **+6.75%**，上限 10%；shape microbench 和
    ordinary-discard 3×1000 结果分别保存在 `shape_microbench_20260926.json`、
    `ordinary_interleaved_20260926.json`。目标回归 **157 tests + 4 subtests**
    通过，`cargo check`、OpenSpec strict、`git diff --check` 通过。可运行全套为
    1,029 passed、45 failed、5 skipped、20 subtests；失败由缺少训练依赖、sandbox
    loopback 限制和 setup 宿主环境条件引起；授权 sandbox 外重跑 clientd/match-runner
    相关 34 tests 全过，因此全项目测试仍不记为全绿。Stage 2 满足预设非退化积分门，
    但 CI 跨 0，不构成确定正收益；性能门通过。应用户明确要求，默认已切到 Phase B。
    OpenSpec 14.1 全量测试仍未全绿；生产发布后的 100 个改动决策人工抽查待发布后完成。

### 性能现状(2026-09-03,shanten 剪枝界重构 + shanten/is_win 记忆化)
- 自博弈(4 bot,同种子 A/B):662ms → 142ms/局(**4.7 倍**)
- 评估负载:4 bots 100 局 12s(8.3 局/秒);1 bot vs 3 随机 20 局/秒
- 纯随机对局:~130 → 251 局/秒(is_win 记忆化)
- shanten 未命中 ~67μs/次;缓存命中 ~22%(跨对局手牌重复有限)
- RL 训练(2026-09-04):SubprocVecEnv + torch 8 线程后 500k 步
  5750s ≈ 87 steps/s(修复前同规模 17h,10.5 倍,见 P3 吞吐修复节)
- 下一档提速选项:分花色 Pareto 前沿缓存(前沿均值仅 2.4 项,但
  纯 Python 合并开销约等于现 DFS,需配 C/Rust)、或多进程并行
  (数据生成线性扩展,8 核 ≈ 60 局/秒,当前 RL 吞吐预估已够用)

### Rust 内核(rust/,2026-09-11 落地并接入默认路径)

**无 sudo 机器+C 工具链缺失的构建路径(2026-09-25,勿重演)**:编译 pyo3
扩展需要 C 链接器,系统没有 gcc/clang 时可 pip 装 `ziglang` 当
`cc`(zig cc == clang + 内置链接器):配 `rust/.cargo/config.toml`
`[target.x86_64-unknown-linux-gnu] linker=/path/to/zigcc.sh`(wrapper 一行
`exec <venv>/ziglang/zig cc "$@"`),然后 `maturin develop --release`。
本机即此路径,rustc 1.98.1 + ziglang 0.16.0,`scripts/rust_parity.py`
全过。若 wrapper 在 /tmp 重启后失效,建议固化进仓库用相对路径。

shanten/ukeire 的 Rust 移植(rust/src/lib.rs,算法与 Python 逐分支
对应,无记忆化)。构建:`python3 -m pip install -e rust/`(maturin);
验收:`python3 scripts/rust_parity.py --e2e`——随机差分(locked 0..4
× 张数 × 财神 9000 手 × 2 函数,含 ValueError 对拍、acc 升序断言)
+ 自博弈端到端轨迹逐局一致(winner/mult)。实测:shanten 40x
(86→2.1μs)、ukeire 97x(4212→43μs)、自博弈 50~60x(纯 Python
1.9 → 100+ 局/秒单核)。

**内核版本门禁(2026-09-24 踩坑,勿重演)**:`rust/src/lib.rs` 与
`mj/shanten.py` 里的 `*_KERNEL_VERSION` / `*_REQUIRED` 是**成对同步的
常量**——只改其一必挂,改完还**必须立刻重建扩展**
(`python3 -m pip install -e rust/`)。只改源码不重建时,
`kernel_runtime_diagnostic()` 报 `weighted_kernel_version_mismatch`
(`degraded=true`),后果不是崩溃而是**整片 legacyV2 静默降级为 v1 回退**:
shape guard 短路(`skipped_reason=kernel_unavailable`)、`bc_data` 拒写
训练标签(`RuntimeError: legacyV2 搜索回退 ...`,守卫是刻意为之)、评估器
指纹变 `legacy-one-ply`、native↔python 对拍不一致。实测一次红 28 个测试
(2026-09-21 构建的 v2 扩展配 09-23 的 v3 源码),重建后全绿。排障入口:
`python3 -c "import mj.shanten as s; print(s.kernel_runtime_diagnostic())"`
——`degraded=false` 才说明环境健康;重建后跑
`python3 scripts/rust_parity.py` 验收(shanten/ukeire/baotou 全交叉对拍)。

**接入方式**(mj/shanten.py):纯 Python 实现保留为 `shanten_py`/
`ukeire_py`(ukeire_py 内部全链走 _py,保证强制回退时纯血);对外
`shanten`/`ukeire` 是调度器——mj_kernels 可导入即优先 Rust,
ImportError 自动回退纯 Python(未构建扩展的机器/CI 不受影响),
`MJ_KERNELS=python` 强制纯 Python(排障/对拍)。bot/features/
rl_env 均按名导入调度器,无需改动。已知行为差异:非 34 维 counts
的报错文案不同(两侧都抛 ValueError);14 张暗牌且 s==0 的 ukeire
Rust 抛 ValueError 而 Python 走 is_win(真实调用不触达)。
测试配套:tests/test_shanten_props.py::TestUkeirePruning 随机差分
同时断言调度器(默认=Rust)与 ukeire_py 两条路径;monkeypatch/
性质断言针对 _py。s==0 分支 Rust 用 shanten==-1 判定,等价性依赖
bug #10 修复后 is_win 与 shanten 一致。平台对接层/引擎状态机有意
留 Python——风险倒挂。

## 三、训练方案设计(已定稿待实施)

### 0. 前置组件
**特征提取器 `mj/features.py`**(✅ 2026-09-03 实现,两阶段共用;
平面 [75|91, 34] float32 + 标量 8,全部值域 [0,1],座位取相对序
自家/下家/对家/上家 → 平移不变;附 109 维动作空间编解码 + legal_mask):

| 组 | 通道数 | 内容 |
|---|---|---|
| 手牌 | 4 | 暗牌多重性 ≥1..≥4(含刚摸) |
| 副露 | 48 | 四座位 ×(吃/碰/杠 × 4 多重性) |
| 牌河时间帧 | 16 | 每家牌河时间四等分,帧内出现置 1 |
| 财神专属 | 2 | 手中/全牌河财神数广播(/4) |
| 刚摸标记 | 1 | 本人刚摸牌;吃碰后的弃牌决策无标记(网络可分辨) |
| 工程特征 | 4 | 向听数广播、弃后向听数地图、进张未见张数广播、进张种类图 |
| oracle(仅训练) | 16 | 三家手牌 ×4 + 活墙 ×4;死墙 = 余集不必单列 |
| 标量 | 8 | 庄家、活墙/64、抓打圈/3、圈内、已吃/2、阶段 one-hot |

工程特征是相对 Suphx 的优势:向听/进张已实现,直接喂网络省样本量。
实测发现并处理的边界:吃/碰后进入弃牌阶段时 drawn=None 但手牌已是
need+1 张(吃进的是副露)——站立手基准取"最优弃张后的手";
extract 含 oracle ~1.6ms/决策点。

**回放校验器 `mj/replay.py`**:平台牌谱逐事件重放,
任何一步合法动作不含日志动作即报错——同时是引擎正确性的最终测试。

### 1. SL 预训练(行为克隆)
- **数据路径(2026-09-03 确认:无平台锦标赛牌谱,走自博弈冷启动)**:
  - 启发式 bot 自博弈生成数据(多进程 8 核 ≈ 60 局/秒,一天 10 万局
    量级;配合花色置换 ×6 增广,BC 数据量足够)
  - teacher 可选升级:弃牌决策用引擎做 1-2 层期望搜索(对每个候选
    弃牌模拟下一摸牌分布,算期望向听/进张)——轻量"搜索蒸馏",
    标签质量显著高于贪心 bot
  - 测试房间 API(`/api/test-rooms/{id}/games`,免认证,含四家手牌)
    是唯一真实数据源:bot 上平台打测试局,攒数据 + replay 规则对账
    一石二鸟;平台账号可用后再回补真实牌谱做 fine-tune
  - 可行性依据:BC 冷启动只求"合法且不蠢"的初始化,天花板由 RL 决定
    (Suphx 经验:SL init 主要影响 RL 稳定性与收敛速度);
    工程特征(向听/进张)已内置专家知识,弱标签也够学
- 动作空间:扁平 109 维 + 合法 mask
  (弃牌 0-33 / 过 34 / 吃 35-37 / 碰 38 / 明杠 39 / 暗杠 40-73 / 加杠
  74-107 / 胡 108;胡为显式动作,弃胡 = 选弃牌)
- 网络:15 层残差 1D 卷积(kernel 3, 256 通道)→ policy/value 双头
- 损失:masked cross-entropy;花色置换增广(万筒条互换,精确对称 ×6)
- 验收:对启发式 bot 胜率(弃牌 top-1 准确率参考 Suphx 76.7%)
- (后置,需登录态)平台 `api/games` 导出锦标赛对局,1 万局 ≈ 250 万样本

### 2. 自博弈 RL(MaskablePPO)
- 环境:`MahjongEnv` 封装 Game;**每家一条独立轨迹**(其余三家为环境动态),
  一局产 4 条轨迹;奖励 = 该家结算分/24,终局一次性,γ=1
- Oracle guiding:完美信息通道 Bernoulli dropout,keep 概率 γ 从 1 退火到 0
  (前 50% 训练量),γ=0 后学习率砍到 1/10 继续
- 对手池:全 4 座位当前策略;每 ~5000 局历史 checkpoint 入池,
  30% 对局换旧版本对手
- 超参:clip 0.2 / GAE λ0.95 / lr 3e-4 余弦衰减 / entropy 0.01 /
  rollout 8192 步 × 4 epoch
- 评估:固定种子 × 10k 局 vs 3 个启发式 bot
- 吞吐预估:单 GPU 1-2 局/秒 ≈ 10-17 万局/天;
  参照 Suphx 150 万局,预计 20-50 万局超越 BC

### 3. Search Teacher Distillation BC(2026-09-16 落地,待生成/训练/评估)
- 基线 `8a94fdeb`;OpenSpec change `search-teacher-distillation-bc`,
  文档 `docs/search-distillation.md`,冻结清单
  `openspec/changes/search-teacher-distillation-bc/artifacts/baseline_freeze.json`
  (rules v34 / 分单位 hero_round_score_points / 各 profile fingerprint)。
- 流程:`search_teacher_generate.py`(policies×冻结对手池轨迹 → 信息集
  teacher 自适应预算 512/2048/8192/16000,forced 只留 1-5% sanity;可断点
  续跑 by work_id)→ `search_bc_train.py`(visit soft CE 默认,Q-soft/hybrid
  版本化;unit-safe 权重;花色增广仅在与掩码/目标同向时启用;逐 epoch
  存 provenance checkpoint)→ `search_bc_eval.py`(8k/16k reference 上
  mean/p50/p95 regret、catastrophic、KL、top1、分桶 + batch=1 全路径
  延迟,`best-by-regret.pt`,top1 仅诊断)。
- 升级/发布门:`scripts/search_bc_paired.py`(pair 同 seed/座位/庄家/YCBK/
  对手;按源局 cluster bootstrap,CI95_lower>0 才算 superior;self_play/
  legacy_shape_v1/frozen_population 分矩阵)+ `PolicyIterationRunner`
  stop/rollback;`mj.decision.release` release manifest 固定 checkpoint
  hash/profile、保留 shape-v2/legacy kill switch 与回滚点,
  `release_gate_report` 验 illegal/NaN/emergency=0。
- 契约要点:policy-v3 新增 `calibration_policy`(policy-only 允许未校准
  纯策略 checkpoint,value 叶仍强制 calibrated);value 目标统一走
  `ValueTransformContract`(tanh,scale=96,修复旧 24 分歧)。
- 吞吐与降规格(2026-09-16 实测,6 核 CPU):优化后 33.7 sims/s/核
  (512 档 ~15s/状态、2048 档 ~61s、8192 档 ~4.1min);原规格 20-50 万
  状态 @2048 在本机需 23-59 天,故 Gen0 采用冻结的降规格 profile
  `artifacts/teacher_budget_reduced_gen0.json`(512→1024 档、2-5 万
  状态、8000 reference、≥1024 对局),完整梯子/16k reference/4096 对局
  与线上切换门仍是发布前提,降规格证据显式标注(design.md §15)。
- YCBK 规则(2026-09-16 定):`you_cai_bi_kao` 视为**永远关闭**;轨迹生成、
  teacher、reference、配对一律 `--ycbk off`,数据集/发布证据只含
  ycbk-off 桶;引擎与运行时仍按平台 flag 推理(见 design §16、
  docs/search-distillation.md)。
- 第二轮优化(2026-09-16 落地 change `regret-aware-active-distillation`):
  candidate pool + 主动采样(normal .40/disagreement .20/hard .20/
  special .15/random .05,比例入 profile)、teacher cache(state+teacher
  version+config hash,只允许 ≤ 已缓存预算复用)、regret-aware loss
  (policy 1.0 + pairwise ranking 0.25,catastrophic 默认 0)、sample
  weight=teacher_confidence×policy_error×importance clip[0.25,4]、
  replay 四桶(recent .50/historical .25/hard .15/special .10,历史
  reservoir 有界)、hard-state 永久回归集(state_id 去重、failure-mode
  覆盖、fixed/regressed 报告)、分级 gate(offline→hard→fast 256-512→
  full 4096→runtime);实验按 E0-E6 单变量推进,top1 仅诊断。模块与
  命令见 docs/search-distillation.md。
- 状态:代码与单测完成(644 用例);降规格 Gen0 长跑、E0-E6 实验与
  平台门待执行。

## 四、待办清单

### P1.5 引擎收尾(优先)
- [x] **规则确认**(指南 v2 + fan-calc 工具实测,2026-09-01):
  - 财神摸到能直接成胡 ✓(指南:爆头态摸白可提交 hu)
  - 豪华七对不接受财神补齐 ✗(fan-calc 实测:只有手中 4 真牌算豪华组)
  - 抓打圈范围:圈内非打出者 ✓(与原实现一致)
  - 弃胡/刚摸牌门禁/动作链/番型公式 → 已按 v2 重构(见上)
- [x] **指南版本自检**(2026-09-03 两次、2026-09-08 第三次,`GET /portal/api/guide/version`):
  - 第一次 → v6:v3 七对允许财飘(引擎飘判定本不限制七对分支,实测
    确认一致);全局最大番 ×512(文档已更新,引擎无改动);v6 fan-calc
    修复(4 白板=手留+链内飘出、chain 0-6、白板>4 报 400)与引擎口径
    一致,新增 `tests/fancalc_parity.py` 20 用例对拍全过;v4/v5 测试
    房间跨轮复用仅影响 P4 客户端协议
  - 第二次 → v10:v7 有财必拷响引擎开关已实现(见一.有财必拷响节);
    v7 赛制多阶段化/status 新值/ready 语义、v8 门户字段、v9 限速粒度、
    v10 state 跨局轮询——均为 P4 客户端协议层,已记入 P4 备注,引擎无涉
  - 第三次 → **v22(v10→v22 共 12 个版本)**:**v21 七对/爆头白板口径
    两条裁定影响引擎,已同步修复**(见一.v21 节,fan-calc 对拍 22/22);
    其余 v11-v20 均为 P4 客户端协议层(见 P4 备注),引擎无涉
- [x] 4 白板口径验证(v6 已定:手留+链内飘出=4,fan-calc 与对局结算
  同源,引擎口径正确;fancalc_parity 全过,待对局回放终验)
- [x] `replay.py` 回放校验器 + 引擎-平台行为对齐(2026-09-08 实现):
  - 免认证数据端点逐事件重放进引擎:每个日志动作断言 ∈ 当点
    `legal_actions()`;round_ended 时引擎 settle vs 服务端
    fan/detail/scores 逐项对账;庄家第 14 张由 start_hands(14 张含
    首摸)消歧;pass 事件缺失容忍(react 序自动代过)
  - **对账结论(2026-09-08 实测)**:random 探针轮 10 局 660 动作
    0 非法;BC 实弹轮 10 局 465 动作 0 非法、全部胡牌结算一致——
    引擎与平台行为对齐正式收口(v21 番型修正获真实对局终验)
  (测试房间 `/api/test-rooms/{id}/games` 免认证拉取事件流,含四家手牌)
- [x] shanten 记忆化 + 剪枝界重构(2026-09-03,4.7 倍;顺带修复
  locked 手牌向听数 bug,新增 5 个性质测试,见上)

### P2 数据与特征(进行中)
- [x] `features.py` 特征提取器 + 动作空间编解码(2026-09-03,
  12 个专项测试:平面语义精确值、bot 对局逐决策点对拍、mask⟺legal、
  109 维往返;oracle 活墙+三家手牌信息完备)
- [x] `bc_data.py` 数据生成(600 局 → 11 万样本/51s,8 worker
  2129 样本/秒;10 万局 ≈ 7 小时,全量数据过夜跑)
- [x] `model.py` + `bc_train.py`(masked CE + value 头 + 花色增广;
  checkpoint weights_only 安全加载)
- [x] 端到端演练(2026-09-03):600 局生成(11 万样本/51s)→
  小网络 2×64 CPU 6 epoch(19 分钟)→ **val top-1 93.0%**;
  对弈验证:1 policy vs 3 bots **25:32:18:25 打平 teacher**
  (BC 冷启动达标),vs 3 随机 55 胜/+16.0(bot 对照 60 胜/+16.5)
- **评估方法注意**:evaluate.py 固定 dealer=0(0 号位永远庄家,
  先摸+×8 先天优势)——上述对局纵向可比但有座位偏差;庄家轮换
  公平对局(200 局):policy 42:60:47:48 ≈ **0.9× teacher**(BC
  上限即 teacher,追平即达标;胜率提升须靠 RL 自博弈,勿在
  全量 BC 上过度投入)
- [ ] 全量 BC:10 万局数据 + 15×256 网络(GPU/MPS 或 CPU 过夜;
  CPU 实测 4×128 bs512 每步 2.1s,2×64 bs256 16 线程 0.52s)
- [ ] (后置)平台牌谱导出脚本(测试房间免认证 / 锦标赛需登录态)

### P3 训练
- [x] `model.py` 网络 + `bc_train.py`(2026-09-03,见 P2)
- [x] `rl_env.py`(2026-09-03):MahjongEnv 单 agent 视角(随机座位/
  随机庄家),其余三家启发式 bot;obs 99×34(91 平面 + 8 标量行,
  oracle 段 [75:91) dropout 置零,训练/推理同构);reward = 终局
  clip(得分/24, ±4);7 个测试(env 步进不变量、oracle dropout、
  随机 rollout 合法性、终局 reward 边界)
- [x] `train_ppo.py`(sb3-contrib MaskablePPO,原生 action_masks 掩码):
  - NetExtractor:Net 主干 99 通道输入 → 特征 cat(p 2×34, v 1×34);
    policy 头 = action_net(零填充移植 BC p_fc,初始策略与 BC **逐位
    一致**,已验证 diff=0);value 头 = vf MLP [128,128]
  - OracleAnnealCallback:oracle_p 1→0 线性退火(前 50% 步数),
    lr 同步砍 1/10;Monitor 包装出 ep_rew_mean 学习曲线;
    SaveNetCallback 每 5 万步存快照(net+action_net 格式,
    policy_player 双格式兼容)
  - **50k 首跑教训(2026-09-03)**:vf=[](裸线性 value 头)+
    ent_coef 0.01 → value 全程学不动(explained_variance 负)、
    策略熵 0.195→0.414 被摊平,fair_match 胜率 11.5% 反低于 BC 基线
    21.9%(hu 决策未退化,60/60 照常胡;是整体牌效劣化)。
    修正:value 头给容量 + ent_coef 0.001;10k 冒烟熵稳定 0.21
- [ ] RL 训练验证:**四轮跑批均未显著超越 BC 基线**(2026-09-04):
  - ppo1 50k:vf 裸线性 + ent 0.01 → 11.5%(熵摊平,见上)
  - ppo2 500k(旧超参):50k 时 22.9%/+0.36 短暂超基线,100k 崩至
    6.2%/-4.41 —— approx_kl 0.05-0.09/次、clip_fraction 0.12 持续
    摚动策略,KL 累积漂移毁掉 BC 先验
  - ppo3 500k(lr 1e-4 + target_kl 0.03,单次更新 KL≈0.014 健康):
    50k 18.8% → 150k 6.2% → 300k 20.8%/-1.62 → 500k 16.7%/-2.52。
    单步 KL 约束不解决累积漂移
  - ppo4 500k(**BC 先验正则** λ=0.5:`BCPriorPPO` 子类,policy loss
    追加 λ·KL(π_new‖π_BC),π_BC = 移植后冻结深拷贝;顺带修复 BN
    冻结漏洞——sb3 `set_training_mode(True)` 在更新期把 BN 切回
    train 模式,ppo1/2/3 的梯度更新实际用了 minibatch 批统计):
    96 局轨迹 50k 21.9%/-1.09 → 150k 20.8%/+0.19 → 250k 19.8%/-0.97
    → 350k **24.0%/+0.01** → 450k 19.8%/-1.25;192 局终评 350k
    21.9%/-0.13、500k 19.3%/-0.68。**正则彻底消除崩塌**(ppo3 同期
    崩到 6-16%),bc_kl 全程钳在 ~0.01;但增益仍在 96 局噪声内
    (~±4%),未显著超基线
  - **总结:①累积漂移用 BC 正则可解,单步 target_kl 不行;②vs 固定
    启发式 bot 的奖励信号上限有限——bot 本身强度(21.9% 胜率水平)
    就是天花板附近,继续提升需换对手(自博弈对手池)或数据(平台
    真实牌谱)。当前最优模型仍为 BC(runs/bc0/best.pt;备选
    runs/ppo4/ckpt_350000.pt,均分更优但胜率持平)**
  - 后续候选路线(未实施):① 自博弈对手池(每 ~5000 局历史 ckpt
    入池,30% 对局换旧版本对手)后重跑 BC 正则 PPO;② 直接接受 BC
    上平台,P4 对接期间攒真实牌谱再做 offline 微调
- [x] 训练吞吐修复(2026-09-04):ppo3 全程 fps 从 59 崩到 2-8
  (偶发单迭代 1800-7200s)。排查结论:
  - shanten dfs 单手实测上限 ~200ms(36 万手全枚举),非元凶;
    cache 满 1M 清空重建也无悬崖(走平 ~170 steps/s)
  - 主因:① DummyVecEnv 顺序 rollout 单线程(bot 决策 shanten
    dfs 是 CPU 大头);② torch 16 线程 oversubscribe 8 物理核
    (bs512 训练 435ms vs 8 线程 305ms)
  - 修复:`--subproc`(SubprocVecEnv,oracle 退火经 env_method
    "set_oracle_p" 转发)+ `--threads 8`;实测 ppo4 500k 步 5750s
    (ppo3 同规模 60356s,**10.5 倍**)
- [x] 评估扩展:fair_match(座位×庄家 16 组合均衡轮转)已就绪,
  ppo2/3/4 全程采用(BC 基线 21.9%/-0.98;96 局噪声 ~±4%,
  关键结论以 192 局为准)
- [ ] GPU 资源确认(决定 RL 排期)
- [ ] (后续)自博弈对手池替换固定 bot 对手

### P4 上线
- [x] **平台对接客户端 mj/platform/(2026-09-08 实现并实弹验证)**:
  ```
  mj/platform/
  ├── api.py         urllib+自签证书姿态、Bearer、429 退避;玩家端点包装
  │                  + 免认证测试房数据端点(room_games/room_events)
  ├── proto.py       牌名映射 + 事件 schema 解析(字段名以 replay.js 与
  │                  实测为准,单一改动点)
  ├── actions.py     引擎动作码 ↔ 平台 JSON(chi 恒显式 tiles;gang 三种)
  ├── mirror.py      事件源公共状态镜像 → 决策点构建 Game(setup 模式);
  │                  墙长按事件计数;富快照全量重建自愈
  ├── bot_client.py  单令牌生命周期:发现房间→register/ready→状态循环
  │                  →每活跃场独立工作线程(M=10 并发);事件驱动决策
  │                  (自家摸牌/吃碰→弃牌;他家弃牌→碰窗立即、吃窗在
  │                  「响应观测齐或截止」先到先触发);批内代打回声/
  │                  窗口超时作废陈旧触发;409/失步→seq=0 重建;场次
  │                  异常连续 3 次才放弃(重派+快照重锚续打)
  ├── runner.py      测试房 4 线程 runner CLI(--strategy policy|bot|random)
  ├── synth.py       引擎自博弈→平台格式事件流+逐决策真值(离线夹具)
  ├── probe.py       首跑探针(字段发现+random 局+replay 对账)
  └── config.py      local/platform.json(gitignored,令牌)
  mj/replay.py       回放校验器(见 P1.5)
  tests/             离线:牌名/动作映射往返、mirror 200 局×4 座位属性
                     测试(64564 决策点合法集零分歧)、FakeApi 全链路
                     对弈(client 循环/窗口防重/重同步)、replay 对账+
                     污染检测——138 个单测全过

- [x] **自由对战对接(/api/match 自动匹配,2026-09-08 实现并实弹验证)**:
  单门户绑定全局令牌挂机攒真实对手数据。设计要点(对齐后实现):
  - **架构**:`BotClient.run_match()` 复用 play_game/窗口状态机/镜像
    重建(锦标赛 run() 路径零改动);config 来源换轨——全局令牌调
    /api/tournaments/me/rules 会 400 TOKEN_NOT_SCOPED,YCBK/BaseScore
    改从 /api/match 响应 config 取;无 register/ready(直连 auto 房
    409 AUTO_MATCH_ONLY);新 CLI `python3 -m mj.platform.match_runner`
    (--strategy policy|bot|random,--ckpt 默认 runs/ppo4/ckpt_350000.pt)
  - **SSE /notify 事件驱动对弈(v12 端点,2026-09-08 实现接入 match
    客户端)**:`BotClient(use_notify=True)` 时 play_game 每场挂一个
    SSE 监听线程(GET /api/games/{gid}/notify,每用户 32 连接、不占
    /state 16/s 额度)——帧只作「状态已变」唤醒信号(**游标纪律:帧
    seq 是包含式水位,绝不当轮询游标**,一律 GET /state?seq=本地游标
    拉增量),无触发批次等帧而不是盲轮询;断流(closed/网络/keepalive
    45s 超时)指数退避自动重连,重连期间自动退回 idle_sleep 轮询节奏
    (优雅降级);403/404 监听退出,主循环靠 /state 收尾。实测帧格式
    `data: {"seq":N}` / 终止 `data: {"seq":N,"closed":true}`;动机
    与效果见下「409 根因」——轮询接收延迟(p50 0.5s/24% 请求 429
    重试)是 409 迟到提交的根因,SSE 把事件感知延迟压到帧级
  - **挂机循环**:match 入席 → 打完整房(finished/closed/void 或
    房间 404 = 本房收官)→ 等 ~65s 宽限关停释放并发额度(v15:每房
    占 10/16 格)→ re-match,直至 --games 打满。**整房为退出粒度**
    (不中途弃房——弃房后剩余场次被服务端代打,污染他人对局);
    _play_room 收官 join 宽限 45s(≥ 一个长轮询周期——5s 会把仍在
    收尾计数的工作线程丢下,误判未达标多开新房,实测踩坑:--games 10
    打了三房)
  - **错误分诊**:403 PORTAL_BINDING_REQUIRED/401/scoped 400 =
    永久抛出(令牌须门户「我的 AI 身份」绑定签发);409 MATCH_BUSY/
    MATCH_LIMIT_REACHED/网络抖动 = 10s 退避重试(≥ /api/match
    10/min 限速);崩溃重启重调 /api/match 幂等返原房(v24)
  - **数据链路**:Recorder 复用,meta 行新增 `mode:match` 字段
    (测试房/正式赛缺省不写);`log2data --mode match|test|all` 按来
    源过滤;`bc_train --init` 微调口(BC 格式全量载入,PPO 格式载
    主干+value 头、policy 头取 action_net 前 68 列=p_conv 特征段
    近似暖启动;老 75 平面 BC ckpt stem 权重按 [planes|scalars] 布
    局分段迁移——oracle 段插中间,scalars 后移,函数不变,有单测);
    数据配比/胜负过滤等数据到手按质量再定
  - 测试:FakeMatchApi(auto 房序列:入席→打完→re-match 循环、
    404 关停、MATCH_BUSY 退避、403 永久、整房退出粒度、mode 落盘
    与 log2data 过滤)+ bc_train --init 函数保持性 + log_replay 轮边界
    跳过降级——165 个单测全过
  - **实弹结果(ppo4/ckpt_350000.pt,1 房 10 场 × 8 局)**:全 10 场
    正常收官(854 动作、16 自摸胡、0 镜像失步、18 次 409 自愈——真实
    对手 bot 抢窗比测试房激进,409 高于测试房基线但无害);日志 10/10
    局干净、log_replay 合法集断言 0 非法、log2data 产出 854 样本
    动作合法率 100%
  - **轮询时代 4 房 40 场批次复盘(2026-09-08,SSE 上线前日志)**:
    39/40 正常收官、总分 -180(场均 -4.62、15 局正分);67 次 409
    INVALID_ACTION(41 chi/16 discard/9 peng/1 pass,全部迟到提交,
    轮询延迟 p50 371ms/p90 1s/max 55.7s)、我方弃牌窗被代打 50 次
    (~1.3 次/场)、1140 次 gap 全部快照自愈 0 失步;唯一残局
    (无 end 记录)= hu_failed 手牌漂移炸线程,已修(见协议发现条目)
  - **409 根因(2026-09-08 排查,免认证房流逐事件对拍 + 计时分析)**:
    全部 20 次 409 都是**迟到提交**(决策/提交本身仅毫秒级),两类:
    ①chi(14 次)——吃窗时序为弃牌+1s 碰窗 →[1s,2s] 吃窗,事件接收
    延迟把"等碰窗齐再提交"的策略推出 2s 关闭点;②draw(4 次)——
    弃牌超时 3s,自家摸牌事件迟到 >3s 时服务端已代打。接收延迟
    分布(17045 事件):p50 0.5s / p90 1.5s / 17.4%>1s / 4.2%>3s /
    尾部数十秒——主因是 10 场并发轮询 ~15/s 贴着 /state 16/s/用户
    限速墙(**24% 请求带重试**,429 指数退避 0.5→10s)+ 长轮询不在航
    间隙(处理/合批 0.35s/decide 锁跨场串行)。客户端逻辑无 bug,
    409 后自愈继续;**治本已实现(2026-09-08):SSE /notify 事件驱动**
    (见上 SSE 条目),实弹复测 10 场 409 从 18-20 次 → **3 次**
    (0.31% 动作率),事件接收延迟 p90 1.47s→0.85s / p99 18.5s→1.8s,
    /state 请求量降 ~4 倍
  - **"吃自己弃牌"陈旧吃窗 bug(2026-09-08 SSE 复测发现并修复)**:
    剩余 3 次 409 里 2 次是同因——①同批 pass 未入账:触发弃牌与
    他家 pass 同批到达时,chi_pending 在批后才创建,pass 丢失 →
    "碰窗响应齐"永远凑不齐;②chi_pending 跨自家摸牌/自家弃牌/
    自家吃窗超时不清理,被后续无关 pass 误触发;③mirror.build_game
    react 相位不校验 pending 归属,对自家弃牌构建出"吃自己"假合法集
    (引擎 _begin_react 只让他家进 react,真实流程不可达,镜像捷径
    绕过)。修复:事件状态机三处清理(自家摸牌/吃碰后/任何新弃牌/
    自家吃窗 timeout window=chi)+ 同批 pass 喂入 chi_pending["seen"]
    + 同批认领使窗口触发失效(顺带消除"碰窗构建失败 无 pending"
    噪音)+ mirror react 相位 pending==自家 → MirrorInconsistent
    (最后防线,有回归测试)。剩余 1 次 409 为 429 重试链尾部
    (单次 /state attempts=4/3.6s,摸牌事件到达时 3s 弃牌窗已
    关——偶发,自愈)
  - **502 网关风暴 + 停摆期迟到 409(2026-09-08 match 第二批实测,
    a_8e42d001a73f/a_85e92476a6ca 两房,15 个 409 逐个归因后全部修复)**:
    平台侧 502 风暴(~2min)暴露客户端四处缺陷——
    ① **api 层 5xx 零重试**:502 一次即炸全部 10 个场次线程
      (ApiError 直穿),房间 10 局 end(error) 全灭、被服务端代打
      污染积分 → `_request` 对 502/503/504 与网络错误共享退避预算
      重试(≤5 次,429 无限重试不变;400/409 等语义错误不重试);
    ② **场次异常永久弃局**:`_play_game_safe` 异常即标记完成,重派
      永不发生 → 改连续 3 次异常才放弃(防崩溃循环),未超限不标
      完成、不落 end(error),监督线程重派工作线程 seq=0 快照重锚
      续打、续写同一份日志;
    ③ **陈旧 draw 触发**(11/15 个 409):停摆期 /state 退避重试
      7.6s+(att=5,0.5+1+2+4s 纯退避),弃牌窗超时被服务端代打,
      恢复后一批齐发 tile_drawn+tile_discarded(自家,同牌)+
      timeout(kind=discard)——`hand_count_ok("draw")` 的 need 分支
      (13=need,吃碰后合法形态)恰好放行,客户端照常决策提交废弃牌
      必 409 → 批内 自家 tile_discarded / timeout kind=discard
      seat=me 作废 draw 触发(auto_played 记账);
    ④ **陈旧 window 触发**(gang 409 1 次):批内我方
      timeout(kind=response,window=peng) 已判超时,仍提交碰/杠 →
      同批作废窗口触发;
    ⑤ **吃窗 gating 过时**(chi 409 1 次):"等碰窗全部响应"观测
      gating 无截止,网络抖动下(实测 req 1.78s/att=3)响应观测迟到
      弃牌 T+2 的吃窗关闭点 → 触发条件改为**「响应观测齐 或 截止
      (观察后 window_wait+50ms)先到先触发」**,提交时刻仍由
      _act_chi 内部固定睡到截止兜底(保证落在碰窗结束后、吃窗内)。
      窗口结构以事件 ts 实证:碰窗超时事件盖弃牌 ts+1、吃窗超时事件
      盖弃牌 ts+2(1961/288 样本零例外);纯时钟驱动(完全去掉
      观测 gating)在假服务器(按事件边界回放、无时钟)下会提前
      决策出幻影吃窗,故保留双条件。回归测试
      `tests/test_stale_trigger.py`(9 用例:代打回声/弃牌超时/碰窗
      超时作废、吃窗截止触发与他家碰作废、api 5xx 重试三态;
      重派语义在 test_hu_failed 更新)
  - **碰窗作废的日志精确化(2026-09-09 match 复测)**:上线次日 5 分钟
    会话出现 22 次窗口触发作废,全部紧跟 1.5-7.7s 慢请求(重试链
    0.5+1+2s 退避;观测延迟 p50 0.62s/p90 0.90s/p99 1.36s/max 3.28s,
    5.1% 批次 >1s——恰为 1s 碰窗的死亡区,链路抖动尾部的固有损失,
    客户端无法根治);但快照回查 22 次中仅 1 次真持有对子,其余
    21 次无碰/杠选项(代过=自选过,零损失)→ 作废时先
    `_lost_claim()`(张数漂移无法评估则保守记账)再决定是否
    `auto_played` 记账刷日志,消除误导性报警。另证:SSE 会话空批率
    ≈0(除快照响应),~14 req/s 均为真实事件驱动,帧水位过滤无可省;
    实际 HTTP 量(含 24% 重试)≈18/s 略超 16/s 限速墙,重试与限速
    互为因果的余量问题暂观察,窗口损失集中在 >1s 观测尾部门
  - **每令牌 `/state` 主动限速+截止调度(2026-09-09 实现，待实弹复测)**:
    `Api` 实例在同一令牌的 10 个场次 worker 间共享 `StateThrottle`，
    默认 12.5/s(80ms 匀速、冷启动 burst=2)，只限制 `/state`，不延迟
    `/notify` SSE 或动作提交；有窗口截止的状态拉取按 EDF 优先于普通
    wake/兜底拉取，普通请求老化避免饥饿。SSE 生产端去重水位、消费端
    合并一阵 wake，帧仍只作唤醒而非游标。摸/吃碰后的 3s 弃牌窗、碰窗
    1s、吃窗 2s 的本地截止均使用 monotonic；`_request` 额外记录
    429/网关/网络重试与退避，带截止的 429 退避最多缩短一次以抢最后
    请求，随后恢复退避避免热循环。JSONL req 新增 throttle/transport
    可选字段，logview 可区分排队、退避与端到端耗时；CLI 可用
    `--state-rate` 调整或 `--no-state-throttle` 回滚。覆盖
    `tests/test_throttle.py`，全套 182 测试通过。多进程复用同一令牌
    不共享本地 limiter，仍依赖服务端 429 防御。
  - **限速实弹唯一 409 定位+抓打圈快照修复(2026-09-09)**：房
    `a_19d56a5bc8ac` 10/10 完成、物理 state=11.05/s、429=2、
    `auto_played=0`，仅 1 次 draw 409；非限速/SSE/seat 漂移，而是
    gap 后 snapshot(`seat=2, turn=1, catch_play=true,
    god_discarder_seat=0`)重建时旧 mirror 只置 `freeze=1/freezer=-1`。
    seat1 弃牌后 freeze 被错误减为 0，seat2 摸南的合法集错误放开到
    任意弃牌，打 1w 被服务端拒绝并在 3s 后代打南。修复从
    `god.god_discarder_seat`、发起者牌河末张白板、snapshot phase/turn
    精确恢复剩余 freeze；缺字段/坏快照走保守冻结，不放宽合法集。
    `tests/test_platform_mirror.py` 覆盖该 seq498 形态：seat1 弃后 seat2
    摸牌只允许弃南。
  - **修复后实弹复测(2026-09-09 下午,续打上午被杀的残房)**:10 场
    全部 finished,**err409=0**(修复前同条件 13-15)、mirror_resets=0、
    decide_errors=0、hu_failed=0;94 次 gap 全部快照自愈;代打 4 次
    (弃牌窗 2 + 真碰窗 2,均链路尾部,观测延迟 p50 0.44s/p99 1.26s/
    >1s 占比 2.2%、重试率 14%,均优于上午);log_replay 逐局合法集
    0 非法、积分对账干净(轮边界跳过按既定降级)。注意:该房上午
    被杀后我方大量回合由服务端代打(全场我方仅 365 决策 vs 正常
    ~850),总分 -246 属污染数据,不作策略强度评估口径
  - **协议新发现(轮边界事件固有丢失,2026-09-08 实弹 + 免认证房流
    对拍确认)**:/state 轮翻转后返回新局全量快照,游标跳到响应水位
    res.seq——旧局尾部事件(窗口 pass/timeout + **round_ended**)不再
    下发。实测 10 局平均仅 1.8/8 个 round_ended 到达自记日志(在线
    决策不受影响:快照全量重建,0 失步;但 log_replay 积分累计对账
    全数误报)。修复:log_replay 从 req 记录推断跳过段(快照/finished
    响应的 [req.seq+1, res.seq]),有跳过时累计对账降级为局数守恒
    (max round_no − 已记录 round_ended 数 = 丢失局数,须有跳过段解释),
    合法集/自家胡结算断言保持严格;免认证房流(auto 房可用)对拍
    证实:全流 8/8 round_ended、各局累计与终局分完全吻合,丢失段
    与服务端全流逐 seq 对上
  - **限速 EDF 两轮实弹实验与结论(2026-09-09,三房对照)**:起因是房
    `a_7ec054478c92`(12.5/s,906 动作)唯一一次 peng 409——他家弃 6b
    后我方 1.44s 才观测到(non-urgent 排队 1242ms),1 秒碰窗耗尽。
    18 次 deadline_missed 逐条归因:queue_wait 全部 ≤83ms,无一排队
    饿死;~11 次为观测迟到致截止生来过期,7 次为 b2 流局过渡期同一
    过期截止被 gap 轮询连发携带。实验 A(他家摸牌预置
    `DISCARD_SEC+WINDOW_SEC` 截止):12.5/s 下 409 与 dm 消失
    (房 `a_be213fbb6ef7`:0/1),但 urgent 占比 17%→38%,普通刷新
    有效服务 7.75/s < 需求,弃牌观测 >1s 占比 4.3%→9.4%、丢碰
    1→3 次——EDF 优先份额在饱和限速下是零和的,**回退**;15/s 下
    无此副作用(房 `a_f1349e3e8500` 观测 5.1%);2026-09-10 复测
    15/s 表现良好(房 `a_9158e09dae81`:0 个 429、10/10 局完成、
    队列 p50 492ms / p95 679ms),是否提为默认待定。实验 B(过期
    截止一次性追赶 `_stale_deadline_step`:携带
    过期截止优先拉取一次后恢复普通刷新,新鲜截止重置资格)——纯
    排废无优先级副作用,**保留**(dm 18→1-3)。最终配置:12.5/s +
    实验 B。观测迟到拆解:主因供需差排队(需求 ~13.4/s 事件驱动
    不可压),22% 慢 HTTP 为服务端「持连接等事件落库」(SSE 帧先于
    落库推送,响应完成≈事件时间戳,非迟到);409 本身是化妆性损失
    (迟提交被拒 vs 代过,碰同样丢)。tests/test_stale_trigger.py 增
    TestStaleDeadlineStep,全套 186 测试通过。后续如需压普通刷新
    尾部:老化请求以「到达+max_normal_wait」为软截止参与 EDF(远期
    预置截止让位、近窗截止仍优先),待实验
  - **服务端 ts 锚定+守卫+懒轮询实弹 v3(2026-09-09 房 `a_513a891456a8`,
    12.5/s)**:窗口截止锚定服务端事件 ts(`_srv_deadline`,观测迟到按
    真实剩余收缩、死窗返回 None)、碰/吃/弃牌三守卫(窗物理上来不及
    才放弃)、`_next_seat_step` 动作者预测 + 懒轮询门(`_poll_urgent`,
    仅 SSE 在航且预测无关时吸收唤醒,LAZY_POLL_WAIT=1.2s)。结果
    **两个自伤 bug 被实弹暴露并修复**:① 吃牌提交时刻改锚服务端 ts
    后普遍提前落进碰窗——事件 ts 相对真实窗开启偏早 ≥0.08s(v3 一次
    房 15 次 chi 409,提交-弃牌间隔 1.08-1.58s 全部被拒),提交下限
    退回「观察后 window_wait+50ms」保守锚(观察 ≥ 真实开启,构造上
    安全;锚定 ts 仅用于截止与迟到守卫);② 碰窗守卫余量 0.12s 过肥,
    T+0.88 即放弃而 T+0.88~T+1 的临界提交大多能成(v3 误杀 14 次),
    收紧为 SUBMIT_EPS=0.05(仅剩物理来不及时放弃)。守卫方向性:锚点
    偏早使迟到守卫偏保守(不误杀),提交锚点偏晚使早退守卫偏保守。
    `tests/test_stale_trigger.py` 增 TestSrvAnchoredWindows/
    TestLazyPollSmoke
  - **/state 长轮询语义发现与长轮询模式(2026-09-09 活体实验)**:指南
    v27 §API 明确 `/state?seq=N`「无新事件时挂起最多 30s 后返回
    {pending:true}」——但 SSE 模式 3 房 ~29000 请求 **0 次**
    pending:true:**SSE /notify 连接在场会禁用服务端挂起**(空查询
    立即返回)。无 SSE 且无同令牌并发轮询干扰时(停 runner 后 45s
    连续探针):29 次挂起全部带事件返回、0 次空唤醒,事件观测迟到
    p50=0.54s/max=0.59s(服务端 ~0.5s 事件刷新粒度,硬上界无尾部);
    同令牌并发轮询会互斥唤醒(单等待者语义,0.05-0.17s 空 pending)。
    由此新增 `BotClient(long_poll=True)` 模式:空闲批次不再等待直接
    再发 /state 由服务端挂起——观测迟到恒 ≤~0.6s(碰窗 1s 恒来得及:
    0.6+决策 2ms+提交 25ms)、请求率=事件簇率(~1/s/场次,全房
    8-12/s 首次低于 12.5/s 限速与 16/s 墙,排队/EDF/懒轮询问题在此
    模式下不存在);吃窗等待改截止直火(响应被刷新推迟、挂起可能悬
    30s,不能依赖观测齐触发);`--long-poll` CLI 隐含 --no-notify。
    **待实弹验证后考虑设为默认**。另:logview 新增 `--windows` 动作
    窗口时间线(观测迟到/决策/提交落点/结果,优化归因入口)
  - **长轮询 v1 实弹(2026-09-09 房 `a_d3db75bf4f4a`)暴露三问题并修复**:
    ① 返回率饱和——每回合产生 ~2-3 个刷新簇(弃牌/T+1 碰超时/
    T+2 吃超时),10 场并发需求 ~24/s 仍贴墙,排队 p50 633ms 回归,
    观测尾部 p99 2.47s/max 16.6s/>1s 2.8%,级联至弃牌代打;
    ② 吃牌提交下限 obs+1.05 在观测迟到时把提交推过 T+2(实测
    obs=T+1.10 → 目标 T+2.15 必 409,单房 3 次)——提交目标改
    `min(obs+window_wait+50ms, 锚定T+2-30ms)`(早侧保守锚×迟侧
    竞速取小,锚点偏早保证竞速点仍在真实窗内);③ 修复=长轮询×
    懒门合并:预测无关簇(含**我方已响应窗口**的超时簇——`_poll_urgent`
    增 window_responded 维)睡到懒截止再发;动作后懒门下限抬到
    **锚定窗关闭点**(mono_at(trigger ts)+2×window_wait+0.1),
    整段跳过 T+1/T+2 超时簇(下一有意义事件=下家摸牌 ≥T+2 无损
    失),需求降至 ~1 簇/回合 ≈ 8-10/s。三项修复后待 v2 实弹
  - **长轮询 v2/v3 实弹迭代(2026-09-09,房 `a_69706026063c`/`a_27c365f004d8`)**:
    v2(懒门预测门控)需求未降——持对子期间(~60-75% 时间)预测恒急,
    需求仍 ~15/s 饱和(队列 p50 568ms);v3 改**懒下限绝对覆盖**(动作后
    锚定窗关闭+0.6s 内无条件不轮询),队列 p50 降至 69ms ✓,暴露三个
    新事实:① **抓打圈强制弃牌由服务端即时代打**——财神弃出后被冻
    座位摸牌与 timeout(discard) 同秒到达,窗口 ≈0,任何客户端都
    不可能也不必提交(v3 房 7 次「弃牌代打」全部如此,已改为不计
    损失);② **事件 ts 双向不可靠 ±0.3s**——obs 可早于 ts 0.28s
    (chi 提交 obs+1.05=T+0.77 落进碰窗 409),ts 也可早于真实窗开
    ≥0.08s(v3-1 房 15 次 chi 409),吃牌提交目标改
    `max(obs, mono_at(ts))+window_wait+50ms`(双时钟取晚,至少其一
    ≥ 真实窗开);③ **懒下限 +0.6s 过肥**——快对手下一张弃牌最早
    T+2.6 到达,可碰弃牌观测被推迟到 ~1.0s 恰好超出 1s 碰窗(v3 房
    5 次真丢碰全在 0.95-1.17s 观测带),收紧至 +0.3s。v4 待实弹
  - **长轮询 v4/v5 实弹终局(2026-09-09,房 `a_5ac890c2069d`/`a_ecaccf24e5c5`)**:
    v4 混入本机 CPU 争抢混杂(启动时并跑模型测试,决策拖到 268ms+
    甚至 14.9s——**验证房期间禁止并跑重任务**);v5 干净复测:
    **err409=1**(仅剩认领先机竞速类:他家先碰则吃窗作废,服务端
    409 属对局竞争非客户端缺陷)、真弃牌代打=0、队列 p50≈100ms。
    409 剩余三类外因无法客户端根治:① 动作 POST 延迟尖峰(实测
    2128/1382ms,服务端侧);② 认领先机(他家 T+0.3 先碰,我方
    T+1.7 吃必 409);③ 事件 ts ±0.3s 双向抖动。丢碰残留 ~1-2%
    (9-19 次/房):服务端事件刷新粒度 ~0.5s + 残余队列 p90 ~0.5s
    叠加超出 1s 碰窗的尾部——**客户端物理边界**(单令牌 12.5/s
    预算下 10 场并发的事件簇需求无法再压),出路=多令牌分摊或
    平台侧放宽刷新粒度。dm 18-28 为懒门设计内统计(下限期内截止
    过期),非真实损失。**结论:SSE + /state 增量拉取、懒门+守卫为当前
    默认客户端配置;`--no-notify` 仅用于普通轮询排障,对局异常仍需按日志归因**。旧
    长轮询实测结论保留为历史基线。* * * 2026-09-10 方案切换记录。**
  ```
  **shape-v2 公共物料修复验收(2026-09-17)**:
  `Mirror` 现保存平台快照公开的 `hand_counts`（只含四家张数，不含
  对手牌面），并投影给 `PublicDecisionContext`；第 14 张快照
  `[14,13,13,13]` 可闭合 full wall 物料 136 张。无法确认的事件流将
  计数标为 unknown，shape-v2 受控回退 legacy，并记录
  `context_material_unknown`，不再以阶段公式造成 `material_conservation`
  崩溃；legacy/shape-v1/GEN0 原生 Game 训练路径不变。
  聚焦 52 passed、全量 650 passed + Rust 对拍/30 局轨迹一致。200 局×3
  轮离线性能报告显示 shape-v2 相对 shape-v1 总耗时 +182.85%、弃牌
  p95 中位约 69.14ms、回退率 97.41%，性能门禁失败，继续 offline-only，
  不切默认。第一 canary 房 `a_47fda164e28e` 10 场对账 3256 动作 0 非法，
  但 1 次 409/1 次 deadline_missed；第二房因房间生命周期中止而对账
  106 非法级联；用户要求不再启动新房，线上门禁未通过。证据与完整日志
  见 `openspec/changes/shape-v2-platform-material-context/`。
  **实测协议发现(2026-09-08,均已消化)**:
  - 快照(seq=0/gap)远比文档丰富:含四家牌河 discards/副露 melds/
    手牌数 hand_counts/wall_remaining(含死墙,开局 83)/last_discard/
    dealer/round_no/waited_seat/window_deadline_ms → Mirror 全量重建
    自愈(比计划的"仅私有锚定"更强);响应窗 turn = 出牌者
  - 事件流:他人摸牌不可见(仅自家 tile_drawn);每弃牌必产生全套
    窗口事件(响应者 pass 或 timeout kind=response window=peng/chi),
    窗口固定走满 1s(不响应=隐式过无惩罚);碰窗 (T, T+1)、吃窗
    (T+1, T+2)(T=弃牌事件 ts,以超时事件戳实证)——吃牌提交须在
    T+2 前到达,客户端从观察时刻固定等 ~1.05s 提交(截止驱动)
  - **无独立 hu 事件**:胡牌只在 round_ended.data{fan,detail,draw,
    scores,round_no,dealer} 与 rounds[]{is_draw,multiplier,winner}
  - **吃碰后偶发 `timeout kind=hu_failed`(2026-09-08 match 首见,
    5/1405 次认领,已在客户端消化)**:认领者的 post-claim 弃牌窗不
    开,turn 直达下家摸牌,手牌自此比引擎预期多 1 张(是否服务端隐性
    代打无从分辨,按 +1 漂移处理)。客户端三层防御(`tests/test_hu_failed.py`):
    ① 我方收到该事件 → 作废认领触发 + seq=0 快照重锚(不提交必 409
    的废弃牌);② `Mirror.hand_count_ok` 决策前张数自洽检查,漂移期的
    反应窗/弃牌回合交服务端代打(auto_played 计数),轮边界快照自愈;
    ③ 决策路径异常(shanten 断言 ValueError 直穿曾静默杀线程、丢一局
    无 end 记录)→ 有限 3 次快照自愈,超限上抛计一次场次异常——
    连续 3 次异常才放弃重派并落 `end(error)` 终态(重派续写同一日志,
    不再有双终态;进程崩溃仍是残局日志的唯一来源)
  - 赛后数据 blocks 按 ≤128 事件分块(首块含 start_hands,续块 null);
    牌河含 pending 牌、被 claim 后弹出(与引擎一致)
  - 测试房 M=10 → 同 4 人 10 圡并发:每 bot 须每场次独立工作线程;
    轮询预算 state 16/s/用户(跨场共享)——空闲批次 sleep 0.35s 合批
    (~1.5 req/s/场)
  - 房间生命周期:finished 后空闲 timeout_min(30min)自动 close 回收
    (令牌随之失效)——打完须尽快 re-ready 跨轮复用(实测踩坑一次)
  - game_id 形如 `{tid}_r{轮}_b{批}_t{桌}`;免认证数据端点房间 id 即
    锦标赛 id;限速 5/s/房(429 退避)
  **实弹结果(BC best.pt,10 场)**:全部胡牌无流局,吃 22+/碰 6+/杠 1
  成功,2 次吃窗 409 竞态;引擎 replay 对账 465 动作 0 非法
- [x] **结构化对局日志(2026-09-08 设计对齐后实现,149 单测全过)**:
  自记为唯一真相源(正式锦标赛赛后无免认证事件流可拉,测试房事件流
  降级为对账参考);训练数据走「原始事件+决策」路线——日志不存 obs
  张量,离线重建(与 features.py 版本解耦);口径为仅自家视角单座位
  (事件流他家暗手不可见,与 rl_env 单 agent 一致,无信息泄漏)。
  ```
  每场对局一个 JSONL:local/games/<YYYYMMDD>/<令牌>_<gid>.jsonl
  记录类型(一行一条):
  - meta      gid/令牌/tid/YCBK/base
  - req       每次状态轮询:请求时游标 seq/响应耗时 ms/状态码/
              尝试次数(含 429 退避,api._request 线程局部计数)/
              响应摘要(n_events/pending/gap/snapshot/finished)
  - snapshot 快照原文(离线重建锚点:my_hand/公共状态/墙长/座次)
  - events    事件批原文(离线重放数据源)
  - decision  决策点:phase/合法动作集/所选动作/decide 耗时/
              镜像摘要(手数/墙余/局号);id 与 action 配对
  - claim_miss 规则允许吃/碰/杠但未成功:chosen(已选动作或 null)/
              client_decision/reason/legal/chosen_legal,与 decision 配对
  - action    payload/结果(成功或错误码)/耗时/配对决策 id
  - reset     镜像失步重建原因
  - end       终局积分/原因(finished/inaccessible)
  ```
  工具链:
  - `mj/platform/recorder.py` GameLog/Recorder(每场一线程独占写,
    写失败静默降级不打断对弈);`--no-recorder` 可关,默认开
    (正式赛数据不可再生,宁落勿漏);--dump 原始报文双轨保留
  - `python3 -m mj.logview <gid>` 时间线复盘(req/decision/action 带
    seq/耗时),--types 过滤、--full-events 展开、延迟摘要
  - `python3 -m mj.log_replay <gid>` 对账校验:重放合法集 vs 线上
    记录逐决策断言(legal_actions 唯一真源的线上验证)、自家胡引擎
    step(HU) 结算 vs 服务端 fan/scores、各局积分累计 vs end 终局
  - `python3 -m mj.log2data` 日志 → data/bc_platform npz(与 bc_data
    同构:75 平面 float16+mask+action+seat+score;bc_train._pad_oracle
    补零 91 平面);**严格过滤**:对局无 illegal/reset、有终局积分,
    样本仅收配对 action 提交成功(非 409)的决策——宁可少不可脏
  测试:synth 全场驱动 → 记录 → 重放 → npz 闭环(test_platform_
  recorder / test_log_replay);FakeApi 终局积分改为与 round_ended
  事件自洽(原来硬编码与事件流矛盾,会被对账正确揪出)

- [ ] 平台对接客户端(注册/对局 API)。指南 v2-v34 协议要点(2026-09-15 在线拉取；
  `updated_at=2026-09-14`):
  - **快照无 allowed_actions**(v2 breaking):须依 seat/phase/turn/
    responding_seats/drawn_tile/god.catch_play 自研判定可执行动作
    ——直接复用引擎 `legal_actions()`;窗口"已响应"须本地跟踪,
    重复提交返回 409 INVALID_ACTION
  - **吃牌可附 `"tiles":[...]`** 指定吃组合(v2):多组合场景必传,
    缺省回退第一组(不可靠,应显式传)
  - **测试房间跨轮复用**(v4/v5):finished 后 4 令牌各 ready 一次即开
    下一轮(round_no 递增,ready 幂等);running 尾窗 ready 得 409
    TOURNAMENT_STARTED → 轮询至 finished 再重试;已报名令牌对 finished
    房 register 幂等放行;finished ≠ 可删除——可能回退 registering,
    空闲 timeout_min(默认 30)自动 close 属正常生命周期
  - **普通锦标赛多阶段化**(v7 breaking):按报名人数 m 自动晋级
    (m≥17:海选→16强→8强→决赛;9-16:海选→8强→决赛;5-8:海选→决赛;
    m=4 直决赛;m<4 作废);非决赛轮打完 = stage_done(等 admin 推进),
    **决赛打完才 finished**——bot 必须循环轮询 status 直至终态;
    决赛 1-4 名同分自动加赛,新 game_id 自动出现于 my_games,无上限
  - **status 新值与阶段字段**(v7 breaking):stage_open(确认期)/
    stage_done(+stage_crashed=true 表中断待重赛);阶段间空转窗口
    active_games 为空 ≠ 结束;ready 在 stage_open 为阶段出席确认
    (名单外 409 NOT_QUALIFIED,确认不跨阶段继承);ranking 的
    games_played 改为当阶段累计(按局计,轮空+1),进度判断改用
    status/stage;晋级排名键序 total_score→place_points→god_count→
    user_id;config 含可变参数 M/Rounds/BaseScore/**YouCaiBiKao**/
    各超时秒数——一律以 API 返回为准,不可写死
  - **/state 跨局边界轮询**(v10):round_ended 后 poll(seq=上次 seq)
    即可在出牌超时前拿到新局快照(seq 落后当前局立即返回全量,
    gap:true;快照含 round_no/phase/my_hand/seat)——旧客户端挂起至
    庄家超时被代打一张的问题消除,轮询姿态不变
  - **限速**(v11 更新):state 轮询 **16/s**(每用户聚合、跨全部对局
    共享一桶,并发挂起 ≤32;M=10 桌事件驱动 bot 需 ~13.4/s,8/s 会
    429 断档错过 1s 吃碰窗);fan-calc 10/s/IP;测试房间数据 API 每
    房间独立 5/s + 每来源 1000/s 兜底(v9 修跨房挤占)
  - **SSE 状态通知流**(v12 新增):GET /api/games/{id}/notify 服务器
    主动推「状态已变」信号替代高频轮询;帧只含 seq(与 /state 同一
    水位),30s keepalive;**游标纪律**:初始帧 seq 是包含式水位,
    不可直接当轮询游标,收帧后 GET /state?seq=本地游标 拉增量;
    每用户 32 并发连接(429),不占 /state 额度——旧长轮询姿态不受影响
  - **分桌机制修复**(v13 breaking,仅新建锦标赛生效,判别:
    config 缺 OnlineConfirm 键 = 存量赛):报名 = 意向,分桌 = 开赛
    时刻「已确认 ready ∧ 在线」(在线 = 任意已认证 Bearer 请求 90s
    内触达),按实到降档、<4 作废——**空转期(registering/
    stage_open/stage_done)无 SSE 可挂,必须对本赛端点轮询 ≤90s
    (建议 ≤60s),否则开赛时刻被判离线剔除**;同一用户报名多赛须
    按赛各保持轮询,勿以他赛流量代替;服务重启开赛顺延 ≤90s 重连窗;
    测试房(kind=test)满员即开豁免
  - **POST /api/match 自动匹配**(v13 全自动语义,supersede v12):
    服务端选房入席一体(就绪席多/开赛早优先),幂等(ready=1 判据),
    返回 {room_id, config, round_no};仅收全局令牌(scoped 报 400
    TOKEN_NOT_SCOPED);错误码 409 AUTO_MATCH_ONLY(直连 register/
    ready 被守卫)/MATCH_BUSY(在途 50 房上限,延时自重试)/
    MATCH_LIMIT_REACHED(16 场并发上限);auto 房打完 ~60s 宽限后
    自动关停,关停后玩家 API 对该房 404,再战须重新 /api/match
  - **/api/match 默认配置上调**(v15 breaking):服务默认 M=1/Rounds=2
    → **M=10/Rounds=8**(满员会话 10 场并发 × 每场 8 局,座次逐场
    重洗,单会话 80 手/人);**显式 body 上限 < 新默认(M∈1..9 或
    Rounds∈1..7)→ 永久 404 NO_ROOM_AVAILABLE 不重试**——调用时
    缺省不带 body 即可;轮询/动作节奏按 10 桌并发核对
  - **指南全文免认证端点**(v14):GET /portal/api/guide?format=text
    返回纯文本正文(与门户 Tab 同源单一数据源)——自检可直接程序化
    拉正文,不必人读 HTML
  - **免令牌数据端点收紧**(v18):正式锦标赛 id 经
    /api/test-rooms/{id}/games/{batch}/events 一律 404(此前会透出
    真名+手牌)——**锦标赛牌谱导出必须走 GET /portal/api/games/{id}/
    events(需 session)**;auto 房 seats 只下 AI 昵称,test 房不变
  - **round_ended 载荷扩展**(v19):胡牌与流局事件均自带 round_no/
    dealer 键(emit 在翻庄前取值)——复盘/跨局对账免自行推导
  - **抓打圈豁免方响应**(v26 changed):`god.god_discarder_seat` 是打财神
    者座位；圈中 `god_discarder_seat == seat` 的本人可吃/碰/明杠/补杠且
    可任意出牌。非财神弃牌时只为该豁免方按规则开固定时长响应窗，其他
    三家仍受抓打限制。
  - **门户排行榜调整**(v27/v28):积分榜新增 `prev`，`top` 不再补出
    `rank=0` 幽灵行；胡大牌榜取消全史次数排序键，改为番数↓→得分↓→
    胡手时刻后发生优先→`user_id`，`count` 只作注记。均不改变 bot 玩家
    API 契约。
  - **全服功能开关**(v29 breaking):`/api/match`、自建测试房、测试房重开
    关闭时返回 `403 FEATURE_DISABLED`，永久条件不要重试；新增门户
    `GET /portal/api/features`，在途房间照常运行。
  - **姓名字段收口**(v30):除管理面正式锦标赛外，他人 `name` 只使用 AI
    昵称，昵称为空返回空串；消费方以 `user_id` 作为稳定身份。
  - **局间暂停**(v31):每局结算后固定 5 秒；期间快照为上一局终态
    `phase="settled"`、`round_no` 不变、`waited_seat=-1`，不能提交动作。
    继续用上一局末游标轮询，发牌时服务端唤醒并返回新局全量快照。
  - **杠爆判定**(v32):摸牌后暗杠/补杠在杠动作时重算爆头；杠后手牌听
    任意牌且补牌胡时按杠爆 `×4`，历史落库分数不回溯。
  - **杠后补牌决策窗口**(v33):暗杠/补杠/明杠补到能胡的牌停在
    `phase="draw"`，客户端可 `hu`、继续杠或弃胡打财神；超时仍由服务端
    自动胡兜底，番型与分数不变。
  - **门户今日榜垫底行**(v34):`GET /portal/api/leaderboard` 新增 `last`，
    仅今日榜且人数大于 32 时返回 `{rooms,firsts,score,is_me}`，否则为
    `null`；纯门户加法，bot 玩家 API 行为不变。
  - **404 TOURNAMENT_GONE 具名**(v35 breaking,**仅契约澄清、wire 零字节
    变化、规则零变化**):服务端用**同一个 404** 承载两种相反语义——
    `TOURNAMENT_NOT_FOUND`(注册表里没有该 id;含正常关停/终态后移除)
    = 永久条件,放弃;`TOURNAMENT_GONE`(注册表里**有**该 id,但 2s 预算
    内没等到房 actor 回执,房忙/库慢,**开赛与结算瞬间最常见**)= 暂时
    条件,退避后重投同一端点(register/ready 幂等,重复提交安全)。
    **判型必须用 body 的 `code`**:只看 `status==404` 会把「暂时不可达」
    读成「房已删」而提前退出(2026-09-23 生产事故:某客户端连挂三次静默
    退出,房 `t_069a55e84b26` 开赛后该席被服务端按超时自动出牌)。
    涉及端点:`POST /api/tournaments/{id}/register`、`/ready`、
    `/api/tournaments/me/ready`、`GET /api/tournaments/{id}`、
    `/api/tournaments/me/rules`,及门户面 register/token/qualify/detail/
    ranking 与 `POST /portal/api/test-rooms/{id}/close`。不受影响:
    `/api/match` 的 409 MATCH_BUSY 与 404 NO_ROOM_AVAILABLE 一字未动;
    `/api/games/{id}/*` 的 404 是 GAME_NOT_FOUND,与本案无关。
    - **本仓处置(2026-09-24)**:判型收口到 `BotClient._formal_gone_error`
      (缺 code / NOT_FOUND 一律按永久条件)。此前已合规:锦标赛轮询与
      `scripts/tournament.py` 的探活都按 code 判型、有界重试
      (`FORMAL_TOURNAMENT_GONE_RETRY_MAX=12`,退避累计 ~80s)。本次补齐
      三处**只看状态码/没看 code** 的缺口:① `_formal_attend` 的
      register+ready 原先每阶段只发一次(占位预占),瞬时 GONE 会被静默
      吞掉 ⇒ 开赛瞬间丢席位——现改为按 `FORMAL_ATTEND_GONE_RETRY_MAX=12`
      释放占位、下一轮轮询(≈1s)重投,上限后记
      `TOURNAMENT_GONE_ATTEND_EXHAUSTED` 警告;② `_formal_resolve_context`
      拉 rules 撞 GONE 原先直接 raise ⇒ 预检 PROTOCOL_FATAL 静默退出,
      现按瞬态退避重试;③ auto 房监督 `_play_room` 的 404 原先一律当
      「房已关停」弃房 ⇒ 现按 code 区分,GONE 原地重试(3s × 12),超限
      才交 re-match 幂等兜底。回归见 `tests/test_tournament_runner.py`
      (`*_gone_404_*`/`*_not_found_*`)与 `tests/test_match_runner.py::
      test_transient_room_gone_404_keeps_room`。
    - **`rules_version` 不随 v35 上调**:`hangzhou-platform-guide-v34` 是
      决策/模型 profile 指纹的一环;v35 未改任何规则,上调会无谓改写证据
      日志里的 fingerprint 并割裂前后对局样本,故各 profile 保持 v34,
      仅 `mj/platform/probe.py:SUPPORTED_GUIDE_VERSION` 升到 35。
  - (门户-only,bot 契约零影响:v12/v14 identity 昵称与令牌轮换、
    v17 大厅剔除 auto 房、v19 胡大牌榜、v20 排行榜 20→32 行、
    v22 分组视图/晋级名单、v23 单场得分榜)
  - **匿名令牌门禁**(v24 breaking):POST /api/users 匿名自注册删除
    (404);非门户绑定(openid_identity IS NULL)的全局令牌调
    POST /api/match 或锦标赛新报名 → 403 PORTAL_BINDING_REQUIRED
    (**永久条件勿重试**);在途照常(已报名 register 幂等放行、
    ready/me/state/action/notify 不拦、auto 房在途重调 /api/match
    幂等返原房——崩溃重启天然续房;房出窗口后再调 = 新会话意图 →
    403);scoped 参赛令牌全链不动。全局令牌唯一合法来源 = 门户
    「我的 AI 身份」(OpenID 绑定,首访明文一次、可轮换)
  - **bot 启动自检**:`GET /portal/api/guide/version` 核对指南版本
    (当前 **v35**,2026-09-23;`mj.platform.probe.SUPPORTED_GUIDE_VERSION`
    同步),版本变化即触发规则复审——v7-v35 本次已核对；
    响应中的 `type=breaking` 仍需人工确认
  - 观赛快照仅覆盖服务进程内存中的场次;历史轮复盘走
    `GET /portal/api/games/{id}/events`(DB 持久)
- [ ] 锦标赛实测(多阶段赛制循环已实现于 bot_client.run,待正式赛验证)
- [ ] (后续)测试房批量攒真实平台牌谱 → BC/PPO 数据源升级
  (`runner --strategy policy --games N` 跨轮复用连打;注意每轮打完
  立即续 ready 防房间空闲回收;M=10 时每轮 10 场,~4 分钟/轮)

## 五、风险与开放问题

0. **对局残留异常(2026-09-09 长轮询终版实测口径,房
   `a_ecaccf24e5c5`/`a_5ac890c2069d`/v7 房,均为 10 局 match)**:
   长轮询+懒门+守卫已固化为 match_runner 默认(`--no-long-poll`
   回滚)。终版稳态:**真弃牌代打 0、镜像失步 0、decide_errors 0、
   队列 p50≈100ms**;以下为残留及其定性——

   | 残留 | 稳态量/房 | 根因 | 属性 |
   |---|---|---|---|
   | chi 409 | 1-3 次 | **认领先机竞速**:他家在碰窗内先碰,我方按 (T+1,T+2) 掐点提交的吃必被拒 | 对局竞争,非缺陷 |
   | chi/peng 409(偶发) | 0-2 次 | **动作 POST 延迟尖峰**(实测最高 2128/1382ms,服务端侧)把提交推过关闭点 | 服务端 |
   | 丢碰(代过) | 9-19 次(可碰窗 ~1-2%) | **服务端事件刷新粒度 ~0.5s** + 残余排队 p90 ~0.5s,叠加超出 1s 碰窗 | 客户端物理边界 |
   | deadline_missed | 13-28 次 | 懒门设计内统计(下限期内窗口截止过期后拉取)+ 少量真排队超时;死窗滞留截止已全部清理 | 基本为统计噪声 |
   | 抓打圈「弃牌代打」 | 每次财神弃牌后 1-3 次 | **被冻座位强制弃牌由服务端即时代打**(窗口≈0,摸牌与 timeout 同秒),非损失,已不计数 | 平台行为 |

   丢碰尾部的构成(实测):观测迟到 0.95-1.5s 带全部丢失,其中服务端
   刷新 ~0.5s 不可压;排队部分已在需求 ~10/s < 12.5/s 供给下尽力。
   **评估过的调度方案**(详见 P4 节实验记录):他家摸牌预置截止
   (EDF 零和挤压,回退)、紧急观测软截止 EDF
   (v6:dm 反升、丢碰不降)、纯 SSE 模式(观测尾部 4.3%)。
   **根治出路**(均超出本仓库):① 多令牌——每令牌独立 12.5/s 预算,
   10 场分摊 2-3 令牌即彻底解除饱和(最有效);② 平台侧事件刷新
   粒度 0.5s→0.1s 或 /state 推送语义;③ 接受现状(对积分影响
   ~0.1-0.3 分/局量级)。
   复查工具:`python3 -m mj.logview <gid> --windows`(每窗口的
   观测迟到/提交落点/结果)。
1. **平台数据可得性**(2026-09-03 更新:确认无锦标赛牌谱,不阻塞训练):
   SL 走 bot 自博弈 BC 冷启动,影响可控——BC 只求合法且不蠢的初始化,
   天花板由 RL 决定;工程特征已内置专家知识,弱标签也够学;
   测试房间数据(免认证)可后续回补做 fine-tune 与规则对账
2. **算力**:Suphx 用 44 GPU×2 天;我们规则简单(无点炮无防守),
   预算可压到 1-2 张 GPU × 1-2 周,但需实测吞吐
3. **规则理解偏差**:主要口径已用 fan-calc 实测校准(v21 后
   2026-09-08 对拍 22/22 全过,含 4 白板/七对财飘/豪华白板口径/
   最大番 ×512);
   残留待验证——飘的边界判定(打出后仍听任意牌)为指南文字推导,
   待测试房间对局回放终验
4. **性能**(2026-09-04 复评):RL 吞吐已够用(500k 步 1.6h,vs
   固定 bot 的训练量不再受算力限制)。**Rust 移植评估结论:当前规模
   不值得**——torch 梯度更新/推理已是 C++ 底座无从收益,可加速的
   仅 env 步进(其中 shanten dfs 占 81%),但修完 --subproc 后
   主进程串行是瓶颈,端到端仅 1.6h→~1h;代价是 ~1500 行规则重写 +
   FFI + 与 fan-calc 对拍,且规则仍在演进(有财必拷响刚加)。
   若自博弈扩到百万局量级,再做局部移植(仅 shanten+win,PyO3);
   廉价中间档:Numba @njit 包 dfs(半天工作量,预期 5-10 倍)待验证
5. **空合法动作掩码异常(未复现,2026-09-04)**:ppo3 排查期采样
   策略 watchdog 观察到一次弃牌决策点 `legal_actions()` 为空
   (各家手牌 [14,13,13,13],弃牌决策必有合法动作,理论不可能);
   引擎随机动作 fuzz 540 万步零空 legal_actions,疑点在 env
   `_advance_to_agent` 交互而非引擎本身。根因未明;sb3-contrib 对
   全 False 掩码静默回退近似均匀采样而非报错——若真发生会悄悄
   采到非法动作并触发 env 断言,训练全程未见,复现即抓
