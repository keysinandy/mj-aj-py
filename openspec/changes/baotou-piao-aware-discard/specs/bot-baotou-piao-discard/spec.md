# Spec Delta

## Purpose

legacy 启发式 bot 弃牌与胡牌抉择的爆头/财飘感知策略：持财神状态下按爆头听/爆头进张
排序进度，墙量不足时落袋为安直接胡；墙量允许延迟时统一仲裁当前 HU、财飘、下一摸爆头与
自杠，确保 two-ply/continuation 与诊断链不被特殊分支绕过。

## ADDED Requirements

### Requirement: 持财神弃牌排序的爆头听优先档

legacy 弃牌排序在手牌持有财神（白板）时 SHALL 引入爆头听优先档：同最低向听候选中，
弃后站立手牌为爆头听（听任意牌）的候选 MUST 排在弃后为普通听牌的候选之前；向听数
仍为硬约束（更高向听候选即使弃后更接近爆头听也不得胜出）。不持财神时，弃牌排序
MUST 与既有 legacy 基线逐候选一致。

#### Scenario: 爆头听候选胜出
- **WHEN** 持财神、最小向听候选同时含「弃后站立手听任意牌」与「弃后普通听牌」两类
- **THEN** 选择弃后为爆头听的候选，即使其普通进张数更低

#### Scenario: 不持财神不漂移
- **WHEN** 手牌不含财神且选择 legacy
- **THEN** 弃牌排序与既有 legacy 冻结基线一致

#### Scenario: 向听硬约束保持
- **WHEN** 某候选弃后向听更接近爆头听但当前向听数更高
- **THEN** 该候选不参与优先档比较，仍按既有向听约束淘汰

### Requirement: 持财神听牌态按爆头与当前自摸胡牌加权进度排序(自适应收手)

持财神且弃后站立手为普通听牌（非爆头听）时，同最低向听候选的进度分 SHALL 为
`1.5 × baotou_ukeire + current_selfdraw_hu_ukeire`。两种进张 MUST 作为一个组合值比较，
不得将爆头进张设为普通自摸胡牌进张之前的独立排序层。当前自摸胡牌进张 SHALL 按每个
候选自己的弃后站立手和当前规则门禁计算；处于爆头听的 tier 0 候选按所有合法下一摸
计数。现有 tier 优先级、财神保护和 X/Y/Z 自适应收手保持不变。

**验收口径：有财必拷响关闭。有财必拷响开启的场景当前项目不考虑**——门禁下的
收手语义（无平胡可收、推进是否回退）未定义、未验收，启用前必须重新评估。

推进 SHALL 受自适应收手调节，任一触发即回退既有 legacy 排序（速度线）且不再
弃胡博倍率：
- 进入推进态（持财神+听牌）后自己的弃牌决策累计轮数达到 X 时收手；
- 任意对手副露数 ≥ Y 时收手；
- 活墙可摸张数（死墙已扣）< Z 时收手；
- 轮数/副露只增、活墙只减，收手 MUST 为吸收态（离开推进态时轮数清零）；
- X/Y/Z 取值 SHALL 由配对 A/B 结算分扫描选定并进入配置常量。
轮数记账按 Game 实例；平台镜像每决策重建 Game 时 X 不累积（Y/Z 仍生效），
该限制 MUST 在文档中声明。

#### Scenario: 比较组合进度而非爆头进张单项
- **WHEN** 同档候选 A 的爆头进张为 7、当前自摸胡牌进张为 9，候选 B 分别为 5 和 21
- **THEN** 选择候选 B，因为组合分 28.5 大于 19.5

#### Scenario: 收手回速度线
- **WHEN** 推进轮数达到 X、或任意对手副露 ≥ Y、或活墙可摸 < Z
- **THEN** 弃牌排序回退既有 legacy 键，且弃胡打白飘在该状态被抑制

#### Scenario: 收手为吸收态
- **WHEN** 收手已触发且推进态持续（仍持财神且听牌）
- **THEN** 后续决策保持速度线，不随轮数/副露/活墙波动摇摆；离开推进态后重新进入则轮数重新起算

### Requirement: 墙量守卫与财飘门限统一

