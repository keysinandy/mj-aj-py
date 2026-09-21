# Proposal

## Why

当前 legacy BOT 在最低向听候选中只比较当前一巡的 ukeire、局部牌型损失和喂牌风险，无法识别“保留的牌型价值被已有搭子覆盖”的情况。`20260920` 对局的 `3t` 与 `9b` 具有相同当前进张，但静态 shape cost 让 BOT 选择了 `3t`；加入一次未来摸牌后的最佳弃牌评估，可以在不引入完整搜索或对手模拟的情况下补足这一层判断。

## What Changes

- 在 legacy 弃牌评价中增加版本化的 V1 二阶前瞻：对当前候选弃牌后的手牌枚举下一张理论可见牌，允许摸牌后再选择一次最佳合法弃牌，并评价子状态的向听与 ukeire。
- 将二阶结果拆成可解释的 `future_improve`（下一摸后降低向听的加权机会）和 `future_ukeire`（下一摸后最佳站立牌面的加权进张）两个字段，不以单一 opaque 分数替代原始特征。
- 只对当前向听和当前 ukeire 达到保留门槛的候选执行二阶评估；使用统一节点/时间预算、有限缓存和事务式回退，未完成前瞻时保持原 legacy 排序。
- 调整 legacy 同层排序为：合法性/最低向听、财神保护、当前 ukeire、未来改良、未来 ukeire、牌型损失、喂牌风险和稳定 tile 顺序；牌型损失降为 tie-breaker。
- 继续使用本家可见牌、四家公开牌河/副露和 `4-visible` 未见权重；不读取对手暗手、真实墙序，不模拟吃碰、杠、对手动作或完整终局 EV。
- 扩展决策解释和回放记录，记录 evaluator/profile fingerprint、实际评价层级、预算/缓存/回退原因以及各候选的二阶特征；字段未计算时显式标记缺失。
- 保持 legacy 名称和默认入口不变，增加可配置的 V1 版本/开关与安全回退，待独立回归、性能和成对收益证据完成后再决定是否长期默认启用。

## Capabilities

### New Capabilities

无。本变更复用现有手牌评价与决策解释能力，不新增独立领域能力。

### Modified Capabilities

- `bot-hand-evaluation`: legacy 弃牌评价增加有限二阶改良特征、候选筛选、缓存/预算和排序契约。
- `bot-decision-explanations`: 决策日志和回放解释增加二阶候选特征、版本/层级/回退状态，并保持旧日志兼容。

## Impact

- 主要影响 `mj/bot.py` 的 legacy `choose_discard` 路径，以及待抽取的 `mj/eval.py`（或等价评价模块）和 `mj/shanten.py` 的纯函数/缓存接口。
- 需要补充 `tests/test_bot.py`、`tests/test_shanten.py` 或新的 evaluator 测试，覆盖 `3t`/`9b` 回归、visible 物料守恒、财神/抓打圈合法性、缓存隔离和预算回退。
- 需要更新 Recorder/回放解释的候选 JSON schema；不得把二阶估计写成真实胜率、完整 EV 或线上原始决策事实。
- 需要新增离线小规模基准和同种子 legacy 对照报告；本变更不包含训练、BC 接入、完整 rollout、规则改动或线上默认切换。
