## ADDED Requirements

### Requirement: 校准数据集按源局隔离并预先冻结

校准流程 SHALL 在拟合前冻结 train、validation、final-test 的源局/种子/房间清单、规则配置、座位庄家组合、teacher/continuation 版本、动作 scope、采样计划和过滤规则。同一整局的所有决策、候选、旋转与重采样 MUST 位于同一分组；shape-v1 已见最终种子 MUST NOT 充当新 holdout。

#### Scenario: 同局决策不能跨集合
- **WHEN** 一局包含多个决策点和多个候选 rollout
- **THEN** 它们全部进入同一数据分组，不能一个进入 train、另一个进入 validation

#### Scenario: 发现集规则改变
- **WHEN** 在观察 validation 后修改桶边界、tau、收益界或过滤阈值
- **THEN** 必须重新冻结切分并将该结果视为新的校准版本，不沿用原 final-test 声明

### Requirement: 校准模型预测同一积分单位并保留缺失语义

每个 Fast EV 层 SHALL 在声明的本局积分单位上独立拟合；初始实现 MUST 支持全局受约束线性模型，LUT 仅在独立验证支持时启用，稀疏桶回退全局模型。EV2、结构、牌型潜力、墙长、庄家、财神数、chain、locked、公开风险及缺失状态的系数、归一化、桶边界和约束 MUST 进入 profile fingerprint。未计算字段 MUST 保持 missing，不能填零冒充完整输入。

#### Scenario: EV2 不可用
- **WHEN** 某候选只有 Q0 特征且 EV2 未完成
- **THEN** 使用 Q0 profile 或回退，不把 EV2 作为零值输入交给完整 EV2 模型

#### Scenario: 稀疏墙长桶
- **WHEN** 某 wall/shanten/locked 桶样本不足预声明阈值
- **THEN** 使用全局模型并记录 bucket_fallback，不单独拟合不稳定权重

### Requirement: 消融和发布模型必须独立验证

校准 SHALL 分别报告移除 I、EV2、B/C、财神/向听硬门槛和动作阈值的消融结果，再冻结最终 profile。收益评估 MUST 使用新留出状态、独立随机世界和按源局聚类的成对统计；至少 4096 对局对（8192 次单局运行）均衡覆盖 16 个 seat/dealer 组合，并报告得分、自摸率、倍率、流局和置信区间。只要平均得分差 95% CI 下界不大于零，shape-v2 MUST 保持 opt-in/legacy 默认。

#### Scenario: 留出收益区间跨零
- **WHEN** shape-v2 相对冻结对手的得分差 95% 区间包含零
- **THEN** 发布闸门失败，保留 legacy 默认并记录收益证据不足

#### Scenario: 胜率提升但积分下降
- **WHEN** 自摸率上升而按规则结算的平均积分差不满足下界
- **THEN** 不得以胡率替代积分收益通过验收

### Requirement: 性能与线上发布必须按实际调用链验收

性能验收 SHALL 使用同机同内核、交错三次 200 局、包含上下文构造和解释序列化；相对冻结 shape-v1 的 elapsed/games 中位数增加 MUST ≤15%，单局和十场并发完整决策 p95 MUST 满足弃牌 ≤20ms、反应 ≤10ms，并报告 p50/p99/max、层级与回退率。每个发布 scope/profile 还 MUST 通过至少三个新房、每房十场的逐窗检查；新增评价造成窗口损失、非法动作、旧动作重发或身份/提交阶段混淆时不得发布。

#### Scenario: 高回退率掩盖性能
- **WHEN** v2 通过大量回退冻结策略满足耗时，但完整层级覆盖率不足
- **THEN** 报告回退率并判定性能/发布证据不完整，不以吞吐单项通过

#### Scenario: 线上窗口证据不完整
- **WHEN** 房间存在身份未知、HTTP 延迟或服务端代打
- **THEN** 将 transport、window、game 和 evaluator 耗时分开归因，不把缺失证据写成策略收益通过

### Requirement: Teacher 标签和 BC 数据保持来源隔离

P7 数据 SHALL 显式记录观测模式 `oracle=False`、teacher/profile/scope、规则、采样、置信度、标签来源和版本。teacher EV/soft target MUST 使用独立字段，不能覆盖实际终局 `score`；歧义或 unsupported 样本按预声明方案排除或使用合法集 soft target。线上成功提交过滤保持现状，离线重标动作 MUST 标记为 counterfactual。

#### Scenario: Oracle 平面被误启用
- **WHEN** 生成 v2 teacher/BC 样本时 `extract(..., oracle=True)`
- **THEN** 数据生成失败或明确拒绝，不能把隐藏信息写入推理输入

#### Scenario: 歧义 teacher 样本
- **WHEN** best 与 runner-up 在 Nmax 后仍跨零
- **THEN** 不生成无歧义硬动作标签，保留 ambiguous 和原始区间
