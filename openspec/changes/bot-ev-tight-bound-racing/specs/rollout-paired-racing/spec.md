## ADDED Requirements

### Requirement: Active 候选必须在共享可能世界上成对采样

rollout teacher SHALL 为同一 `sample_id` 的所有 active root candidates 使用相同初始可能世界，并记录 world fingerprint。每轮 batch MUST 先补齐 active candidates 的共享样本，再进行 racing；候选顺序和 worker 调度不得改变 sample id 或世界内容。

#### Scenario: 候选顺序反转

- **WHEN** 同一配置下以相反顺序传入合法 root actions
- **THEN** action 规范化后共享相同 sample/world 对应关系，成对收益和停止结论保持可复现

#### Scenario: 候选被淘汰

- **WHEN** 某 action 在一轮结束后被 paired UCB 淘汰
- **THEN** 后续 batch 只运行剩余 active actions，历史 rows 仍保留该 action 已完成的 paired 观测

### Requirement: Paired delta CI 必须使用候选界之和

对共享 sample 定义 `D_i(a,b)=R_i(a)-R_i(b)`，teacher SHALL 直接维护每个 active pair 的 delta 均值、有效样本数和区间。若两个候选的 rollout absolute bounds 为 `B_a` 与 `B_b`，则该 pair 的 delta interval MUST 使用至少 `B_a+B_b` 的安全支持界；实际 reward 不得裁剪。

#### Scenario: CRN 降低差值方差

- **WHEN** 两个候选在同一 world 的终局 reward 高度相关
- **THEN** racing 使用逐 sample delta 的区间，而不是把两个 marginal CI 的宽度相加来判断胜负

#### Scenario: 候选 bound 不同

- **WHEN** `B_a` 与 `B_b` 不相等
- **THEN** paired delta 的安全界使用两者之和，并在报告中记录每侧 bound 和 pair bound

### Requirement: 候选淘汰和置信停止必须受多候选序贯错误率控制

teacher SHALL 在预声明的最大 looks、候选 pair 假设和总 alpha 内分配置信预算；候选相对当前 leader 的 paired delta 只有在其 two-sided interval 的 `high < 0` 时才可淘汰。marginal CI MAY 用于诊断报告，但 MUST NOT 作为 paired racing 的唯一停止依据。

#### Scenario: 明显落后候选

- **WHEN** `UCB(E[R_candidate-R_leader]) < 0`
- **THEN** 记录 pair、look、alpha、样本数、bound 和完整区间后淘汰 candidate

#### Scenario: 区间仍跨零

- **WHEN** 候选 delta 的上置信界不严格小于零
- **THEN** candidate 保持 active，不能因 marginal mean 较低或观测最大值较小而淘汰

#### Scenario: 达到最大样本数仍有竞争者

- **WHEN** 达到预声明 `nmax` 且至少两个 active candidates 的 paired 区间仍无法分离
- **THEN** 输出经验 leader、完整区间和 `ambiguous=true`，不生成无歧义硬标签

### Requirement: 共享样本失败不得伪装成收益

对当前 active group，任一候选 rollout 非法、异常或未到终局 SHALL 使该 shared row 对 active paired statistics 无效，并保留失败分母。失败 MUST NOT 被替换为 legacy action、零分、截断收益或另一侧的成功结果；已淘汰候选不再使后续 active group 失败。

#### Scenario: 一个 active candidate 未终局

- **WHEN** 同一 shared world 中一个 active rollout 触发 `incomplete_rollout`
- **THEN** 当前 active group 的该 row 标记失败，所有受影响 pair 不增加有效 delta 样本，但失败原因仍可审计

### Requirement: Sparse rows 与 resume 必须保持确定性

teacher resume state SHALL 保存 active actions、淘汰记录、pair interval/certificate、bound 配置、look/alpha、稀疏 outcomes 和 stop state。恢复 MUST 不重复已完成 sample id；配置、候选集合、bound 或统计方法指纹不一致时 MUST 拒绝续跑。

#### Scenario: 中断后恢复

- **WHEN** 已保存若干完整 shared rows 后重新加载相同配置
- **THEN** 已完成 rows 不重复计数，继续生成的 sample id、active 状态和最终 stop reason 与一次性执行一致

#### Scenario: 使用旧 dense resume

- **WHEN** resume state 来自没有 racing/bound version 的旧 teacher
- **THEN** 系统拒绝将其作为新配置续跑，并报告 fingerprint/schema 不匹配

### Requirement: Racing 证据必须可复算且不泄露隐藏信息

teacher artifact SHALL 输出每个候选的 EV/marginal CI/样本与失败数、active pair 的 paired delta/CI/有效 sample ids、best/runner-up、淘汰证书、停止原因和配置指纹。输出 MUST 只包含公开上下文标识和世界 fingerprint，不得包含隐藏手牌或真实墙序。

#### Scenario: 淘汰结果复核

- **WHEN** 离线读取某候选的 elimination certificate
- **THEN** 可以用记录的 pair、sample ids、bound、alpha/look 和 delta interval 重算淘汰条件
