可以。结合你刚提交的两笔核心改动——`72a4f65` 的 shape-aware evaluator，以及最新 `917e7f8` 的 Q lookahead 性能优化——我建议不要再把下一版理解成“继续调几个牌型权重”，而是把它正式拆成 **在线最佳舍牌近似器 + 离线真实 EV Teacher** 两层。

你现在的 `shape-v1` 已经把地基打得挺好了：`EvalContext` 明确禁止读取真实牌墙和对手暗手，Q0/Q 有完整层级回退，I/H2 做了下一摸前瞻，结构分解 B/C 考虑了复合形和财神，最近又加入了 Rust `best_future_discard`、decision-local cache 和 Q 上界安全剪枝。最新提交记录的本地 20 局是 795 次弃牌、均值约 17.76ms、p95 22.87ms；但 4096 局收益、10 场并发和线上新房验收还没有完成。

我建议下一版正式命名为 **`shape-v2 / EV-discard`**。

---

# 一、先重新定义“最佳舍牌”

真正的目标不应该再是：

$$
\max(\text{进张、牌型、改良})
$$

而是：

$$
a^*(s)=\arg\max_a E[\Delta Score\mid PublicState=s,\ Action=a]
$$

也就是说：

> 在当前能够观测到的信息下，打哪一张牌，使这一局最终积分的条件期望最高。

这里有个很重要的边界：“最佳”不是知道真实牌墙后的 Oracle 最优，而是**在相同公开信息和同一套隐藏信息概率模型下的最优**。

这正好延续你当前 `EvalContext` 的设计：现有 evaluator 只拿自己手牌、visible、公开副露等信息，不读取 `Game.wall` 或对手暗手。

因此以后我们可以明确区分：

```text
Oracle Best
    知道真实墙和对手手牌
    × 禁止作为机器人决策依据

Belief Best
    只知道机器人当时能看到的信息
    √ 我们真正追求的“最佳舍牌”

Fast Approximation
    20ms 内逼近 Belief Best
    √ 线上机器人实际使用
```

---

# 二、当前 shape-v1 已经解决了什么，还缺什么

现在的评分其实是：

$$
Q_0=p_1+w_BB+w_CC
$$

$$
Q=p_1+w_II+w_HH_2+w_BB+w_CC
$$

其中 `p1` 是直接进张概率，`I` 是同向听改良，`H2` 是两次自摸以内的模型胡牌概率，B/C 是牌型结构与公开可兑现副露潜力。当前设计还明确假设：

```text
uniform_unseen_no_opponent_actions
```

也就是均匀未见牌、不模拟对手动作。

这已经比原来的：

```text
向听 → 财神 → ukeire → shape_cost → feed_risk
```

高了一个层级。

但是离“最佳舍牌”还有四个关键缺口。

第一，当前 `evaluate_discard_candidates()` 仍然先找：

```python
best_s = min(shanten(after_discard))
```

然后**只保留最低向听候选**进入 Q0/Q。

所以：

```text
0 向听、普通平胡路线
```

天然可以把：

```text
1 向听、但很高概率形成豪华七对/爆头/高倍链
```

直接淘汰。

从积分 EV 角度，这不是严格成立的。

第二，财神目前仍然是硬保护。最终排序时实际上是：

```python
non_w = [x for x in results if x[0] != W]
pool = non_w or list(results)
```

所以只要存在非财神的最低向听候选，即使：

```text
Q(打白板) > Q(其他所有牌)
```

也不会选择白板。

这对于稳定 teacher 很安全，但它不能叫最终 EV 最优。

第三，`H2` 优化的是**胡牌概率**而不是**积分**。

例如：

```text
路线 A
两摸胡概率 35%
平均倍率 1

路线 B
两摸胡概率 24%
平均倍率 4
```

当前 Q 容易更喜欢 A。

真正积分 EV 很可能应该喜欢 B。

你的 `scoring.py` 已经有完整的七对、豪华、杠/飘动作链、4 白板、爆头、庄闲结算，因此没有理由最终还用“胡牌概率”替代“积分价值”。

第四，`choose_shape_action()` 目前主动把：

```text
HU / 财飘
KONG
```

冻结在 legacy 路径，shape-v1 只真正优化普通舍牌。

这意味着最关键的一类价值交换：

```text
现在胡  vs  弃胡财飘
现在打普通牌 vs 杠后补牌
```

暂时还没有进入统一价值体系。

---

# 三、完整架构：Fast EV + Rollout Teacher

