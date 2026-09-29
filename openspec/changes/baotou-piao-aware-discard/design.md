# Design

## Context

legacy 弃牌决策链为 `choose_action` →（无 HU/杠时）`choose_discard`：先取最小向听候选，
再按 `(t==W, -uke, shape, feed, t)` 排序；`_should_piao`（bot.py:416）在爆头态摸白时以
`live_wall_left() >= 5` 决定弃胡打白飘。`ukeire` 是纯函数
`(counts, locked, visible)`，被 BC/RL/shape 评价器与 Rust 内核对拍（`scripts/rust_parity.py`）
共用。CLAUDE.md 硬约束：`legal_actions()` 是合法性唯一真源；shanten 类优化必须配随机差分
验证；引擎/规则行为改动必须同步 PROGRESS.md。线上策略走 `Mirror.build_game`，
Mirror 提供 `live_wall_left()`（mirror.py:572，`64 − _pops` 口径）。

## Goals / Non-Goals

**Goals:**
- 持财神状态下，legacy 弃牌排序反映真实进度：爆头听优先档 + 爆头与当前合法自摸胡牌的加权进度。
- 墙量守卫统一为一个常量（活墙可摸 < 6 → 直接 HU），弃牌层与飘决策通贯。
- HU 合法且活墙允许延迟时，完整比较 immediate HU、弃白财飘、弃非白进入下一摸爆头听、
  自杠等 action-root；任何爆头 helper 都不得在财飘/当前 HU/two-ply 进入比较前提前返回。
- seq856 明确保持“弃白不成立”，seq880 起明确识别“弃白可保持爆头并形成财飘”的状态迁移。
- 新计算有界、可回退、可归因，不破坏既有延迟约束。

**Non-Goals:**
- 不改 `legal_actions()` 的 HU 门禁（YCBK 语义不动）。
- **有财必拷响开启的场景当前项目不考虑**（用户口径 2026-09-18）：
  收手语义在门禁下未定义，启用前必须重新评估；YCBK 开下的 A/B
  （+0.97/局 CI[+0.51,+1.45]）仅作参考数据存档，不作为验收依据。
- 不改 `ukeire` 既有语义与 Rust 对拍口径；新度量为并列纯函数。
- 不改 shape-v1 评价器排序。
- 不引入无限深搜索；HU 窗口复用既有 one-draw / weighted two-ply / continuation 能力，
  只把比较层级提升到 action-root，避免特殊分支绕过现有评价器。
- 不回刷既有 `data/bc/` 分片。

## Decisions

### D1. 落点：bot 策略层 + 并列纯函数，ukeire 契约不动

新度量 `baotou_ukeire(counts, locked, visible)` 实现为独立纯函数（与 ukeire 同层放置，
便于单测与后续 Rust 化），`choose_discard` 在触发态改用它。`ukeire` 本身不动——策略
语义（优先爆头/财飘）不属于通用效率度量，且其契约被 BC/RL/对拍共用。
*替代方案（否决）*：给 `ukeire` 加 `ycbk/baotou` 参数改 tenpai 分支——契约波及
全链路，训练数据口径被动漂移。

### D2. 触发态与排序键

触发条件：`hand[W] > 0` 且候选弃后站立手为听牌（s==0）。s>0（未听牌）时排序完全
不变——向听/普通进张仍是正确的构型信号，爆头是听牌后的选择。触发态排序键：

```
key = (tier, t == W,
       -(1.5 * baotou_uke + current_selfdraw_hu_ukeire),
       shape, feed, t)
tier = 0 if is_baotou_wait(弃后站立手) else 1
```

- tier 0（弃后爆头听）整体优先于 tier 1；tier 0 内部保持既有财神保护次序
  （不主动弃白——弃白飘由 `_should_piao` 与引擎 chain 规则在 HU 决策点管）。
- 同档的进度信号使用 `1.5 * baotou_uke + current_selfdraw_hu_ukeire`；爆头进张与
  当前合法自摸胡牌进张合并比较，不作为两个先后级别。tier 0 的当前自摸胡牌进张也
  按爆头听的全牌听口计算。
- 不持财神：键与既有 legacy 完全一致（回归保证）。

**自适应收手（2026-09-18 增补，替代「无条件生效」初版）**：30720 局 A/B 实测——
YCBK 关 −0.37/局 CI[−0.67,−0.07]——无条件推进在关的场景净亏（普通进张可兑现，
速度损失 > 倍率增益，与 09-17 番型感知结论同构）。故推进受三个收手信号调节，
任一触发即回速度线（legacy 键 + 不弃胡）：
X = 推进态轮数上限（进入「持财神+听牌」后自己的弃牌决策数，per-Game 实例记账，
WeakKeyDictionary；镜像局每决策重建 Game 故 X 不累积、Y/Z 仍生效）、
Y = 任意对手副露数阈值、Z = 活墙可摸张数阈值。轮/副露只增、活墙只减 → 收手为
吸收态；离开推进态轮数清零。弃胡打白飘在收手态（轮数/副露两信号，墙量走
PIAO_WALL_GUARD 硬门）同样落袋为安。
X/Y/Z 由 `local/ab_baotou_sweep.py` 网格扫描（粗筛 3840 局/配置 + 精跑 30720 局/
配置，**YCBK 关口径**的配对结算分）选定，参照点含 X=0（=始终速度线）与
X=99/Y=99/Z=0（=无条件推进）。

