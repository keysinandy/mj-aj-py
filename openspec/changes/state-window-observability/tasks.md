## 1. 基线与窗口身份契约

- [ ] 1.1 以独立提交 `3ae3fd6` 为实现基线，复核 `catch_play` 修复及完整
  `python3 -m pytest tests/ -q` 结果，不将该修复与本 change 混合修改。
- [ ] 1.2 在 `mj/platform/bot_client.py` 或独立平台模块中定义
  `WindowId`/`WindowAttemptKey` 数据结构，统一携带 game、round、owner、
  source discard seq、tile、phase 和 identity status。
- [ ] 1.3 收紧 source sequence 提取：权威字段优先，缺失时标记
  `legacy/unresolved`；禁止把 snapshot watermark、牌河长度或副露数升级为
  跨 seq=0 的强身份，并保留弱 fallback 诊断字段。
- [ ] 1.4 将 `peng → chi`、seq=0 重锚、换轮/新弃牌和动作防重路径统一接入
  `WindowAttemptKey`，为 authoritative 与 legacy 两种语义分别保留测试断言。

## 2. per-gid StateDemand 协调器

- [ ] 2.1 实现 per-gid demand 对象，维护 `wanted_seq`、`kind_priority`、
  `deadline`、`reason_mask`、`generation`、`in_flight` 及逻辑 reason 的局部
  状态；SSE queue 只作为需求输入/唤醒，不再承担完成语义。
- [ ] 2.2 实现需求合并规则：watermark 取 max、有效 deadline 取最早值、reason
  取 OR、优先级固定为 `WINDOW_CONFIRM > RESYNC > SSE_DELTA`；过期窗口按现有
  规则降级，不改变 StateThrottle 的 15/s/EDF/429 接口。
- [ ] 2.3 将 `BotClient._play_loop()` 的 SSE、deadline、seq=0 重锚和窗口确认
  触发改为提交 demand；同一 gid 在途期间只更新目标并递增 generation，物理
  请求返回后最多补一次最新需求。
- [ ] 2.4 实现 reason-specific satisfier：RESYNC、SSE_DELTA 和 WINDOW_CONFIRM
  分别判断 watermark/镜像重建、目标覆盖以及 WindowId/phase/responding_seats/
  精确截止条件，不能以一次 seq=0 返回清空全部 reason。
- [ ] 2.5 在本地合法集检查后对无非 pass 合法动作的弃牌窗口清除 urgent，回退
  普通 SSE 追赶；有合法候选、过期截止和 stale/closed 窗口分别记录原因。
- [ ] 2.6 确认所有物理 state 尝试（含 retry）仍只经共享 `Api.state_throttle`，
  不新增并发请求、不改变默认 rate、sleep、release-aware EDF 或动作安全边界。

## 3. HTTP 与记录器观测

- [ ] 3.1 在 `mj/platform/api.py` 的 state/action 物理 attempt 中捕获
  `http_start`、`headers_received`、`body_finished` 边界，并用 monotonic 派生
  `pre_read_ms`、`read_ms`、`total_ms`；DNS/connect/TLS/send 保持 unavailable。
- [ ] 3.2 为每个 attempt 保存 `retry_after_raw`、`server_date_raw`，并在可解析
  时补充 `retry_after_seconds`（秒数或 HTTP-date）和 `server_date_epoch`；解析
  失败不丢 raw，server Date 不参与 deadline 控制。
- [ ] 3.3 记录 `deadline_left_at_send` 与 `deadline_left_at_response`，严格由
  本地 monotonic deadline 计算；保留每次 retry 的独立 throttle/HTTP 诊断和逻辑
  请求的聚合 attempts/429/queue。
- [ ] 3.4 扩展 `mj/platform/recorder.py` 的 req/action/window_confirm 字段，
  携带 WindowId、attempt key、reason、generation、identity status 和观测字段；
  保持旧 JSONL 缺字段时可回放。

## 4. 离线回归与契约测试

- [ ] 4.1 在 `tests/test_window_confirmation.py` 增加 authoritative WindowId、
  peng→chi 共享身份、seq=0 同窗重锚、换轮/新牌失效和 legacy unresolved
  不强去重的 fake-clock 场景。
- [ ] 4.2 在 `tests/test_state_scheduling.py` 增加十场并发模拟：100 SSE 帧一条
  demand、同 gid 单在途、跨 gid 独立、wanted/deadline/reason 合并和 generation
  变更只补一次请求。
- [ ] 4.3 增加 reason 分别完成、窗口确认条件不满足、urgent 降级、未来窗口压过
  普通 backlog、过期确认自动降级，以及 SSE wake + deadline wake 单次消费场景。
- [ ] 4.4 增加 API fake response/HTTPError/read failure 测试，断言阶段边界、raw
  header、数值/HTTP-date 解析、unavailable 字段和本地 deadline 剩余语义。
- [ ] 4.5 增加 recorder/回放兼容测试，确认旧日志读取不报错且 unknown identity
  不被当作 authoritative；确认逻辑请求与物理 retry 可关联。
- [ ] 4.6 运行 focused 回归、完整 `python3 -m pytest tests/ -q` 和
  `git diff --check`，失败先定位并补齐上述契约场景。

## 5. 验收分类、指标与文档

- [ ] 5.1 扩展验收脚本/报告，将房间分类为 `complete`、`partial`、
  `protocol_skipped`，并区分日志缺尾与协议没有 `round_ended`；只有 complete
  房进入房级主指标。
- [ ] 5.2 在 complete 房报告中分层输出 transport/window/game 指标，增加
  `WINDOW_CONFIRM seq=0 / eligible windows` 和
  `duplicate_or_coalesced_state_demands / game`，同时输出各类完整性数量和
  物理尝试归一化分母。
- [ ] 5.3 同步 `README.md`、`HANDOFF.md`、验收/计划文档和运行说明：线上基线
  显式使用启发式 BOT、SSE+增量 `/state`、15/s；policy 只作专项对照。
- [ ] 5.4 在冻结代码后以标准 BOT 命令运行 3～5 个完整房，记录 checkpoint、
  rate、传输模式和完整性分类；partial/protocol_skipped 只保留诊断，不混入 A/B
  主汇总，不在本 change 中进行 rate/sleep/策略实验。
