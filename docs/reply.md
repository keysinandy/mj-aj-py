我按当前 OpenSpec 的 **Requirement + Scenario + SHALL/MUST** 结构整理，并把它写成一份可直接作为 `spec.md` 使用的 future-state capability spec；OpenSpec 当前规范强调行为契约和可验证场景。([The Docs][1])

# Mahjong Replay Debugger Specification

## Purpose

本能力用于对照以下三类数据源，对单局麻将进行确定性重放与状态诊断：

1. 线上服务端牌局时间线；
2. 本地接收到的 SSE 事件；
3. 本地发起的 `/state` 请求及响应。

系统 SHALL 在任意 `seqNo` 与任意本地处理步骤下，还原：

* 四家牌河；
* 四家吃 / 碰 / 杠副露；
* 我的手牌；
* 在数据允许的情况下，其他玩家真实手牌；
* 本地服务器在该时刻实际已知的信息；
* 当前行动者及上一关键动作；
* 本地请求状态；
* 服务端状态与本地状态的差异。

系统的核心目标 SHALL 是回答：

1. 某个 `/state` 请求是否必要；
2. 如果 `/state` 修复了状态，为什么本地此前发生了状态漂移；
3. 某次吃 / 碰 / 杠是否应该由本地状态机完成，但实际未完成；
4. 服务端真实状态与本地状态第一次发生分歧的位置在哪里。

---

## Definitions

### Server Ground Truth

`Server Ground Truth` 指由线上服务端时间线重建得到的真实牌局状态。

除非输入数据本身缺失或存在冲突，否则 Server Ground Truth SHALL 被视为复盘时的基准状态。

### Local Observed State

`Local Observed State` 指仅依据：

* 本地收到的 SSE；
* 本地实际执行的状态转换；
* `/state` 响应及实际 merge 行为；

重建得到的本地状态。

### Local Expected State

`Local Expected State` 指：

> 根据本地已经收到的信息以及本地状态机的预期行为，本地理论上应该得到的状态。

Local Expected State 用于判断：

* SSE 已经收到但本地 transition 未执行；
* 吃 / 碰 / 杠已经具备执行条件但未更新状态；
* 本地状态机存在漏处理。

### Seq Frame

每一个服务端 `seqNo` SHALL 对应一个 `SeqFrame`。

一个 SeqFrame SHALL 描述：

* 服务端进入该 seq 前的状态；
* 当前服务端事件；
* 服务端执行后的状态；
* 与该 seq 关联的全部本地步骤；
* 本地在每个步骤前后的状态；
* 当前 seq 产生的诊断结果。

### Local Step

一个 `seqNo` 内可能发生零个或多个 Local Step。

典型 Local Step 包括：

* `SSE_RECEIVED`
* `SSE_PARSED`
* `LOCAL_TRANSITION`
* `STATE_REQUEST`
* `STATE_RESPONSE`
* `STATE_MERGE`
* `SSE_DISCONNECT`
* `SSE_RECONNECT`

### Cursor

复盘器 SHALL 使用二维游标：

```text
Cursor = seqNo + localStep
```

其中：

* `seqNo` 表示服务端牌局进度；
* `localStep` 表示本地在该服务端进度内处理到的位置。

---

# Requirements

### Requirement: Preserve Raw Evidence

系统 SHALL 原样保留所有参与复盘的原始数据。

原始数据 SHALL NOT 因标准化、排序、重放或诊断操作而被修改。

原始记录至少 SHALL 包括：

* 原始时间戳；
* 数据来源；
* 原始 seqNo 或 event id；
* 请求 id；
* HTTP 请求信息；
* HTTP 响应信息；
* SSE payload；
* 服务端事件 payload。

#### Scenario: Normalize an SSE event

* **GIVEN** 一条原始 SSE 记录
* **WHEN** 系统将其转换为标准事件
* **THEN** 标准事件 SHALL 保存对原始记录的引用
* **AND** 原始记录 SHALL 保持不变

---

### Requirement: Normalize Mahjong Events

系统 SHALL 将不同数据源转换为统一事件模型。

至少 SHALL 支持：

```text
ROUND_START
DEAL
DRAW
DISCARD

CHI
PON
KAN_OPEN
KAN_CLOSED
KAN_ADDED

READY
RIICHI

WIN
ROUND_END

STATE_REQUEST
STATE_RESPONSE
STATE_MERGE

SSE_CONNECT
SSE_DISCONNECT
SSE_RECONNECT

UNKNOWN
```

不同种类的杠 MUST 被保留为不同事件类型。

#### Scenario: Normalize a pon

