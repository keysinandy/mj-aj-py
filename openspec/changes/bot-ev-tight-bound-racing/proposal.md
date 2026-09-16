## Why

shape-v2 与 rollout-v1 目前共用固定的 `24 × base × 2^72` 理论收益界，导致 Fast EV 的上界几乎无法剪枝，也使 teacher 的有界置信区间过宽。teacher 虽然已经让候选共享随机世界，但只在最终 best/runner-up 上计算 paired delta，序贯停止仍依赖 marginal CI，未充分利用 Common Random Numbers。

现在需要把规则可证明的局面资源约束和成对差值统计接入同一条版本化契约，在不改变麻将规则、动作合法性、平台传输或默认策略的前提下，同时降低线上搜索成本和离线 teacher 样本成本。

## What Changes

- 新增局面/根候选相关的 reward envelope，按 Fast EV 与完整 rollout 的不同收益支持范围分别提供安全上界或有符号范围。
- 用当前 root action 后的 chain、chain_piao、杠槽、现有碰牌、财神材料、七对/豪华潜力、4 白板、爆头和庄闲结算因子推导可审计的规则上界；对 rollout 负收益覆盖四家潜在获胜者，不读取隐藏牌身份或真实墙序。
- 将 context/candidate bound、证书明细、bound version 纳入 Fast EV/profile 和 teacher 配置指纹；保留显式 scalar bound override 作为兼容测试与诊断入口。
- **BREAKING** 将 paired teacher 的停止判断改为 active candidates 的 paired-delta racing：当候选相对当前 leader 的 delta 上置信界严格小于零时淘汰，并记录淘汰证书。
- 保留 marginal EV/CI 作为报告字段，但不再用它作为 paired racing 的置信停止依据；继续使用预分配 alpha、固定 batch barrier 和确定性 sample id。
- 扩展 teacher 的 rows、resume state 和结果元数据，支持淘汰候选在后续 batch 缺席，同时保证重排、续跑和固定配置下的停止结论可复现。
- 增加边界、失败、候选淘汰、成对区间、续跑、证书安全性和 bound 覆盖率测试，并刷新受影响的 smoke/manifest 证据。

## Capabilities

### New Capabilities

- `bot-ev-reward-bound`: 为 Fast EV 与离线 rollout 提供按局面/候选、带版本和证明明细的规则收益 envelope。
- `rollout-paired-racing`: 在共享可能世界上执行 active-candidate paired delta 统计、序贯淘汰、置信停止和可复现续跑。

### Modified Capabilities

- `bot-decision-explanations`: 解释与离线 teacher 证据增加 reward bound、pairing/racing 状态、淘汰证书和稀疏样本语义，同时保持隐藏信息与线上动作关联边界不变。

## Impact

- 主要影响 `mj/decision/score_value.py`、`mj/decision/fast_ev.py`、`mj/decision/profile.py`、`mj/rollout/evaluator.py` 及相关 contract tests。
- 需要更新 teacher artifact schema/manifest、profile fingerprint、运行统计和 OpenSpec 证据；旧算法的未完成 resume state 不得与新 bound/racing 配置混用，旧结果仍作为历史证据保留。
- `bot-ev-discard` 的现有 PublicDecisionContext、ScoreValue、全候选 frontier、失败不补零和整层回退契约是本变更的前置边界；本变更不重新实现这些能力。
- 不修改 `mj/game.py` 的规则与结算语义，不改变合法动作、StateDemand/Throttle、平台 API、默认 evaluator 或线上重试行为。
