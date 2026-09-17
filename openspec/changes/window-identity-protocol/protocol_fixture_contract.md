# 协议 fixture 契约：反应窗口身份

`window-identity-protocol` 任务 1.2 交付物。依据 2026-09-17 的字段普查
（`artifacts/online_identity_evidence_20260917.md`）：当前服务端（guide v34）
任何地方都不携带窗口身份字段；事件流已经在每条 `tile_discarded` 上带有
单调的每局 `seq`，这是推荐的来源。

## 选定字段

`source_discard_seq` —— 开出反应窗口的那次弃牌的事件 `seq`。

- 携带位置：`tile_discarded` 事件（已存在）与 pending 反应快照（今天缺失；
  所需的服务端增量）。
- 客户端回退拼法（`_source_identity` 接受）：`source_discard_seq`、
  `last_discard_seq`、`discard_seq`，位于 payload 顶层或 `data` 内。服务端
  MUST 选定一种拼法并保持稳定；客户端不会把两种拼法合并成同一身份。
- 显式不能作为身份：`/state` 响应的 `seq`（包含式状态 watermark——会因
  无关事件变化）、快照 watermark、弃牌列表长度、副露计数、owner/tile 对
  或时间戳。

## fixture 编码的保证

| # | 保证 | fixture |
| --- | --- | --- |
| 1 | 同一弃牌的 `response_peng` 与 `response_chi` 暴露相同身份 | `peng_chi_shared_identity.json` |
| 2 | `seq=0` 全量重锚保留开窗的身份 | `seq0_reanchor_retention.json` |
| 3 | 同座同牌重复弃牌获得不同身份 | `repeated_same_tile_distinct.json` |
| 4 | 被认领弃牌的身份不会复用到下一个窗口 | `claimed_discard_not_reused.json` |
| 5 | 事件/快照身份不一致是 MISMATCH，降级，永不提升 | `identity_mismatch_downgrade.json` |
| 6 | 快照首见窗口（无协议字段、未观测到事件）保持 `legacy_unresolved` 并被排除在强完备之外 | `snapshot_only_remains_legacy.json` |

## 畸形 / 缺失行为（客户端契约，已实现）

- 处处缺失字段 → `identity_status=legacy_unresolved`、
  `identity_origin=legacy_snapshot`，仅作弱诊断 fallback 键；绝不跨重锚去重，
  也绝不计为 authoritative。
- 仅事件带字段 → `identity_origin=tile_discard_event_seq`、
  `first_seen_via=event`；跨重锚携带为 `carried_event_seq`/`reanchor`。
- 快照带字段 → `identity_origin=explicit_source_field`。
- authoritative 期望序号与实际不一致 → MISMATCH（不以陈旧身份提交）；
  身份未知 → 保持 pending 并有界的确认预算；客户端绝不猜测。
- 显式声明身份不可用的协议版本可报告为 `protocol_skipped_identity`；
  客户端不能仅凭字段缺失推断这一点。目前不存在声明通道，因此任何现有
  fixture 都不产生该状态。

## 回放测试组

`tests/test_window_identity_protocol.py` 加载每个 fixture 并通过真实
`BotClient` 身份解析回放（共享碰→chi 用例则跑一整局脚本化 `/state` 对局）。
这些 fixture 同时是服务端开始携带该字段那天的验收测试组。