墙量守卫 SHALL 统一：HU 合法且活墙可摸张数（`live_wall_left()` 口径，已扣除死墙）
小于 6 时 MUST 直接提交 HU，不得弃胡博爆头/财飘；爆头态弃胡打白飘的墙门 SHALL 为
活墙可摸张数 ≥ 6，与守卫共用同一常量。

#### Scenario: 墙量不足直接胡
- **WHEN** 爆头态摸到财神、HU 合法、活墙可摸张数为 5
- **THEN** 提交 HU，不弃胡打白飘

#### Scenario: 墙量充足允许财飘进入比较
- **WHEN** HU 合法、活墙可摸张数 ≥ 6 且合法弃财神后站立手仍听任意牌
- **THEN** 财飘 MUST 作为 HU-window action-root 候选进入统一价值比较；墙量达到门槛本身
  MUST NOT 等价为“必弃胡”，也不得被非财神爆头候选提前截断

### Requirement: HU 合法窗口统一仲裁立即胡、财飘、下一摸爆头与自杠

当前摸牌窗口 `HU` 合法时，BOT MUST 先完整建立合法 action-root 候选，再做选择。
除 `live_wall_left() < PIAO_WALL_GUARD` 的直接 HU 硬守卫外，任何爆头/财飘 helper
MUST NOT 在候选集建立完成前提前返回。

候选 MUST 至少包含：

- `immediate_hu`：当前合法 HU；
- `piao_discard`：`W` 在当前 `legal_actions()` 中，且弃 `W` 后
  `is_baotou_wait(standing, locked)` 为真；
- `baotou_next_draw`：合法非 `W` 弃牌，弃后保留财神且
  `is_baotou_wait(standing, locked)` 为真；
- `self_kong`：当前合法的暗杠/补杠候选。

财神与非财神弃牌 MUST 都从引擎动作集出发。实现 MAY 保留
`_next_draw_baotou_discard()` 作为非财神候选发现器，但该 helper MUST NOT 拥有
“发现即覆盖 HU”的最终决策权；其 `tile == W` 过滤 MUST NOT 阻止独立的
`piao_discard` 候选生成。

#### Scenario: seq856 尚未形成财飘

- **WHEN** 13 张为
  `1w×2 4w×2 1b×2 2t 3t×2 6t 白×3`，摸到中形成 HU 合法窗口
- **THEN** 弃中可作为 `baotou_next_draw` 候选；由于弃白后不能保持全牌爆头听，
  MUST NOT 生成 `piao_discard`

#### Scenario: seq880 是财飘成立的分界点

- **WHEN** seq880 摸 2t 后可形成稳定 13 张
  `1w×2 4w×2 1b×2 2t×2 3t×2 白×3`，当前 HU 合法且弃白为合法动作
- **THEN** 弃白后形成
  `1w×2 4w×2 1b×2 2t×2 3t×2 + 当前进张×1 + 白×2` 的全牌爆头听，
  `piao_discard` MUST 存在，并与约 4 番 immediate HU、非白下一摸爆头候选在同一层比较；
  MUST NOT 先返回 `hu_baotou_next_draw_override`

#### Scenario: 后续稳定结构持续产生财飘候选

- **WHEN** 回放来到 seq904/943/967/991/1015/1039/1063，手牌仍满足 seq880 后的稳定结构，
  且弃白合法并保持 `is_baotou_wait`
- **THEN** 每次都 MUST 生成 `piao_discard`；活墙 14、10、6 只要未低于硬守卫，
  均 MUST 进入统一仲裁，而不是由非白爆头分支提前返回

#### Scenario: 墙量不足时当前 HU 直接胜出

- **WHEN** HU 合法且活墙可摸张数 < `PIAO_WALL_GUARD`
- **THEN** 直接 HU，不构造或执行任何延迟胡 two-ply/continuation

### Requirement: HU 窗口延迟胡候选必须执行统一价值比较

当活墙达到硬门且存在 `piao_discard` 或 `baotou_next_draw` 时，当前 evaluator 的
continuation / two-ply 评价 MUST 真正执行，不得以“弃后已全牌爆头听”为理由跳过。
`immediate_hu` SHALL 作为确定性基线；财飘/下一摸爆头的倍率收益、下一次轮到本家的概率、
剩余墙量与被他家先胡的风险 SHALL 保留在延迟动作价值中。