我建议最终形成下面这条链：

```text
                    Public State
                         │
                         ▼
                所有合法 Root Action
                         │
             ┌───────────┴───────────┐
             ▼                       ▼
        shape-v2 Fast EV       rollout-v1 Teacher
          线上 ≤20ms               离线慢搜索
             │                       │
             │                 Belief Sampling
             │                       │
             │                 完整模拟到局末
             │                       │
             │                  Final Score EV
             │                       │
             └──────────校准/验收────┘
                         │
                         ▼
                   最佳舍牌策略
```

重点是：

**线上不跑完整 Monte Carlo。**

否则 10 场并发和你的平台窗口预算根本扛不住。

完整 rollout 是离线裁判；在线的 `shape-v2` 是一个通过离线裁判不断校准的低延迟近似器。

---

# 四、第一项核心改造：取消“最低向听绝对硬门槛”

这里我会直接改。

现在：

```text
所有合法 discard
      ↓
先找 min shanten
      ↓
其他候选全部扔掉
      ↓
Q0 / Q
```

改成：

```text
所有合法 discard
      ↓
批量计算：
shanten / U1 / p1 / waits
      ↓
全部进入 Fast-Q0
      ↓
只有经过“可证明价值上界”才能剪枝
      ↓
Fast-EV / Q2
```

注意，我不是建议：

```text
min_shanten + 1 全留下
```

这种又换一个拍脑袋门槛。

而是**shanten 从 hard gate 变成 value feature**。

例如：

| 候选 | 向听 | U1 | 两摸价值 | 预期胡牌倍率 |  Fast EV |
| -- | -: | -: | ---: | -----: | -------: |
| 9筒 |  0 |  8 |    高 |    1.1 |     +2.6 |
| 1索 |  0 |  6 |    中 |    2.0 |     +3.1 |
| 白板 |  1 | 10 |    中 |    4.7 | **+3.8** |

如果白板这条高倍路线真的值钱，就允许它赢。

这才是“最佳舍牌”。

---

# 五、第二项：把 H2 升级成「积分型有限深度 EV」

这是 shape-v2 最值得做的核心。

目前 H2：

> 两次自摸以内胡牌的模型概率。

下一版改成 `EV2`：

> 两次未来自摸以内，在当前规则下能够获得的**期望积分**。

例如站立牌 `x`：

$$
V_1(x)
=
\sum_t P(t)
\begin{cases}
Reward(x+t), & t可以胡\\
Tail(x+t), & otherwise
\end{cases}
$$

两层则：

$$
V_2(x)
=
\sum_tP(t)
\begin{cases}
Reward(x+t), & HU\\
\max_dV_1(x+t-d), & otherwise
\end{cases}
$$

这里最大的变化在于 `Reward` 不再是：

```text
HU = 1
```

而直接调用你现有计分：

```text
hand_multiplier(...)
settle(...)
```

所以自动得到：

```text
平胡
七对
豪华七对
爆头
4 白板
杠开 / 财飘 / 杠飘链
庄家 ×8
```

这些信息。

这样：

```text
35% × 小胡
```

和：

```text
20% × 大胡
```

终于能放在一把尺子上比较。

---

# 六、EvalContext 需要补充“积分上下文”

现在的 `EvalContext` 对 shape 足够，但对 EV 不够。

我建议不要污染现有 `EvalContext`，新增：

```python
DecisionContext
```

或者：

```python
ScoreEvalContext
```

它包含现有字段，再增加：

```text
dealer
hero seat
base score

chain_count
chain_piao

round / dealer state
live_wall

all public melds
all discards
per-seat public concealed-count estimate

current turn / phase
```

这里仍然有一道铁律：

> 绝对禁止保存 opponent concealed tiles 和真实 wall tiles。

现有测试已经专门验证 hidden state 不应该影响 evaluator，这个安全边界继续保留。

---

# 七、第三项：财神从“硬保护”改成真正的动作价值

下一版应该删掉：

```python
pool = non_w or list(results)
```

改为：

```text
白板和普通牌一视同仁进入候选
```

但是不是意味着机器人会乱打财神。

恰恰相反。

因为现在：

```text
保留财神的万能牌价值
打财神产生财飘的倍率价值
爆头价值
未来 4 白板价值
动作链价值
剩余活墙风险
```

全部进入 EV。

最后可能得到：

```text
保留财神 EV = +7.2
打财神 EV   = +3.8
→ 不打
```

也可能：

