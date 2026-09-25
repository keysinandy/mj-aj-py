# Design

## Context

现有回放已经统一为：

```text
线上 jsonl / 本地记录
        ↓
mj/clientd/replay.py
        ↓
ReplaySession
  └── ReplayStep[]
        └── ReplayFrame
        ↓
ReplayEngine
        ↓
ReplayStore
        ↓
ReplayViewer / GameTable / Timeline
```

已存在的关键事实：

- 线上 `ReplayFrame.round_no` 来自 `Mirror.round_no`；
- `ReplayStep.seqNo` 使用服务端 `seqNo`，旁路 `/state`、decision、diagnostic 只挂在已有 step 上；
- `Mirror.drawn` 已保存“自家刚摸牌”，`Game.drawn[seat]` 保存本地完整摸牌状态；
- `EV_HU` 已进入事件流，但 `Mirror` 当前只把它当标注事件，结算在 `round_ended`；
- `TimelineEntry` 已有 `actor` / `isMine`，但 UI 尚未提供筛选；
- 一个线上 gid/房间可以包含多个 `round_no`，当前 `ReplaySession.steps` 会连续装入所有场次。

因此本变更不另造新的房间回放协议，而是在统一 session 上增加场次索引和帧级展示状态。

## Goals / Non-Goals

**Goals**

- 同一个线上房间包含多场时，用户能明确看到“第几场 / 共几场”并切换。
- 当前场的时间线、首尾跳转、自动播放和筛选导航都受场次边界约束。
- 摸进来的牌有稳定、可测试的特殊展示，且不泄漏线上对手暗手。
- `hu` 发生后在对应玩家区域显示赢家标记，发生前不剧透。
- 可一键筛选我方动作，并在筛选结果中前后跳转。
- 保持 `seqNo`、Mirror 重建、统一 ReplaySession 和现有旁路挂载语义不变。

**Non-Goals**

- 不改变平台服务端事件 schema。
- 不推断线上对手摸到的具体牌。
- 不实现新的全房间统计报表、胜率聚合或跨房间检索。
- 不重做 RoomsPage 的实时房间墙。
- 不改变 BOT 决策或线上请求行为。
- 不把一个房间拆成多个磁盘文件；拆场只发生在回放 session 的索引/视图层。

## Decisions

### D1：一个房间仍是一个 ReplaySession，场次使用索引而不是复制 session

新增统一模型：

```ts
export interface ReplayRound {
  roundId: string;
  ordinal: number;       // 1..N，按记录中出现顺序
  roundNo: number;       // 平台 round_no
  startStepIndex: number;
  endStepIndex: number;  // inclusive
  startSeqNo: number | null;
  endSeqNo: number | null;
  winnerSeats?: number[];
  ended?: boolean;
}
```

`ReplaySessionMetadata` 增加：

```ts
rounds?: ReplayRound[];
```

后端 `_session_from_frames()` SHALL 基于 `steps` 中**连续的 `state.round_no` 段**建立索引。不能仅按 `round_no` 数值全局 group，因为重连、异常记录或未来协议可能产生非连续的相同 round_no；非连续段必须视为不同 replay round。

`roundId` 必须在当前 session 内稳定且唯一，建议：

```text
r{ordinal}-n{roundNo}-s{startSeqNo|startStepIndex}
```

场次边界只允许来自可靠的 `round_no` 连续变化或 session 起止，不允许按时间间隔猜测。

### D2：ReplayEngine 保留全量全局索引，ReplayStore 增加 active round view

不复制 `ReplayEngine`。Store 增加：

```ts
activeRoundId: string | null;
rounds: ReplayRound[];

selectRound(roundId)
nextRound()
previousRound()

roundStartIndex()
roundEndIndex()
visibleRoundSteps()
```

选择新场次时，游标跳到该场 `startStepIndex` 并停止播放。

以下控制在有 active round 时必须被限制在当前场：

- stepForward / stepBack
- firstStep / lastStep
- autoplay
- timeline entries
- filtered next / previous

`jumpToSeqNo(seqNo)` 如果目标属于其他场，允许自动切换到该场并定位，因为 seqNo 是明确目标；普通“下一步”不得无提示跨场。

旧 session 没有 `metadata.rounds` 时，前端 SHALL 用连续 `frame.round_no` 派生相同的只读索引，保证历史记录可用。

### D3：场次选择 UI 明确展示房间内 N 场，而不是把 round_no 当普通标签

ReplayViewer 顶部增加：

```text
场次  [ 第 3 / 10 场 · round 3 ▼ ]  [上一场] [下一场]
```

要求：

