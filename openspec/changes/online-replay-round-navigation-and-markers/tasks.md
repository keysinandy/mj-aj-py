# Tasks

> 本 change 只规划回放行为，不修改 BOT 决策和平台协议。验证继续纳入现有 Python pytest 与前端组件/Store 测试。

## 1. ReplaySession 场次索引

- [x] 1.1 在 `mj/clientd/replay.py` 增加从完整 frames/steps 构建连续 round segment 的 helper；边界依据连续 `round_no` 变化，不使用时间间隔，不对非连续相同 round_no 做全局合并。
- [x] 1.2 为每个 round 输出 `round_id / ordinal / round_no / start_step_index / end_step_index / start_seq_no / end_seq_no / winner_seats / ended`。
- [x] 1.3 在 `_session_from_frames()` 的 metadata 中增加 `rounds`，同时保持 `steps` 原顺序、全局 step index 与原始 seqNo 不变。
- [x] 1.4 更新 `client/src/replay/session.ts`，增加 `ReplayRound`、backend snake_case 映射及旧 session 的兼容派生。
- [x] 1.5 测试：构造同一线上记录包含 10 个 round，每个 round 的 index 范围互不重叠且完整覆盖 session steps；非连续重复 round_no 不合并。

## 2. ReplayStore 场次边界导航

- [x] 2.1 在 `client/src/replay/replayStore.ts` 增加 `rounds / activeRoundId` 与 `selectRound / previousRound / nextRound`。
- [x] 2.2 `setSession()` 默认选择第一场；切场跳到该场首 step 并停止播放。
- [x] 2.3 让 `stepForward / stepBack / firstStep / lastStep` 在 active round 内工作；stepForward 到场尾停止，不进入下一场。
- [x] 2.4 `jumpToSeqNo()` 定位到其他场 seq 时同步切换 active round。
- [x] 2.5 增加 Store 测试：上一/下一场、场尾停止、seq 跨场定位、旧 session round 派生。

## 3. 场次选择 UI

- [x] 3.1 在 `ReplayViewer.tsx` 顶部增加“第 X / N 场”选择器和上一场/下一场按钮。
- [x] 3.2 Timeline 只接收当前 active round 的 entries，但 entry.index 必须保持全局 ReplayEngine index。
- [x] 3.3 ReplayControls 的首尾、自动播放进度文案按当前场边界展示；StepInspector 仍显示真实 step/seq。
- [x] 3.4 前端测试：10 场切换后牌桌与 timeline 均落到对应 round，不能出现上一场/下一场条目混杂。

## 4. 摸牌帧契约

- [x] 4.1 在 `_online_frame()` 中从 `Mirror.drawn / Mirror.draw_origin` 输出 `drawn_tile / drawn_seat / draw_origin`；仅自家有可靠牌身份时输出。
- [x] 4.2 在 `_local_frame()` 中从 `Game.drawn` 输出同一字段，本地全知帧保留真实摸牌身份。
- [x] 4.3 更新 `client/src/replay/frame.ts` 类型；旧帧字段缺失时保持兼容。
- [x] 4.4 Python 测试：自家 tile_drawn 后 frame 有 drawn tile，弃牌后清空；对手不可见 tile_drawn 不得产生具体 drawn tile；snapshot 的 `drawn_tile` 能正确恢复。

## 5. 摸牌特殊展示

- [x] 5.1 在 `GameTable.tsx` / `HandTiles` 增加 drawn tile 渲染参数。
- [x] 5.2 当 drawn tile 已包含在 count 中时，从 standing copy 中减一，再单独渲染，确保总张数不增加。
- [x] 5.3 单独摸牌与普通手牌留视觉间距，并增加明确“摸”badge/highlight；杠后补牌可根据 `draw_origin` 增加可选 title，不改变主展示语义。
- [x] 5.4 前端测试：摸牌前无 marker、摸牌后恰好一张特殊牌、打出后 marker 消失、重复牌值不重复计算张数。
- [x] 5.5 固定牌桌网格和座位容器尺寸；听牌弹窗以视口浮层展示，不参与页面布局。

## 6. 赢家帧状态

- [x] 6.1 `_OnlineBuilder` 增加当前 round winner 集合；遇到 `EV_HU` 后记录合法 seat，并让当前及后续 frame 继承 `winner_seats`。
- [x] 6.2 round 切换时清空 winner 状态；`EV_ROUND_ENDED` 后输出 `round_ended=true`。
- [x] 6.3 本地 frame 在引擎已产生结果后按相同字段映射 winner；未结束 frame 保持空集合/缺省。
- [x] 6.4 round summary 从该 segment 最终帧提取 winner，但 UI 历史 step 禁止使用 summary 渲染 winner。
- [x] 6.5 Python 测试：HU 前 winner 为空，HU step 开始出现，回退到 HU 前再次为空；多 HU 事件可累积多个赢家。

## 7. 赢家 UI

- [x] 7.1 在 `PlayerArea` 增加 winner badge，例如 `胡`。
- [x] 7.2 badge 可与庄家/行动中标记共存，不改变座位布局。
- [x] 7.3 `ReplayViewer.test.tsx` / `GameTable.test.tsx` 覆盖“HU 前不显示、HU 后显示、回退消失、多赢家分别显示”。

## 8. 我方动作结构化筛选

- [x] 8.1 在 `client/src/replay/timeline.ts` 增加 action kind 归一化和 `isGameplayAction`，禁止通过中文 label 文本直接判断 actor。
- [x] 8.2 定义 `isMine = actor === my_seat && isGameplayAction`；snapshot、gap、旁路 request/diagnostic 不属于我方 gameplay action。
- [x] 8.3 ReplayStore 增加 `actorFilter: "all" | "mine"`，并提供 `setActorFilter`。
- [x] 8.4 ReplayViewer 增加 `[全部] [我方动作]` 切换；过滤后的 TimelineEntry 保留全局 index。
- [x] 8.5 测试：只保留自家 draw/discard/chi/peng/gang/hu/pass 等动作；其他三家和系统步骤隐藏，但牌桌当前状态与 session 总步数不变。

## 9. 筛选内导航

- [x] 9.1 在 ReplayStore 增加 `nextFilteredStep / previousFilteredStep`，仅在当前 active round 的过滤结果中查找目标。
- [x] 9.2 UI 提供语义明确的“上一我方 / 下一我方”按钮；普通上一步/下一步仍逐事件推进。
- [x] 9.3 到当前场过滤结果首尾时禁用，不自动跨 round。
- [x] 9.4 测试：从任意普通事件可以跳到下一条我方动作，且返回的 replay index 是原始全局 index。

## 10. 回归与验收

- [x] 10.1 `python -m pytest tests/test_online_replay_frames.py tests/test_replay_session.py -q` 通过。
- [x] 10.2 前端 ReplayViewer / GameTable / replayStore / timeline 相关测试通过。
- [ ] 10.3 手工验收一个包含约 10 场的真实线上房间：
  - 场次数正确；
  - 每场时间线没有其他场步骤；
  - 上一场/下一场切换正确；
  - 场尾自动播放停止；
  - 自家摸牌有特殊展示且无重复；
  - 手牌张数变化和打开听牌提示时，四个座位和牌桌尺寸不变；
  - 对手未知摸牌不泄漏；
  - HU 后赢家可见，HU 前不可见；
  - “我方动作”只展示我方真实麻将动作；
  - “上一我方/下一我方”不跨场；
  - seqNo 和 StepInspector 与原记录一致。
- [x] 10.4 回归打开旧线上记录和本地记录，确认没有新增字段时仍能正常回放。
