# Proposal

## Why

legacy 启发式 bot 的舍牌 tie-break 用 `ukeire` 度量进度，而 ukeire 的听牌分支（s==0）数的是
`is_win` 胡牌张。在「手中有财神、站立手非爆头」的状态下这是假优先级：有财必拷响（YCBK）开启时，
门禁看的是摸牌前站立手是否爆头、与摸到什么牌无关——这手牌"听"的所有牌一张都提交不了 HU，
真实进展只有两条路：弃成爆头听（听任意，之后任意摸牌可胡、爆头 ×2）与杠开。此外即使 YCBK 关闭，
爆头/财飘的倍率（×2 起 / ×4 起）也使"优先爆头与财飘"在期望上是更优策略。当前排序对两者都盲。

爆头态的弃胡打白飘决策（`_should_piao`）已存在，但门限为 `live_wall_left >= 5`，且弃牌抉择层
（`choose_discard`）完全没有爆头/财飘感知，两个决策层不通贯。

## What Changes

- **legacy `choose_discard` 引入爆头听优先档**：同最低向听候选中，弃后站立手为爆头听
  （听任意牌）的候选排在普通听牌候选之前；财神保护口径不变。
- **持财神非爆头听牌态的进度度量替换**：该状态下普通 ukeire 的胡牌张（YCBK 下不可兑现）
  不再作为唯一进度信号，排序引入「爆头进张」——摸 t 后存在合法弃牌 d 使
  `standing + t − d` 为爆头听的未见加权张数，与普通进张同尺度参与 tie-break。
- **墙量守卫**：`Game.live_wall_left()`（已扣除死墙、即"接下来可摸的牌"口径）**< 6 时
  落袋为安直接胡**，不博爆头/财飘；`_should_piao` 的墙门从 `>= 5` 收紧到 `>= 6`，
  两处守卫同一常量。
- **HU 窗口统一仲裁**：HU 合法且活墙达到 6 张轮回门槛时，不再让“合法非财神弃牌可形成
  爆头听”直接覆盖当前 HU。策略 MUST 先完整构造 immediate HU、弃白财飘、弃非白进入下一摸
  爆头听、自杠等合法 action-root，再用当前策略的 continuation / two-ply 价值统一比较；只有
  活墙 < 6 仍保留直接 HU 的硬短路。
- **Guaranteed Next-Draw HU 特例**：若某个延迟候选弃后已是全牌爆头听，且按当前公开未见牌
  质量下一次自己的摸牌 100% 可胡，则它不再受 `rounds`、`opp_melds`、
  `BAOTOU_PUSH_MIN_LIVE` 等 X/Y/Z 软收手归零。只保留 `PIAO_WALL_GUARD` 硬墙门，随后用真实
  next-draw raw EV 与 immediate HU 比较。该特例同样适用于满足条件的 `piao_discard`。
- **高价值摸牌自然计分**：Guaranteed Next-Draw HU 的 raw EV 必须逐种下一摸继续调用现有
  `hand_multiplier + settle`；例如下一摸形成豪华七对子时自然提高该摸牌分支价值，不得再叠加
  人工 `luxury_bonus` 造成双算。
- **财飘候选不得被非白爆头提前截断**：HU 窗口枚举弃牌时财神（白板）与非财神均从
  `legal_actions()` 出发；只要弃白后仍为全牌爆头听，白板 MUST 作为财飘候选进入同一比较，
  `_next_draw_baotou_discard()` 一类 helper 不得在候选集建立完成前提前返回。
- **新增 Piao Search 财飘搜寻态**：当站立手白板数量 ≥2 且已经是全牌爆头听，但当前摸牌
  还不能合法弃白保持爆头时，不直接用“固定等待 N 轮”决定过胡。先计算轻量
  `piao_ukeire / piao_ratio`，衡量下一次自摸后“固定弃一张白仍保持爆头”的机会密度；
  再结合保守的未来自摸次数 `self_draw_horizon` 决定是否值得把搜索动作送入 HU-window 仲裁。
