# bot-decision-explanations Specification

## Purpose
TBD - created by archiving change bot-shape-aware-evaluation. Update Purpose after archive.
## Requirements
### Requirement: 决策携带可识别的评价版本与实际层级

BOT 的运行元信息 SHALL 记录 evaluator、配置指纹、内核和规则开关；每次实际策略决策 SHALL 附带实际评价层级、预算、回退原因及所选动作。兼容的 `decision.evaluation` 字段 MUST 不影响原 decision id 与 action 关联。旧日志无评价信息时 MUST 显示 `legacy_unrecorded`，不得推算其权重。

#### Scenario: shape-v1 在运行中回退
- **WHEN** 运行配置为 shape-v1 而本次因预算回退 legacy
- **THEN** 日志同时记录请求的 evaluator 与实际 legacy 层级及原因，不将本次标成完整前瞻

#### Scenario: 读取旧日志
- **WHEN** decision 记录没有 evaluation 对象
- **THEN** logview 和回放仍可处理，评价元信息显示缺失而不补造

### Requirement: 候选解释保留原始值与加权贡献

完整解释 SHALL 包括候选合法性/淘汰原因、向听、U1/p1、I、H2、结构与对子用途、所用层级下的 Q、加权贡献、最佳后续弃牌、模型假设及最终选择理由。未计算字段 MUST 以缺失与状态表示，不能伪装为零分。仅当某字段在该层级有定义时才参与排序。

#### Scenario: 直接进张相同但结构取舍不同
- **WHEN** 同向听候选的 U1 相同而 shape-v1 选择与 legacy 不同
- **THEN** 解释显示造成差异的已完成分量及最佳分解/改良路径

#### Scenario: 对子保护未胜出
- **WHEN** BOT 拆开一个天然对子
- **THEN** 解释可以复核剩余雀头来源、该对子公开碰牌潜力及胜出候选的其他收益，不仅输出“对子权重较低”

### Requirement: 解释采样不改变选择或重复搜索

实时解释 SHALL 复用本次已计算结果，只保存选中项、legacy 最优项及至多另外三项，并记录总候选数；离线完整输出可保留全部候选。开启/关闭或截断解释 MUST NOT 改变动作、候选搜索、计算层级或预算。解释构造开销 MUST 进入性能验收。

#### Scenario: 精简与完整解释一致
- **WHEN** 相同状态和固定节点预算分别输出精简解释与全候选解释
- **THEN** 选择相同，共有候选的分量一致，不为输出而第二次搜索

### Requirement: 反事实重算不补造线上决策

系统 SHALL 按原日志关联策略决策、动作提交和窗口/服务端终态；MUST 区分 strategy PASS、确认未通过、未提交和 server auto discard。没有实际策略调用的窗口 MUST NOT 生成补造的线上 decision。离线重算 MUST 标为 `counterfactual_evaluation`，关联原事件并声明其不代表原时点实际执行。

#### Scenario: 8 筒窗口身份未知
- **WHEN** 回放案例 C 具有 `identity_unknown` 且没有该窗 decision/action，离线评价选择 PONG
- **THEN** 原因保留为确认未通过，PONG 只显示为反事实策略结果

#### Scenario: 4 筒超时代打
- **WHEN** 回放案例 D 包含弃 4p 与本家 `timeout kind=discard`，无配对决策
- **THEN** 记录为服务端代打；离线选择 4w 不得被说成原 BOT 曾选择或提交 4w

### Requirement: 日志关联保持本家视角和并发隔离

解释 SHALL 通过 gid 与 decision id 关联 action，反应侧沿用已有 WindowAttemptKey/身份质量信息。MUST NOT 用弱身份升级授权，不得记录令牌、对手暗手或真实墙序，也不得跨游戏混用解释。

#### Scenario: 相同牌值在不同窗口出现
- **WHEN** 同房相同牌值在不同回合被打出
- **THEN** 不仅按牌值关联解释和动作，仍使用各自决策及已有窗口身份；缺失强身份保持缺失

