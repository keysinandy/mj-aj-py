# bot-hand-evaluation Specification

## Purpose
TBD - created by archiving change bot-shape-aware-evaluation. Update Purpose after archive.
## Requirements
### Requirement: 评价只使用本家可见状态并保持规则优先

评价器 SHALL 只读取本家手牌、公开牌河/副露、规则与阶段、吃牌资格和活墙长度，MUST NOT 读取对手暗手或真实墙序。候选 MUST 来自该状态合法动作；对弃牌后的站立暗牌先比较最低向听，同向听内保留既有财神保护。HU/财飘、KONG、抓打圈与吃牌额度规则 MUST 由既有规则入口决定。

#### Scenario: 隐藏状态变化不影响策略
- **WHEN** 两个状态的本家可见输入、规则、profile 和固定节点预算相同，仅对手暗手或真实墙序不同
- **THEN** 评价分量、候选排序和最终动作相同

#### Scenario: 好型奖励不允许退听或非法弃牌
- **WHEN** 某个结构分更高的候选向听更差，或不在当前合法弃牌集中
- **THEN** 该候选不能因结构或前瞻分胜出

### Requirement: 可见牌与假想摸牌使用一致的未见数口径

评价器 SHALL 用本家手牌加四家当前牌河及全部副露计算 visible，并以 `max(0,4-visible[t])` 表示未见数。MUST NOT 将其标为真实活墙数量。被吃碰移出牌河的牌不得重复扣除；每次模拟摸牌 MUST 同时增加手牌及 visible，随后弃牌不减少 visible。无未知牌时概率定义为零；物理计数超过四张等无效输入 MUST 显式报告并停止依赖该输入的新评价。

#### Scenario: 模拟连续摸牌不重复计算同一张剩余牌
- **WHEN** 牌 t 只有一张未见且第一摸已取得 t
- **THEN** 第二摸中 t 的未见权重为零，第一摸后的弃牌不将 t 放回未知池

#### Scenario: 吃碰前后可见量不变
- **WHEN** 已可见的 pending 牌与本家两张牌被移动到本家副露
- **THEN** 全部 visible 数量逐牌不变，不再额外扣除被模拟移入副露的牌

### Requirement: 整手分解不得重复使用牌或财神

整手评价 SHALL 明确面子、雀头、搭子、对子与单张的用途，按既有规则分别评价标准形及可用的七对分支。一个分解中真实牌和财神消耗不得超过输入；面子/搭子收益不得超过剩余成牌需求。复合形的互斥分解 MUST 分别评价，MUST NOT 将它们作为同时存在的牌组相加。

#### Scenario: 1234 的两个顺子互斥
- **WHEN** 手牌局部为 1234 且每张仅一张
- **THEN** 可以评价 123+4 和 1+234，但不能计入两组同时完成的顺子

#### Scenario: 889 中对子与搭子共享材料
- **WHEN** 手牌局部为 889
- **THEN** 88 的对子用途与使用其中一张 8 的 89 搭子受到材料守恒约束，不能重复获得两份独立完成收益

#### Scenario: 财神不能构造天然碰牌
- **WHEN** 某牌只有一张真牌且分解使用财神补成对子
- **THEN** 该分解不得据此产生天然 PONG 潜力或非法碰牌动作

### Requirement: 对子按雀头和面子用途评价

评价器 SHALL 区分对子承担雀头、天然碰牌面子与七对的用途，并使用未见同牌数、剩余面子需求、雀头替代性及规则资格计算边际价值。MUST NOT 对每个对子加相同的固定保护分，MUST NOT 将多个互斥用途简单累加。吃碰潜力 SHALL 显式标为公开信息启发分，而非对手出牌概率。

#### Scenario: 同种牌耗尽仍可保留雀头
- **WHEN** 某天然对子外部剩余两张均已可见
- **THEN** 该对子碰牌潜力为零，但仍可保留合法的雀头或七对价值

#### Scenario: 有替代雀头时保留对子不是硬规则
- **WHEN** 已有另一可用雀头且拆该对子后的完整同层级评价更高
- **THEN** BOT 可以拆对子，并解释雀头替代与其他收益