### D3. `baotou_ukeire` 定义、剪枝、缓存与 Rust 内核

定义：对站立手 S（13−3·locked 张），
`u1_baotou = Σ_{t} (4 − visible[t])`，其中 t 满足「存在合法弃牌 d 使
`is_baotou_wait(S + t − d)`」且 `S[t] < 4`。visible 口径与 ukeire 相同（含被评估
手牌，单处折算）。

- 剪枝（比 ukeire 更激进）：爆头听的自然牌部分必须互相连接，远处孤立牌唯一
  途径是 (W,W,t) 刻子 + 第 3 个财神配对——故 `counts[W] >= 3` 不剪（保守冗余），
  否则用 `_ukeire_candidates`；剪枝安全性由随机差分保证（W=0..2 手牌，
  `tests/test_shanten.py`）。
- **预算与确定性（e2e 轨迹分歧教训）**：初版用墙钟预算（8ms），`scripts/rust_parity.py
  --e2e` 暴露 Python 慢路径下 tier-0 检查累积超预算 → 回退、Rust 快路径不回退 → 同种子
  轨迹分叉（shape-v1 墙钟抖动的同款问题）。修正：**预算只按节点数**（确定性、同种子
  可复现），墙钟耗时仅记 `info["baotou_elapsed_ms"]` 诊断；**无 Rust 内核时整档跳过**
  （`BAOTOU_UKEIRE_RUST` 门,纯 Python 枚举 90–220ms/决策不可用),行为回退旧排序。
  修正后 e2e 轨迹逐局一致(winner/mult)。
- **Rust 内核（2026-09-18 修订，D3 原「暂缓 Rust」翻案）**：实测纯 Python 全量
  枚举 90–220ms/决策（is_win 冷 ~30µs/次 × ~20 万次判定），任何预算下新档必回退。
  落地 `mj_kernels.baotou_ukeire` 批量算子：
  - **`is_baotou_wait` O(1) 等价刻画**：听任意 ⟺ 持 ≥1 财神 且 (a) 去一张财神后
    恰组成 4−locked 个面子（`melds_complete`，win.py::_melds 的 Rust 移植），或
    (b) locked=0 七对路径（自然单张 s 满足 wilds ≥ s+1 且 (wilds−s−1) 为偶）。
    把 34 次向听/和牌判定换成一次面子回溯；
  - 实测：触发态决策冷 3.5–5.7ms / 暖 0.9ms（节点预算内常驻，无回退），
    相对纯 Python 30–50×；
  - 差分：`scripts/rust_parity.py` 扩 `parity_baotou`（locked 0..4 × 财神 0..4 ×
    None/随机 visible，5000 手全过）+ `tests/test_shanten.py` 剪枝差分；
  - 调度器：`mj/shanten.py::baotou_ukeire` Rust 优先、`baotou_ukeire_py` 回退，
    `MJ_KERNELS=python` 强制回退；线上机器需重装 `pip install -e rust/`。
- Python 回退实现保持定义版（34 次 is_win + win.py 记忆化），语义以它为基准。

### D4. 墙量守卫

常量 `PIAO_WALL_GUARD = 6`（bot.py 模块级）：
- `_should_piao`：`live_wall_left() >= PIAO_WALL_GUARD`（5 → 6 收紧）。
- `choose_action`：`HU ∈ legal_actions 且 live_wall_left() < PIAO_WALL_GUARD`
  时短路直接返回 HU——跳过弃胡飘与杠的期望比较（落袋为安）。
- 口径声明：`live_wall_left()` = 全桌剩余可摸张数、已扣除死墙（`len(wall) − DEAD_WALL`），
  与既有 `_should_piao` 的 5 同尺度；「不包含不能摸的牌」即死墙已扣。线上 Mirror
  同口径。若后续要按「自家可摸 ≈ live_wall/4」改口径，只动常量换算处，spec 不变。

### D4a. HU 窗口 action-root 统一仲裁

`_choose_draw_action` 在 `HU` 合法时先执行唯一硬短路：`live_wall_left() <
PIAO_WALL_GUARD` 直接 HU。墙门通过只表示“允许评估延迟胡”，**不表示必须过胡**。

随后一次性建立 action-root 候选集，候选来源必须全部受 `legal_actions()` 约束：

1. **immediate_hu**：当前 HU，价值由现有即时结算/番型口径给出；
2. **piao_discard**：合法弃 `W` 且弃后 `is_baotou_wait(standing, locked)` 为真；
3. **baotou_next_draw**：合法弃非 `W` 且弃后保留财神并
   `is_baotou_wait(standing, locked)` 为真；
4. **self_kong**：当前合法暗杠/补杠，沿用既有杠 hard gate 与 continuation。

