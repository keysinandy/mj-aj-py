## 1. Protocol contract

- [ ] 1.1 与服务端协议负责人确认本部署的 authoritative 字段是 `source_discard_seq` 还是不透明的 `response_window_id`。
- [x] 1.2 在 protocol fixture contract 中写明字段范围、唯一性、碰→吃复用、seq=0 保留、轮次迁移以及畸形/缺失行为。

## 2. Server and fixture coverage

- [x] 2.1 增加代表性事件与快照 fixture：同一身份贯穿 response_peng、response_chi 与 seq=0 重锚响应。
- [ ] 2.2 增加同座同牌重复弃牌、被认领弃牌、新轮次、身份不一致、以及显式跳过身份的协议版本的 fixture。

## 3. Client validation and provenance

- [ ] 3.1 消费选定的协议字段，并在事件、快照、窗口确认与动作记录中保留 `identity_origin` 与 `first_seen_via`。
- [ ] 3.2 检测身份不一致或复用，把受影响证据降级，不提升快照 watermark 或结构猜测。
- [ ] 3.3 协议覆盖得到验证之前保留 `legacy_unresolved` 行为；不添加客户端回退身份。

## 4. Acceptance coverage

- [ ] 4.1 增加身份覆盖与 protocol-skipped 计数，不改变传输/窗口/游戏层验收分母。
- [x] 4.2 增加回放测试，证明身份跨 seq=0 与碰→吃迁移存活，且 legacy 窗口仍被排除在强完备之外。
- [ ] 4.3 在启用强身份门之前，用一次全新冻结版本跑批验证已部署的协议覆盖。