### Requirement: 直接进张与同向听改良分别计算

weighted frontier SHALL first evaluate only the best child shanten for each
public-information draw and SHALL accumulate improvement bounds using remaining
tile weight. Child ukeire MUST NOT be evaluated while a strict improvement bound
already proves a winner. If Stage A completes without a strict winner and the
maximum improvement is tied, Stage B SHALL evaluate the existing child ukeire
metrics and preserve exact two-ply ordering. Stage-A-only results MAY be used
only as decision-safe partials; missing child ukeire MUST remain missing.

#### Scenario: 13 与 89 的改良存在条件差异
- **WHEN** 合成整手中其他条件对称，13 摸 4 可转 34 且增加有效进张，而对照边张无相同改良
- **THEN** 对应可行路径贡献正改良分，并显示牌 4 的未见权重和最佳弃牌

#### Scenario: 改良所需牌耗尽
- **WHEN** 其余输入相同但上述牌 4 的 visible 增加到四张
- **THEN** 通过摸 4 的改良贡献变为零；其他牌的贡献独立计算

#### Scenario: 仅未知池缩小不算改良
- **WHEN** 摸入无用牌并原样摸切，子站立牌面与原手一致，仅 visible 和未知池分母变化
- **THEN** 该路径的 I 贡献为零，不能因 p1 分母缩小误报结构改善

#### Scenario: Improvement is weighted by remaining copies
- **WHEN** two draw kinds both improve shanten but have remaining weights 4 and 1
- **THEN** the aggregate improvement weight increases by 5, not by two equal units

#### Scenario: Exhausted improvement tile contributes zero
- **WHEN** an improving tile has visible count four
- **THEN** it is omitted from the draw frontier and contributes zero improvement weight

#### Scenario: Partial means do not treat unknown mass as zero
- **WHEN** a root has weighted child ukeire 40 over 20 covered draw weight and 10 additional draw weight remains unvisited
- **THEN** its reported partial future ukeire mean is 2.0, with covered weight 20 and total weight 30 shown separately

#### Scenario: Improvement bounds include only unknown mass
- **WHEN** a root has observed improvement weight `I` and unvisited draw weight `R`
- **THEN** its improvement lower bound is `I` and its upper bound is `I+R`

#### Scenario: Future improvement resolves the winner early
- **WHEN** one root lower bound is strictly greater than every other root upper bound
- **THEN** the kernel stops before child ukeire, returns `future_shanten_only`, and the adapter may accept that root as a safe partial

#### Scenario: Full Stage A has a unique improvement winner
- **WHEN** all draw weights are evaluated and exactly one root has the greatest future improvement weight
- **THEN** Stage B is skipped and the unique root is selected without fabricating future ukeire values

#### Scenario: Improvement tie enters Stage B
- **WHEN** Stage A cannot distinguish the maximum improvement among retained roots
- **THEN** Stage B evaluates child ukeire using Stage-A best-shanten children and produces the same action and future metrics as exact weighted two-ply

#### Scenario: Stage-A-only metrics are not zero-valued
- **WHEN** a safe partial stops before child ukeire
- **THEN** future ukeire and future tile-type means are null/missing, with a phase and skip reason recorded

### Requirement: 有限前瞻声明模型和后续策略

新评价器 SHALL 使用非递归基础评价 Q0 选择模拟下一摸后的最佳合法弃牌，并按听牌叶子的合法和牌集合计算两次自摸内的模型概率 H2。MUST 声明未见牌均匀、无放回、忽略对手动作及提前胡牌的假设；MUST 遵守活墙可用机会、抓打圈及有财必拷响门禁，不能假造杠补牌。H2 MUST NOT 被输出为实测胜率或完整回合期望。

#### Scenario: 普通未来摸牌不能伪造杠开
- **WHEN** 有财必拷响开启且某假想普通摸牌不满足合法 HU 门禁
- **THEN** 该分支不计为和牌，即使一般牌形判定已成牌

#### Scenario: 墙尾无后续摸牌机会
- **WHEN** 已知活墙不足以支持某条后续自摸路径
- **THEN** 该路径不贡献 H2，并保留机会限制信息