候选集建立完成前不得 return。现有 `_next_draw_baotou_discard()` 可继续作为“非白候选发现器”
或被拆成通用枚举 helper，但它不得拥有覆盖 HU 的最终决策权，也不得因为过滤 `tile == W`
而阻止财飘候选进入 action-root 比较。

HU 窗口内，X/Y/Z 自适应收手不得作为**非对称早退门**：不能出现“财飘因软收手被删掉，
但非白爆头却忽略软收手直接覆盖 HU”。这些信号若继续使用，只能作为所有延迟胡候选共享的
评价输入/惩罚；硬合法性与 `PIAO_WALL_GUARD` 仍然有效。

### D4b. action-root two-ply / continuation 价值

legacyV2 在 HU 窗口发现任一延迟胡候选时，必须让当前配置的评价链真正参与：
immediate HU 作为确定性基线；`piao_discard` 与 `baotou_next_draw` 进入与当前策略一致的
下一摸/continuation 价值计算；自杠沿用既有替换摸牌 continuation。不得因为某个候选
`is_baotou_wait` 为真就跳过 weighted two-ply / Stage B。

这里比较的是**动作价值**而不是“8 番数字天然大于 4 番所以无条件弃胡”。番型倍率必须进入
候选价值，但延迟一轮的被抢胡风险、剩余墙量、可摸概率也必须保留。换言之，`wall >= 6`
只开放财飘/爆头候选，不等价于选择它们。

### D4c. 回放边界与审计

2026-09-29 round4 回放作为强制回归：

- **seq856**：13 张
  `1w×2 4w×2 1b×2 2t 3t×2 6t 白×3` 摸中。HU 合法；弃中可恢复全牌爆头听；
  弃白后不能保持爆头，因此 **不得生成 piao_discard**。
- **seq880**：摸 2t 后实际弃 6t，形成
  `1w×2 4w×2 1b×2 2t×2 3t×2 白×3`。从此状态起，后续自己的摸牌窗口只要动作合法，
  弃白后仍为全牌爆头听，因此 **必须生成 piao_discard**。
- **seq904/943/967/991/1015/1039/1063**：同一稳定结构下的后续进张继续验证上述性质；
  即使活墙为 14、10、6，只要未低于硬墙门，都应进入统一仲裁，而不是
  `hu_baotou_next_draw_override` 提前返回。

解释字段必须与真实路径一致：进入该比较时 `decision_scope=hu_window_arbitration`；
爆头/财飘候选扫描实际执行时 `baotou_scope.entered=true`（或等价显式字段）；
`stage_b_entered` / continuation 字段必须反映是否真正执行；候选解释至少区分
`immediate_hu`、`piao_discard`、`baotou_next_draw`、`self_kong`，并记录最终
selected/reason。不得再把真实爆头候选路径包装成 `decision_scope=legacy` 且
`baotou_scope.entered=false`.

### D5. 测试与验收

- 单测：新排序用例（tier0 胜出 / tier1 组合进度排序 / 不持财神回归 / 墙 5 直接胡 /
  墙 ≥6 进入 HU-window 仲裁 / YCBK 开关不影响弃牌选择）；旧 legacy 排序断言显式限定到不持财神状态。
- 回放回归：seq856 断言无财飘候选；seq880 与 904/943/967/991/1015/1039/1063 断言财飘候选存在、
  不再由 `hu_baotou_next_draw_override` 提前返回，并检查 Stage B/continuation 与审计字段。
- 随机差分：`baotou_ukeire` 剪枝前后等值（随机手牌 × locked 档）。
- 评估：新旧 bot `fair_match` 对弈（192 局口径）确认无胜率/均分回退；
  线上先 match_runner 冒烟（必须走 `Mirror.build_game`），再进锦标赛。
- PROGRESS.md：bot 决策原则节同步（新增优先档描述与墙守卫常量）。

## Risks / Trade-offs

- [爆头进张枚举纯 Python 开销] → 触发态限定 + 双层记忆化 + 预算回退；压测超
  p95 再评估 Rust 算子。
- [bot 行为变化 → BC teacher / RL 对手分布漂移] → 只影响之后生成的数据；
  是否重训另行决策，不在本 change 内。
- [「可爆头/财飘时不能胡」若为平台无条件新规则，与 PROGRESS v34 裁定冲突]
  → 本变更不动门禁，冲突归零；若平台核实出新规则，另立 change 改
  `legal_actions` 并重对拍 fan-calc。
- [现有测试断言 legacy 排序] → 旧断言限定不持财神，新增持财神用例覆盖新档。

## Migration Plan

1. 实现 + 单测 + 差分 + `fair_match` 对比，全绿后合入（单 commit，可整体 revert 回滚）。
2. match_runner 冒烟 → 锦标赛启用（`--strategy bot --bot-evaluator legacy`）。
3. 回滚策略：revert 该 commit；无数据迁移、无线上状态依赖。

## Open Questions

- 爆头进张是否把「杠开」也计为进度（杠后补牌绕开门禁）：暂不计，杠路径仍由
  `choose_action` 既有期望比较决定；若线上验收发现持财听牌态错过杠开，再补。
