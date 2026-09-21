# Spec Delta

## MODIFIED Requirements

### Requirement: 决策携带可识别的评价版本与实际层级

BOT 的运行元信息 SHALL 记录 evaluator、配置指纹、内核和规则开关；每次实际策略决策 SHALL 附带实际评价层级、预算、回退原因及所选动作。实际内核不是原生 weighted 内核时（缺少 `mj_kernels` 或 `MJ_KERNELS=python`），运行侧 MUST 显式报告降级：启动诊断与每条决策记录都要给出实际内核与降级原因，前端与回放据此可提示，不得只在个别字段里可查。前沿护栏的准入、截断与短路状态 MUST 一并记录。兼容的 `decision.evaluation` 字段 MUST 不影响原 decision id 与 action 关联。旧日志无评价信息时 MUST 显示 `legacy_unrecorded`，不得推算其权重。

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
