## Why

`docs/ev.md` 提出将牌效率评价升级为公开信息下的积分期望评价。当前 `917e7f8` 的 shape-v1 仍先过滤最低向听、硬保护财神，并以胡牌概率 H2 代替积分价值；需要建立可解释的在线近似器和独立的离线 rollout teacher，逐步覆盖普通舍牌、HU/财飘和 KONG。

## What Changes

- 新增显式选择的 `shape-v2` 在线评价器：所有合法舍牌进入完整候选层，向听和财神成为价值特征；Python 参考实现与 Rust 批量 frontier 对拍。
- 新增不可变本家可见决策上下文和计分适配，使用现有规则门禁、倍率和四家结算计算有限两次未来自摸的 EV2；明确其与整局积分 EV 的区别。
- 新增 `rollout-v1` 离线 teacher：从公开信息采样隐藏世界，共享随机世界进行候选成对比较，以完整局末积分为收益，输出不确定性、歧义和 regret 数据。
- 用独立 teacher 数据校准版本化 LUT/受约束线性模型，保留全候选完整层级回退，并记录实际运行层级、候选贡献和模型假设。
- 按 P0–P7 分阶段交付基线验收、全舍牌 frontier、EV2、teacher、校准发布、HU/财飘、KONG/反应动作和 BC 数据接入；每次扩大动作范围重新验收。
- 将既有反应决策规范限定到适用 evaluator/action scope，保留 legacy 和 shape-v1 的普通舍牌/反应语义，并把 v33 摸后暗杠/补杠策略作为单独版本化的委托路径；上线显式 opt-in，发布闸门通过后才具备调整默认值的条件。

## Capabilities

### New Capabilities

- `public-decision-context`: 本家可见输入、字段来源和完整性、计数守恒、模拟所需公共状态及不可读隐藏信息的边界。
- `bot-ev-decision`: 全合法候选、计分型 EV2、积分模型、预算与完整层级回退，以及逐阶段的根动作评价。
- `rollout-ev-teacher`: belief sampling、规则模拟、固定后续策略、共享世界、序贯比较和可复现 teacher 标签。
- `ev-policy-calibration`: 数据隔离、模型拟合、独立收益/性能/线上验收、版本发布和 BC 蒸馏数据契约。
- `ev-decision-evidence`: 在线解释、离线候选比较、regret 和现有决策/窗口证据的兼容关联。

### Modified Capabilities

- `bot-react-decision`: 为现有向听门槛、财神保护和 HU/财飘/KONG 委托行为增加明确的版本与阶段适用范围；完整动作阶段使用同单位积分 EV 比较。

## Impact

- 预计新增 `mj/decision/`、`mj/rollout/`；接入 `mj/bot.py`、`mj/shanten.py`、`rust/src/lib.rs`，复用 `mj/game.py`、`mj/win.py`、`mj/scoring.py`。
- `Mirror.build_game()` 不是完整模拟快照。需要单独的公开上下文投影和离线世界构建器，不能复制其零值他家暗手、占位墙和单座位反应队列用于 rollout。
- 扩展离线评估/压测入口、两个平台 runner、Recorder/logview 和 BC 数据元信息。复盘 HTML 通过可选数据接口衔接 `mahjong-replay-debugger`，不成为 EV 核心依赖。
- 与 `bot-shape-aware-evaluation` 共享现有基础实现但独立管理交付；其未完成的发布任务和本变更的发布任务分别保留。归档前需协调双方对 `bot-react-decision` 的 delta。
- 本次产物仅为设计与规范；不修改应用代码，不执行训练、在线对局、默认切换或既有 change 的归档。
