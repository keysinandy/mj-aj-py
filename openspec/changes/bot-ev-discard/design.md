## Context

本文依据 [docs/ev.md](../../../docs/ev.md) 和 2026-09-14 工作树整理。核对时 `HEAD=917e7f8`，前序为 `72a4f65`；引用这些版本用于确定基线，不把历史实验描述当成本次验收结果。实施路线见 [tasks.md](tasks.md)，行为契约见 `specs/*/spec.md`。

### 当前实现与差距

| 当前位置 | 已有能力 | 本次需要补足 |
| --- | --- | --- |
| `mj/bot.py::choose_action/choose_shape_action` | legacy 默认、shape-v1 显式选择；HU/财飘与 KONG 分支冻结 | 显式 shape-v2 与 action scope，逐阶段接入 |
| `mj/hand_eval.py::EvalContext` | 本家手牌/visible、规则、摸牌门禁、墙长；不可变上下文 | 庄家、底分、四家公开状态、动作链、轮次/来源/完整性 |
| `evaluate_discard_candidates` | 最低向听候选、非财神优先、Q0/Q 完整层级与上界剪枝 | 全合法舍牌、全层级积分比较、新模型的独立上界 |
| `_best_future_discard` / Rust `best_future_discard` | 内部仍过滤最低向听；非有财必拷响路径批处理 | EV2 的未来节点也需全合法候选，不能直接沿用原内核的选择语义 |
| `mj/game.py` / `mj/scoring.py` | 合法动作、20 张死墙、动作链、显式 HU、倍率与自摸结算 | 无隐藏信息的计分输入和可验证的世界恢复入口 |
| `mj/platform/mirror.py::build_game` | 本家合法决策投影；他家暗手为零、墙为占位值、反应队列仅本家 | 与在线投影分离的完整 rollout 状态，公共字段缺失不得补猜 |
| `scripts/bot_shape_eval.py` | shape-v1/legacy 成对对局，对手固定 legacy | 任意冻结候选/基线、逐对明细、错误即失败和按种子聚类统计 |
| `scripts/bot_shape_perf.py` | 串行全 `choose_action` 耗时与回退统计 | 十场实际调度方式下的并发和含序列化的计时 |
| `mj/bc_data.py` / `mj/log2data.py` | 动作掩码、实际终局 score、严格线上提交过滤 | teacher 版本/标签来源/置信度；不能把估计 EV 冒充实际 score |
| `mj/features.py::extract` | 默认本家特征；可选 oracle 平面 | 本路线数据必须显式 `oracle=False`；兼容补零不等于允许 oracle 输入 |

shape-v1 的 tasks 7–9 仍有未完成项；源码中的 17.5ms 弃牌内部预算、7ms 反应内部预算不构成完整决策或十场并发已达标的证明。`docs/ev.md` 的示例 EV/延迟数字不是新版本验收证据。

## Goals / Non-Goals

目标是近似 `argmax_a E[R_hero | I_hero, a, belief, continuation]`，其中 `I_hero` 含本家暗牌和当时可见公共信息，`R_hero` 是本局终局积分增量。teacher 是指定 belief 和后续策略下的有限样本估计，不称为真实墙 oracle、博弈论最优或无模型误差的“真实 EV”。

线上执行有预算的 Fast EV；离线执行完整 rollout、校准和验收。本路线不引入在线 Monte Carlo/MCTS，不修改规则倍率、服务端协议、窗口授权、StateDemand/Throttle 调度，不实施复盘 HTML 页面。BC 接入覆盖 P7，网络蒸馏为独立可选交付，不能用训练完成代替策略验收。

## Decisions

### 1. 两层架构与依赖方向

```text
Game / Mirror / 历史证据（仅到当前决策）
                  │ 本家可见投影
                  v
        PublicDecisionContext + legal root actions
                  │
        ┌─────────┴──────────┐
        v                    v
  decision/frontier     rollout/belief
        │                    │ 同一批可能世界
  score_value + fast_ev  simulator + 固定后续策略
        │                    │ 完整局末 score
  线上动作 + explanation    evaluator + teacher_data
        ^                    │
        └── 冻结 LUT / 参数 ─┘ 离线校准
```