* **WHEN** 服务端记录某玩家碰牌
* **THEN** 系统 SHALL 产生 `PON`
* **AND** SHALL 记录执行玩家
* **AND** SHALL 记录被碰的牌
* **AND** SHALL 记录该牌来源玩家
* **AND** SHALL 尽可能记录引发该碰牌的 `DISCARD` 事件

---

### Requirement: Reconstruct Server State

系统 SHALL 根据服务端时间线确定性重建 Server Ground Truth。

任意 seqNo SHALL 可以获得：

```text
serverBefore
serverEvent
serverAfter
```

#### Scenario: Inspect state before a seq

* **GIVEN** 当前选择 `seqNo = 183`
* **WHEN** 用户选择 Before
* **THEN** UI SHALL 显示服务端执行 seq 183 之前的牌局状态

#### Scenario: Inspect state after a seq

* **GIVEN** 当前选择 `seqNo = 183`
* **WHEN** 用户选择 After
* **THEN** UI SHALL 显示服务端执行 seq 183 之后的牌局状态

---

### Requirement: Reconstruct Local State

系统 SHALL 根据本地事件独立重建 Local Observed State。

Local Observed State MUST NOT 直接复制 Server Ground Truth。

#### Scenario: Local transition is missing

* **GIVEN** 服务端已经执行 PON
* **AND** 本地已经收到足够的信息判断该 PON
* **AND** 本地状态中没有该副露
* **WHEN** 重放进行到该位置
* **THEN** Local Observed State SHALL 保持本地实际状态
* **AND** 系统 SHALL NOT 自动利用服务端状态修复 Local Observed State

---

### Requirement: Compute Local Expected State

系统 SHALL 独立维护 Local Expected State。

Local Expected State SHALL 反映本地状态机基于当前已知输入理论上应该产生的状态。

#### Scenario: PON should have been locally applied

* **GIVEN** 本地已获得触发 PON 所需的信息
* **AND** Local Expected State 可以合法执行 PON
* **AND** Local Observed State 未执行该 transition
* **THEN** 系统 SHALL 报告 `MISSING_LOCAL_TRANSITION`
* **AND** expected action SHALL 为 `PON`

---

### Requirement: Maintain Four Player States

系统 SHALL 为四个座位分别维护玩家状态。

每个玩家状态至少 SHALL 包含：

```text
seat
river
melds
hand
handKnowledge
flags
```

牌河中的牌 SHALL 可以表达：

```text
tile
discardSeqNo
called
calledBy
callType
```

#### Scenario: Discard is claimed

* **GIVEN** 玩家 P3 打出 `5m`
* **WHEN** 玩家 P1 对该牌执行 PON
* **THEN** P3 牌河中的 `5m` SHALL 保留
* **AND** SHALL 标记 `called = true`
* **AND** SHALL 标记 `calledBy = P1`
* **AND** SHALL 标记 `callType = PON`

---

### Requirement: Maintain Meld State

系统 SHALL 分别维护 CHI、PON、KAN_OPEN、KAN_CLOSED 和 KAN_ADDED。

每个副露 SHALL 尽可能记录：

```text
type
tiles
ownerSeat
fromSeat
sourceDiscard
createdSeqNo
updatedSeqNo
```

#### Scenario: Added kan

* **GIVEN** 玩家已有一个 PON
* **WHEN** 后续事件将该 PON 升级为加杠
* **THEN** 系统 SHALL 将对应副露表示为 `KAN_ADDED`
* **AND** SHALL 保留其原始 PON 的来源关系

---

### Requirement: Maintain My Hand

系统 SHALL 在数据允许时重建我的手牌。

我的手牌变化 SHALL 可以追溯到：

* 发牌；
* 摸牌；
* 打牌；
* 吃；
* 碰；
* 杠；
* 服务端状态同步。

#### Scenario: Hand changes after discard

* **WHEN** 我的玩家执行 DISCARD
* **THEN** 对应牌 SHALL 从我的手牌移除
* **AND** SHALL 出现在我的牌河中

---

### Requirement: Support Three Hand Visibility Modes

系统 SHALL 提供以下三个观察模式：

```text
PLAYER_VIEW
LOCAL_KNOWLEDGE
OMNISCIENT
```

#### Scenario: Player view

* **WHEN** 当前视图为 `PLAYER_VIEW`
* **THEN** 系统 SHALL 显示我的可见手牌
* **AND** 其他玩家不可见手牌 SHALL 以背面牌或隐藏状态展示

#### Scenario: Local knowledge view