- 打开房间默认选择第一条可回放场次；
- 切场后 timeline 只显示当前场；
- `currentIndex` 继续保持全局 step index，TimelineEntry 保存全局 index，点击条目仍能准确回到原 session；
- UI 不重新编号或改写服务端 `seqNo`；
- 场次内部可显示相对序号，但 StepInspector 继续展示真实全局 step/seq。

### D4：摸牌身份进入 ReplayFrame，但只在有可靠证据时填充

`ReplayFrame` 增加：

```ts
drawn_tile?: number | null;
drawn_seat?: number | null;
draw_origin?: "normal" | "kong_replacement" | string | null;
```

后端规则：

**线上**

- 只有 `Mirror.drawn !== null` 时才暴露 `drawn_tile`；
- `drawn_seat = mirror.me`；
- `draw_origin` 映射 `Mirror.draw_origin`；
- 对手 `tile_drawn` 没有牌身份时必须保持 `drawn_tile = null`，不得从后续弃牌倒推。

**本地**

- 当前状态若 `Game.drawn[seat]` 有值，可暴露对应 `drawn_tile / drawn_seat`；
- 本地全知模式允许显示其他座位的真实摸牌，因为记录本身具备全知数据；
- 玩家视角仍只对当前可见手牌做特殊展示。

### D5：摸牌 UI 从排序手牌中视觉分离，不能重复显示

现有 hand count 已包含刚摸牌，因此 UI 不得简单在手牌末尾再追加一张。

渲染时对当前可见座位：

1. 复制 `counts`；
2. 若 `drawn_seat == seat` 且 `drawn_tile` 合法且 count > 0，从 standing counts 中减去 1；
3. 先按现有顺序渲染 standing hand；
4. 留 8–12px 视觉间距；
5. 单独渲染 `drawn_tile`，增加 `摸` badge / highlight。

概念结构：

```text
1m 2m 3m 4p 5p 6p ...  |  [8s]
                              摸
```

若当前帧没有可靠 `drawn_tile`，保持现有手牌渲染，不猜测。

当发生弃牌、吃碰、杠状态切换导致 `drawn` 被清空时，特殊展示同步消失。

### D6：赢家状态是“截至当前 step 已知”的帧状态，禁止用最终摘要提前渲染

`ReplayFrame` 增加：

```ts
winner_seats?: number[];
round_ended?: boolean;
```

`_OnlineBuilder` 维护当前 round 的 `winner_seats`：

- 遇到 `EV_HU` 后，把事件 `seat` 加入赢家集合；
- `_emit()` 后续帧继承该集合；
- 新 round 的可靠 snapshot / round_no 切换时清空；
- `EV_ROUND_ENDED` 可标记 `round_ended = true`，但不得覆盖掉已记录赢家；
- 若平台允许多个 `hu` 事件，集合支持多赢家。

非常重要：`ReplayRound.winnerSeats` 可以作为场次摘要保存最终赢家，但 `GameTable` 当前 step 的赢家 badge **只能读取 `frame.winner_seats`**，不得读取 round summary，否则在牌局前半段会剧透。

本地回放如果引擎动作已经产生 `game.result`，可按同一帧字段映射；未结束时为空。

### D7：赢家标记放在玩家区域，且只表达已发生的事实

`PlayerArea` 增加：

```text
P2  （胡）
```

或等价显著 badge。

要求：

- `winner_seats.includes(seat)` 后才显示；
- badge 与庄家、当前行动中标记可同时存在；
- 切回 HU 之前的 step，赢家 badge 必须消失；
- 切到 HU 或之后 step，赢家 badge 出现；
- 多赢家分别显示；
- 不以分数变化、终局文件名或最终 round summary 猜测赢家。

### D8：我方动作筛选建立在结构化事件 actor 上，不按 label 文本匹配

新增：

```ts
type TimelineActorFilter = "all" | "mine";
```

`TimelineEntry` 增加标准化字段：

```ts
actionKind:
  | "draw"
  | "discard"
  | "chi"
  | "peng"
  | "gang"
  | "hu"
  | "pass"
  | "timeout"
  | "other";
isGameplayAction: boolean;
```

线上按事件 `type + seat` 归一化；本地 `type="action"` 使用已有 `actor` 与 action label / action code 归一化。

“我方动作”定义：

```text
actor == frame.my_seat
AND isGameplayAction == true
```

以下不算我方动作：

- snapshot
- state request
- decision attachment
- diagnostics
- gap marker
- 仅因为挂在我方 seq 上的旁路记录

Timeline 的筛选必须保留 entry 原始 `index`，不得 map 后用新位置作为 replay index。

### D9：筛选只改变视图和筛选内跳转，不改变重放状态

