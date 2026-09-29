## Context

当前有两道门：

~~~text
same best shanten
 -> raw current ukeire
 -> singleton / frontier admission
 -> weighted two-ply
 -> root comparator still starts from raw current ukeire
~~~

因此只修 admission 不够。本 change 同时修“谁值得比较”与“进入比较后谁有资格反超”。

## Decisions

### D1 Marginal structure role

沿用 versioned MarginalStructureRole：lost pair/taatsu、completed-meld redundancy、alternative routes、singleton live connectivity、critical compound break、loss tier。只使用 hero/public 信息。

### D2 Competitive speed ratio

~~~text
current_speed_ratio = current_ukeire / best_current_ukeire
~~~

初版 threshold：

~~~text
shanten 0   -> 1.00
shanten 1   -> 0.90
shanten 2   -> 0.82
shanten >=3 -> 0.78
~~~

配置必须 profile 化并进入 fingerprint。

### D3 Speed dominance

challenger ratio 低于 threshold 时，best-current root 可以仅凭直接速度排除 challenger；达到 threshold 后，raw current_ukeire 不得单独构成淘汰证明。

~~~text
57 vs 48, shanten=2:
48/57=0.842 >= 0.82
=> no speed-only dominance

57 vs 35:
35/57=0.614 < 0.82
=> speed dominance may prune
~~~

### D4 Pareto frontier

competitive band 内至少用四个维度：

~~~text
maximize current_ukeire
maximize current_ukeire_types
minimize marginal_loss_tier
maximize standing_shape_quality
~~~

A 只有在所有维度不差于 B 且至少一维严格更优时，才能 dominate B。

因此“速度快但结构差”和“速度稍慢但结构好”都属于非支配候选。

### D5 Bounded cap

- baseline best-current root 永远保留；
- 保留 non-dominated roots；
- 超过 slot 时依次按更低 marginal loss、更高 speed ratio、更高 standing shape、更高 ukeire types、legacy shape/feed、stable tile 截断；
- 总 roots <=3。

### D6 Conditional singleton

只有其它 roots 均被 legality、speed dominance、Pareto dominance 或 deterministic cap 安全淘汰后，才允许 frontier_singleton。

### D7 Root comparator inside competitive band

进入同一 band 后：

~~~text
1 legality/wildcard gates
2 future shanten improvement
3 future ukeire mean
4 future ukeire types mean
5 marginal structure loss tier
6 future shape quality
7 standing shape quality
8 raw current ukeire
9 feed risk
10 stable tile
~~~

current ukeire 不删除，而是作为 band gate + 后置 tie-break。

### D8 Stage A certificate

band 内 Stage A 只有在 future-improvement bounds 严格证明 winner 时才可接受 partial。raw current_ukeire 差异不能在 band 内单独构成 winner certificate。

### D9 899s golden

~~~text
9s 82
3m 77
77/82=0.939 >= shanten>=3 threshold 0.78
~~~

因此不得 raw-speed singleton。完整 search 成功时 selected != 9s。

### D10 2w vs 4s golden

~~~text
discard 2w:
shanten=2
ukeire=57
types=17
shape_loss=5
role=break taatsu

discard 4s (log 4t):
shanten=2
ukeire=48
types=15
shape_loss=2
role=connected singleton

after 4s:
2w 3w 3w 7w 8w 8w 9w 4b 5b 5b 6b 7t 9t
~~~

验收：

- 48/57≈0.842 >=0.82；
- speed_dominated=false；
- 2w 因 marginal loss 更差，不得 Pareto-dominate 4s；
- 4s 必须进入 bounded weighted frontier，除非 deterministic cap 被另一个更优 challenger 合法占用，且原因可诊断；
- root comparator 不得在 future metrics 前以 57>48 结束；
- 冻结该 replay 的 future metrics 后，若 2w 未建立 earlier future-speed dominance，而 4s marginal loss 更低，则最终 fixture 选择 4s。

### D11 7899s + 5w anti-overfit

lost_pair_option 可以成立，但 completed_meld_redundancy=true；pair 标签不得形成 hard protection。5w live connectivity 必须正确表达。

### D12 Diagnostics

root：

~~~text
current_speed_ratio
speed_band_threshold
in_competitive_speed_band
speed_dominated
speed_dominated_by
pareto_dominated
pareto_dominated_by
pareto_vector
marginal_loss_tier
~~~

decision：

~~~text
speed_band_version
frontier_singleton_proven
frontier_singleton_blocked
pareto_frontier_before_cap
pareto_frontier_after_cap
singleton_block_reason
~~~

### D13 Flags

~~~text
speed_band_enabled
speed_band_version
speed_band_min_ratio_by_shanten
pareto_frontier_enabled
~~~

关闭时恢复当前 main 的 raw-current-ukeire ordering。

## Validation

- 57/48 同 band；
- 57/35 speed dominated；
- Pareto 不让“速度快但结构差”错误支配结构候选；
- band 内 future/structure 可反超 raw current ukeire；
- Stage A 与新 comparator 一致；
- frontier<=3；
- feature off parity=100%；
- p95<=+10%，p99<=+15%，fallback<=+1pp，4-bot median<=+10%；
- Stage1 >=4096 paired，Stage2 >=30720 paired。