* **WHEN** 当前视图为 `LOCAL_KNOWLEDGE`
* **THEN** 系统 SHALL 只展示截止当前 Local Step 本地服务器实际已经知道的牌信息
* **AND** 服务端独有信息 SHALL NOT 被泄露到该视图

#### Scenario: Omniscient view

* **WHEN** 当前视图为 `OMNISCIENT`
* **AND** 服务端数据包含其他玩家手牌
* **THEN** 系统 SHALL 显示其他玩家真实手牌
* **AND** SHALL 明确标记这些信息来自 Server Ground Truth

---

### Requirement: Preserve Information Provenance

任何可能仅由某个数据源知道的信息 SHOULD 带有来源标记。

至少应能够区分：

```text
SERVER
SSE
STATE_RESPONSE
LOCAL_DERIVED
UNKNOWN
```

#### Scenario: Server-only opponent hand

* **GIVEN** 某玩家手牌只存在于线上服务端时间线
* **WHEN** 用户查看 `LOCAL_KNOWLEDGE`
* **THEN** 该手牌 SHALL NOT 被视为本地已知
* **WHEN** 用户查看 `OMNISCIENT`
* **THEN** 该手牌 MAY 被显示
* **AND** SHALL 标记来源为 `SERVER`

---

### Requirement: Navigate by SeqNo

HTML 复盘器 SHALL 提供 SeqNo 调整器。

用户 SHALL 能够：

* 上一个 seq；
* 下一个 seq；
* 跳转到指定 seq；
* 跳到第一个 seq；
* 跳到最后一个 seq。

#### Scenario: Jump directly to seq

* **WHEN** 用户输入合法 seqNo
* **THEN** 系统 SHALL 将主游标移动到该 seq
* **AND** SHALL 还原该 seq 对应的牌桌状态
* **AND** SHALL 显示关联本地步骤

#### Scenario: Jump to nonexistent seq

* **WHEN** 用户输入不存在的 seqNo
* **THEN** 系统 SHALL NOT 进入无效状态
* **AND** SHALL 向用户说明该 seq 不存在

---

### Requirement: Navigate Local Steps

每一个 SeqFrame SHALL 提供独立的 Local Step 调整器。

例如：

```text
SERVER
→ SSE_RECEIVED
→ SSE_PARSED
→ LOCAL_TRANSITION
→ STATE_REQUEST
→ STATE_RESPONSE
→ STATE_MERGE
```

#### Scenario: Inspect state request

* **WHEN** 用户选择 `STATE_REQUEST`
* **THEN** 系统 SHALL 显示请求发生时的 Local Observed State
* **AND** SHALL 显示请求 payload
* **AND** SHALL 显示关联 seqNo
* **AND** SHALL 显示可确定的请求触发原因

#### Scenario: Inspect state response

* **WHEN** 用户选择 `STATE_RESPONSE`
* **THEN** 系统 SHALL 显示 response payload
* **AND** SHALL 显示 response 相对于请求前本地状态的 diff

---

### Requirement: Maintain Cursor Contract

当前复盘位置 SHALL 表示为：

```json
{
  "seqNo": 183,
  "phase": "AFTER",
  "localStepIndex": 2
}
```

`phase` SHALL 至少支持：

```text
BEFORE
AFTER
```

#### Scenario: Changing seq

* **WHEN** 用户移动到另一个 seqNo
* **THEN** 牌桌、服务端事件、本地事件和诊断面板 SHALL 更新到同一 Cursor

---

### Requirement: Display Server and Local Worlds Together

系统 SHALL 可以同时显示：

```text
Server Ground Truth
Local Observed State
Local Expected State
```

用户 SHALL 能够判断当前差异来自：

* 服务端事件尚未到达本地；
* 本地已经收到但尚未处理；
* 本地 transition 漏执行；
* `/state` 尚未 merge；
* 数据源本身缺失。

#### Scenario: Server ahead of local

* **GIVEN** 服务端已经产生事件
* **AND** 对应 SSE 在当前 Local Step 尚未到达
* **THEN** 系统 SHALL 显示 Server 与 Local 的差异
* **AND** SHALL NOT 将其立即判定为本地处理错误

---

### Requirement: Model Local Requests as First-Class Events

所有 `/state` 请求和响应 SHALL 作为可步进事件存在于时间轴中。

每个 request SHALL 尽可能包含：

```text
requestId
seqNo
localTimestamp
trigger
stateBefore
requestPayload
responsePayload
stateAfterMerge
```

#### Scenario: Multiple state requests in one seq

* **GIVEN** 同一个服务端 seqNo 内产生多个 `/state`
* **THEN** 每一个请求 SHALL 分别出现在 Local Step 时间轴
* **AND** SHALL 保持其实际顺序