拟新增 `mj/decision/{context,frontier,score_value,fast_ev,report}.py` 和 `mj/rollout/{belief,simulator,evaluator,teacher_data}.py`。底层 rules/scoring/shanten 不反向依赖 teacher；在线模块不能因 import、日志或解释触发 rollout。现有 `hand_eval.py` 保留 shape-v1 行为，可抽取纯特征函数，但不能把其硬过滤带入 v2。

### 2. PublicDecisionContext 字段与来源

| 分组 | 字段和约束 |
| --- | --- |
| 身份 | schema、rule/profile 版本、gid/round/seq/decision id（可缺）、输入指纹、各字段 provenance |
| 本家 | hero seat、34 维 hand、drawn、是否杠补摸牌、本家 locked/chows |
| 公共物料 | 四家未被认领的牌河、四家可见副露及种类、visible、每座暗牌数量及其来源 |
| 进程 | turn、phase、pending、react mode/已完成响应及剩余顺序、freeze 剩余次数/freezer、live/dead wall 数量 |
| 积分 | dealer、base、you_cai_bi_kao、四家可知 chain/chain_piao、本局 score 口径 |
| 完整性 | fast_valid、rollout_valid、missing_fields、已知/推导/未知状态；未知值使用 null，不使用零代替 |

输入以值复制，不保存 Game/Mirror 引用、真实 wall、其他玩家暗牌或源 Game RNG。其他玩家暗牌数量只能来自公开计数或依据阶段/副露推导，不能读取 `sum(g.hands[other])` 作为捷径。所有 actor 后续决策也只能得到其自己的视角。

`visible = hero hand + 未被认领牌河 + 全部可见副露`，pending 若仍在牌河只计一次；被吃碰杠的牌从牌河物料中移入副露，历史弃牌时间线另存。杠计四张。若协议不公开某类暗杠牌种，未知材料必须另建槽位/约束；首版不支持时拒绝 teacher 样本，不能偷取服务端完整回放。

完整世界的逐牌约束为 `visible[t] + sum(other_hidden[t]) + wall[t] = 4`。墙包括当前 20 张不可摸死墙；`live_wall` 与 unseen pool 大小不同。普通舍牌时他家站立暗牌数量通常为 `13 - 3*locked`；根玩家摸牌/吃碰后多一张，反应阶段全部站立。特殊计数异常直接拒绝，不裁剪到四张、不造牌。

Mirror 当前只跟踪本家 chain/chain_piao；`apply_snapshot()` 的现有字段不足以证明四家完整链。后续上下文适配需保留可用公共事件和快照来源，逐字段对账。本家 EV 必需计分字段缺失时回退既有策略；仅 teacher 必需字段缺失时仍允许 Fast EV，但 teacher 标记 `unsupported_context`。不为凑齐字段新增线上请求。

### 3. 动作范围与阶段契约

| scope | 优化范围 | 其他分支 |
| --- | --- | --- |
| `discard`（P1–P4） | 无 HU、无 KONG 可选分支中的全部合法弃牌，含吃碰后立即弃牌与普通打白 | HU/财飘、KONG、反应侧明确委托冻结 shape-v1/legacy |
| `hu-piao`（P5） | 再覆盖无 KONG 分支中的 HU 与全部合法弃牌；财飘是打 W 的规则效果 | 有 KONG 和反应侧仍委托冻结策略 |
| `all-root`（P6） | 当前阶段所有合法动作：弃牌、HU、暗杠/加杠或 PASS/CHOW/PONG/明杠 | 单一合法动作直接返回 |

scope 进入 profile 和所有数据/运行 manifest。P1/P2 可离线诊断，P4 才可发布 `discard` 版本。P5/P6 分别生成新 profile 与 teacher 数据并重做闸门。不能将 `discard` 的通过报告描述成全动作 EV 已通过。

财飘没有新动作编码：根动作仍为 W；只有 `Game._do_discard` 所定义的打后爆头状态才增加链，否则白板弃牌也会断链。打白触发抓打圈，不能仅加一个财飘奖励。P6 对明杠必须同时比较同窗 PONG/PASS；对暗杠/加杠比较 HU/全部合法舍牌，不混用旧向听分数和积分。

