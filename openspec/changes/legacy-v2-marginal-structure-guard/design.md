## Context

当前流程在 unique current-ukeire winner 时可能直接：

~~~text
same best shanten
  -> current ukeire
  -> unique max
  -> frontier_singleton
  -> return legacy-one-ply
~~~

这使 standing/future shape 根本没有机会执行。

本 change 修正的是“什么时候 singleton 足够安全”，不是扩大搜索深度。

## Decisions

### D1 MarginalStructureRole

建议字段：

~~~text
version
lost_pair_option
same_tile_unseen
lost_taatsu_option
lost_completed_meld
completed_meld_redundancy
alternative_route_count_before
alternative_route_count_after
singleton_connectivity
singleton_live_connectivity
critical_compound_break
loss_tier
~~~

所有字段只能使用 hero 手牌、副露、公开可见计数与规则状态。

same_tile_unseen 只表达保留对子后的公开碰牌选择权，不表示任何对手出牌概率。

### D2 899s 最低语义

899s 至少有：

~~~text
89 taatsu + 9
99 pair + 8
~~~

弃一张 9s 后只剩 89s。

因此至少要求：

- lost_pair_option=true
- alternative route 数下降
- completed_meld_redundancy=false
- 可在结构需求允许时判为 critical_compound_break=true

### D3 7899s 反例语义

7899s 至少有：

~~~text
789 meld + 9
78 taatsu + 99 pair
~~~

弃一张 9s 后仍保留完整 789s。

因此：

- lost_pair_option=true 可以成立
- completed_meld_redundancy=true
- lost pair 本身不得推出 critical_compound_break=true
- 不得因 pair 标签自动压过 5w 这类高连接中张孤张

### D4 Singleton connectivity

孤张必须区分连接潜力，并结合公开剩余张数。

在其它条件相同时，4/5/6 的 live connectivity 应高于 1/9；若相关邻张已全部公开耗尽，则不得继续给虚高连接性。

### D5 Conditional frontier singleton

新 profile：

~~~python
winner = speed_best
challengers = role_aware_challengers(...)

if not challengers:
    return winner

frontier = bounded([winner] + challengers)
run_weighted_two_ply(frontier)
~~~

marginal role 只决定是否值得比较，不直接成为最终 root 大分。

当 marginal guard 阻断 singleton 且 weighted 结果完整时，最终排序仍只使用
已有 weighted future metrics（future ukeire mean/type 与 improvement）以及
current ukeire、牌型和喂牌稳定 tie-break；marginal role 本身不加分。关闭
marginal guard 时沿用原有 current-ukeire-first key，保证 feature-off action
parity。

### D6 Shanten-aware slack

初版：

~~~text
shanten 0   -> 0
shanten 1   -> 2
shanten 2   -> 4
shanten >=3 -> 6
~~~

只有 ukeire_gap <= slack 的 challenger 才允许因结构角色扩围。

### D7 Bounded admission

- baseline speed winner 永远保留
- 优先加入不发生 critical compound break 的 challenger
- 再按 loss_tier、current ukeire、standing shape、legacy shape/feed、stable tile 稳定排序
- 总 root 数始终 <=3

### D8 用户 899s golden

固定 fixture：

~~~text
PON 1m
3m 7m 2p 3p 4p 4p 7p 4s 8s 9s 9s
~~~

冻结 current metrics：

~~~text
9s 82
8s 80
4p 78
3m 77
7p 77
7m 73
4s 72
~~~

在 marginal guard 开启且 weighted search 完整成功时：

- 9s 不得通过 frontier_singleton 直接返回
- 至少一个 ukeire >=76 的非 critical challenger 进入 frontier
- weighted_two_ply_entered=true
- search_used=true
- future_nodes>0
- final selected discard != 9s

若 kernel unavailable、deadline、work budget 或 unsafe partial 导致 fallback，允许恢复 baseline，但必须明确记录 fallback，不能伪造 future 指标。

### D9 7899s + 5w fixture

该 fixture 只防止硬保对子：

- 9s lost_pair_option=true
- 9s completed_meld_redundancy=true
- 不得仅因 99 存在标记 critical
- 5w 的 live connectivity 必须高于边张孤张
- 最终动作由完整牌面 current/future metrics 决定，不在 spec 中硬编码

### D10 Diagnostics

root 级至少记录：

~~~text
marginal_role_version
marginal_loss_tier
lost_pair_option
same_tile_unseen
lost_taatsu_option
lost_completed_meld
completed_meld_redundancy
alternative_route_count_before
alternative_route_count_after
singleton_connectivity
singleton_live_connectivity
critical_compound_break
~~~

decision 级：

~~~text
frontier_singleton_proven
frontier_singleton_blocked
singleton_block_reason
role_guard_slack
role_guard_challengers
admitted_by=marginal_structure_guard
~~~

### D11 Feature flag

建议新增：

~~~text
marginal_structure_guard_enabled
marginal_structure_role_version
marginal_structure_slack_by_shanten
~~~

关闭时 candidate set、singleton 行为与最终动作必须和当前 main 一致。

实现阶段初始默认保持关闭；2026-09-28 用户明确要求将在线
`legacyV2` 默认切换为 `marginal_structure_guard_enabled=true`。
`legacyV2-offline`、baseline/对照 evaluator 继续显式关闭，线上仍可传入
`false` 回滚。性能、Stage 1 与 Stage 2 证据门尚未完成，继续作为发布后的
监控与回滚依据，不把本次用户授权当作这些门禁已通过。

## Validation

Correctness：

- 899s 与 7899s role 分类不同
- 7899s 不因 99 标签被硬保护
- 5w live connectivity > 1w/9w
- hidden state 不影响 role/admission
- 用户 golden 完整搜索后不弃 9s
- gap 超过 slack 不扩围
- frontier <=3
- feature off action parity=100%

Performance：

- hard budget 保持 50ms
- ordinary discard p95 增幅 <=10%
- p99 增幅 <=15%
- fallback 增加 <=1 percentage point
- 4-bot elapsed/game median 退化 <=10%

Score：

Stage 1 >=4096 paired games；mean < -0.10/局或 95% CI upper <0 则停止。

Stage 2 独立 seeds >=30720 paired games；默认启用要求 overall mean >=0 且 95% CI lower >= -0.10/局。