---

### Requirement: Calculate State Response Diff

系统 SHALL 对每个 `/state` response 计算有效状态差异。

至少 SHALL 比较：

* 当前行动玩家；
* 四家牌河；
* 四家副露；
* 我的手牌；
* 已知的其他玩家手牌；
* round / hand 标识；
* last action；
* 其他参与业务判断的重要状态。

#### Scenario: State response changes nothing

* **GIVEN** `/state` response 与请求前本地有效状态一致
* **THEN** effective diff SHALL 为空

---

### Requirement: Classify State Requests

每一个 `/state` 请求 SHALL 被归类。

至少 SHALL 支持：

```text
REDUNDANT
VALIDATION_ONLY
RECOVERY_REQUIRED
RECOVERY_CAUSED_BY_MISSED_EVENT
RECOVERY_CAUSED_BY_LOCAL_TRANSITION
RECOVERY_CAUSED_BY_RECONNECT
SUSPICIOUS
UNCLASSIFIED
```

#### Scenario: Redundant state request

* **GIVEN** state response 没有产生有效状态变化
* **AND** 当前没有必要的重连恢复条件
* **THEN** 请求 SHOULD 被标记为 `REDUNDANT`

#### Scenario: State repairs missing pon

* **GIVEN** response 新增一个 PON
* **AND** 本地此前已经具有足够信息执行该 PON
* **AND** Local Expected State 已包含该 PON
* **AND** Local Observed State 不包含
* **THEN** 请求 SHALL 被标记为 `RECOVERY_CAUSED_BY_LOCAL_TRANSITION`

#### Scenario: State request after reconnect

* **GIVEN** SSE 发生断线或丢失区间
* **AND** `/state` 用于恢复未知状态
* **THEN** 请求 SHOULD 被标记为 `RECOVERY_CAUSED_BY_RECONNECT`

---

### Requirement: Detect Missing Local Mahjong Transitions

系统 SHALL 判断以下动作是否应由本地状态机执行但实际未执行：

```text
CHI
PON
KAN_OPEN
KAN_CLOSED
KAN_ADDED
```

系统 MAY 进一步支持其他麻将 transition。

#### Scenario: Missing chi

* **GIVEN** Local Expected State 根据输入合法地产生 CHI
* **AND** Local Observed State 未产生该 CHI
* **THEN** 系统 SHALL 生成 `MISSING_LOCAL_TRANSITION`

#### Scenario: Missing kan

* **GIVEN** 本地已经获得执行杠所需的信息
* **AND** Local Expected State 已完成对应 KAN
* **AND** Local Observed State 未完成
* **THEN** 系统 SHALL 指出具体 KAN 类型
* **AND** SHALL 指出首次发生差异的 Local Step

---

### Requirement: Validate Mahjong Transition Preconditions

状态机在应用 CHI、PON、KAN 等动作前 SHALL 检查其前置条件。

检查 SHOULD 包括：

* 来源弃牌是否存在；
* 来源玩家是否正确；
* tile 是否一致；
* 动作玩家是否正确；
* 当前状态是否已有重复副露；
* 动作是否合法应用到当前状态。

#### Scenario: Pon references nonexistent discard

* **WHEN** PON 找不到可关联的 DISCARD
* **THEN** 系统 SHALL 标记该 transition 为异常
* **AND** SHALL 保留事件用于人工检查

---

### Requirement: Validate Post-Transition Invariants

每次关键 transition 后 SHALL 执行状态一致性检查。

至少 SHOULD 检查：

* 牌河状态；
* 副露状态；
* hand size；
* 当前 turn；
* 来源弃牌 called 状态；
* 重复 meld；
* 不可能的牌数量。

#### Scenario: Pon created but river not marked

* **GIVEN** PON 已经应用
* **AND** 来源弃牌仍处于未调用状态
* **THEN** 系统 SHALL 产生 invariant diagnostic

---

### Requirement: Maintain Causality

系统 SHOULD 建立关键麻将事件之间的因果关系。

例如：

```text
DISCARD
   ↓
PON
   ↓
NEXT DISCARD
```

Normalized Event SHOULD 支持：

```text
eventId
causedBy
relatedEvents
```

#### Scenario: Trace recovered pon

* **GIVEN** `/state` 修复了一个缺失 PON
* **THEN** 系统 SHOULD 能定位触发该 PON 的 DISCARD
* **AND** SHOULD 能定位最早漏掉该 PON 的本地步骤

---

### Requirement: Detect First Divergence

系统 SHALL 自动计算 `First Divergence`。

First Divergence SHALL 表示：

