# bot-ev-discard 使用说明

`shape-v2` 默认是保持关闭的 `discard`-scope opt-in 评价器。它枚举所有合法
普通舍牌，使用公开信息、规则计分适配器和最多两次本家未来摸牌的 EV2 特征；
默认入口中的 HU/财飘、KONG 和反应窗口仍由 legacy 委托路径处理。显式的
`hu-piao`/`all-root` profile 另有公开 HU/财飘、KONG 和完整响应游标 fast helper，
并配有专用 public fixture/teacher 与反应压测探针；独立收益、性能和线上发布
闸门仍未全部满足，不能当作默认全动作策略。`all-root` 工厂 profile 默认用一
次未来本家摸牌的反应层，若要做更深离线比较可显式提高 `horizon`。
其中 v33 杠后补牌的暗杠/补杠会在公开信息下比较下一张补牌的积分期望，
shape-v2 本身仍只评价普通舍牌，不把该策略升级宣称为 EV2 全动作评价。

shape-v2 的默认内部评价预算为 36ms；这是评价器预算，不改变平台动作截止、
SSE/HTTP 传输超时或 shape-v1 的预算。完整决策耗时仍须按实际调用链单独验收。

本地成对评估以本家每局 `Game.scores[seat]` 的净积分增量为主指标：
`shape_score.mean`/`legacy_score.mean` 是平均每局积分，`score_delta.mean`
是同种子下 shape 减 legacy 的配对差；胜率只作辅助诊断。示例：

```shell
python3 scripts/bot_shape_eval.py --games 4096 --evaluator shape-v1 \
  --output /tmp/bot-shape-score.json
```

EV2 的完整离线积分 smoke 使用独立的大预算 profile；它只用于本地诊断，
不会切换默认策略：

```shell
PYTHONPATH=. python3 scripts/bot_ev_full_eval.py --games 16 \
  --node-budget 10000000 --time-budget-ms 30000 \
  --output /tmp/bot-ev-full-score.json
```

如果需要同时跑 legacy 对照积分，可使用同一离线大预算：

```shell
PYTHONPATH=. python3 scripts/bot_ev_score_eval.py --games 16 \
  --full-ev2 --output /tmp/bot-ev-paired-full-score.json
```

校准必须显式绑定冻结的 train/validation/final-test manifest；不满足契约时
可以用 `--require-contract` 让命令失败：

```shell
python3 scripts/bot_ev_calibrate.py --input rows.jsonl \
  --split-manifest split.json --profile-json profile.json \
  --require-all-splits --require-contract \
  --manifest-output /tmp/bot-ev-evidence-manifest.json \
  --output /tmp/bot-ev-calibration.json
python3 scripts/bot_ev_regret.py q-regret.jsonl \
  --require-independent-worlds \
  --output /tmp/bot-ev-q-regret.json
```

同机性能验收使用交错调度；正式门禁固定为每个 profile 三轮、每轮 200 局。
输出同时保留解释序列化、内核、层级、回退率和弃牌/反应分位数：

```shell
python3 scripts/bot_shape_perf.py --interleaved \
  --games 200 --repetitions 3 \
  --evaluators shape-v1,shape-v2 \
  --output /tmp/bot-ev-interleaved-perf.json
```

该报告只表示本机离线计时；如果 shape-v2 主要回退到 Q0，
`performance_gate_passed` 会保持 false，不能用回退路径的吞吐替代完整 EV2
覆盖。2026-09-17 的 200 局×3 轮实测中，shape-v2 相对 shape-v1
总耗时增加 182.85%，弃牌 p95 中位约 69.14ms，弃牌回退率约 97.41%，
性能门禁失败，继续 offline-only，不得切换默认。校准证据需要额外使用
`--strict-evidence` 检查 split/profile/kernel 指纹自洽。

平台镜像的 `hand_counts` 修复已单独落地：可信快照用于公开四家张数，
不可确认的事件流标为 unknown 并以 `context_material_unknown` 受控回退
legacy；该修复不改变原生 Game/GEN0 训练路径。线上 canary 证据仍须同时
满足逐窗、对账和性能门禁，不能以一次无非法对账房间替代完整发布条件。
详见 `openspec/changes/shape-v2-platform-material-context/`。

HU/财飘、KONG 和反应根动作使用独立的 public fixture/teacher 与反应窗口
scope 探针：

```shell
PYTHONPATH=. python3 scripts/bot_ev_root_teacher.py --n0 32 --batch 32 --nmax 512
PYTHONPATH=. python3 scripts/bot_react_perf.py --repetitions 3 --concurrent-games 10
```

反应报告必须同时查看 `level`、`fallback_rate`、`nodes`、`kernel_calls` 和
`p50/p95/p99/max`；legacy 委托或不完整 v2 不能被当成性能通过。

`bot_ev_score_eval.py` 和 `bot_ev_regret.py` 产生的比较均带有
`counterfactual_evaluation=true`、`oracle=false`，不能当作线上实际动作证据。
只有 4096 对局、独立世界、性能和线上逐窗门禁全部通过，才可以改变
`legacy` 默认策略。

积分比较应使用相同 seed、seat、dealer 和规则配置；开启
`--you-cai-bi-kao` 时应作为独立规则分组报告，不能与关闭配置混合。

```python
from mj.bot import choose_action

action, evaluation = choose_action(
    game, seat, evaluator="shape-v2", return_evaluation=True)
```

评价记录包含 profile/context/kernel 指纹、实际层级、候选总数、节点/耗时、
缺失和回退原因。Q0 完整而 EV2 未完成时，所有候选统一使用 Q0；未计算的
EV 不写成零。线上解释只保留选中项、legacy 最优项和至多三个其他候选，
不会触发第二次搜索或额外平台请求。

rollout teacher 只允许离线运行：

```shell
python3 scripts/bot_ev_teacher.py --seed 242048 --n0 32 --batch 32 --nmax 512 \
  --output /tmp/teacher.json
python3 scripts/bot_ev_report.py local/games/20260915/bot_GID.jsonl
```

teacher 估计的是固定公开上下文、均匀 unseen belief 和冻结 continuation 下的
`Q^pi(s,a)`，不是真实墙最优。失败世界、歧义、identity_unknown、服务端代打
和实际 POST 结果分层保留；反事实结果不会创建线上 decision/action，也不会
升级窗口授权。BC CLI 默认写入版本化元数据；需要兼容旧六数组布局时显式使用
`--legacy-layout`。数据固定 `oracle=False`，teacher EV/置信度使用独立字段，
实际终局 score 不被覆盖。

发布前必须使用 OpenSpec 中冻结的切分和 4096 对局对门槛，并重新验证规则、
profile、内核、scope 和线上窗口证据；在这些门槛全部通过前，legacy 仍是
默认策略。
