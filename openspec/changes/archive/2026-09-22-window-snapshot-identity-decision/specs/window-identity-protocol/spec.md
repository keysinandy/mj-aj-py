## MODIFIED Requirements

### Requirement: 每个权威反应窗口具有稳定身份

协议 MUST 为每个合规反应窗口暴露稳定的 `source_discard_seq` 或不透明的
`response_window_id`。该身份 MUST 在 `response_peng`、`response_chi` 与
`seq=0` 快照之间保持稳定，且 MUST 独立于状态 watermark。

**决策/证据拆分**：首次出现于新鲜权威快照、没有协议身份的合规反应窗口，
MAY 在弱 epoch 键 `(round_id, discard_owner, tile, 弃牌家牌河尾位置)` 下
决策。弱键决策仅在既有提交守卫仍允许该 POST 时获得授权。弱键窗口 MUST
NOT 被提升为 `authoritative`，MUST NOT 在重锚间去重（除非 round/owner/
tile 匹配且弃牌家牌河长度一致），且 MUST 保持排除在强完备之外。

#### Scenario: 快照首见窗口在弱键下决策

- **WHEN** 新鲜权威快照暴露一个合规反应相位，带合法选项、无协议身份，
  且剩余截止仍够 decide + 提交余量
- **THEN** 客户端在 epoch 内弱键下决策并提交
- **AND** 决策记录带 `identity_status=legacy_unresolved` 与弱键，409 通过
  既有同环路径恢复且不重发

#### Scenario: 碰与吃共享同一弃牌身份

- **WHEN** 一次弃牌先开出碰相位、随后开出吃相位
- **THEN** 两个相位暴露相同的权威窗口身份，而客户端保持各自独立的相位
  尝试键

#### Scenario: 全量快照保留开窗身份

- **WHEN** 客户端在反应窗口仍开启时请求 `/state seq=0`
- **THEN** 快照携带与来源弃牌相同的窗口身份，不替换为快照 watermark

### Requirement: 新窗口与新轮次获得不同身份

服务端 MUST 为新弃牌或新轮次签发不同的身份。窗口身份 MUST NOT 依赖弃牌
列表长度、副露计数、单独的 owner/tile，或弃牌被认领后是否仍然可见。

**弱键 epoch 规则**：同尾弱键仅在一个快照 epoch 内有效；增长弃牌河的新
弃牌改变尾位置，因此也改变键。

#### Scenario: 同座同牌重复弃牌在弱键下不同

- **WHEN** 同一座在同一快照 epoch 内再次弃出同一张牌，且前一个窗口已不
  再 pending
- **THEN** 两个窗口具有不同的弱键，且前一个决策不压制后一个

#### Scenario: 弱键不在不一致的重锚间携带

- **WHEN** 发生重锚且弃牌家牌河长度与被携带窗口不一致
- **THEN** 弱键不被携带，该窗口按未见过处理

### Requirement: 身份来源与不一致可观测

协议与客户端记录 MUST 保留身份值、字段来源、首见路径，以及事件与快照
身份之间的任何不一致。缺失、变化或畸形的身份 MUST 报告为弱/无效证据，
且 MUST NOT 被客户端启发式提升。

弱键决策 MUST 随 `identity_status=legacy_unresolved` 与弱键一起记录，
使验收报告在不改变分母的前提下区分弱键提交、弱键 409 结果与
authoritative miss。

#### Scenario: 事件与快照身份不一致

- **WHEN** 一个来源事件与一个权威快照为同一 pending 反应暴露不同的窗口
  身份
- **THEN** 客户端记录两个值、拒绝该身份作为 authoritative，并阻止强
  跨重锚归因

#### Scenario: 快照首见身份保持 legacy

- **WHEN** 快照包含合规反应相位但没有协议窗口身份，也不存在可携带的
  authoritative 事件身份
- **THEN** 客户端记录 `legacy_unresolved`，在提交守卫允许时于弱键下
  决策，验收将该窗口排除在强身份覆盖之外

### Requirement: 协议覆盖是独立的验收维度

验收 MUST 分开报告合规、authoritative、legacy 与 protocol-skipped 的身份
计数。缺失协议身份 MUST NOT 降低健康的传输或 StateDemand 证据，但在没有
显式 protocol-skipped 规则适用时 MUST 阻止 `strong_window_complete`。

#### Scenario: Legacy 身份阻断强窗口完备

- **WHEN** 至少一个合规窗口仍为 `legacy_unresolved`
- **THEN** 窗口证据为 `partial_identity`，不能宣布强完备

#### Scenario: 协议版本显式跳过身份

- **WHEN** 服务端声明其协议版本无法提供窗口身份
- **THEN** 验收单独报告 `protocol_skipped_identity`，不把受影响窗口伪装
  成 authoritative