> Server Ground Truth 与 Local Observed / Expected State 第一次出现具有业务意义差异的位置。

First Divergence SHALL 至少记录：

```text
seqNo
localStep
serverEvent
expected
actual
affectedFields
probableCause
recovered
recoveredBy
```

#### Scenario: First missed pon

* **GIVEN** seq 183 之前状态全部一致
* **AND** seq 183 后本地漏掉 PON
* **THEN** First Divergence SHALL 指向 seq 183
* **AND** SHALL 指向导致首次状态差异的具体 Local Step

---

### Requirement: Distinguish Delay from Processing Failure

系统 SHALL 尽可能区分：

```text
NOT_RECEIVED_YET
RECEIVED_NOT_PROCESSED
PROCESSED_INCORRECTLY
RECOVERED_BY_STATE
```

#### Scenario: SSE has not arrived

* **GIVEN** Server Event 已发生
* **AND** 当前 Cursor 下 SSE 尚未收到
* **THEN** 系统 SHALL NOT 将该差异标记为 `MISSING_LOCAL_TRANSITION`

#### Scenario: SSE arrived but transition missing

* **GIVEN** SSE 已经收到
* **AND** SSE 已经被解析
* **AND** 本地具有足够数据完成 transition
* **AND** Local Observed State 仍未更新
* **THEN** 系统 SHOULD 判定为本地处理异常

---

### Requirement: Preserve Logical and Wall-Clock Order

系统 SHALL 同时保存：

* logical order；
* server timestamp；
* local receive timestamp；
* local request timestamp；
* source seq / event id。

系统 MUST NOT 单纯依赖 wall-clock timestamp 作为所有事件的唯一排序依据。

#### Scenario: State response timestamp appears early

* **GIVEN** 不同机器时钟存在偏差
* **WHEN** wall-clock 时间与明确的因果关系冲突
* **THEN** 系统 SHALL 优先保留已知逻辑关系
* **AND** SHALL 显示原始时间供调试

---

### Requirement: Show Timing Information

系统 SHOULD 显示关键网络或处理延迟。

例如：

```text
server event → SSE receive
SSE receive → parse
parse → local transition
state request → state response
divergence → recovery
```

#### Scenario: Delayed SSE

* **WHEN** SSE 比服务端事件晚 487 ms 到达
* **THEN** UI SHOULD 显示 `+487 ms`

---

### Requirement: Support Diagnostic Navigation

复盘器 SHALL 提供以下快捷跳转能力：

```text
Previous Error
Next Error

Previous /state
Next /state

Previous CHI/PON/KAN
Next CHI/PON/KAN

First Divergence
```

#### Scenario: Jump to first divergence

* **WHEN** 用户点击 `First Divergence`
* **THEN** Cursor SHALL 移动到对应 seqNo
* **AND** SHALL 移动到对应 localStep
* **AND** SHALL 显示该异常详情

---

### Requirement: Support Replay Playback

HTML UI SHOULD 提供自动播放功能。

支持的播放单位 SHOULD 为 seqNo 或 Local Step。

播放 SHOULD 支持暂停。

播放速度 MAY 包括：

```text
0.25x
0.5x
1x
2x
4x
```

#### Scenario: Play local events

* **WHEN** 播放模式选择 Local Step
* **THEN** 每次前进 SHALL 移动一个 Local Step
* **AND** 牌桌 SHALL 同步刷新

---

### Requirement: Render Mahjong Table

HTML SHALL 提供牌桌视图。

牌桌 SHALL 同时显示四名玩家。

每名玩家至少显示：

* seat；
* 牌河；
* 吃碰杠；
* 对应模式下允许显示的手牌。

我的玩家 SHALL 清晰区分于其他玩家。

#### Scenario: Inspect table at seq 300

* **WHEN** Cursor 位于 seq 300
* **THEN** 牌桌 SHALL 完整反映当前所选状态来源与 Cursor

---

### Requirement: Render Request Inspector

HTML SHALL 提供本地请求检查区域。

当前 Local Step 为请求相关步骤时，至少 SHOULD 显示：

```text
request id
method
endpoint
timestamp
associated seqNo
trigger
request payload
response payload
response diff
classification
```

#### Scenario: Review redundant request

* **WHEN** 用户选择一个被归类为 REDUNDANT 的 `/state`
* **THEN** UI SHALL 显示 response 未产生有效状态变化的依据

---

### Requirement: Render State Diff

HTML SHALL 提供结构化状态 Diff。

Diff SHOULD 以业务结构而不是整段 JSON 字符串进行展示。

例如：

```text
P1 meld
[] → [PON 5m]

P3 river 5m
called=false → called=true

turn
P3 → P1
```

