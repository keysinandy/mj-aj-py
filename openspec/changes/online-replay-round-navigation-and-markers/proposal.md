# Proposal

## Why

当前统一回放已经具备 `ReplaySession / ReplayStep`、`seqNo` 导航、线上 `round_no` 帧字段和 `TimelineEntry.isMine`，但线上一个房间包含多场牌局时，所有步骤仍落在同一个连续时间线中，用户无法明确区分和切换场次；摸牌状态只存在于 `Game.drawn` / `Mirror.drawn`，没有进入稳定的 `ReplayFrame` 展示契约；`hu` 事件虽然会被回放，但牌桌没有持续标记已胡玩家；时间线虽然能识别我方 actor，也没有提供“只看我方动作”的筛选与筛选内导航。

这会直接降低线上房间复盘效率：一个 10 场房间需要在长时间线里人工寻找边界，无法一眼识别刚摸牌、赢家，也需要从其他玩家和系统事件中手工定位我方决策。

## What Changes

- 为统一 `ReplaySession` 增加**场次索引**：按真实 `round_no` 的连续边界把一个房间拆成多个 `ReplayRound`，每场记录稳定 `round_id`、起止 step/seq、局号和终局摘要；回放查看器增加场次选择器、上一场/下一场，并默认只展示当前场的时间线。
- 保持底层 `ReplaySession.steps` 为完整房间序列，场次只是索引/视图层，不复制、不重新解释事件，也不改变原始 `seqNo`。
- 为 `ReplayFrame` 增加可选的 `drawn_tile` / `drawn_seat` / `draw_origin`。当刚摸牌身份有可靠证据时，手牌 UI 将该牌从排序手牌中视觉分离并标记“摸”；线上对手摸牌身份不可见时不得猜测。
- 为 `ReplayFrame` 增加**随时间推进的胡牌状态**，至少包含 `winner_seats`。只有当前 step 已经发生 `hu` 后才显示赢家标记，禁止使用最终结果提前剧透。
- 在时间线增加 `全部 / 我方动作` 筛选，并支持在筛选结果中上一条/下一条跳转；筛选只改变展示和导航目标，不删除步骤、不修改状态重建。
- 固定四个座位的牌桌网格尺寸；手牌/牌河/副露变化只影响座位内部内容，听牌提示作为视口浮层显示，不触发布局重排。
- 我方动作按“事件 actor == `my_seat` 且属于真实麻将动作”判定；`snapshot`、`req`、诊断等系统/旁路记录不因挂在我方 seq 上就算作我方动作。
- 补齐后端、前端和回归测试，覆盖同房间 10 场、多次 round 切换、摸牌展示、胡牌不剧透、我方动作筛选以及 seqNo 稳定性。

## Capabilities

### New Capabilities

无。

### Modified Capabilities

- `client-replay-viewer`: 增加场次索引与导航、摸牌特殊展示、赢家标记、我方动作筛选和筛选内导航。

## Impact

- **后端回放管线**：`mj/clientd/replay.py`
  - 继续复用现有 `_OnlineBuilder` / `Mirror`；
  - 在 `ReplaySession.metadata` 中输出场次索引；
  - 在帧中暴露可靠的摸牌身份与随 step 推进的赢家状态。
- **前端模型**：
  - `client/src/replay/frame.ts`
  - `client/src/replay/session.ts`
  - `client/src/replay/replayStore.ts`
  - `client/src/replay/timeline.ts`
- **前端 UI**：
  - `client/src/components/ReplayViewer.tsx`
  - `client/src/components/GameTable.tsx`
  - `client/src/styles.css`
  - `client/src/components/Timeline.tsx`
  - `client/src/components/ReplayControls.tsx`（若筛选导航按钮放在控制区）
- **测试**：
  - `tests/test_online_replay_frames.py`
  - `tests/test_replay_session.py`
  - `client/src/__tests__/ReplayViewer.test.tsx`
  - `client/src/__tests__/GameTable.test.tsx`
  - `client/src/replay/__tests__/replayStore.test.ts`
- **兼容性**：
  - 旧 session 没有 `rounds`、`drawn_tile`、`winner_seats` 时仍可回放；
  - 前端可从旧帧的连续 `round_no` 派生只读场次索引作为兼容兜底；
  - 不修改平台事件协议、不修改 `Mirror` 合法性语义、不改变 `/state` 挂载原则。
