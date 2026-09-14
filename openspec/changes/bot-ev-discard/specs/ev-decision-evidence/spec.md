## ADDED Requirements

### Requirement: 在线决策记录实际评价层级和输入指纹

每次实际 v2 决策 SHALL 记录 evaluator/version、scope、profile fingerprint、rule/kernel 版本、输入 hash、实际层级、预算/节点/内核调用、耗时、回退或委托原因、所选动作和候选总数。候选解释 SHALL 保留合法性、shanten/U1/p1/I/EV2/结构/风险、加权贡献、上界证书和缺失状态；未计算字段不得写成零。

#### Scenario: EV2 中断回到 Q0
- **WHEN** Q0 完整而 EV2 因预算只完成部分候选
- **THEN** 记录实际使用 Q0、EV2 incomplete 原因和候选层级，不标成完整 v2 EV

### Requirement: 解释输出不能改变策略选择

精简与完整解释 MUST 复用同一搜索结果，不得因序列化、截断或开启解释再次搜索。实时日志 SHALL 保存选中项、legacy 最优项和至多另外三项，并记录总候选数；离线报告可保存全部候选。开关解释 MUST NOT 改变动作、预算、层级、缓存语义或请求数量。

#### Scenario: 解释开关对拍
- **WHEN** 相同输入固定节点预算下分别关闭、精简和完整解释
- **THEN** 动作、层级、候选数和共有候选特征完全一致

### Requirement: 离线 regret 必须标记为反事实并正确关联

离线补算 SHALL 以 `counterfactual_evaluation` 标识，按 gid/round/seq/decision_id/input_hash/scope 关联原日志；actual action、提交成功/拒绝/未知、server auto discard、strategy PASS 和窗口身份质量 MUST 分开保存。缺少实际决策、teacher 覆盖或有效 EV 时 regret MUST 为 null，不得补造线上 decision/action。

#### Scenario: 无决策的服务端代打
- **WHEN** 原日志只有 timeout discard/server auto discard，没有本家策略 decision
- **THEN** 只记录服务端代打与反事实结果，不声称本家选择过该牌

#### Scenario: 相同牌值多次出现
- **WHEN** 同房不同窗口都出现同一牌值
- **THEN** 通过决策/窗口身份和输入 hash 关联，不仅按牌值或 seq 拼接

### Requirement: 证据边界与隐藏信息安全保持不变

解释、teacher 输出和复盘接口 MUST NOT 写入令牌、URL、请求 body、对手暗牌或真实墙序；公开信息缺失、identity_unknown、协议不可观测和 transport 错误 MUST 保留原始状态并分层报告。评价字段不得改变窗口授权、StateDemand/Throttle、动作重试或平台 API。

#### Scenario: identity_unknown 的反事实 PONG
- **WHEN** 窗口身份未知且离线模型选择 PONG
- **THEN** 原窗口仍标确认未通过/未知，PONG 只标 counterfactual，不升级授权

#### Scenario: 解释失败
- **WHEN** 解释序列化或离线补算失败
- **THEN** 保留已完成动作/传输证据，评价字段标缺失，不重试动作或改变线上状态
