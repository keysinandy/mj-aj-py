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
计数。现有 tier 优先级与财神保护保持不变；X/Y/Z 继续服务普通爆头推进，但
白板≥2且已经爆头的 HU-window 财飘搜寻 SHALL 按后文 Piao Search 动态规则处理，固定轮数
不得作为该状态的主要过胡依据。

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

### Requirement: 白板≥2且已爆头时进入财飘搜寻资格

当一个合法弃牌后的 13 张站立手 `S` 满足 `S[W] >= 2` 且
`is_baotou_wait(S, locked)` 时，BOT SHALL 将该状态标记为 Piao Search eligible。
该资格不等于必须过 HU，也不等于当前已经可以财飘。

若当前 HU 窗口中 `W` 是合法弃牌且弃 `W` 后仍为 `is_baotou_wait`，则状态为
`PIAO_READY`，直接进入既有 `immediate_hu vs piao_discard` action-root 仲裁；
否则状态为 `PIAO_SEARCH`，只有通过机会密度与 horizon 快门后，才允许把“继续保白等待
财飘”的动作送入昂贵 continuation/two-ply。

#### Scenario: seq856 具备搜索资格但当前不能财飘

- **WHEN** seq856 摸中前的站立手持白×3且为全牌爆头听，摸中后 HU 合法，但弃白不能保持爆头
- **THEN** `piao_search_eligible=true`、`piao_ready_now=false`；不得因为白板≥2就直接弃白，
  是否继续过 HU 需要计算 Piao Search 快特征

#### Scenario: seq880 后当前财飘成立

- **WHEN** seq880 及后续回放窗口中，弃白为合法动作且弃后仍为全牌爆头听
- **THEN** 标记 `piao_ready_now=true`，不再走“寻找机会”快门，直接把
  `piao_discard` 放入 HU-window action-root 比较

### Requirement: 财飘机会密度使用轻量 piao_ukeire 计算

对 Piao Search eligible 的 13 张站立手 `S`，系统 SHALL 计算一个独立的
`piao_draw_mask(S, locked)`：对每种下一摸 `t`，仅判断
`S + t - W` 是否仍为 `is_baotou_wait`。该计算 MUST 固定弃一张白，不得在每个 `t`
下面再枚举其它弃牌。

基于当前 visible 口径 SHALL 导出：

- `piao_live`：mask 中仍未见的总张数；
- `piao_types`：mask 中仍有剩余的牌种数；
- `piao_ratio = piao_live / draw_live`；
- `full_piao_search`：所有当前仍可能摸到的牌都在 mask 中。

结构 mask MUST 与 visible 加权解耦并可按 `(hand_bytes, locked)` 缓存；visible 变化只更新
剩余张数，不得使结构缓存失效或被错误复用为另一个手牌。

#### Scenario: 固定弃白而不是搜索最佳弃牌

- **WHEN** 评估下一摸 `t` 是否产生财飘机会
- **THEN** 只检查“摸 `t` 后弃一张白是否仍爆头”，MUST NOT 为该 `t` 再枚举所有弃牌

#### Scenario: visible 只改变权重

- **WHEN** 同一站立手结构不变但牌河/副露使 visible 增加
- **THEN** `piao_draw_mask` 保持相同，`piao_live/piao_ratio` 按新的剩余张数重新计算

### Requirement: Piao Search 使用自摸 horizon 动态收手而非固定等待轮数

系统 SHALL 使用保守的
`self_draw_horizon = floor(live_wall_left() / 4)` 作为低成本未来自摸次数门槛。

- 当前已经 `PIAO_READY` 时，财飘至少需要 1 次未来自摸兑现；最终是否弃白仍由 action-root
  价值比较决定。
- 当前为 `PIAO_SEARCH` 时，至少需要“下一次自摸找到财飘机会 + 再下一次自摸兑现”，因此
  `self_draw_horizon < 2` 时 MUST 结束搜索并选择当前 HU/正常安全动作。
- `self_draw_horizon >= 2` 只允许搜索候选参与比较，MUST NOT 强制过 HU。
- 固定 `max_search_passes` MAY 作为 safety cap，但 MUST NOT 取代
  `piao_ratio + horizon + action-root value` 的动态判断；每次自己的新摸牌窗口 MUST 重算。

如果使用 `max_search_passes`，线上实现 MUST 能跨 `Mirror.build_game` 的 Game 重建持久化该计数；
不能持久化时 SHALL 禁用该 cap，而不是每次重置后继续宣称已限制轮数。

#### Scenario: 只剩一次未来自摸时不再寻找财飘

