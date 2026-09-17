# window-identity-protocol 线上证据（2026-09-17）

来源：`local/games/20260917/`，三个自由对战房间，各 10 局，全部结束。
口径：match 模式、单一全局令牌、`you_cai_bi_kao=false`、git 版本
`0acf6f26db78ec91b74e0c499b613429d9aca16e`。房间覆盖三个 evaluator，
说明身份缺口与 evaluator 无关。

| 房间 | evaluator | 决策 | 已授权窗口 | claim_miss |
| --- | --- | ---: | ---: | ---: |
| `a_b477ae66dabe` | legacy | 879 | 180 | 4 |
| `a_150836e6911d` | shape-v1 | 854 | 141 | 3 |
| `a_8b7798f44804` | shape-v2 | 625 | 107 | 1 |

全部 8 条 `claim_miss` 均为 `chosen=null` 且 `legal_check=not_decided`：
策略从未被调用（这些对局中 legacy decide p50 < 7 ms）。每个 miss 都是
传输/可观测性问题，不是策略延迟。

## 在范围内：合规的 `legacy_unresolved` 窗口

四个窗口首次从快照 pending 状态观测到，没有 authoritative
`source_discard_seq`。全部带有合法反应选项。三个在身份确认预算耗尽后以
`server_timeout_*` miss 过期；一个经 `identity_changed` 良性关闭（另一家
先认领）。

| 对局 | 轮 | phase | owner | tile | 结果 | 确认循环 |
| --- | ---: | --- | ---: | ---: | --- | --- |
| `a_b477ae66dabe_r1_b1_t0` | 4 | response_chi | 1 | 8 | `server_timeout_chi` miss | 10 次拉取 → `identity_confirmation_budget_exhausted` |
| `a_b477ae66dabe_r1_b9_t0` | 3 | response_chi | 0 | 0 | `server_timeout_chi` miss | 9 次拉取 → `identity_confirmation_budget_exhausted` |
| `a_150836e6911d_r1_b3_t0` | 1 | response_chi | 0 | 23 | `server_timeout_chi` miss | 9 次拉取 → `identity_confirmation_budget_exhausted` |
| `a_150836e6911d_r1_b6_t0` | 5 | response_peng | 1 | 25 | `identity_changed` TERMINAL（无 miss） | 6 次拉取 → 状态变化 |

三个房间的合计计数：

- 返回 `identity_unknown` 的 `window_confirm` 拉取：37
  （legacy 房 16、shape-v1 房 21、shape-v2 房 0）
- `identity_confirmation_budget_exhausted` 事件：4
- `identity_status=legacy_unresolved` 的 TERMINAL 窗口：4（上表）

按变更契约，这些窗口都没有获得客户端回退身份；它们保持
`legacy_unresolved` 并被排除在强完备之外。

## 范围外：观测迟到的 miss

五个 miss 有 authoritative 身份，但授权它的 `/state` 请求挂起超过了服务端
截止（1.5–3.6 s：相关对局中观测到 3619/1755/1531/1536/2856 ms 的请求）。
这些是 StateDemand/传输延迟，明确不是本变更的目标；在此记录只为保证
miss 账目完整、证据边界干净。

## 协议字段普查（2026-09-17 晚，match 房 `a_dde6f91b8576`）

第四个房间以原始 `/state`/`/action` JSON dump 跑批（4860 个 state dump、
329 个 action dump；服务端 guide v34，2026-09-14 更新），用实证回答任务
1.1 的问题：当前服务端到底能支持哪个身份字段？

方案 A —— `source_discard_seq`（事件序号）：

- 事件流已经为每条 `tile_discarded` 携带单调的每局 `seq`（观测到 1579 条
  事件）；碰/吃两相同享一个来源事件，因此身份复用与轮次迁移天然正确。
- pending 反应快照不携带它：252 个反应相位快照只暴露 `last_discard`、
  `waited_seat`、`responding_seats`、`window_deadline_ms`——没有任何形式
  的来源引用。
- gap 重锚从不回放漏掉的事件：观测到 40 个 gap 状态，0 个带非空
  `events` 列表。一旦错过开窗弃牌事件，其序号不可恢复——这就是上面每个
  `identity_unknown` 确认失败背后的机制。
- 启用 A 的服务端增量：给 pending 反应快照（以及 `seq=0`/gap 快照）加
  一个字段，携带已存在的来源弃牌事件序号。

方案 B —— `response_window_id`（不透明窗口 ID）：

- 全字段普查没有在任何地方发现窗口 ID：响应顶层键（`seq`、`gap`、
  `snapshot`、`events`、`pending`、`finished`）、快照键（上面列出的
  18 个键）、事件顶层键（`seq`、`type`、`seat`、`tile`、`data`、`ts`）、
  事件 `data` 键、动作 payload（`action`/`tile`）与动作响应（`ok`）全部
  无身份。
- 对整个 dump 语料 grep `source_discard|window_id|discard_seq|
  response_window` 零命中。
- 启用 B 的服务端增量：新 ID 分配与生命周期（碰→吃复用、`seq=0` 保留、
  跨弃牌/轮次不复用）加两条通道的携带——严格大于 A。

dump 房本身又新增两个合规 `legacy_unresolved` miss 窗口
（`a_dde6f91b8576_r1_b8_t0` 第 2 轮与 `r1_b9_t0` 第 5 轮，均为
`chosen=null` 的 `server_timeout_chi`，均为快照首见），当日合计六个合规
未解析窗口，其中五个损失了合法认领。

## 与任务 1.1 的关系

这次普查把字段选择从开放问题变成了有测量依据的建议：**方案 A**。事件
序号身份在服务端已存在；唯一缺失的携带是 pending/gap 快照上的一个字段。
方案 B 为同样的保证需要一套全新的分配子系统。因此任务 1.1 可以作为
一个具体的单字段请求提交给协议负责人，而不是一场开放的设计讨论。