#### Scenario: Multiple affected fields

* **WHEN** 一次缺失 PON 同时影响 meld、river 和 turn
* **THEN** Diff SHALL 分别显示全部受影响字段

---

### Requirement: Distinguish Unknown from Empty

系统 MUST 区分：

```text
unknown
empty
hidden
not-applicable
```

例如未知手牌 MUST NOT 被解释为 0 张手牌。

#### Scenario: Opponent hand not known locally

* **WHEN** Local Knowledge 无法确定某玩家手牌
* **THEN** hand 状态 SHALL 为 unknown
* **AND** SHALL NOT 使用空数组暗示玩家没有手牌

---

### Requirement: Tolerate Partial Logs

系统 SHALL 在输入不完整时尽可能继续重放。

无法确定的状态 SHALL 被明确标记为 unknown。

#### Scenario: Missing SSE region

* **GIVEN** SSE 日志中缺失一段数据
* **WHEN** 后续 `/state` 恢复完整状态
* **THEN** 系统 SHALL 保留缺失区间标记
* **AND** SHALL 可以从 state response 后继续重放

---

### Requirement: Avoid Fabricated State

系统 MUST NOT 在缺失证据时虚构：

* 对手手牌；
* 未知摸牌；
* 未知副露；
* 未知 seq；
* 未知请求触发原因。

推断值 SHALL 与确定值区分。

#### Scenario: Unknown state request trigger

* **WHEN** 无法从日志确定 `/state` 的真实 trigger
* **THEN** 系统 SHALL 标记 trigger 为 unknown
* **AND** SHALL NOT 将猜测展示为事实

---

### Requirement: Deterministic Replay

相同输入和相同配置 MUST 产生相同：

* normalized timeline；
* SeqFrames；
* Server Ground Truth；
* Local Observed State；
* Local Expected State；
* diagnostics。

#### Scenario: Replay twice

* **WHEN** 用户对同一输入执行两次完整重放
* **THEN** First Divergence SHALL 相同
* **AND** `/state` 分类 SHALL 相同

---

### Requirement: Snapshot Replay State

实现 SHALL 允许使用 Snapshot 优化随机跳转。

Snapshot 优化 MUST NOT 改变最终状态结果。

#### Scenario: Seek using snapshot

* **GIVEN** 系统存在 seq 180 的 snapshot
* **WHEN** 用户跳到 seq 183
* **THEN** 系统 MAY 从 snapshot 180 重放至 183
* **AND** 结果 MUST 与从牌局开始重放一致

---

### Requirement: Report State Request Statistics

系统 SHOULD 汇总 `/state` 使用情况。

至少 SHOULD 提供：

```text
total requests
redundant
validation only
recovery required
recovery caused by missed/local event
recovery caused by reconnect
suspicious
```

#### Scenario: Session summary

* **WHEN** 一局重放完成
* **THEN** 用户 SHOULD 能看到 `/state` 请求分类统计

---

### Requirement: Report Missing Local Actions

系统 SHOULD 汇总本地遗漏的麻将 transition。

至少 SHOULD 按：

```text
CHI
PON
KAN_OPEN
KAN_CLOSED
KAN_ADDED
```

进行统计。

每个异常 SHOULD 可以跳回对应 SeqFrame。

---

# Canonical Data Contract

## ReplaySession

```json
{
  "sessionId": "string",
  "rawEvents": [],
  "normalizedEvents": [],
  "seqFrames": [],
  "snapshots": [],
  "diagnostics": [],
  "cursor": {
    "seqNo": 183,
    "phase": "AFTER",
    "localStepIndex": 2
  },
  "view": {
    "handVisibility": "LOCAL_KNOWLEDGE"
  }
}
```

---

## NormalizedEvent

```json
{
  "eventId": "string",
  "type": "DISCARD",
  "source": "SERVER",
  "seqNo": 183,
  "sourceSeq": "string|null",

  "serverTimestamp": 0,
  "localTimestamp": 0,

  "seat": 3,
  "fromSeat": null,

  "tile": "5m",
  "tiles": [],

  "causedBy": "event-id|null",
  "relatedEvents": [],

  "rawRef": "raw-event-id"
}
```

Unknown fields MUST be represented explicitly as unknown/null according to the host implementation contract and MUST NOT be silently invented.

---

## SeqFrame

```json
{
  "seqNo": 183,

  "serverBefore": {},
  "serverEvent": {},
  "serverAfter": {},

  "localSteps": [],

  "diagnostics": []
}
```

---

## LocalStep

