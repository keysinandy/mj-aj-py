# Spec Delta

## Purpose

扩展统一回放查看器，使一个线上房间中的多场牌局具有明确场次边界，并提供摸牌特殊展示、胡牌玩家标记、我方动作筛选和筛选内导航，同时保持既有 `ReplaySession / ReplayStep / seqNo / Mirror` 重放一致性。

## MODIFIED Requirements

### Requirement: 步进与进度条

回放SHALL支持逐步前进与逐步后退，以及进度条拖动定位到任意步；每一步SHALL展示该时刻的手牌、四家牌河、四家副露、剩余墙数与轮次。对于包含多个场次的线上房间，普通逐步导航、首尾跳转与自动播放SHALL受当前场次边界约束，MUST NOT在用户未切换场次时自动跨入下一场。

#### Scenario: 当前场前进一步
- **WHEN** 用户在当前场非末尾点击“下一步”
- **THEN** 牌面状态推进一个动作/事件，时间线高亮对应条目，且仍属于当前场

#### Scenario: 当前场到达末尾
- **WHEN** 用户在当前场最后一步继续播放或点击“下一步”
- **THEN** 回放停在当前场末尾，不自动进入下一场

#### Scenario: 切换下一场
- **WHEN** 用户点击“下一场”
- **THEN** active round 切换到下一场，游标定位到该场首个可回放步骤并停止自动播放

#### Scenario: seqNo 精确定位跨场
- **WHEN** 用户显式跳转到一个属于其他场次的 seqNo
- **THEN** 查看器切换到包含该 seqNo 的场次并定位该步骤，不改写原始 seqNo

### Requirement: 时间线内容

时间线SHALL呈现记录中的结构化条目：事件、决策、动作提交、诊断和缺口标记。对于多场房间，默认时间线SHALL只展示当前 active round 的条目；条目即使被场次过滤或 actor 过滤，其 replay index MUST仍指向完整 `ReplaySession.steps` 中的原始全局 index。

#### Scenario: 多场房间不混线
- **WHEN** 一个房间包含 10 个连续场次且用户选择第 4 场
- **THEN** 时间线只显示第 4 场步骤，不包含第 3 场或第 5 场事件

#### Scenario: 过滤后点击条目
- **WHEN** 用户启用我方动作筛选并点击过滤结果中的某条记录
- **THEN** 查看器按该条目的原始全局 replay index 定位，牌面状态与未过滤时定位到同一步完全一致

### Requirement: 统一步骤模型

本地批次与线上记录SHALL继续转换为同一 `ReplaySession / ReplayStep` 模型。一个包含多个场次的线上房间仍SHALL是一个 `ReplaySession`，场次信息 SHALL作为该 session 的索引元数据存在，MUST NOT复制为互相独立且重复解释事件的多套 session。

`ReplaySession` SHALL暴露当前 session 的 `ReplayRound[]` 索引。每个 `ReplayRound` 至少包含稳定 `roundId`、出现顺序 `ordinal`、平台 `roundNo`、起止 step index 与起止 seqNo。场次边界SHALL依据记录中连续 `round_no` 段确定，不得按时间间隔推断，也不得把非连续出现的相同 `round_no` 全局合并。

#### Scenario: 同一房间十场
- **WHEN** 一个线上 gid 的 steps 依次出现 10 个 round_no 连续段
- **THEN** session 暴露 10 个 ReplayRound，每个 round 的 step 范围互不重叠并共同覆盖全部对应步骤

#### Scenario: 非连续 round_no 重复
- **WHEN** round_no=3 的一段之后出现 round_no=4，后续异常记录又出现新的 round_no=3 段
- **THEN** 两个 round_no=3 段拥有不同 roundId，不得合并为同一场

#### Scenario: 缺少 round metadata 的旧记录
- **WHEN** 新客户端打开没有 `metadata.rounds` 的旧 replay session
- **THEN** 客户端使用连续 `frame.round_no` 派生只读场次索引并保持可回放

## ADDED Requirements

### Requirement: 场次选择与房间内导航

对于包含多个场次的 ReplaySession，查看器SHALL显示当前为“第 X / N 场”，并提供直接选择、上一场和下一场。切换场次后，牌桌、时间线、首尾跳转、自动播放和筛选内导航 SHALL统一使用所选场次边界。

#### Scenario: 场次选择器显示总数
- **WHEN** 房间 session 包含 10 个 ReplayRound
- **THEN** 查看器显示当前场次序号与总数，例如“第 3 / 10 场”

#### Scenario: 切场后所有区域一致
- **WHEN** 用户从第 2 场切换到第 7 场
- **THEN** 牌桌定位到第 7 场首步，时间线只显示第 7 场，首尾和自动播放边界也变为第 7 场

#### Scenario: 最后一场没有下一场
- **WHEN** 当前为最后一个 ReplayRound
- **THEN** “下一场”不可继续前进，不创建虚假场次

### Requirement: 摸进来的牌特殊展示

当当前可见手牌存在可靠的刚摸牌身份时，ReplayFrame SHALL暴露 `drawn_tile` 和 `drawn_seat`，查看器 SHALL将该牌与原站立牌视觉分离并明确标记为“摸”。若牌的身份不可知，MUST NOT猜测或根据后续弃牌反推。

手牌基础 count 若已包含刚摸牌，渲染时MUST从站立牌副本中扣除一张后再单独展示 drawn tile，保证总牌数不因 UI 特殊展示增加。