### Requirement: 同向听排序使用版本化共享评分

shape-v1 SHALL 按设计中的 `Q0=p1+wB*B+wC*C`、`Q=p1+wI*I+wH*H2+wB*B+wC*C` 评价同最低向听、同财神档候选；I/H2/B/C 的定义、归一化、权重、稳定排序、阈值与预算 SHALL 进入配置指纹。B/C MUST 按设计的分解公式计算并取自同一个可行分解，不能将互斥方案的各项最大值组合。系数 MUST 由被评价站立牌面的向听与活墙档决定，同档的 PASS 与 claim 相同，不依赖候选来源。权重 MUST 经过预声明的校准与独立验证，不得为单个房间硬编码例外。

#### Scenario: 少量直接进张劣势可被已完成的后续收益抵消
- **WHEN** 两个合格候选同向听，直接进张较少者的完整 Q 更高
- **THEN** shape-v1 选择完整 Q 更高者，并输出两者原始特征和加权差异

#### Scenario: 相同输入和配置可复现
- **WHEN** 输入状态、profile、候选集、实际评价层级和固定节点预算一致且未触发时钟兜底
- **THEN** 改变候选遍历次序不改变动作；最终完全同分时才使用声明的稳定动作次序

### Requirement: 预算耗尽回退到全候选完成层级

The weighted hard deadline SHALL be checked before each child discard, shanten
cache miss, and ukeire candidate expansion, including inside every parallel
worker. The kernel MAY reserve a small fixed amount of time for result
construction, but MUST report the configured budget, internal deadline and
explicit `hard_deadline` reason. Work-budget accounting by uncached shanten
remains independent from deadline accounting and MUST be aggregated across
workers before deciding whether the run exceeded the work budget.

#### Scenario: 最后一个候选前瞻被中断
- **WHEN** 部分候选完成 weighted frontier，另一个保留候选因预算未完成，而所有候选的完整当前 ukeire 已完成
- **THEN** 只有在 decision-safe bounds 证明胜者时使用已提交的 weighted 结果，否则使用完整 legacy 结果并记录回退

#### Scenario: 基础新评价也未完整完成
- **WHEN** weighted frontier 阶段预算耗尽且保留候选没有达到安全证明条件
- **THEN** 整次决策回退到完整 legacy 结果，不因为后遍历候选缺少分数而排除它

#### Scenario: Coverage-qualified online partial
- **WHEN** weighted frontier reaches the hard deadline after every retained root has the configured minimum coverage
- **THEN** the evaluator may rank the retained roots using only their committed weighted metrics, marks the layer partial, and records coverage and the hard-deadline reason

#### Scenario: Partial root cannot be silently ranked
- **WHEN** one retained root has no committed row or cannot produce a valid lower/upper bound
- **THEN** the entire future layer is ignored and the complete legacy action is returned with an explicit partial fallback reason

#### Scenario: Exact mode remains transactional
- **WHEN** exact two-ply is incomplete
- **THEN** all exact metrics are discarded and the complete legacy action is selected

#### Scenario: Coverage-qualified but unsafe partial
- **WHEN** every retained root exceeds the configured coverage threshold but the improvement bounds still overlap
- **THEN** the evaluator rejects partial ranking and returns the complete legacy action with an explicit partial fallback reason

#### Scenario: Decision-safe partial
- **WHEN** every retained root has a committed row and one root's observed improvement lower bound is strictly greater than every other root's improvement upper bound
- **THEN** the evaluator may select that root without waiting for the configured coverage threshold, marks the result partial, and records both bounds

#### Scenario: Work budget covers inner DFS
- **WHEN** child evaluation invokes additional shanten calls while computing child ukeire
- **THEN** the weighted budget is charged against actual uncached shanten work and may stop before child-node count reaches the historical node limit

#### Scenario: Ukeire inner loop crosses the deadline
- **WHEN** a child ukeire loop reaches the internal hard deadline
- **THEN** it stops without committing that branch, preserves earlier committed rows, and reports hard-deadline fallback/coverage