```json
{
  "stepId": "string",
  "index": 2,

  "type": "LOCAL_TRANSITION",

  "timestamp": 0,

  "relatedSeqNo": 183,
  "relatedEventId": "string|null",
  "requestId": "string|null",

  "stateBefore": {},
  "stateAfter": {},

  "payload": {},
  "diagnostics": []
}
```

---

## GameState

```json
{
  "round": {
    "roundId": "string|null",
    "dealerSeat": 0,
    "currentTurn": 2,
    "remainingTiles": null
  },

  "players": [
    {
      "seat": 0,
      "river": [],
      "melds": [],
      "hand": {
        "status": "KNOWN",
        "tiles": [],
        "source": "SERVER"
      },
      "flags": {}
    }
  ],

  "lastAction": {
    "eventId": "string|null",
    "type": "DISCARD|null",
    "seat": 3,
    "tile": "5m"
  }
}
```

---

## RiverTile

```json
{
  "tile": "5m",
  "discardSeqNo": 182,

  "called": true,
  "calledBy": 1,
  "callType": "PON",

  "sourceEventId": "event-id"
}
```

---

## Meld

```json
{
  "meldId": "string",

  "type": "PON",

  "ownerSeat": 1,
  "fromSeat": 3,

  "tiles": ["5m", "5m", "5m"],

  "sourceDiscardEventId": "string|null",

  "createdSeqNo": 183,
  "updatedSeqNo": 183
}
```

---

## StateRequestRecord

```json
{
  "requestId": "string",

  "seqNo": 183,

  "requestedAt": 0,
  "respondedAt": 0,

  "trigger": {
    "type": "AFTER_SSE",
    "eventId": "string|null",
    "confidence": "KNOWN"
  },

  "stateBefore": {},

  "request": {},
  "response": {},

  "effectiveDiff": [],

  "stateAfterMerge": {},

  "classification": "REDUNDANT"
}
```

---

## Diagnostic

```json
{
  "diagnosticId": "string",

  "seqNo": 183,
  "localStepIndex": 2,

  "type": "MISSING_LOCAL_TRANSITION",
  "severity": "ERROR",

  "expected": {},
  "actual": {},

  "affectedFields": [
    "players[1].melds",
    "players[3].river",
    "round.currentTurn"
  ],

  "probableCause": "LOCAL_PON_NOT_APPLIED",

  "causedByEventId": "string|null",

  "recovered": true,
  "recoveredByRequestId": "request-189"
}
```

---

# Diagnostic Types

第一版 SHALL 至少支持以下诊断：

```text
FIRST_DIVERGENCE

MISSING_LOCAL_TRANSITION

SERVER_LOCAL_STATE_MISMATCH

LOCAL_EXPECTED_OBSERVED_MISMATCH

REDUNDANT_STATE_REQUEST

STATE_RECOVERY

SSE_DELAY

SSE_GAP

STATE_INVARIANT_ERROR

UNEXPLAINED_STATE_CHANGE

UNKNOWN_DATA
```

---

# State Request Decision Contract

对每一个 `/state`，系统 SHALL 按以下逻辑分析。

## Rule 1: No Effective Diff

```text
stateBefore ≡ stateResponse
```

且没有 reconnect / unknown-gap 恢复需求：

```text
classification = REDUNDANT
```

---

## Rule 2: Validation Without Repair

若请求只验证数据一致性，但没有修复：

```text
classification = VALIDATION_ONLY
```

是否将其视作业务必要请求 MAY 由后续配置决定。

---

## Rule 3: Recover Missing Local Transition

如果：

```text
Local Expected State != Local Observed State
```

且 state response 将 Local Observed State 修复为 Expected State：

```text
classification =
RECOVERY_CAUSED_BY_LOCAL_TRANSITION
```

系统 SHALL 尽量输出：

```text
missingAction
sourceEvent
firstBrokenStep
recoveryRequest
recoveryDelay
```

---

## Rule 4: Recover Missing Input

如果本地从未收到产生状态变化所需要的 SSE：

```text
classification =
RECOVERY_CAUSED_BY_MISSED_EVENT
```

该情况 MUST NOT 被误标为本地 transition 未执行。

---

## Rule 5: Reconnect Recovery

如果状态同步发生在 SSE reconnect 后，并填补未知时间段：

```text
classification =
RECOVERY_CAUSED_BY_RECONNECT
```

---

# First Divergence Contract

First Divergence SHALL 优先寻找最早的根因，而不是后续连锁错误。

例如：

