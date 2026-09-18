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
- **无条件生效**：新排序与守卫不依赖 `you_cai_bi_kao` 标志。YCBK 只继续影响 HU 合法性门禁
  （`legal_actions`，本变更不改）；策略偏好（优先爆头/财飘）对任何对局生效。
- 仅改 legacy 路径；shape-v1 评价器、`ukeire` 纯函数契约
  （`(counts, locked, visible)`）、Rust 内核与对拍口径均不动。

## Capabilities

### New Capabilities
- `bot-baotou-piao-discard`: legacy 弃牌与胡牌抉择的爆头/财飘感知策略——爆头听优先档、
  持财神非爆头态的爆头进张度量、墙量守卫（< 6 直接胡）与 `_should_piao` 门限统一。

### Modified Capabilities
- `bot-react-decision`: 既有 requirement 规定「legacy 的 `choose_discard()` 排序 MUST 保持；
  `_should_piao` 财飘行为 MUST 保持既有语义」。本变更换掉持财神状态下的 legacy 弃牌排序、
  并把 `_should_piao` 墙门 5→6，需要 delta 修订该条 requirement 的约束范围
  （非持财神状态的排序与门槛语义保持不变）。

## Impact

- `mj/bot.py`：`choose_discard`、`_should_piao`、`choose_action` 的 HU/飘基线分支。
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