- **固定轮数只作 safety cap**：财飘搜索每次自摸都重新计算机会密度和剩余 horizon；
  `max_search_passes` 只防止长期连续过胡，不作为主要收益判断。
- **快门先行、贵搜索后置**：`piao_ukeire` 只枚举最多 34 种下一摸并做固定“摸牌→弃白→
  `is_baotou_wait`”结构检查，不调用 shanten/ukeire/scoring，也不嵌套枚举弃牌；只有快门通过
  才允许进入现有 Stage B/continuation。结构 mask 必须缓存并受确定性节点预算约束。
- **无条件生效**：新排序与守卫不依赖 `you_cai_bi_kao` 标志。YCBK 只继续影响 HU 合法性门禁
  （`legal_actions`，本变更不改）；策略偏好（优先爆头/财飘）对任何对局生效。
- 爆头听/进张排序仅改 legacy 路径；HU 窗口统一仲裁放在共享动作分支，所有 evaluator
  一致生效。shape-v1 评价器排序、`ukeire` 纯函数契约（`(counts, locked, visible)`）、
  Rust 内核与对拍口径均不动。
- **审计语义修正**：进入 HU 窗口统一仲裁时必须准确记录 `decision_scope`、爆头候选扫描、
  Stage B/continuation 是否实际执行以及各 action-root 候选；不得再把真实爆头覆盖路径标成
  `decision_scope=legacy`、`baotou_scope.entered=false`。

## Capabilities

### New Capabilities
- `bot-baotou-piao-discard`: legacy 弃牌与胡牌抉择的爆头/财飘感知策略——爆头听优先档、
  持财神听牌态的组合进度分、墙量守卫（< 6 直接胡）、HU action-root 统一仲裁、
  白板≥2爆头态的低成本 Piao Search 与可审计解释。

### Modified Capabilities
- `bot-react-decision`: 既有 requirement 规定「legacy 的 `choose_discard()` 排序 MUST 保持；
  `_should_piao` 财飘行为 MUST 保持既有语义」。本变更换掉持财神状态下的 legacy 弃牌排序、
  并把 `_should_piao` 墙门 5→6，需要 delta 修订该条 requirement 的约束范围
  （非持财神状态的排序与门槛语义保持不变）。

## Impact

- `mj/bot.py`：`choose_discard`、`_should_piao`、`choose_action` 的 HU/飘基线分支，以及
  Piao Search 快门、horizon、HU-window candidate-specific delay policy 与候选解释。
- `mj/shanten.py` / `rust/`：可新增独立 `piao_draw_mask`/批量结构算子；不得修改通用
  `ukeire` 契约。Python 实现只有在性能门通过时才可作为线上路径，否则使用 Rust 或关闭该档。
- `mj/hand_eval.py`：仅 `budget_fallback_legacy` 回退路径经 `choose_discard` 间接继承新排序，
  评价器 requirement 不变。
- **下游数据口径**：bot 是 BC 冷启动 teacher 与 RL 对手——bot 行为变化会改变后续
  `mj.bc_data` 生成样本的分布；已产出的 `data/bc/` 分片不回改。
- 平台线上（match_runner / tournament_runner 的 `--strategy bot --bot-evaluator legacy`）
  行为随之变化。
- `PROGRESS.md` bot 决策原则节与测试（bot 行为单测 + 新排序回归）必须同步。
- 语义风险声明：用户口径「可爆头/财飘时不能胡」若指无条件规则，则与 PROGRESS 记录的
  平台 v34 裁定（YCBK 关时持财神平胡合法、爆头态摸白可直接胡）矛盾——本变更**不动
  `legal_actions` 门禁**，将其落为策略偏好（弃胡推进爆头/财飘）而非合法性变更；
  若需改门禁须先对 `GET /portal/api/guide/version` + fan-calc 对拍重新核实。
