## MODIFIED Requirements

### Requirement: 决策携带可识别的评价版本与实际层级

shape-aware legacyV2 SHALL 额外区分实际弃牌决策 scope：

- `baotou_scope`：持财神、best_s=0 的专用爆头推进分支直接返回；
- `weighted_two_ply`：进入 weighted frontier / Stage A/B；
- `legacy`：回退或冻结基线排序。

baotou_scope MUST NOT 被记录为 weighted search 已使用；若该分支直接返回，`stage_b_entered` MUST 为 false，future-shape 指标 MUST 保持 missing/null。

#### Scenario: 财神专用分支提前返回

- **WHEN** 持财神且 best_s=0，`_choose_discard_baotou()` 成功选出弃牌
- **THEN** explanation SHALL 标记 `decision_scope=baotou_scope`
- **AND** SHALL NOT 声称 weighted two-ply 或 Stage B 参与了最终动作

#### Scenario: shape-v1 在运行中回退
- **WHEN** 运行配置为 shape-v1 而本次因预算回退 legacy
- **THEN** 日志同时记录请求的 evaluator 与实际 legacy 层级及原因，不将本次标成完整前瞻

#### Scenario: 内核缺失导致的降级被显式报告
- **WHEN** 客户端进程所在环境没有可用的原生 weighted 内核
- **THEN** 启动与决策记录显式标注降级与实际层级，回放/前端可提示"本次决策走 legacy 回退"，无需逐字段排查

#### Scenario: 护栏状态可见
- **WHEN** 一次决策启用了前沿护栏
- **THEN** 解释给出准入/截断明细与候选级 `admitted_by`，可复核哪些候选因护栏进入比较

#### Scenario: 读取旧日志
- **WHEN** decision 记录没有 evaluation 对象
- **THEN** logview 和回放仍可处理，评价元信息显示缺失而不补造

#### Scenario: Safe Stage-A partial is used
- **WHEN** future improvement bounds prove a winner before child ukeire
- **THEN** top-level explanation marks `search_used=true`, phase
  `future_shanten`, partial acceptance, and null future ukeire fields

#### Scenario: Stage B produces a complete result
- **WHEN** improvement is tied and Stage B completes for the retained frontier
- **THEN** explanation marks `search_used=true`, phase `two_ply`, complete=true,
  and exposes child ukeire/type metrics for each retained root

#### Scenario: Search result falls back
- **WHEN** deadline/work budget prevents a safe decision certificate
- **THEN** the action is the complete legacy result, `search_used=false`, and the
  fallback reason/coverage remain visible without partial future values

### Requirement: 候选解释保留原始值与加权贡献

对于 shape-aware baotou_scope，解释 SHALL 至少保留参与最终 tie-break 的候选级字段：

- baotou tier；
- 是否为财神弃牌；
- baotou_ukeire；
- standing shape signature/quality；
- legacy discard shape cost；
- feed risk；
- stable tile；
- `baotou_shape_used` / 是否由 standing shape 改变 winner。

#### Scenario: 124s 财神牌例可复核

- **GIVEN** 牌串 `23455m 124s EE w`、副露 `789p`
- **WHEN** 1s 与 4s 的 baotou tier / baotou_ukeire 打平
- **THEN** explanation SHALL 显示旧局部弃牌损失 5/3 与新的 standing shape 比较
- **AND** 可明确归因 shape-aware 模式为何由旧选 4s 改为选 1s

#### Scenario: 直接进张相同但结构取舍不同
- **WHEN** 同向听候选的 U1 相同而 weighted frontier 选择与 legacy 不同
- **THEN** 解释显示造成差异的已完成 weighted improvement、child ukeire、coverage 和最佳后续路径

#### Scenario: 对子保护未胜出
- **WHEN** BOT 拆开一个天然对子
- **THEN** 解释可以复核剩余雀头来源、该对子公开碰牌潜力及胜出候选的 future 收益，不仅输出“对子权重较低”

#### Scenario: 直接进张相同但未来取舍不同
- **WHEN** 同向听候选的当前 U1 相同，而 legacy V1 通过未来特征选择了不同于基线 legacy 的弃牌
- **THEN** 解释显示两者的当前进张、未来改良权重、未来进张、牌型损失、喂牌风险、最佳后续弃牌及最终排序差异

#### Scenario: 20260920 的 9b 反事实重算可复核
- **WHEN** 离线重算 `u_9812ba08fe2f_a_e18e58e3acb8_r1_b9_t0` 的 `seq=100`，并比较 `9b` 与 `3t`
- **THEN** 结果同时标明原日志实际选择、V1 反事实候选、输入状态和评价版本；不得把离线选择写成原时点已执行动作

#### Scenario: V1 预算回退不补零
- **WHEN** legacy V1 只完成当前 ukeire，未来特征因预算或输入不完整而未完成
- **THEN** 解释将未来字段标记为缺失，记录实际 legacy 层级和回退原因，并按完整 legacy 排序展示，不把缺失字段序列化为 0

#### Scenario: Accepted partial includes coverage
- **WHEN** online weighted frontier accepts a partial result
- **THEN** the selected candidate explanation contains `covered_weight`, `total_weight`, `coverage`, `partial_accepted`, and the search counters used to reach it

#### Scenario: Incomplete fallback does not fake future values
- **WHEN** coverage is insufficient and the evaluator falls back to legacy
- **THEN** future metrics remain missing/null, while the fallback reason and actual legacy level are recorded

#### Scenario: Equal current ukeire has future evidence
- **WHEN** two roots have equal current shanten and current weighted ukeire but different future metrics
- **THEN** the explanation shows both roots' weighted improvement and expected child ukeire before shape/feed tie-breaks

#### Scenario: Accepted partial includes decision-safe bounds
- **WHEN** online weighted frontier accepts a partial result
- **THEN** the selected candidate explanation contains covered/total weight, coverage, observed improvement, lower/upper improvement bounds, the partial mean denominator, and search counters

#### Scenario: Unsafe partial does not fake future values
- **WHEN** coverage is high but no root is proven safe by the improvement bounds
- **THEN** future metrics remain missing/null in the selected legacy fallback explanation, while the fallback reason and actual legacy level are recorded

#### Scenario: Singleton frontier records short-circuit
- **WHEN** current shanten and current ukeire leave only one retained root
- **THEN** the explanation records a one-ply short-circuit reason and does not claim that uncomputed future metrics are zero or complete

#### Scenario: Fast but discarded search
- **WHEN** Rust returns partial rows but bounds overlap and legacy is selected
- **THEN** benchmark counts fallback and `search_used=false`, even though raw
  search time is recorded