```text
现在已经爆头
活墙较深
财飘以后倍率翻升
下一摸高概率胡

保留财神 EV = +18.4
打财神 EV   = +26.7
→ 财飘
```

这比：

```python
if live_wall_left >= 5:
    piao
```

合理得多。

最终 `_should_piao()` 应该逐步退化为：

```text
legacy fallback
```

而不是主策略。

---

# 八、离线 Rollout Teacher：真正定义“最佳”

上面 `EV2` 仍然忽略了一个巨大的现实因素：

```text
另外三个人也在玩。
```

当前模型自己也很诚实地写着：

```text
uniform_unseen_no_opponent_actions
```

所以最终我们需要一个离线 `rollout-v1`。

它负责回答：

> 在当前公开信息下，如果我打 6 万、9 万、白板……分别完整把这一局打完，平均最后到底得到多少分？

这里必须做 **Belief Sampling**。

已知：

```text
自己的手牌
所有牌河
所有副露
公共状态
剩余墙长度
```

得到：

```text
unseen[t] = 4 - visible[t]
```

然后随机产生一个与当前信息一致的可能世界：

```text
unseen pool
    │
    ├── 对手 A 暗手
    ├── 对手 B 暗手
    ├── 对手 C 暗手
    └── wall
```

注意：

> 这个过程绝不能偷偷使用当前离线 `Game` 里本来就存在的真实 opponent hands / wall。

否则 teacher 被 Oracle 信息污染，以后 BC 反而学不到线上可用策略。

---

# 九、每个候选必须共享同一批随机世界

例如候选：

```text
6万
9万
白板
```

世界 #17 一旦采样出来：

```text
Opponent hands
Wall
Random tie seeds
```

三张候选都必须在**世界 #17** 中比较。

然后世界 #18 再全部比较一次。

也就是：

$$
\Delta_i =
Score_i(A)-Score_i(B)
$$

要做 paired comparison。

这是 Common Random Numbers。

麻将的单局方差巨大，如果每张候选各自随机 128 个完全不同牌墙，你会拿很多计算力去测“谁随机得比较走运”。

共享世界后：

```text
A 与 B 的差值
```

方差会明显下降。

---

# 十、Rollout 后续策略不要递归调用自己

根节点：

```text
尝试所有候选舍牌
```

之后 Hero 和其他三家统一走固定 continuation policy。

第一版我建议：

```text
Hero future policy:
shape-v1 / shape-v2-fast

Opponent policy:
shape-v1
```

而不是：

```text
每个未来摸牌点
再次完整 rollout
```

后者立即指数爆炸。

数学上我们估计的是：

$$
Q^\pi(s,a)
$$

然后：

$$
\pi'(s)=\arg\max_a Q^\pi(s,a)
$$

这是标准的 rollout policy improvement。

等新的 teacher 变强后，再用：

```text
shape-v2 作为 continuation policy
```

重新生成下一代数据即可。

---

# 十一、Rollout 的 reward 就用最终实际积分

不要再人为写：

```text
向听 -100
ukeire +2
七对 +10
爆头 +20
```

全部不要。

唯一的核心 reward：

$$
R = FinalScore_{hero}-InitialScore_{hero}
$$

因为你的完整规则状态机和结算代码已经存在。

所以 rollout 自然会学会：

```text
早点胡的价值
庄家的价值
高番路线价值
别人先胡造成的损失
给别人吃碰后改变局面的代价
流局
杠 / 飘
```

而且不用我们猜权重。

---

# 十二、Rollout 不需要固定 1024 次：用 Sequential Sampling

每个决策都无脑：

```text
14 candidates × 1024 rollouts
```

太浪费。

我建议：

```text
第一批：每候选 32
        ↓
计算 paired mean / stderr
        ↓
明显落后的候选淘汰
        ↓
剩 2~3 张继续 +32
        ↓
直到：
best 与 runner-up 的置信区间分离
或者达到 Nmax
```

典型参数可以从：

```text
N0 = 32
batch = 32
Nmax = 512
```

起步。

高方差特殊局面：

```text
财飘
豪华七对
连杠
4 白板
```

可能跑到 512。

普通孤张选择可能 64～128 次已经分出来。

Teacher 最终输出：

```text
best_action
EV(best)

runner_up
EV(runner_up)

delta_EV
confidence

samples
win_rate
avg_win_multiplier
```

如果两张牌根本分不出来：

```text
ambiguous=true
```

不要强造标签。

---