### 4. 全舍牌 frontier 与可证明剪枝

`discard_frontier(hand, locked, visible, legal_discards)` 的 Python 参考与 Rust 批量实现返回所有不同合法舍牌的 `tile, shanten, U1, ukeire_bitset`。候选集合必须与合法动作的舍牌子集相等；抓打圈可能只有刚摸牌可打。规则限定后的合法 HU waits 与纯结构进张分开命名，有财必拷响必须经过门禁适配。

最低向听仅作特征。v2 不使用 `non_w or results`，也不在未来弃牌节点调用带最低向听过滤的旧 `best_future_discard` 作为 EV 最优器。Python 为语义参考；缺 Rust 使用相同语义的 Python 或整个策略回退，并记录内核版本。

所有候选先完成便宜层。昂贵层只可用当前模型下有证明的积分上界淘汰，要求严格小于已完成候选的下界；相等时保留稳定 tie-break。shape-v1 的 `q_upper_bound` 不可直接用于 EV2 或允许负权重的拟合模型；无法证明则上界为未知并禁剪枝。被证明淘汰项保留证书/模型版本，不把 Q0 分数伪装成 EV2。

### 5. ScoreValue 与 EV2 的精确定义

当前引擎每个 Game 表示一局，`_win()` **覆盖** `g.scores` 为本局 `settle(...)`，不是在已有累计分上相加。因此 teacher 世界起始本局分为零，终态 reward 为 `g.scores[hero]`；若导入房间累计分，在适配边界先转成本局增量。流局为零，他家自摸为对应负支付。

即时 HU 必须通过与 `Game._can_hu()` 一致的刚摸牌/有财必拷响门禁，再用 `hand_multiplier(concealed14, standing13, locked, chain_count, chain_piao)` 和 `settle(...)[hero]`。standing13 是该次摸牌前手牌，不能用任意移除一张后的牌面替代。庄家自摸为 `24*base*mult`，闲家为 `10*base*mult`，不简写成统一乘八。底分可以在拟合时归一，但报告必须还原到声明的积分单位。

P2 的 EV2 是“均匀未见牌、无对手动作、最多两次未来本家自摸”的**自摸收益特征**，不是包含他家抢胡/吃碰损失的整局净 EV。模型名固定为 `uniform_unseen_no_opponent_actions_score_v1`。令 x 为已应用根动作的本家站立状态：

```text
V0(x) = 0
Vk(x) = Σt remaining[t]/N × {
    settle(HU at draw(x,t))[hero],              合法 HU（P2 模型遇胡即止）
    max_d V(k-1)(apply_discard(draw(x,t), d)),   否则，d 遍历全部合法弃牌
}
EV1 = V1；EV2 = V2（已含第一摸的收益，不能再次加 EV1 重复计分）
```

摸牌从 remaining 无放回扣一张并增 visible；弃牌只是把手牌移到牌河，不减 visible。普通非飘舍牌清零 chain/chain_piao；合法财飘延续规则链，抓打圈限制未来动作。根状态立即 HU 的价值与未来 EV 分开。首版 horizon 未胡尾值明确为零；后续学习的 tail 必须单独版本化且只在未胡分支使用。

在无吃碰/杠的自摸模型中，根弃牌后每轮需消耗四次摸牌才再次轮到本家：live wall 小于四张时没有未来本家摸牌，少于八张时无第二次。中间他家摸牌在交换性均匀模型下积分掉，仅扣墙长，不能把未观察到的牌加入本家 visible。P5/P6 的补牌、跳转和抓打圈变化需按对应动作转移计算机会，不能继续套固定四步。

P5 的模型增加合法 HU 与继续动作价值比较；P6 补充不同 action scope 的转移。离线 teacher 在 P3 起已完整使用 Game 规则和固定后续策略，不受 EV2 遇胡即止近似限制。

### 6. Fast EV 校准与完整层级回退