- **WHEN** 当前 HU 合法、白板≥2、已爆头但尚未 PIAO_READY，且 `self_draw_horizon=1`
- **THEN** 不得为了“先找财飘再兑现”继续过 HU

#### Scenario: 两次以上未来自摸只开放搜索资格

- **WHEN** 同样状态下 `self_draw_horizon>=2`
- **THEN** 允许根据 `piao_ratio` 和 action-root value 评估搜索，MUST NOT 仅因 horizon 足够就过 HU

#### Scenario: 固定轮数只是最后上限

- **WHEN** 动态模型连续多个窗口仍判断搜索有正价值
- **THEN** MAY 在 `max_search_passes` 达到校准上限时强制收手；未达到上限不代表必须继续搜索

### Requirement: Piao Search 快特征必须有严格性能边界

首版 Piao Search MUST 在完整 Stage B/continuation 之前执行低成本快门，并满足：

- 对一个站立手最多检查 34 个 draw type；
- 每个节点只允许 `is_baotou_wait` 等固定结构判断，不得调用 `shanten`、通用 `ukeire`、
  `scoring/settle` 或嵌套最佳弃牌搜索；
- 结构 mask 必须缓存，visible 权重为 O(34)；
- 首版每个决策只评估现有逻辑已选出的一个最佳 `baotou_next_draw` 站立手；
- 只有性能与收益门均通过后才 MAY 扩到 top-K，且 K MUST ≤ 3；
- 行为预算必须按确定性节点数控制，不得用墙钟是否超时改变同一状态的动作结果。

性能验收：Piao Search 快特征增量 p95 ≤ 1ms、p99 ≤ 2ms，同时 legacy 普通弃牌既有
p95 ≤ 20ms 门不得退化。若 Python 实现无法稳定满足，MUST Rust 化或关闭 Piao Search，
不得静默使用部分计算结果。Stage B/continuation 单独计时，不计入快特征预算。

#### Scenario: 低机会状态不进入昂贵搜索

- **WHEN** Piao Search 快门因 `piao_ratio` 低于当前校准阈值或 horizon 不足而失败
- **THEN** 不运行仅为财飘搜索服务的 Stage B/continuation，直接使用当前 HU/既有安全基线

#### Scenario: 快门通过才调用既有价值链

- **WHEN** Piao Search 快门通过
- **THEN** 搜索动作 MAY 进入 HU-window action-root continuation/two-ply，与 immediate HU 比较，
  快门本身不得直接决定过胡

### Requirement: Piao Search 参数必须通过配对 A/B 校准

首版 SHALL 对至少以下参数网格做配对 A/B，而不是手工固定“等两轮”：

- `min_piao_ratio ∈ {0.25, 0.40, 0.55, 0.70, 0.85}`；
- `min_search_self_draws ∈ {2, 3}`；
- `max_search_passes ∈ {1, 2, 3}`。

主指标 SHALL 为平均结算分/局，并额外记录：
`pass_hu_count`、`piao_opportunity_found`、`piao_cashout_success`、
`search_lost_before_opportunity`、`piao_lost_before_cashout`、最终直接 HU 次数与平均倍率增益。
参数只有在收益与性能门同时通过时才可设为线上默认。

#### Scenario: 高倍率但低兑现率不能自动晋级

- **WHEN** 某参数配置显著提高财飘倍率，但 `search_lost_before_opportunity` 或
  `piao_lost_before_cashout` 同时使平均结算分下降
- **THEN** 该配置不得因为“8 番次数更多”而晋级线上默认



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
`immediate_hu` SHALL 作为确定性基线。普通延迟候选保留倍率收益、剩余墙量与风险惩罚；
但后文 Guaranteed Next-Draw HU requirement 明确定义的候选 SHALL 使用 candidate-specific
soft-delay policy，不得再被 X/Y/Z 二值归零。

`wall >= PIAO_WALL_GUARD` 只表示延迟胡候选可参与比较，MUST NOT 被解释为“必须过胡”。
X/Y/Z 自适应收手也 MUST NOT 形成非对称早退；对于普通延迟候选应一致应用，对于
Guaranteed Next-Draw HU 则按其专用规则一致忽略软门。

#### Scenario: 4 番立即胡与 8 番财飘进入同一价值层

- **WHEN** 当前 immediate HU 为七对+爆头约 4 番，弃白后下一次自摸路线为
  七对+财飘+爆头约 8 番，且硬墙门通过
- **THEN** 两条路线 MUST 同时出现在 action-root 评价中；选择由完整动作价值决定，
  不得由候选枚举顺序决定

#### Scenario: two-ply 不得被爆头 override 绕过