# 十三、shape-v2 在线评分怎么从 Teacher 学回来

因为完整 rollout 不能在线跑，我们让它变成 shape-v2 的裁判。

当前 `wI=0.25 / wH=0.5 / wB=0.05 / wC=0.025` 本身在代码里就标注为 diagnostic defaults，不是经过最终积分校准得到的参数。

下一版不要继续凭感觉调。

从 rollout 数据拟合：

$$
EV =
f(
shanten,
U1,
p1,
I,
EV2,
B,
C,
fanPotential,
wall,
dealer,
wildCount,
chain,
locked,
claimRisk
)
$$

如果你仍希望它保持“启发式机器人”，我不建议第一版上复杂神经网络。

先用：

```text
分桶 LUT
+
受约束线性模型
```

例如：

```text
wall bucket:
0-8
9-16
17-32
33+

shanten:
0
1
2
3+

dealer:
yes/no

locked:
0/1/2/3/4
```

再在桶内拟合权重。

这样最终解释仍然可以长这样：

```text
打 1s

base_speed      +1.83
direct_win_ev   +0.91
future_ev2      +1.27
shape_B         +0.13
claim_C         +0.04
fan_potential   +0.88
claim_risk      -0.21
---------------------
fast_EV          4.85
```

可调、可审计，也非常适合你的复盘工具。

---

# 十四、Rust 下一步最值得写的不是更多 DFS，而是「全舍牌批处理」

你这次 `917e7f8` 已经证明路线是对的：把“下一摸后最佳弃牌”放进 Rust batch kernel 后，Q 成本明显降下来了。`shanten.py` 现在也已经暴露了 `best_future_discard()`，并把 kernel version 放进 evaluator profile。

下一步我会新增：

```python
discard_frontier(...)
```

一次 FFI 返回当前 14 张手里**所有合法不同舍牌**：

```text
tile
shanten
U1
ukeire_bitset
```

而不是 Python：

```python
for tile:
    shanten(...)
    ukeire(...)
```

结果类似：

```python
[
  (tile=0, shanten=1, u1=27, mask=...),
  (tile=3, shanten=0, u1=5,  mask=...),
  ...
]
```

这样有两个好处。

第一，取消 min-shanten hard gate 后，不会因为候选翻倍导致 Python/Rust FFI 调用量爆炸。

第二，后面的：

```text
Fast-Q0
EV2
上界剪枝
```

都能共享同一份 frontier。

---

# 十五、保留你现在非常正确的“事务式回退”

你这版代码里有个设计我建议原样保留。

现在是：

```text
legacy
  ↓
所有候选完整 Q0
  ↓
所有候选完整 Q
```

如果 Q 做不完：

```text
所有候选统一按 Q0 比
```

而不是：

```text
候选 A 用 Q
候选 B 用 Q0
候选 C 没算完
```

这点非常重要。OpenSpec 也明确要求“最近一个完整全候选层级”作为回退。

shape-v2 改成：

```text
Legacy
   ↓
V2-Q0：全部合法舍牌完整
   ↓
V2-EV2：完整候选层
   ↓
可选更深层
```

任意一层没完整：

```text
退回上一完整层
```

绝不混排。

---

# 十六、建议的代码结构

我不会把 rollout 塞进现在已经很大的 `hand_eval.py`。

建议最终演化成：

```text
mj/
├── bot.py
├── hand_eval.py           # 保留 shape-v1 / 基础结构特征
│
├── decision/
│   ├── context.py         # PublicDecisionContext
│   ├── frontier.py        # 全合法舍牌候选
│   ├── fast_ev.py         # shape-v2 在线 evaluator
│   ├── score_value.py     # HU/番数/结算期望
│   └── report.py
│
├── rollout/
│   ├── belief.py          # unseen → opponent hands + wall
│   ├── simulator.py       # 固定 continuation policy
│   ├── evaluator.py       # paired Q(a)
│   └── teacher_data.py
│
└── ...
```

依赖保持：

```text
rules/game/scoring
        ↓
hand_eval
        ↓
decision/fast_ev
        ↓
rollout
        ↓
teacher data
```

不能让底层规则反向依赖 rollout。

---

# 十七、日志必须直接支持“最佳舍牌 regret”

每一个普通摸牌决策都建议输出：

```text
actual_action

shape_v1_action
shape_v2_action

candidate:
    tile
    shanten
    U1
    I
    B
    C
    EV1
    EV2
    fast_ev
    q_upper_bound

level
fallback_reason
elapsed_ms
kernel_calls
profile_fingerprint
```

