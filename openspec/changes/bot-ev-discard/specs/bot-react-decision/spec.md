## MODIFIED Requirements

### Requirement: 吃/碰按"副露 + 最佳弃牌后站立牌面"完整评价

启发式 BOT 对 CHOW_LOW/MID/HIGH、PONG 的反应评价 SHALL 根据 evaluator 版本选择旧 `(shanten, ukeire)` 或同单位积分 EV。legacy/shape-v1 保留既有副露后枚举立即弃牌、最低向听/财神保护和 KONG_OPEN 冻结语义；all-root v2 在已支持且完整的 scope 中才可比较积分 EV。所有候选仍须来自当前 react mode 合法集，吃碰后的 locked、吃额度、七对资格和 visible 快照必须正确更新。

#### Scenario: legacy 吃碰回归
- **WHEN** evaluator 为 legacy 或 shape-v1 且某吃碰达到既有向听/进张门槛
- **THEN** 按既有动作顺序选择，KONG_OPEN 同窗不进入新 EV

#### Scenario: all-root 共享积分评价
- **WHEN** all-root scope 已完成且多个合法吃碰候选通过统一层级
- **THEN** 按同一积分单位和稳定 tie-break 选择，并记录立即弃牌与转移状态

#### Scenario: 预测弃牌与真实弃牌一致
- **WHEN** 吃碰成功后的手牌、visible、规则、profile 和层级一致
- **THEN** 预选立即弃牌与实际决策一致

### Requirement: PASS 基准为反应时点站立暗牌原样评价

反应侧 SHALL 保留 PASS 基准：legacy/shape-v1 使用原 `(shanten, ukeire)`；支持的 all-root v2 使用与动作相同层级和积分单位。pending 他家弃牌不得加入 PASS 手牌；缺少反应顺序或完整上下文时 v2 回退并记录，不凭空补 PASS。

#### Scenario: PASS 不偷用 pending 牌
- **WHEN** pending 牌不在本家手牌
- **THEN** PASS 仍以原站立暗牌和 visible 评价

#### Scenario: 缺少反应上下文
- **WHEN** 不能确定更高优先级响应是否结束
- **THEN** teacher/v2 不产生已验证动作价值，legacy 行为按原规则继续

### Requirement: 等向听时按版本化评价收益门槛执行

legacy MUST 保持 PONG 增量 ≥2、CHOW 增量 ≥4；shape-v1 保持现有版本化收益门槛。all-root v2 SHALL 使用 profile 中同尺度的 `Q_claim-Q_pass >= tau[action]`，向听更高时拒绝，未知池为零时不以零阈值接受。阈值、单位和完整层级 MUST 进入指纹。

#### Scenario: legacy 边界保持
- **WHEN** legacy 碰增量恰为 2 或吃增量为 3
- **THEN** 前者接受、后者 PASS

#### Scenario: v2 计分边界
- **WHEN** all-root 的完整积分差恰好达到 profile tau
- **THEN** 可以接受并记录可复算比较式

### Requirement: 可见牌快照在反应评价内复用

所有 evaluator SHALL 使用反应时点单份 visible 快照贯穿 PASS、动作候选和立即弃牌；claim 前后逐牌物料必须相等，pending 不重复计数。无法满足快照不变量时 v2/teacher MUST 拒绝或回退，不修改平台请求。

#### Scenario: 吃碰前后 visible 不变
- **WHEN** 模拟 pending 转副露且移除本家消耗牌
- **THEN** visible 逐牌相等

### Requirement: 七对分支获得自然偏向保护

legacy/shape-v1 SHALL 继续依赖 shanten 的标准形/七对双分支，副露后退出七对。all-root v2 可以把七对和对子用途作为价值特征，但 MUST NOT 同时累加互斥分支或新增无条件对子特判；标准形与七对领先关系仍先由规则语义验证。

#### Scenario: 七对领先时 PASS
- **WHEN** 七对向听严格优于副露后的标准形候选
- **THEN** 选择 PASS 或冻结策略结果，不以结构奖励覆盖最低分支

### Requirement: 弃牌侧与胡/飘路径行为保持不变

legacy/shape-v1 的 `choose_action()` HU 优先、`_should_piao` 和 `choose_discard()` 语义 MUST 保持。discard scope v2 仅在明确覆盖的普通合法舍牌分支接管；hu-piao/all-root 只有独立 scope 完整、计分和发布闸门通过后才可改变 HU/财飘选择。

#### Scenario: 范围外 HU 委托
- **WHEN** v2 为 discard scope 且 HU 合法
- **THEN** 委托冻结 HU/财飘分支并记录原因

#### Scenario: legacy 不漂移
- **WHEN** 显式 evaluator 为 legacy
- **THEN** 既有弃牌/HU/财飘测试和行为保持

### Requirement: 反应侧决策性能受闸门约束

legacy/shape-v1 SHALL 保持既有 ukeire 调用与动作性能基线。all-root v2 的上下文、frontier、积分、解释和回退开销 MUST 纳入完整决策预算，并按 scope 单独验证反应 p95 ≤10ms、全调用链 elapsed 增长 ≤15% 和十场并发回退率；不能以 legacy KONG 冻结或高回退率掩盖 v2 未完成。

#### Scenario: scope 性能报告
- **WHEN** 运行 all-root 反应压测
- **THEN** 报告实际层级、节点/内核、回退和 p50/p95/p99/max，并与冻结 shape-v1 对比
