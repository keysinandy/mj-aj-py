## Context

`bot-ev-discard` 已提供 `PublicDecisionContext`、`ScoreValue`、全合法舍牌 frontier、共享可能世界和固定 continuation。当前两个消费者仍有同一个统计/剪枝瓶颈：`mj/decision/score_value.py` 的 `theoretical_reward_bound()` 把 63 个潜在链事件和额外翻倍相乘，`mj/decision/fast_ev.py` 用它做候选上界，`mj/rollout/evaluator.py` 也用它构造 Hoeffding 区间。

当前 `PairedTeacher` 在每个 sample 中运行所有候选，保存最终 paired delta，但每轮停止条件比较候选各自的 marginal CI。这个流程在语义上已经共享随机世界，却没有在停止阶段利用同一世界带来的差值方差降低；同时，候选被淘汰后需要新的稀疏样本和续跑契约。

规则计分允许安全收紧 bound，但 Fast EV 和 rollout 的收益支持不同：Fast EV 的 P2 模型只有本家未来自摸正收益；rollout 的终局 hero reward 还包括对手获胜时的负支付。rollout 的安全界因此必须覆盖所有潜在获胜者，不能只读取 hero 的牌型潜力。任何 bound 都只能使用公开上下文、根动作转移和规则可证明的资源上限。

## Goals / Non-Goals

**Goals:**

- 提供按 `context + root candidate` 计算、带版本和证书的规则 reward envelope。
- 为 Fast EV 提供更紧的 hero-only EV 上界，并在无法证明时安全地禁用剪枝或使用兼容的宽界。
- 为 rollout 提供覆盖正负终局 reward 的安全界，并以候选界之和构造 paired delta 界。
- 将 active-candidate paired delta racing 接入固定 batch、固定 alpha 和确定性 sample/resume 流程。
- 保留完整的 marginal 报告、失败分母、淘汰证书、配置指纹和隐藏信息边界。
- 让旧的显式 scalar bound override 和旧历史 artifact 保持可识别、不可与新版本无标记混用。

**Non-Goals:**

- 不修改杭州麻将规则、`Game`/`ScoreValue` 的合法性和结算语义。
- 不实现 EV3、survival/tail value、shape-v2 continuation、MCTS、empirical Bernstein 或默认策略切换。
- 不改变 StateDemand/Throttle、平台窗口、动作提交、重试和网络协议。
- 不把 marginal CI、观测最大收益或不完整候选结果用作安全证明。
- 不要求首版把所有候选并行化；批次边界和确定性优先于异步吞吐。

## Decisions

### 1. 用一个 envelope 表达两种收益语义

新增版本化的规则 envelope 概念，而不是让一个裸 `float` 在不同消费者之间隐式复用。其逻辑字段至少包括：

```text
RewardEnvelope
  version
  fast_upper                 # Fast EV: 0 <= R <= fast_upper
  rollout_lower/upper        # rollout terminal reward range
  rollout_abs                # max(abs(lower), abs(upper))
  components                 # 可复核的资源上限
  certificate                # proof/fallback/unknown
```

`reward_bound(context, candidate)` 可以保留为兼容的标量入口，但内部必须由 envelope 产生。显式 profile/teacher scalar override 优先于自动推导，并记录为 `override`；没有可证明的候选界时，Fast EV 不得剪枝，rollout 可退回旧的宽界并记录 `legacy_conservative_fallback`。

### 2. 以根动作后的资源上限计算安全界

先通过现有 `ScoreValue.discard()` 得到根动作后的 hand、chain 和 chain_piao，再按公开材料计算上限。首版使用可证明但仍易审计的资源 envelope：

- 每个 seat 的新增杠事件不超过 `4 - locked + existing_pong_count`，并受可用活墙限制；加杠只升级既有碰，不重复增加 meld slot。
- hero 的新增财飘不超过根动作后可持有的白板和公开未见白板材料；对手使用只依赖公开计数的最坏分配上限。
- `locked == 0` 时七对/豪华组数受 14 张暗牌容量和可达四张自然牌数量限制；有副露时不把七对分支算入上界。
- 4 白板和爆头各自最多贡献一个额外二倍因子，且只有 envelope 证明存在材料/结构可能时才计入；无法证明不存在时宁可保留该因子。
- 最终 multiplier 乘以现有 `settle()` 的庄闲支付系数。Fast EV 只取 hero 自摸正收益上界；rollout 还要在所有潜在 winner 中取对 hero 负支付的最坏范围。

这些项可以独立相乘形成安全上界，即使某些组合不可同时实现也不会破坏安全性。证书必须同时保留 `chain_max`、`kong_max`、`piao_max`、`seven_pairs_groups_max`、`four_white_possible`、`baotou_possible`、`multiplier_max` 和 settlement factor，便于发现“界过松”与“界不安全”分别属于哪一类。

### 3. Fast EV 只用同层证明的上界剪枝

Fast EV 为每个 root candidate 计算一次 envelope。当前 profile 的 scalar override 继续生效；否则使用 candidate-specific `fast_upper`。上界仍只能在严格小于已完成同层最优值时淘汰，等于时保留稳定 tie-break。

上界计算不改变 candidate value、EV2 语义或事务式回退。高层结果不完整时仍回到完整 Q0；bound 缺失时只失去剪枝，不得把未知界当成零或用旧 shape-v1 Q bound 冒充 EV 界。profile payload 增加 bound version/模式，使 bound 实现变化自动使证据失效。

### 4. 以 paired delta 作为 teacher racing 的唯一置信停止信号