#### Scenario: 自家正常摸牌
- **WHEN** 线上回放到自家 `tile_drawn` 后且 Mirror 已知具体 drawn tile
- **THEN** 该牌在手牌末端以独立间距和“摸”标记展示，其他手牌保持原排序

#### Scenario: 重复牌值摸入
- **WHEN** 原手牌已有两张 5 万且本次又摸入一张 5 万
- **THEN** UI 总共仍只显示三张 5 万，其中恰好一张作为独立 drawn tile 展示

#### Scenario: 弃牌后取消摸牌标记
- **WHEN** 从摸后状态推进到已完成弃牌的步骤
- **THEN** drawn tile 特殊展示消失，手牌按普通站立状态渲染

#### Scenario: 对手摸牌身份未知
- **WHEN** 线上事件仅表明其他座位摸了一张牌但没有牌身份
- **THEN** 查看器不显示具体 drawn tile，也不使用其后弃出的牌倒推刚摸牌

#### Scenario: 快照恢复摸牌态
- **WHEN** 权威 snapshot 明确给出自家 `drawn_tile`
- **THEN** 回放帧恢复该 drawn tile 并按同一特殊样式展示

### Requirement: 胡牌玩家标记

ReplayFrame SHALL保存“截至当前步骤已经发生的胡牌玩家集合”。查看器 SHALL在玩家区域对这些座位显示明显的“胡”标记。赢家状态只能来自当前步骤及之前已经发生的 `hu`/本地终局事实，MUST NOT使用场次最终摘要在更早步骤提前显示。

#### Scenario: 胡牌前不剧透
- **WHEN** 用户定位在某场 HU 事件之前的步骤
- **THEN** 所有玩家区域均不显示该场未来赢家标记，即使 ReplayRound summary 已知最终赢家

#### Scenario: HU 事件后显示赢家
- **WHEN** 当前步骤已经应用 seat=2 的 HU 事件
- **THEN** P2 玩家区域显示“胡”标记，之后同场步骤继续保持该标记

#### Scenario: 回退到 HU 前
- **WHEN** 用户从 HU 后步骤回退到 HU 前步骤
- **THEN** 赢家标记立即消失

#### Scenario: 多赢家
- **WHEN** 同一场存在多个合法 HU 事件
- **THEN** 每个已经发生 HU 的座位均显示赢家标记

#### Scenario: 新场清空赢家
- **WHEN** 用户切换到下一场且新场尚未发生 HU
- **THEN** 上一场的 winner 状态不得残留到新场

### Requirement: 我方动作筛选

回放查看器SHALL提供 `全部 / 我方动作` 时间线筛选。我方动作 MUST通过结构化事件 actor 与 `my_seat` 判定，并且该条目必须属于真实麻将 gameplay action；MUST NOT通过中文 label 字符串或旁路请求所属 seq 猜测。

至少以下类型在 actor 为我方时可属于我方 gameplay action：摸、打、吃、碰、杠、胡、过；系统 snapshot、state request、decision attachment、diagnostic、gap marker 不属于我方 gameplay action。

#### Scenario: 只看我方动作
- **WHEN** 用户启用“我方动作”筛选
- **THEN** 当前场时间线只显示 actor 为 `my_seat` 的真实麻将动作

#### Scenario: 系统记录不混入
- **WHEN** 某我方 seq 同时挂有 `/state` 请求、decision 和 diagnostic
- **THEN** 这些旁路记录仍可在 StepInspector 查看，但不会各自作为“我方动作”筛选条目出现

#### Scenario: 关闭筛选
- **WHEN** 用户从“我方动作”切回“全部”
- **THEN** 当前场完整时间线恢复，ReplaySession.steps 和当前牌面状态未被修改

### Requirement: 我方动作筛选内导航

查看器SHALL支持在当前场的筛选结果中跳转到上一条/下一条我方动作，并与普通逐事件“上一步/下一步”保持不同语义。筛选内导航 MUST NOT自动跨越 ReplayRound 边界。

#### Scenario: 跳到下一我方动作
- **WHEN** 当前 step 之后存在本场下一条我方 gameplay action
- **THEN** “下一我方”直接定位到该动作的原始全局 replay index，中间其他玩家事件仍保留在 session 中但不逐条停留

#### Scenario: 普通下一步仍逐事件
- **WHEN** 用户启用了我方动作筛选但点击普通“下一步”
- **THEN** 回放仍推进到完整 session 中的下一个事件，不把普通步进偷偷改成筛选跳转

#### Scenario: 当前场最后一个我方动作
- **WHEN** 当前已是本场最后一条我方 gameplay action
- **THEN** “下一我方”不可继续，不自动跳到下一场

### Requirement: 场次、摸牌与赢家状态的重放一致性

新增的 round index、drawn marker、winner marker 与 actor filter SHALL建立在既有 `ReplayStep` 和 Mirror/Game 状态上。它们MUST NOT修改平台事件顺序、合法动作重建、`/state` 挂载、原始 seqNo 或历史步骤状态。

#### Scenario: 新功能不改变 seqNo
- **WHEN** 同一记录分别由变更前后的回放管线加载
- **THEN** 对应服务端事件的 seqNo 顺序一致，新版仅增加场次和展示元数据

#### Scenario: 筛选不改变状态
- **WHEN** 用户在同一个全局 replay index 分别以“全部”和“我方动作”视图查看
- **THEN** GameTable、StepInspector 和公共状态完全一致

#### Scenario: gap 不伪造场次
- **WHEN** 同一 round_no 内存在 seq 缺口并由 snapshot 重锚
- **THEN** 缺口仍属于该 round；只有可靠 round_no 边界才能创建新 ReplayRound