#### Scenario: Cache hit before deadline
- **WHEN** a shanten or ukeire state is served from the decision-local cache
- **THEN** it is counted as a hit, does not consume uncached work budget, and still obeys the surrounding deadline checks

#### Scenario: 并行 worker 触发截止
- **WHEN** 任一 worker 在 Stage B 期间触达内部 hard deadline
- **THEN** 整次 Stage B 事务性作废，所有 root 记录同一 `hard_deadline` 原因，不提交任何部分 future 指标

#### Scenario: 并行下的工作预算
- **WHEN** 各 worker 完成后的未缓存 shanten 合计超过 work_budget
- **THEN** 整次搜索以 `work_budget_exceeded` 中止，且不因为某片已完成的子集而提交结果

### Requirement: 缓存和解释支持并发隔离

缓存 SHALL 有容量上限，键 MUST 包括影响结果的手牌、locked、visible、规则/阶段/资格、profile 及层级。解释结果 SHALL 与单次调用关联，MUST NOT 通过可变全局“最近结果”在多个游戏线程间传递。预算相关缓存命中 MAY avoid work charging, but every uncached shanten evaluation MUST be counted in the weighted work metric.

#### Scenario: 相同手牌不同牌河
- **WHEN** 两个状态手牌相同但 visible 不同
- **THEN** 不能复用与可见牌有关的旧评价结果

#### Scenario: 多场同时决策
- **WHEN** 同一 BOT 进程十场线程交错产生候选解释
- **THEN** 每场记录的状态、profile、动作与解释来自自身那次决策

#### Scenario: Cache hit does not consume DFS work
- **WHEN** a child shanten state is found in the decision-local cache
- **THEN** shanten work misses remain unchanged while the cache hit is reported in search metrics

### Requirement: 默认切换受独立收益和性能验收约束

实现 SHALL 提供显式 legacy/shape-v1 选择并先保持 legacy 默认。切换默认前 MUST 完成全量测试、差分、strict 校验、冻结参数的独立成对留出评估与至少三房线上逐窗验收。留出集至少 4096 个成对牌局，种子/座位/庄家安排和对手版本固定；按种子分组的 95% 成对区间 MUST 满足得分差下界 >0、自摸率差下界 ≥-1 个百分点。样本扩展方案必须事前声明，未达标不得将不确定收益写成通过。

#### Scenario: 牌例改善但独立收益不确定
- **WHEN** 固定牌例通过但留出评估的得分差区间跨零
- **THEN** 保持 legacy 默认，并报告收益证据不足

#### Scenario: 新房窗口损失不能与策略收益混为一谈
- **WHEN** 新房存在身份未知、HTTP 延迟或新增评价计算超额
- **THEN** 分别报告其证据；有明确新增评价耗时导致的窗口损失时发布闸门不通过

### Requirement: Legacy V1 评价一次未来摸牌后的改良

legacy V1 弃牌评价 SHALL 在根弃牌后的站立牌面上，对理论上仍未见的每种牌进行一次未来摸牌模拟；摸牌后 MUST 更新手牌和 visible，并允许执行一次当前规则下合法的最佳弃牌。子状态 MUST 先按较低向听优先、再按较高直接进张选择，MUST NOT 递归展开第二次未来摸牌。评价 SHALL 输出 `future_improve_weight`、`future_ukeire` 以及可选的归一化展示值 `future_ukeire_mean`：前者为能使子状态向听低于根状态的理论剩余张数之和，后者为各未来摸牌下最佳子状态 ukeire 乘理论剩余张数后的总和；二者是未见牌权重，不得标称为胜率或完整 EV。若需要展示平均值，`future_ukeire_mean` MUST 单独以总理论剩余张数归一化，不能覆盖总量字段。

#### Scenario: 未来摸牌允许最佳摸切
- **WHEN** 根弃牌后某张牌 t 尚有理论剩余张数，摸入 t 后存在多个合法弃牌
- **THEN** 未来评价使用其中向听最低且进张最高的子状态，并以该子状态计算改良权重和未来进张