ReplayStore 增加：

```ts
actorFilter: "all" | "mine";
setActorFilter(...)
nextFilteredStep()
previousFilteredStep()
```

当 `actorFilter="mine"`：

- Timeline 仅显示当前 round 内的我方 gameplay actions；
- 当前牌桌状态保持用户当前 step，不因为隐藏 timeline 条目而被重算；
- 点击筛选条目使用其全局 index 跳转；
- “下一条/上一条”在当前 round 的过滤结果内跳转；
- 到头时禁用/停止，不跨 round；
- 关闭筛选恢复完整当前 round 时间线。

普通 `stepForward/stepBack` 仍代表逐事件推进；筛选模式不得偷偷改变这两个按钮的语义。筛选内跳转使用独立按钮或明确的“上一我方 / 下一我方”。

### D10：自动播放受 round 边界限制

当前 ReplayViewer 的 autoplay 直接调用 `stepForward()`。变更后：

- active round 存在时，播放到 `endStepIndex` 自动停止；
- 不自动进入下一 round；
- 用户切换下一场后可再次播放；
- 这样一个 10 场房间不会在无感知情况下重新混成一条播放流。

### D11：后端 round summary 与帧状态分离

后端生成 `ReplayRound` summary 时可以从该 segment 最后的已知帧提取：

- winner seats
- ended
- start/end seq

但 summary 只服务于场次选择器/终局摘要。

所有逐步牌桌状态必须来自对应 `ReplayFrame`，不能用 summary 回填历史 frame。

### D12：兼容旧记录与缺口记录

- 缺少 `round_no` 的旧帧：整个 session 作为单场 `roundNo=1` 兼容展示，并标记为派生 round。
- seq gap 不创建新场次，仍按 `round_no` 连续段归属。
- gap 后 snapshot 若 `round_no` 改变，则在 snapshot 处开始新场。
- 旧帧没有 `drawn_tile` / `winner_seats` 时，UI 不显示相应 marker；不得根据后续事件倒推。
- 旧 session 没有 round metadata 时，前端派生索引，后端新记录则优先使用权威 metadata。

## Data Contract

建议后端 session：

```json
{
  "metadata": {
    "source": "online",
    "id": "room-gid",
    "step_count": 428,
    "rounds": [
      {
        "round_id": "r1-n1-s100",
        "ordinal": 1,
        "round_no": 1,
        "start_step_index": 0,
        "end_step_index": 41,
        "start_seq_no": 100,
        "end_seq_no": 151,
        "winner_seats": [2],
        "ended": true
      }
    ]
  },
  "steps": []
}
```

建议 frame 新字段：

```json
{
  "round_no": 3,
  "seq_no": 277,
  "drawn_tile": 18,
  "drawn_seat": 0,
  "draw_origin": "normal",
  "winner_seats": [],
  "round_ended": false
}
```

HU 后：

```json
{
  "round_no": 3,
  "seq_no": 291,
  "winner_seats": [0],
  "round_ended": false
}
```

## Risks / Trade-offs

- **同 round_no 非连续重复**：如果简单 `groupBy(round_no)` 会再次把不同段混合；因此必须按连续段切分并用 ordinal/start seq 组成唯一 round id。
- **摸牌重复展示**：hand count 已含 drawn tile；UI 必须先减一再单独渲染。
- **线上对手摸牌泄漏**：对手 draw event 没有身份时绝不能根据后续 discard 反推成“刚摸牌”。
- **赢家剧透**：round summary 有最终赢家，但历史 frame 不能读取它。
- **筛选索引错位**：过滤后的数组位置不是 ReplayEngine index；TimelineEntry 必须保留全局 index。
- **自动播放跨场**：必须在 round end 停止，否则有场次 selector 仍会造成“混播”。
- **旧记录字段缺失**：全部新字段可选，前端需有兼容派生路径。

## Migration Plan

纯兼容性扩展，无磁盘记录迁移。

落地顺序：

1. 后端帧增加 drawn/winner 字段，并生成 session round metadata；
2. 前端类型与兼容解析；
3. ReplayStore 场次边界导航；
4. 场次选择器；
5. 摸牌特殊展示与 winner badge；
6. Timeline 我方动作筛选与筛选内跳转；
7. 补齐后端/前端测试。

旧客户端继续忽略新增 JSON 字段；新客户端打开旧记录时使用兼容派生逻辑。

## Open Questions

无阻塞性问题。第一版按当前平台事件能力实现：
- 线上只特殊展示自家可确认的摸牌；
- winner 以实际 `hu` 事件 seat 为准；
- 场次边界以连续 `round_no` 为准，不按时间间隔推断。