离线跑 Teacher 后，再附：

```text
teacher_best
teacher_ev

actual_ev
shape_v2_ev

regret =
teacher_ev - actual_ev

shape_v2_regret =
teacher_ev - shape_v2_ev
```

以后复盘页面就可以直接显示：

```text
Seq 596
摸：X

实际：9s
shape-v1：9s
shape-v2：8s
Teacher：8s

EV(9s) = +3.21 ±0.24
EV(8s) = +4.02 ±0.22

estimated regret = 0.81
```

这会比单纯说：

> “这里打 8s 牌型比较好”

有用太多。

也正好可以接你之前做的 seqNo 复盘页面。

---

# 十八、验收标准我建议这样定

| 层面      | 验收                                                                         |
| ------- | -------------------------------------------------------------------------- |
| 信息安全    | 修改真实 wall / opponent hidden hands，公开状态不变时，shape-v2 决策必须完全不变                |
| 合法性     | 根候选严格来自 `legal_actions()`                                                  |
| 计数      | belief 每种牌总数永远 ≤4，隐藏手+wall+visible 守恒                                      |
| Rollout | 同候选同 seed 完全确定性                                                            |
| CRN     | 同一个 sample id 的所有候选共享同一 determinization                                    |
| Score   | terminal reward 与现有 scoring/Game settlement 对拍 0 差异                        |
| Fast-v2 | 不再硬删除 `shanten > min` 或财神候选                                                |
| 回退      | 不能混合 incomplete EV2 与 complete Q0 排名                                       |
| 性能      | 单局与 10 场并发都按现有 20ms 舍牌窗口验收，不只看 evaluator 内部 budget                         |
| 收益      | 4096 局 paired/fair evaluation，`shape-v2 vs shape-v1` 的平均积分提升 95% CI 下界 > 0 |
| Teacher | 独立测试状态上 shape-v2 的平均 rollout regret 明显低于 shape-v1                          |
| 线上      | 至少 3 个新房逐窗验收，不能出现 evaluator 导致的窗口损失                                        |

你最新提交自己已经明确写了“尚未完成 4096 局独立收益、十场并发性能闸门和三个新房逐窗验收”，所以这些正好可以直接成为下一阶段 gate。

---

# 十九、实际开发顺序

我建议不要现在就碰 MCTS。

最稳的路线是这一条：

```text
P0
冻结 917e7f8 为 shape-v1 baseline
补 4096 / concurrency / online acceptance

        ↓

P1
Rust discard_frontier
所有合法舍牌进入 Q0
取消 min-shanten hard gate
取消财神 hard exclusion

        ↓

P2
增加 score context
H2 → score-aware EV2
先只优化普通 discard

        ↓

P3
实现 BeliefSampler + paired rollout teacher
完整模拟到局末
产出 EV / regret dataset

        ↓

P4
用 rollout 标签校准 shape-v2 参数/LUT
4096 独立 A/B

        ↓

P5
把 HU vs 财飘加入统一 root EV

        ↓

P6
再把 KONG 加进统一 root EV

        ↓

P7
Rollout teacher 生成 BC 数据
需要时再蒸馏进 policy 网络
```

我尤其建议 **P2 和 P3 分开**。

P2 给你一个仍然完全确定性、可解释、低延迟的 score-aware heuristic；P3 才负责回答“这个 heuristic 到底离真正最佳还有多少 regret”。

---

## 最终目标

你当前版本可以概括为：

```text
shape-v1
= “哪张牌的牌效率和两摸牌型最好？”
```

我建议下一版变成：

```text
shape-v2
= “在公开信息下，哪张牌的低延迟预计积分最高？”
```

再用：

```text
rollout-v1
= “如果把每一种可能世界都真正打一遍，哪张牌的最终积分 EV 最高？”
```

作为离线裁判。

这样以后你再遇到“明明 9s 进张更多，为什么应该打 8s”“为什么这里宁可退一向听”“为什么这里应该打财神财飘”，都不用继续加人工 if/else——**直接看 candidate EV、teacher EV 和 regret**。

从你现在刚提交的代码状态看，我认为下一步最值得优先落地的是 **`all-discard frontier + score-aware EV2`**，而不是继续扩展 B/C 或调 `wI/wH`。现有 Q/I/H2 框架可以大量复用，而这一步恰好会把它从“牌型评估器”推到真正的“舍牌价值评估器”。