#### Scenario: 摸牌后 visible 不把牌放回未知池
- **WHEN** 牌 t 的根状态理论剩余数为 1，模拟未来摸入 t 后再评估其他牌
- **THEN** t 在子状态的未见权重为 0，随后模拟弃牌不恢复 t 的未知数

#### Scenario: 未来分支不读取隐藏信息
- **WHEN** 两个局面具有相同本家手牌、公开牌河/副露、规则、阶段和 visible，但对手暗手或真实墙序不同
- **THEN** legacy V1 的未来特征、候选排序和最终动作完全相同

### Requirement: Legacy V1 只在当前效率前沿内比较未来改良

legacy V1 SHALL 继续以合法动作和弃牌后最低向听作为硬约束，并继续执行财神保护。当前最低向听候选中，当前 ukeire 较低的候选 MUST 先被排除，不得依靠二阶特征逆转当前一巡进张劣势；仅对当前 ukeire 并列的候选比较未来改良。排序优先级 SHALL 为：合法性和最低向听、财神保护、当前 ukeire、`future_improve_weight`、`future_ukeire`、牌型损失、喂牌风险和稳定动作顺序。牌型损失与喂牌风险不得压过已完成的未来特征。

#### Scenario: 20260920 的 3t 与 9b 由未来效率决胜
- **WHEN** 回放 `u_9812ba08fe2f_a_e18e58e3acb8_r1_b9_t0` 的 `seq=100` 状态中，`3t` 和 `9b` 都为最低向听且当前 ukeire 相同
- **THEN** legacy V1 比较两者的未来改良和未来进张；若 `9b` 的未来特征更高，则选择 `9b`，即使其静态牌型损失高于 `3t`

#### Scenario: 当前进张劣势不能被二阶特征越级补偿
- **WHEN** 一个候选的当前 ukeire 严格低于另一个合法最低向听候选
- **THEN** 较低当前 ukeire 的候选不能因未来改良、牌型损失或喂牌风险胜出

#### Scenario: 非法动作和最低向听退步不能进入未来比较
- **WHEN** 未来摸牌后的某个弃牌不在该子状态合法动作集，或根弃牌已使向听高于最低值
- **THEN** 该根候选或子候选不得参与未来评分，也不得通过未来分数改变最终动作

### Requirement: Legacy V1 有界、可复现并安全回退

legacy V1 SHALL 对一次根决策使用固定节点预算和单调时间预算；预算覆盖未来摸牌、子状态弃牌、缓存和解释构造。缓存键 MUST 包含会影响结果的手牌、locked、visible、规则/阶段/合法资格、profile 和评价层级，并有容量上限。候选遍历和缓存命中顺序不得改变完整预算内的结果。任一候选或未来层未完整计算时，系统 MUST 丢弃本次未完成的 V1 影响，使用完整 legacy 排序；不得把缺失特征当作零或把部分候选与完整候选混排，并 MUST 记录实际层级和回退原因。

#### Scenario: 二阶预算耗尽
- **WHEN** 当前效率前沿中部分候选已完成未来评价，剩余候选在节点或时间预算内无法完成
- **THEN** 整次决策回退到完整 legacy 结果，部分未来分不得影响动作，并记录 `future_budget_exceeded` 或等价原因

#### Scenario: 缓存不跨 visible 状态复用
- **WHEN** 两次决策的本家手牌相同但牌河或副露导致 visible 不同
- **THEN** 两次未来特征不能复用同一个缓存结果

#### Scenario: 同输入保持确定性
- **WHEN** 输入状态、legacy V1 profile、预算和合法候选相同，仅改变候选枚举顺序或缓存冷热
- **THEN** 未来特征、最终动作、评价层级和回退状态相同；完全同分时只使用声明的稳定动作顺序

### Requirement: Legacy V1 采用显式版本和独立发布门槛

系统 SHALL 为 legacy V1 提供可识别的 evaluator/profile 版本和配置指纹，并保留关闭 V1 的 legacy 基线入口。V1 默认启用与否 MUST 由独立回归、物料/信息安全、性能和成对收益证据决定；在证据不足、性能超限或收益区间跨零时 MUST 保持 legacy 基线或显式 opt-in，不得把单个回放牌例的改善写成发布通过。

