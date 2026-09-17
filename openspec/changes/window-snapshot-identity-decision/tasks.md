## 1. Contract

- [x] 1.1 Proposal、design 与 spec delta 已完成；决策/证据拆分已随
      2026-09-17 的线上证据成文。

## 2. Implementation

- [x] 2.1 `mj/platform/bot_client.py`：在 `_resolve_window_confirm` 中，
      当解析结果为 `UNKNOWN` 且快照是新鲜权威的 pending 观测时，返回弱键
      确认，让 pending 动作路径去决策而不是停在 PENDING。
      （`_weak_key_open_observation` + `weak_key_open` 记录；期望身份
      本身必须是 legacy——快照无法复现的 authoritative 期望身份保持
      PENDING。配套改动 `_act_window`：快照授权门接受 legacy 快照首见
      窗口，否则碰窗会在每次拉取上重新 raise 一个全新确认、永远到不了
      策略。）
- [x] 2.2 截止驱动预算：当剩余窗口时间只够 decide + POST 余量时，停止
      调度 WINDOW_CONFIRM 拉取；届时在弱键下决策。
      （`_window_confirm_retry_expired` 内的 `_confirm_pull_budget_expired`；
      同相位快照截止收紧上界，跨相位快照回退到确认调度截止。）
- [x] 2.3 epoch 内弱键账目：在快照 epoch 内按
      `(round_id, discard_owner, tile, 牌河尾位置)` 去重提交；跨重锚仅
      保守携带（round/owner/tile 匹配 + 牌河长度一致）。
      （legacy fallback 现为 `(len(discards[owner]), 副露计数)`——副露
      元组防止认领弹牌河后同牌两窗撞到同一个弱键；`_weak_key_still_pinned`
      把关携带。）
- [x] 2.4 在 decision/action 与 `claim_miss` 上记录决策键的身份状态，
      使弱键结果保持可区分。
      （identity_status 原本就随键贯穿 decision/action/claim_miss；新增
      `window_confirm_weak_open` / `weak_key_decisions` 计数与
      `weak_key_open` 确认 reason。）

## 3. Tests

- [x] 3.1 扩展 `tests/test_window_identity_protocol.py`：快照首见未解析
      窗口决策并提交（fixture 组的 chi 用例）；弱键在同 epoch 同牌重弃下
      不撞键；弱键提交 409 后恢复且不重发。
      （另加快照首见 peng 用例：`_act_window` 弱授权；
      `test_window_confirmation_distinguishes_unknown_identity_from_mismatch`
      中一条既有断言编码的是被本变更反转的"legacy 保持 pending"规则，已
      更新为弱键确认结果——它守护的场景，authoritative 期望 vs 未知实际，
      行为不变。）
- [x] 3.2 预算测试：临近截止的未解析窗口停止确认并决策；连决策余量都
      没有时仍落观测预算记录。
- [x] 3.3 回归：既有窗口套件（确认、验收、stale trigger）不变通过。

## 4. Validation

- [x] 4.1 本地 fixture 组 + 全量窗口测试套件绿。
      （`pytest tests/`：663 passed / 1 skipped，含新增 5 例弱键决策测试。）
- [x] 4.2 一个线上测试房（4 令牌，legacy，16/s），前后对比
      `legacy_unresolved` miss 计数，并把新的弱键决策结果记入证据
      artifact。
      （新房 `t_772639fd4c27` vs 旧房 `t_b6a783a7fbb0`，各 4 令牌×10 局：
      `identity_unknown` 59→0、预算耗尽 7→0、legacy miss 7→0；唯一出现的
      弱键窗口端到端 POST 成功并被服务端回声确认。详见
      `artifacts/online_validation_20260917.md`。）
