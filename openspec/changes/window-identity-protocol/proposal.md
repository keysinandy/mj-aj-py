# Proposal

## Why

冻结的三房间基线中有五个合规的 `legacy_unresolved` 窗口。它们都是首次从快照观测到、没有 authoritative 的来源弃牌序号；客户端回退身份被有意禁止，因为它既无法在 `seq=0` 重锚后存活，也无法区分同座同牌的重复弃牌。

这使强窗口完备性（strong window completeness）无法达成，即便传输与 StateDemand 证据都是健康的。缺失的事实属于协议边界，而不是再补一个客户端启发式。

## What Changes

- 为来源弃牌身份定义一个稳定的协议字段：`source_discard_seq` 或不透明的 `response_window_id`。
- 要求同一弃牌的 `response_peng` 与 `response_chi` 在增量响应与 `seq=0` 快照中共享该身份。
- 要求新弃牌、新轮次必须获得不同的身份。
- 要求身份独立于快照 watermark、弃牌列表长度、副露计数，以及弃牌被认领后是否仍然可见。
- 保留客户端的 `identity_origin` 与 `first_seen_via` 显式诊断。
- 协议部署之前，快照首见的窗口保持弱/诊断口径；不添加猜测性客户端回退，也不静默提升 legacy 证据。
- 为明确声明无法提供该字段的部署定义 `protocol_skipped_identity` 状态，且不把这些窗口升级为强完备。

## Capabilities

### New Capabilities

- `window-identity-protocol`：反应窗口及其碰/吃两相的稳定、跨重锚身份。

### Modified Capabilities

无。既有 `online-room-window-attribution` 变更仍是客户端身份与 legacy 排除基线；本变更定义改善覆盖所需的协议事实。

## Impact

- 服务端/API 响应与事件 schema 必须暴露并保留稳定的窗口身份。
- `mj/platform/bot_client.py` 与 `scripts/window_acceptance.py` 消费该字段并报告来源/覆盖，不添加启发式回退。
- 协议 fixture 与线上验收测试需要显式 source identity 与快照携带用例。
- 本变更与状态限速、重试、提交余量和对局结算标记相互独立。
