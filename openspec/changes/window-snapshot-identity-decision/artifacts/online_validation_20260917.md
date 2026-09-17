# 弱键决策线上验证(2026-09-17,任务 4.2)

对比口径:同日测试房,4 令牌 × 10 局,启发式 BOT(legacy evaluator),
16/s /state 主动限速,git 工作区(弱键实现 + `tests/test_window_identity_protocol.py` 扩展)。

- **旧房 `t_b6a783a7fbb0`**(旧代码,基线):`local/games/20260917/bot_t_b6a783a7fbb0*`
- **新房 `t_772639fd4c27`**(弱键代码):`local/games/20260917/*_t_772639fd4c27*`

## 前后对比

| 指标 | 旧房(旧代码) | 新房(弱键代码) |
| --- | ---: | ---: |
| `identity_unknown` 确认拉取 | 59 | **0** |
| `identity_confirmation_budget_exhausted` | 7 | **0** |
| legacy `server_timeout_chi` miss(策略从未被调用) | 7 | **0** |
| `identity_status=legacy_unresolved` TERMINAL 窗口 | 9 | **0** |
| 弱键授权(`weak authorization`) | — | 1 |
| 弱键决策(`weak_key_decisions`) | — | 1 |
| 弱键提交被拒(409 兜底触发) | — | 0 |
| peng 409 竞速(authoritative 身份,既有竞速) | 10 | 9 |
| 被拒 POST 总数(含 draw/settled 陈旧) | 13 | 14 |

## 弱键窗口端到端案例

`gid t_772639fd4c27_r1_b5_t0`(worker 青龙,汇总 `weak_key_decisions=1`;
日志文件 `白虎_..._b5`,文件名来自先到的 meta,四个 worker 共写同一局
文件):response_peng 快照首见、无协议身份(`identity_origin=legacy_snapshot`、
`first_seen_via=unknown`),弱键 `(round, owner, tile, 牌河尾位置)` 授权后
以剩余 1106ms 决策 peng,POST 以 1062ms 余量发出 → `OK`/`SUCCESS`,
服务端回声 `peng seat=2 tile=发`。旧代码下该窗口会进入 legacy 确认循环,
按基线模式大概率以 `identity_confirmation_budget_exhausted` +
`server_timeout_*` miss 收场(策略从未被调用)。

## 口径备注

- 新房 `gaps=0`,本批自然出现的快照首见窗口很少(仅 1 个),弱键样本量
  为 1——这是端到端正确性证明,不是体量证明;legacy miss 7→0 的对比
  仍然成立,但部分来自本批窗口出现频率低。
- 新房的 peng 409 与旧房同模式(authoritative 身份、决策完成时仍有
  100-500ms 余量、三家 timeout 同秒到达、POST 被服务端以
  `INVALID_ACTION` 拒),是既有的"服务端提前关窗/固定走满假设偏乐观"
  竞速,与弱键路径无关(9 例全部 authoritative;弱键 0 拒绝)。
  被拒 POST 按 worker 汇总分布为青龙 7 / 朱雀 4 / 玄武 3 / 白虎 0,
  worker 间延迟方差所致。
- JSONL 文件按 gid 命名(四个 worker 共写同一局文件,文件名取自先到的
  meta),不能按文件名归因 worker;per-worker 结论以控制台汇总为准。
- 本批其余损失与代打:14 条 claim_miss 全部 response_peng 且牌均无人
  认领(8 POST 被拒 + 3 相位迁移未及决策 + 2 碰窗超时);服务器弃牌窗
  代打 9 次/40 局;`hu_failed` 0、抓打圈强制弃牌 0、镜像漂移 0、
  决策异常 0。

## 附:线上 match 房复测(`a_3d2568cb2673`,legacy,弱键代码)

自由对战 10 局(单全局令牌,默认 16/s + 长轮询):弃牌代打 **0**、
被拒 POST **0**、`identity_unknown` 确认循环 **0**、claim_miss 仅 **1 条**
(b6 response_chi `server_timeout_chi`:弃牌事件观测迟到 1.95s,吃窗只剩
~50ms,authoritative 身份——观测迟到/停摆家族,与 identity 无关)。
对照旧代码 legacy 基线房(`a_b477ae66dabe`,4 条 claim_miss)。
弱键路径第二个线上端到端实证:b9 response_peng 快照首见、无协议身份,
弱键授权后 847ms 余量决策、846ms 余量 POST → `OK`/`SUCCESS`。