第一层 `V2-Q0` 为全舍牌基础特征分：P1 可用版本化诊断参数，但明确 `uncalibrated`，不以“预计积分”发布。P2 完成 EV1/EV2 特征后提供 `V2-EV2` 层。P4 将每层分别拟合为同一 reward 定义下的积分预测：向听、U1/p1、I、EV2、B/C、牌型潜力、墙长、庄家、财神数、chain、locked、公开认领风险均需定义和缺失标记。任意影响排序的系数、归一化、桶边界、约束、tail、剪枝和 scope 都进入指纹。

首版采用全局受约束线性模型；LUT 桶候选来自 `wall=0–8/9–16/17–32/33+`、`shanten=0/1/2/3+`、庄家、locked。只有独立验证支持时启用分桶，稀疏桶回退全局模型。不强制“退向听一定减分”这类重新引入硬门槛的约束。每层独立拟合；不能用缺 EV2 的全零输入冒充完整模型。

```text
完整冻结策略动作 → 全候选 V2-Q0 → 全候选 V2-EV2 → 后续版本更深层
                         ↑ 任一高级层未完成则整层退回
```

预算覆盖上下文、合法集、frontier、回退动作、搜索、内核及解释构造；不可抢占内核超时也记录。高级层只有“已精确计算或已被同层有效上界证明不能胜出”才算完整。Q0 未完成则全动作委托冻结策略，不能用部分候选结果择优。时钟中断结果可随机器负载不同；固定节点预算下要求候选顺序/缓存冷热不改变结果。teacher 的 continuation 使用确定性节点模式，时钟仅作使样本失败的安全上限。

### 7. BeliefSampler 与完整世界恢复

初版 belief 为满足当前可见物料、牌数、公开硬约束的均匀隐藏分配；它不宣称根据对手未吃碰等行为得到精确历史后验。可公开证明的约束必须满足，无法表达时标记 unsupported；不能在拟合前丢弃不利的世界。

按 `context_hash + belief_version + seed + sample_id` 生成一次洗牌，从 unseen pool 无放回分配三家暗手，再分配剩余完整墙；墙顺序与 `Game._draw` 的 `pop()` 约定一致，尾部摸到 20 张后停止。将公共副露/牌河/阶段/冻结/四家链/吃额度和本家摸牌标志恢复到独立 Game。世界样本携 fingerprint，可重建但不写入线上解释。

反应阶段不能沿用 Mirror 的 `react_seq=[hero]`：必须还原规则反应顺序和已完成响应。协议只给部分同时响应信息、存在更高优先级未决动作时，根动作应经单独验证的窗口适配恢复；无法确定则该反应状态不进入 teacher。P3 先支持普通舍牌完整状态，P5/P6 在覆盖对应状态恢复后扩展，不凭空假设前面的人已 PASS。

世界恢复前后核对根合法集、物料、暗牌数、墙长、链和根状态指纹。不能调用 `Game(seed)` 发新牌后只覆盖部分字段；构建器必须列全字段并测试独立性。禁止推进在线镜像本体。

### 8. Rollout、共享世界与统计边界

每个 sample_id 下，所有根候选克隆同一初始可能世界。根强制应用对应合法动作，然后各玩家使用冻结 continuation；初版本家和对手均可选固定 shape-v1，后续本家可改固定 shape-v2-fast。任何后续策略都不能调用 rollout、读取别人的样本暗牌或未来墙序。

随机 tie 使用按 sample/actor/该 actor 决策序号分流的随机流；候选枚举和 worker 调度不改变 sample_id。不同根动作会导致不同后续轨迹，不承诺每个后续节点牌面相同。跑到 Game.done 才计分；非法动作、超限、异常不得偷偷以 legacy 修复、零分或临时截断分计入样本。整组 world 标为失败并报告，发布数据不接受未解释失败。

采样预设 `N0=32, batch=32, Nmax=512`，每轮活跃候选在同一批世界补齐后比较。候选均值、样本数、配对差值均值/标准误和有效配对 id 都保留；不同样本数的候选不能用非配对均值误称 paired comparison。

序贯淘汰和“置信胜出”需要预先冻结、控制多候选/多次查看错误率的方法。实现首个正确参考可采用有界收益的同时区间，为有限检查点和候选对分配总 alpha=0.05；收益界必须来自规则证明，不能取观测最大值或裁剪大番。普通 t/正态区间重复检查只能作诊断，不能授权置信淘汰。若正确区间因高番过宽，达到 Nmax 输出 `ambiguous=true`，不强造标签；后续更紧方法需独立统计验证和新版本。