- **WHEN** legacyV2/支持 Stage B 的 evaluator 在 HU 窗口同时存在 immediate HU 与延迟胡候选
- **THEN** 若该 profile 按正常规则应进入 Stage B/continuation，则 MUST 实际进入并记录；
  `hu_baotou_next_draw_override` MUST NOT 作为提前返回原因

### Requirement: 下一次自摸必胡的爆头候选忽略 X/Y/Z 软收手归零

HU-window MUST 对每个延迟候选独立决定 delay policy，不得再使用一个共享
`delay_factor = 0 if _push_abort_reason else 1` 覆盖所有候选。

一个 `piao_discard` 或 `baotou_next_draw` 候选 SHALL 标记
`guaranteed_next_draw_hu=true`，当：

- 弃牌来自当前 `legal_actions()`；
- 弃后 `is_baotou_wait(standing, locked)` 为真；
- 按当前公开 visible/remaining 口径，所有仍可能摸到的牌都能合法胡，即
  `winning_mass == total_unseen` / `win_probability == 1.0`；
- HU-window 已通过 `PIAO_WALL_GUARD`；
- 该候选评价完整且无 fallback。

对 `guaranteed_next_draw_hu=true` 的候选，推进轮数 X、对手副露 Y 和
`BAOTOU_PUSH_MIN_LIVE` Z 均 MUST NOT 将 candidate value 归零；其有效值 SHALL 保留
完整 next-draw raw value（等价 `delay_factor=1.0`）。唯一继续保留的活墙硬门是
`live_wall_left() < PIAO_WALL_GUARD` 时直接选择 immediate HU。

该特例不代表无条件过 HU：Guaranteed candidate 仍 MUST 与 immediate HU 和其它根候选比较，
只有动作价值更高时才选择延迟路线。

#### Scenario: seq351 对手副露不得把必爆头候选归零

- **WHEN** seq351 当前 HU 合法且 immediate HU 为七对 2 番、结算价值 20，无财飘候选；
  弃 8万 / 6万 / 8筒后均形成全牌爆头听，且 next-draw `win_probability=1.0`
- **THEN** 即使 `_push_abort_reason` 为 `opp_melds`，上述候选 MUST 保持
  `delay_factor=1.0`（或等价不应用软惩罚），不得从 raw value 约 41.5 变为 0；
  最终按有效 value 与 immediate HU=20 比较

#### Scenario: rounds 与 Z 软墙门同样不归零

- **WHEN** Guaranteed Next-Draw HU 候选同时触发推进轮数上限或
  `live_wall_left() < BAOTOU_PUSH_MIN_LIVE`，但仍满足
  `live_wall_left() >= PIAO_WALL_GUARD`
- **THEN** rounds/Z 仅作为观察诊断，不得把该候选归零

#### Scenario: 硬墙门仍直接胡

- **WHEN** 当前 HU 合法且 `live_wall_left() < PIAO_WALL_GUARD`
- **THEN** immediate HU 直接胜出，不因 Guaranteed Next-Draw HU 特例继续等待

### Requirement: Guaranteed Next-Draw HU 的高价值摸牌由真实计分自然进入 EV

Guaranteed candidate 的 raw EV SHALL 沿用当前逐牌 next-draw 评价：对每个有剩余质量的
下一摸构造 final hand，并调用现有 `hand_multiplier + settle`。不得为豪华七对子、
更高爆头番型等再增加手写 bonus。

#### Scenario: seq351 摸 8筒升级豪华七对子

- **WHEN** seq351 某个 Guaranteed Next-Draw HU 候选的下一摸为 8筒，且最终牌型按规则构成
  豪华七对子
- **THEN** 该 draw 的 reward MUST 由 `hand_multiplier + settle` 返回真实更高价值，并自然
  提高 candidate raw EV；MUST NOT 再叠加 `luxury_bonus`

### Requirement: Guaranteed Next-Draw HU 审计必须区分观察到的风险与实际应用的惩罚

候选解释 MUST 至少输出
`guaranteed_next_draw_hu`、`conditional_next_draw_win_probability`、
`delay_policy`、`delay_factor`、`raw_value`、`effective_value`。
若 X/Y/Z 信号被 Guaranteed policy 忽略，SHALL 将其记录到
`ignored_delay_reasons`（或等价字段），不得继续把 `delay_penalty_reason=opp_melds`
表现成已实际将 value 乘为 0。

#### Scenario: seq351 诊断可解释

- **WHEN** seq351 因对手副露触发旧的 `opp_melds` 信号
- **THEN** 诊断 SHALL 显示该信号被观察到但被 Guaranteed policy 忽略，
  `delay_factor=1.0`，并同时展示 raw/effective value 与最终 selected_type

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
