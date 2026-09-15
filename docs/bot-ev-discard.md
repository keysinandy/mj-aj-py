# bot-ev-discard 使用说明

`shape-v2` 是当前默认保持关闭的 opt-in 评价器。它只在 `discard` scope
中枚举所有合法的普通舍牌，使用公开信息、规则计分适配器和最多两次本家
未来摸牌的 EV2 特征；HU/财飘、KONG 和反应窗口记录为委托并沿用冻结策略。

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