输出包括每动作 EV/CI、best/runner-up、paired delta、samples、win_rate、avg_win_multiplier、draw_rate、ambiguity、停止原因、belief/continuation/统计指纹。无自摸样本的平均倍率为 null。样本不足、算法不支持、世界恢复失败与歧义分别记录。

### 9. 数据集、regret 与 BC

先按原始整局/源种子/房间分 train、validation、final-test，同局所有决策、候选、旋转和重采样必须同组；本轮不得复用 shape-v1 已看过的最终种子充当新 holdout。冻结数据来源、teacher/policy/profile、规则、动作 scope、采样和过滤阈值。切分清单在拟合前形成 artifact。

定义模型内遗憾 `regret(a)=Q(a_teacher)-Q(a)`。离线选 teacher_best 与最终 regret 测量使用独立随机世界：冻结在发现集选出的 teacher_best，再在固定验证世界中成对评估它、shape-v1、shape-v2 和 actual。`estimated_regret` 可以因采样噪声为负，不得截为零；置信区间和歧义都展示，不称真实最优 regret。因两策略使用同一参考，验收也直接报告 `Q(shape-v2)-Q(shape-v1)` 的成对区间，避免只看 best 的选择偏差。

线上只记录本次实际已计算内容；shape-v1/另一 v2 profile/teacher 的补算在离线执行，标明 counterfactual。缺失 actual EV 或 teacher 覆盖时 regret 为 null。按 gid/round/seq/decision id/input hash/scope 关联，不能仅按 seq 或牌值拼接；预期动作与已提交/被接受动作分开。

P7 为现有 NPZ 增加独立元数据与可选 teacher 标签：观测 `extract(..., oracle=False)`，动作经 `action_to_flat`/legal_mask 检查。硬标签仅来自置信充分且 scope 覆盖的样本；歧义样本按预声明方案排除或使用合法集 soft target。teacher EV 存独立字段，不能覆盖实际终局 `score`。线上 `log2data` 对干净局和成功提交的过滤保留；离线重标数据使用新的来源标签，不能伪装成曾成功提交的动作。

### 10. P0–P7 实施与发布次序

| 阶段 | 前置条件 | 交付物 | 出口 |
| --- | --- | --- | --- |
| P0 | 当前代码/旧 change 审核 | 917e7f8 冻结 manifest、旧基线缺口报告、新数据切分与闸门计划 | 区分已验证/待验；基线可复现 |
| P1 | 基线已冻结；P0 耗时验收可继续 | context、Python/Rust 全舍牌 frontier、诊断 Q0 | 信息隔离、候选完整、内核对拍 |
| P2 | P1 | ScoreValue、EV2、预算与解释、显式 discard scope | 计分对拍、未来候选完整、整层回退 |
| P3 | P1/P2 上下文稳定 | belief/world builder、paired rollout、teacher 数据 CLI | 物料守恒、可复现、完整结算、有效不确定性 |
| P4 | P2/P3 与切分冻结 | 校准 profile、独立 regret/4096 对局/性能/新房报告 | discard scope 发布条件全部通过 |
| P5 | P4 普通舍牌闸门 | HU/财飘 scope 和专用 teacher/fixture | 专项计分、全套增量发布闸门 |
| P6 | P5 与反应世界适配 | KONG 与同窗替代动作全根 EV | 四类动作转移、反应预算、发布闸门 |
| P7 | 对应 scope 的 teacher 已验收 | 版本化 BC shard、加载兼容、可选蒸馏计划 | 无隐藏输入/数据泄漏；模型另行验收 |

P0 补验结果不要求一定证明 shape-v1 收益为正才允许研究 v2，但必须如实保留旧结果，默认仍遵守旧发布闸门。最终归档需全部必做任务完成；若先交付 P4，应拆分/迁移后续阶段再归档，不能把 P5–P7 标完成。

## Validation Strategy

