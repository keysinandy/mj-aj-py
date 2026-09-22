# Proposal

## Why

服务端不会部署窗口身份字段。线上证据（2026-09-17，
`../window-identity-protocol/artifacts/online_identity_evidence_20260917.md`）
显示每个合规的 `legacy_unresolved` 窗口都走同一条损失链：快照首见的
pending 窗口进入 CONFIRM_PENDING，确认循环烧光请求预算去拉 `/state`，
而这个事件协议已经无法送达（gap 重锚与循环自身的 `seq=0` 拉取把
watermark 推过开窗弃牌），最终窗口过期、策略从未被调用。一天内五次合法
认领以这种方式损失，16/s 验证房间中另有七次。

服务端是纯校验（`/action` 执行合法动作、对非法动作返回 409，且已有同环
409 恢复），因此弱身份用于决策的最坏情形与 miss 相同：一次被拒的 POST
加一次重锚。现有契约把身份的两个用途——决策授权与证据归因——混为一谈并
同时禁止。证据必须保持严格；决策不必。

## What Changes

- 拆分身份契约：`legacy_unresolved` 窗口在首次出现于新鲜权威快照时，
  MAY 用弱 epoch 键决策；它们永远不会被提升为 `authoritative`，也永远不会
  计为强证据。
- 确认预算改为截止驱动：当剩余窗口时间不再够一轮确认往返加提交余量时，
  停止确认、在弱键下决策。
- 弱键为当前快照 epoch 内的 `(round_id, discard_owner, tile, 弃牌家牌河尾
  位置)`：pending 牌在被认领前位于弃牌家牌河顶端，因此牌河长度在 epoch
  内唯一确定该实例。跨重锚仅在 round/owner/tile 匹配且牌河长度一致时
  允许携带；任何歧义都回退到 409 兜底，绝不猜测强身份。
- 不改变合法动作推导、截止、守卫、传输、限速或验收分母；`claim_miss`
  记录新增弱键决策结果，保持归因诚实。

## Capabilities

### Modified Capabilities

- `window-identity-protocol`：决策路径可以在 409 兜底下消费弱身份；
  证据/强完备规则不变。

## Impact

- `mj/platform/bot_client.py` 确认循环（`_resolve_window_confirm`、
  `_window_confirm_retry_expired`）与 pending 动作路径。
- 测试：扩展 `tests/test_window_identity_protocol.py` fixture 组，增加
  快照首见决策场景与 409 恢复变体。