```text
seq 183
P3 DISCARD 5m
       ↓
local received
       ↓
local applied discard

seq 184
P1 PON 5m
       ↓
local should apply PON
       ↓
local did nothing      ← FIRST DIVERGENCE

seq 185
turn mismatch

seq 186
river mismatch

seq 188
GET /state
       ↓
state repaired
```

系统 SHALL 将：

```text
seq 184 / missing PON
```

作为 First Divergence。

系统 SHOULD NOT 将：

```text
seq 185 / turn mismatch
```

作为首要根因。

---

# HTML Interaction Contract

页面主要结构 SHOULD 为：

```text
┌─────────────────────────────────────────────────────┐
│ Seq Controller                                      │
│ ◀◀  ◀  Seq [183 / 824]  ▶  ▶▶   Play/Pause        │
│                                                     │
│ Local Step                                          │
│ SERVER → SSE → PARSE → APPLY → STATE → MERGE       │
│                                                     │
│ View                                                │
│ Player | Local Knowledge | Omniscient               │
└─────────────────────────────────────────────────────┘

                       Player 2
                 Hand / River / Melds

Player 3                                         Player 1
Hand                                               Hand
River                                             River
Melds                                             Melds

                         Me
                  Hand / River / Melds


Server Event             Local Event / Request

State Diff               Diagnostics
```

---

# UI State Contract

任何一次 Cursor 改变 SHALL 原子性更新：

```text
table
server event inspector
local step inspector
request inspector
state diff
diagnostics
timing
```

不同 panel MUST NOT 显示来自不同 Cursor 的数据。

---

# Out of Scope — Initial Version

第一版 MAY 暂不实现：

* 实时连接生产服务器；
* 自动修改生产状态；
* 从复盘器向生产环境发送 `/state`；
* AI 自动判断麻将策略是否正确；
* 推测未记录的隐藏牌；
* 修改原始服务端 timeline；
* 跨局统计；
* 多用户协同标注。

本系统第一版 SHALL 保持为只读复盘与诊断工具。

---

# Acceptance Criteria

本能力可以被视为第一版完成，当且仅当以下场景全部满足：

1. 可以加载一局 server timeline、SSE 和 `/state` 数据。
2. 可以构造统一的 normalized timeline。
3. 可以使用 seqNo 前后移动。
4. 可以直接跳到任意存在的 seqNo。
5. 可以查看每个 seq 内全部 Local Step。
6. 可以查看 Before / After 状态。
7. 可以还原四家牌河。
8. 可以还原场上的 CHI / PON / KAN。
9. 可以还原我的手牌。
10. 可以在 `PLAYER_VIEW`、`LOCAL_KNOWLEDGE`、`OMNISCIENT` 间切换。
11. 其他玩家手牌在非授权视图下不会泄露。
12. 每个 `/state` 可以查看 request / response / effective diff。
13. `/state` 可以被自动归类。
14. 系统可以检测本地漏执行的 CHI / PON / KAN。
15. 系统可以区分“事件尚未到达”和“已经到达但没有处理”。
16. 系统可以自动定位 First Divergence。
17. 用户可以一键跳到 First Divergence。
18. 用户可以在诊断之间前后跳转。
19. 相同日志重复重放产生完全相同的诊断结果。
20. 任意不确定状态均明确展示为 unknown，而不是由系统猜测。

---

# Core Product Principle

整个复盘器 SHALL 始终维护三个彼此独立的世界：

```text
Server Ground Truth
        │
        │ compare
        ▼
Local Expected State
        │
        │ compare
        ▼
Local Observed State
```

并使用：

```text
seqNo
+
localStep
```

作为统一复盘坐标。

最终任意异常 SHOULD 能被回答为：

```text
发生了什么？
↓
本地当时知道什么？
↓
本地理论上应该做什么？
↓
本地实际上做了什么？
↓
第一次在哪里不同？
↓
后续哪个 /state 修复了它？
↓
这个 /state 原本是否可以避免？
```

以上行为构成本复盘系统的核心契约。

这份更适合直接放到 `openspec/specs/mahjong-replay-debugger/spec.md`，作为**最终行为契约**。如果你下一步要喂给 coding agent 开始实现，我建议再基于它拆出 `design.md` 和 `tasks.md`：前者规定 replay engine / state machine / HTML 模块边界，后者直接拆成可逐项验收的开发任务。OpenSpec 的默认 spec-driven 工作流本身也是按 proposal → specs/design → tasks 组织的。([openspec.dev][2])

[1]: https://thedocs.io/openspec/concepts/spec-format/?utm_source=chatgpt.com "OpenSpec - Core Concepts - Specification Format"
[2]: https://openspec.dev/docs/schemas/spec-driven?utm_source=chatgpt.com "spec-driven | OpenSpec"
