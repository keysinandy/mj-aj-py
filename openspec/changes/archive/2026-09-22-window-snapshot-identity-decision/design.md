# Design

## Context

`_resolve_window_confirm` 对每个 `identity_unknown` 解析都返回
`identity_unconfirmed`，使窗口保持 PENDING 并调度下一次 WINDOW_CONFIRM
拉取，直到 `_window_confirm_retry_expired`（8 次重试或固定时间界）触发，
随后窗口以 miss 关闭，策略从未被调用。单一个 `identity_unknown` 无法区分
"事件还追得上"与"事件被永久跳过"——这个循环是对第一种情形的有界下注，
但 2026-09-17 的证据显示实践中第二种情形占主导。

## Goals / Non-Goals

**Goals:**

- 让合规的 `legacy_unresolved` 窗口在首个新鲜观测上决策。
- 当剩余截止只够 decide + POST、不够再一轮确认往返时，停止确认循环。
- 保持每个 miss/尝试在验收报告中可归因。

**Non-Goals:**

- 把弱身份提升为 `authoritative` 或强完备。
- 改变合法动作推导、精确截止锚、提交守卫、限速/EDF 调度或传输重试。
- 重启服务端字段请求（范围外：服务端已冻结）。

## Decisions

### 1. 决策/证据拆分（核心决策）

身份服务两个主人：决策（现在提交哪个动作）与归因（去重、跨重锚记账、
验收覆盖）。服务端的纯校验契约使错误的弱键决策有安全兜底（409 + 既有
同环恢复），而拒绝决策具有同样的下界且零收益。因此：在弱键上决策，但
在账目中记为 `legacy_unresolved` 并排除在强完备之外。

### 2. 弱键 = 快照 epoch 牌河位置

pending 牌在被认领前位于弃牌家牌河顶端，因此
`(round_id, discard_owner, tile, len(discards[owner]))` 在一个快照 epoch
内唯一——这正是既有的诊断 fallback 元组。去重仅允许在 epoch 内；跨重锚
仅在 round/owner/tile 匹配且牌河长度一致时携带。同牌重弃歧义一律
"不携带"，回退到 409 兜底。

### 3. 截止驱动的确认预算

`_window_confirm_retry_expired` 保留计数器，但当
`window_deadline - now < decide 余量 + POST_RTT 估计 + 守卫` 时，循环停止
调度确认拉取，此时 pending 动作路径在弱键下立即决策。观测预算记录
（`identity_confirmation_budget_exhausted`）在连决策余量都没有的窗口上
仍然保留。

### 4. 记录结果保持诚实

`claim_miss` 与 decision/action 记录带上决策键的身份状态，使被 409 驳回的
弱键提交在验收报告中可区别于 authoritative miss。验收分母不变。

## Risks / Trade-offs

- [重锚后弱键重复提交] → epoch 内账目 + 保守携带规则 + 409 恢复；用
  fixture 覆盖。
- [弱键提交与真实新窗口竞速] → 同牌/轮/家必须全部匹配且牌河长度一致；
  否则不携带。
- [截止算术过于乐观] → 原样复用既有提交余量守卫；只有守卫仍允许 POST
  时才决策。