每轮只在共享 sample batch 完成后进行比较。初始 active 集为全部合法 root actions；同一 sample 先为所有 active action 生成同一 `world`，再分别执行 root rollout。

```text
active actions + shared worlds
             |
             v
  D(a,b) = R(a) - R(b) for each active pair
             |
             v
  UCB(D(candidate, leader)) < 0  -> eliminate candidate
             |
             v
  next batch only for remaining active actions
```

leader 采用当前有效 paired/marginal mean 的确定性排序，平手使用稳定的 tile/action 顺序；leader 本身在该轮不被淘汰，下一轮允许 leader 变化。对每个 active pair 计算有界 two-sided interval，淘汰只使用 `high < 0` 的严格条件。若候选的 rollout absolute bound 为 `B_a`、`B_b`，则 `D(a,b)` 使用 `B_a + B_b`；不裁剪实际 reward。

`alpha` 在预声明的最大 looks 和全部候选 pair 假设之间分配。候选数、`n0/batch/nmax`、look 数和统计方法进入 config fingerprint；不使用重复查看后未经控制的普通 t/正态区间。剩余 active action 在 `nmax` 仍不能被成对区间分离时输出 `ambiguous=true`。

### 5. 用稀疏 rows 保存淘汰和续跑状态

teacher row 增加本批 `evaluated_actions`/active snapshot；候选淘汰后，后续 row 可以没有该 action。单个 active action 失败仍使该共享 row 对当前 active group 失败，避免 paired 样本只保留一侧；已经淘汰的 action 不再影响后续 group failure。

resume state 必须保存：active actions、elimination records、每次淘汰的 pair、delta interval、bound、alpha/look、stop reason 和稀疏 rows。相同 config 下恢复不得重复 sample id；旧 dense rows 可以作为历史 artifact 读取，但不能绕过新 config fingerprint 直接续跑。候选输入顺序先规范化排序，worker 调度不能影响 world/sample 结果。

### 6. 报告区分诊断 CI 与 racing 证据

每个 candidate 仍输出 EV、marginal CI、有效样本数、失败数和 win/mult/draw 统计，但明确标为 candidate summary。teacher 另外输出 active pair 的 paired delta、区间、有效 paired sample ids、elimination certificate 和最终 best/runner-up 关系。`stop_reason` 使用版本化值，例如 `paired_elimination`、`paired_ci_separated`、`nmax_ambiguous`、`bound_fallback`。

解释/离线报告只保存公共 envelope components、action、sample id 和指纹；不得写入隐藏手牌、真实墙序或完整 sampled world。线上说明继续只记录本次实际计算层级，不能把离线 racing 结果当成曾经提交的线上动作。

## Risks / Trade-offs

- **[Bound 推导遗漏规则路径]** → 采用独立 reward range/envelope；无法证明时禁用 Fast 剪枝或退回旧宽界，并用小墙穷举、规则 property test 和随机完整 Game 终局验证。
- **[只按 hero 资源界定 rollout]** → rollout envelope 对四家潜在 winner 取最坏 multiplier/支付；hero-only bound 只允许用于 Fast EV。
- **[自适应 leader 造成选择偏差]** → 在所有候选 pair 和所有预声明 looks 上做 simultaneous alpha 控制；淘汰只接受 paired UCB 严格小于零。
- **[淘汰后样本数不同导致误称 paired]** → pair 只使用两侧同一 sample id 且 group 未失败的行；报告每个 pair 的有效 n，不用不同 n 的 marginal mean 冒充 paired mean。
- **[失败候选污染共享世界]** → active group 任一失败使该 row 对 active pair 无效并保留失败分母；不以零分、legacy 动作或截断分填充。
- **[新 rows 破坏旧续跑]** → 引入 racing/schema/config fingerprint；旧 artifact 保留只读，旧 scalar override 仅作为明确兼容模式。
- **[bound 变紧但仍不足以加速]** → 先分开记录 bound 规模、剪枝率、有效样本量和耗时；不以速度提升替代统计正确性。

## Migration Plan

1. 先实现 envelope 纯函数和证书测试，保持旧 `theoretical_reward_bound()` 作为显式兼容 fallback。
2. 在 Fast EV 中以 opt-in bound version 接入，验证候选集合、选择、完整层级回退和旧 scalar override。
3. 在 teacher 中先保留完整 shared-world 执行，再接入 paired interval/racing 和稀疏 resume；用可枚举 toy worlds 对拍。
4. 重新生成 bound/racing 版本的 smoke、profile、teacher manifest；旧 artifact 不与新 artifact 混合拟合或续跑。
5. 在同机测量剪枝率、每 batch active 数、rollout 次数、CI 覆盖和耗时；通过后仍保持 legacy/default 不变。

回滚时选择旧 profile/teacher algorithm fingerprint 或显式 legacy bound，不需要改动 Game、规则或线上协议。若新 envelope 校验失败，Fast EV 安全地关闭剪枝，teacher 使用记录了 fallback 的宽界；不得继续使用未证明的窄界。

## Open Questions

- 首版是否把 asymmetric `[lower, upper]` 直接用于 Hoeffding range，还是先统一使用 `rollout_abs` 保持实现简单；前者更紧，后者更容易与现有 interval helper 对拍。
- 当前首版是否每轮淘汰所有满足条件的候选，还是只淘汰最差一个；前者节省 rollout，后者更保守但可能降低收益。
- 4 白板/爆头“可达性”首版只做公开资源上界，还是为常见状态增加额外结构证明；任何额外剪枝都必须有独立差分覆盖，不能凭经验排除。