`wall >= PIAO_WALL_GUARD` 只表示延迟胡候选可参与比较，MUST NOT 被解释为“必须过胡”。
X/Y/Z 自适应收手也 MUST NOT 形成非对称早退：不能删除财飘候选的同时让非白爆头候选
忽略同一软门直接覆盖 HU。若 X/Y/Z 继续参与 HU-window，MUST 以所有延迟胡候选共享的
评价输入/惩罚形式使用。

#### Scenario: 4 番立即胡与 8 番财飘进入同一价值层

- **WHEN** 当前 immediate HU 为七对+爆头约 4 番，弃白后下一次自摸路线为
  七对+财飘+爆头约 8 番，且硬墙门通过
- **THEN** 两条路线 MUST 同时出现在 action-root 评价中；选择由完整动作价值决定，
  不得由候选枚举顺序决定

#### Scenario: two-ply 不得被爆头 override 绕过

- **WHEN** legacyV2/支持 Stage B 的 evaluator 在 HU 窗口同时存在 immediate HU 与延迟胡候选
- **THEN** 若该 profile 按正常规则应进入 Stage B/continuation，则 MUST 实际进入并记录；
  `hu_baotou_next_draw_override` MUST NOT 作为提前返回原因

### Requirement: HU 窗口审计字段必须反映真实决策路径

进入统一仲裁时，解释数据 MUST 能区分 action-root 与普通 legacy 弃牌路径。
`decision_scope` SHALL 为 `hu_window_arbitration`（或等价稳定枚举值）；
爆头/财飘候选扫描实际执行时 MUST 记录 `baotou_scope.entered=true` 或等价显式字段；
`stage_b_entered` / continuation 状态 MUST 与真实执行一致。候选解释 MUST 至少能区分
`immediate_hu`、`piao_discard`、`baotou_next_draw`、`self_kong`，并记录 selected/reason。

#### Scenario: 不再误标为 legacy 未进入爆头作用域

- **WHEN** HU 窗口执行了爆头/财飘候选扫描并完成统一仲裁
- **THEN** 不得输出 `decision_scope=legacy` 且 `baotou_scope.entered=false` 的组合

#### Scenario: seq880 审计可解释

- **WHEN** 回放 seq880 进入 HU-window 仲裁
- **THEN** 解释中能同时观察 immediate HU 与财飘候选、墙量、是否执行 Stage B/continuation、
  最终动作及其 reason

### Requirement: 爆头进张的可见牌口径

爆头进张的未见折算 SHALL 沿用与既有进张计算相同的单一 visible 口径：可见计数包含
被评估手牌，折算只在 `4 − visible` 一处发生；MUST NOT 对自家持牌出现双重扣减。
候选间的最终排序 MUST 保留既有稳定排序兜底（牌编号仅作同分裁决）。

#### Scenario: 自持刻子不双重扣减
- **WHEN** 构造爆头进张评估时手牌自持某牌多张
- **THEN** 该牌的未见折算与既有进张计算口径一致，只扣除一次

### Requirement: 爆头进张计算受预算与回退约束

爆头进张计算 MUST 有界并计入 legacy 弃牌决策的既有性能约束（弃牌 p95 ≤ 20ms 口径）；
计算超预算时 MUST 回退到既有 legacy 弃牌排序，且回退原因 MUST 可归因记录，MUST NOT
静默混用新旧排序的部分结果。预算判定 MUST 确定性（按节点数，不依赖墙钟——同种子
决策可复现；墙钟耗时仅作诊断记录）。Rust 内核不可用时 MUST 整档跳过（纯 Python
枚举不可用），行为回退既有 legacy 排序。

#### Scenario: 预算超限回退
- **WHEN** 某决策点的爆头进张计算超出声明节点预算
- **THEN** 该决策点使用既有 legacy 排序产出弃牌，并记录回退原因

#### Scenario: 无 Rust 内核跳过新档
- **WHEN** mj_kernels 扩展未安装或被 MJ_KERNELS=python 强制回退
- **THEN** 弃牌排序与既有 legacy 基线一致，不产生超时回退归因

#### Scenario: 决策确定性
- **WHEN** 同一手牌与牌面在任意机器负载下重复决策
- **THEN** 弃牌选择相同（回退判定不受墙钟影响）