#### Scenario: V1 证据不足时保持基线
- **WHEN** 固定回放牌例中 V1 选择更符合预期，但独立成对评估无法证明收益为正或性能门槛未通过
- **THEN** 默认入口继续使用基线 legacy，V1 只能通过显式配置启用，并报告未通过原因

#### Scenario: 版本指纹变化可识别
- **WHEN** V1 的权重、预算、visible 口径或子状态排序规则发生变化
- **THEN** evaluator/profile 指纹发生变化，离线对照和回放解释不能将新旧结果混为同一版本

### Requirement: 并行前瞻必须与串行逐位一致

weighted kernel MAY 并行执行 Stage B 的 `(root, draw)` 工作单元，但 MUST 保持可观察结果与
单线程执行一致：动作、future improvement/ukeire/类型指标与 best discard 行及其顺序
MUST NOT 依赖线程数或线程调度。预算未触发时完成判定 MUST 同样与单线程一致；预算触发时
回退按聚合后的 deadline/work budget 统一判定，且 MUST NOT 提交部分 future 指标。缓存
命中/未命中以及依赖缓存命中的调用计数 MAY 因工作分配而不同，但它们 MUST 只作为性能记录，
MUST NOT 进入排名或指标。每个 worker MUST 使用调用/线程局部缓存，内核 MUST NOT 通过
跨调用的可变全局状态共享结果或缓存。并行度 MUST 可配置，并 MUST 能回退到单线程路径。

#### Scenario: 线程数不同的两次执行
- **WHEN** 同一手牌、同一 profile、同一预算分别以 workers=1 与 workers>1 执行且都完整完成
- **THEN** 两次结果的 selected、future 指标、best discard 行顺序与 Stage A 计数完全相同，
  仅缓存命中/未命中及其派生的调用计数可能不同

#### Scenario: 同一并行度重复执行
- **WHEN** 以相同输入和相同 workers 重复执行多次
- **THEN** 每次的 selected、future 指标与 best discard 行完全相同，不出现调度相关的漂移

#### Scenario: 工作单元不足以分片
- **WHEN** Stage B 只有零个或一个工作单元，或配置 workers<=1
- **THEN** 内核走串行路径，不创建线程，并给出与并行路径相同的结果

#### Scenario: 并行不引入共享状态
- **WHEN** 并行执行期间多个 worker 写入各自的 shanten/ukeire 缓存
- **THEN** 不同 worker 之间没有可变共享缓存或全局“最近结果”，解释仍与单次调用关联

### Requirement: 向听记忆化必须与参考实现逐位一致

shanten 内核 MAY 使用按花色预计算或记忆化的分解表，但结果 MUST 与 `mj/shanten.py` 参考
实现逐位一致，包括财神配刻/配对/成对雀头、`locked` 副露的剩余面子需求、七对分支与张数
校验。表 MUST 构建后只读并可被并行 worker 共享，MUST NOT 依赖调用顺序、线程调度或历史
调用内容；表不可用时 MUST 回退到参考实现路径且不改变结果与解释字段。启用新内核 MUST
以差分对拍与基准为门槛，未通过时保持 opt-in。

#### Scenario: 随机差分对拍
- **WHEN** 对随机手牌、不同财神数量与 locked 0-4 的组合比较新内核与参考实现
- **THEN** 所有 s 值逐位一致，包含 13/14 张与张数非法输入的错误行为

#### Scenario: 并发读表
- **WHEN** 多个 Stage B worker 同时读取同一张记忆化表
- **THEN** 每个 worker 得到相同结果，无锁竞争导致的值差异，且不引入可变全局状态

#### Scenario: 表不可用
- **WHEN** 表未构建完成或被显式关闭
- **THEN** 内核走参考路径，结果与关闭前一致，并在指标中记录该回退

#### Scenario: 只改变延迟
- **WHEN** 同一手牌、同一 profile 分别以新内核与参考内核完整执行
- **THEN** 动作、future 指标与 best discard 行完全相同；只有预算边界与耗时可能不同
