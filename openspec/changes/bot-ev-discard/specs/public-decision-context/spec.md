## ADDED Requirements

### Requirement: 决策上下文只包含本家可见信息

系统 SHALL 使用不可变、版本化的 PublicDecisionContext 表达本家手牌、合法动作和当时可见的公共状态。上下文 MUST NOT 包含对手暗牌、真实墙内容、源 Game RNG 或可访问这些内容的对象引用；输入投影和特征生成 MUST NOT 使用未来事件。相同可见输入、规则、profile、固定节点预算的决策 MUST 相同。

#### Scenario: 隐藏世界改变但可见状态不变
- **WHEN** 替换真实墙序和对手暗牌而保持本家可见字段不变
- **THEN** context 指纹、合法候选、固定节点评价和同 seed teacher 采样结果不变

#### Scenario: 回放包含未来公开事件
- **WHEN** 从完整回放为某个 seq 的决策构造上下文
- **THEN** 只读取该决策已知的事件；后续打牌、胡牌和终局信息不进入输入

### Requirement: 上下文保存计分与进程所需状态及来源

上下文 SHALL 包括 hero/dealer/base、规则、drawn/kong_draw、四家公开 melds/discards/chows、可知 chain/chain_piao、turn/phase/pending、freeze/freezer、墙长和反应进度。用于采样的暗牌数量 MUST 来自公开计数或公开阶段推导。每个非直接观测字段 MUST 标记推导来源；未知值不得伪造为零。在线合法性投影 MUST 与离线完整模拟状态分开。

#### Scenario: Mirror 的占位状态不能用作采样依据
- **WHEN** 输入来自 `Mirror.build_game()`，他家暗牌为零且墙为占位牌
- **THEN** 只使用已确认的本家/公共字段；不将零暗牌数、占位墙内容或单座位反应队列作为完整模拟事实

#### Scenario: 单独记录缺少的对手动作链
- **WHEN** 本家计分字段完整但四家公共链状态无法恢复
- **THEN** 上下文可以为 Fast EV 有效，对 rollout 标记 missing_fields 和 unsupported_context

### Requirement: 物料计数必须守恒且不重复计算已认领牌

系统 SHALL 将 visible 定义为本家手牌、当前未被认领牌河及公开副露物料之和，杠计四张；pending 在牌河中只计一次。历史弃牌记录 MUST 与物料牌河区分。完整世界 MUST 满足每种牌 `visible + 他家暗牌 + 当前完整墙 = 4`，完整墙包括死墙；live wall MUST NOT 被当成 unseen 数量。

#### Scenario: 碰后公开物料不变
- **WHEN** pending 从他家牌河转入本家碰牌副露，本家两张手牌也移入该副露
- **THEN** visible 逐牌不变且不重复计算 pending；历史弃牌记录仍可用于解释

#### Scenario: 非法计数不能静默修复
- **WHEN** visible 超过四张、暗牌数与阶段不符或 unseen 槽位无法守恒
- **THEN** 拒绝该 EV/teacher 输入并给出原因，不裁剪计数、不补牌、不取真实隐藏牌填空

### Requirement: 缺失公共信息应按使用目的降级

系统 SHALL 独立判断 fast_valid 与 rollout_valid。本家计分/合法动作所需字段缺失时 MUST 回退冻结策略并记录原因；仅完整模拟所需字段缺失时 MUST 拒绝 teacher 样本。反应进度、不可见暗杠材料或历史硬约束无法表达时 MUST 标记 unsupported。字段补齐 MUST 复用已有事件/快照，不新增策略驱动的平台请求或修改窗口授权。

#### Scenario: 反应顺序不可确定
- **WHEN** 快照仅给 responding_seats，不能确定更高优先级响应是否已结束
- **THEN** teacher 不默认其他玩家已 PASS，不产生已验证反应根 EV 标签

#### Scenario: 本家链状态缺失
- **WHEN** 普通舍牌/财飘价值需要 chain_piao 而数据来源不能确认该值
- **THEN** 本次回退并记录 context_incomplete，不能把缺失链按零积分计算