| 类别 | 可判定标准 |
| --- | --- |
| 信息/物料 | 改真实墙和他家暗牌、保持本家可见上下文不变，固定节点配置结果与 teacher 分布不变；每种牌严格守恒 |
| 规则/积分 | HU、七对/豪华、爆头、四白板、庄闲/base、有财必拷响、杠/飘/断链、抓打圈、墙尾与现有 Game/scoring 对拍零差异 |
| 内核/回退 | Python/Rust 随机和定向对拍；候选顺序、缓存冷热、各边界中断；没有候选遗失/层级混排 |
| Teacher | 同输入/seed/样本计划确定性；共享世界 id 可审计；无非法动作或未终局分冒充样本；统计覆盖在可枚举小模型上验证 |
| 离线 regret | 新留出状态、独立验证世界；以源局聚类的 95% 成对 CI 满足平均 `regret(v1)-regret(v2)` 下界 >0；同时报告歧义/排除覆盖率 |
| 真实对局收益 | 至少 4096 **对**（8192 次单局运行），相同 seed/规则/座位/庄家，对手固定 shape-v1；16 个 seat/dealer 组合均衡；按源 seed 聚类的得分差 95% CI 下界 >0 |
| 规则配置 | 每个声明发布的 you_cai_bi_kao 配置单独满足收益样本与 CI 闸门；未验收配置禁用该 profile。自摸率/倍率/流局分布完整报告，不用胡率替代得分 |
| 性能 | 同机同内核交错各三次 200 局，候选相对冻结 shape-v1 的 elapsed/games 中位数增加 ≤15%；单局及十场并发的完整决策（含上下文/回退/解释序列化）p95 弃牌 ≤20ms、反应 ≤10ms，报告 p50/p99/max/层级/回退率 |
| 线上 | 对每个发布 scope/profile 至少三个新房、每房十场，BOT、SSE+增量、显式 15/s；逐窗核对 decision/action/合法候选/截止/服务端终态，明确 evaluator 引起的窗口损失为零 |

性能阈值是本项目验收目标，不是服务器硬窗口只有 20ms；排队、HTTP 和身份确认另计。profile、规则、内核、运行方式任一变化都需评估受影响证据是否失效。只依赖节点预算的“理论 20ms”或高回退率掩盖实际退回旧策略都不能替代独立收益与全调用压测。

目前可复用的检查入口是 `python3 -m pytest tests/ -q -p no:cacheprovider`、`scripts/rust_parity.py`、两个 bot_shape 脚本和 `scripts/window_acceptance.py`；EV 专用 CLI/参数在任务中新增，本文不声称它们现在已可运行。每阶段保存无秘密 manifest、测试日志、原始成对行和报告，不提交 local 令牌或完整隐藏回放。

## Risks / Trade-offs

- 放开向听/财神会显著改变 teacher 分布；先做普通舍牌 scope，特征消融必须包括硬过滤移除和 EV2 的独立贡献。
- 均匀 belief 忽略对手行为推断；固定 continuation 也产生策略依赖。用不同冻结对手做敏感性诊断，但不能把结果混入预声明主闸门或宣称普遍最优。
- 高番造成巨大方差，严谨置信区间可能长期歧义。允许无标签，扩样须事前冻结；不能边看最终测试边改阈值。
- 反应窗口公共信息不完整会限制 teacher 覆盖；保留 unsupported 分母，并以本地完整公共历史的 fixture 先验收，不把猜测世界当线上事实。
- P6 与旧反应规范有范围冲突。`bot-react-decision` delta 当前以主 spec 的七个 Requirement 名称定位；shape-v1 归档重命名后，必须先重基这些标题和完整内容，保留两版本语义，再进行归档验证。

## Open Questions and Resolution Points

1. 线上快照是否足够恢复四家 chain/chain_piao、公开暗牌数和反应顺序：P1 用现有样本逐字段审核，明确可恢复集合；P3/P6 对缺失状态拒绝采样，不阻止完整离线状态开发。
2. 序贯区间在真实高番样本上是否足够有效：P3 先实现正确参考与可枚举覆盖测试，P4 报告标签覆盖率；更紧方法或更大 Nmax 必须在最终留出前冻结。
3. 是否需要 LUT 桶和 policy 蒸馏：分别以 P4 独立验证和 P7 数据验收决定；不提前承诺复杂模型或默认切换。
