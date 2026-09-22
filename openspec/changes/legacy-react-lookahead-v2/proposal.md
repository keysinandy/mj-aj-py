## Why

commit `5e0a405` 已把 legacy 吃/碰/杠从“有动作就做/固定 ukeire 小阈值”推进到可解释的第一版：

- CHOW/PONG：向听下降直接做；同向听只有爆头、财飘、听牌宽度、下一摸降向听能力显著改善才做；
- KONG：结构安全 → 牌效不降 → post-KONG 已听且存在公开活杠开张 → 才进入补牌 EV；
- `123333m` 暗杠 3m 等明显结构错误已经被固定牌例锁住。

当前剩余误差不再主要来自“明显错误动作”，而来自 **v1 只看当前站立牌面，尚未比较下一层牌效与副露的摸牌时机成本**：

1. 同样是 1 向听，`ukeire=20` 不代表一定优于 `ukeire=14`；后者摸入后可能形成更宽的一向听/听牌，而前者多数进张是“假好形”。
2. CHOW/PONG 会改变下一次本家摸牌的位置。上家打牌时 PASS 可以立刻摸下一张，而 CHOW 后通常要等一圈；当前 +4/+6、1.50x 只是在间接补偿这个 tempo 成本。
3. 当前 post-claim 的“最佳弃牌”仍主要按当前层 progress 选择，可能在 U1 就提前裁掉未来质量更好的弃牌。
4. KONG shape gate 目前只硬保护 shanten、普通 ukeire、已成爆头；仍可能损失财飘推进或爆头推进质量。
5. KONG 通过硬门后的 legacy EV 主要看 replacement draw 立即胡；非胡补牌后的继续听牌/下一摸价值没有进入同单位比较。
6. PONG 与 KONG_OPEN 同窗时仍不是完全同单位：PONG 主要是 shape progress，KONG 同时拥有 replacement score EV。

本变更不把 legacy 改造成完整搜索器，而是增加一个 **低成本、可回退、可解释的 Legacy Reaction V2**：继续用 v1 硬规则决定“动作是否值得考虑”，再用现有 weighted two-ply Rust 能力做 U2 veto/择优；tempo 采用离散 guard 而不是拍浮点权重；shape-v2 all-root 只做 shadow teacher 与校准，不直接进入线上默认。

## What Changes

- 冻结当前 `legacy-shape-progress-v1` 为 rollback profile；新增 `legacy-react-v2`：
  - 显式 `evaluator="legacy"` 保持 v1 行为；
  - 默认 `legacyV2` 的 reaction/KONG 路由到 v2-online；
  - `legacyV2-offline` 路由到 v2-offline，要求完整 U2，不允许静默退回 v1 标签。
- 从 `mj/bot.py` 抽离 reaction/KONG 评价到独立 legacy 模块，`bot.py` 只保留 routing 和少量通用入口。
- 在 `mj/legacy_eval.py` 增加可复用的 **standing frontier** API，直接复用现有 weighted two-ply Rust kernel，输出 `FutureEvaluation`（future improve / future ukeire / types / coverage / complete），不再为 reaction 另写 DFS。
- CHOW/PONG v2 采用两阶段：
  1. v1 Gate：降向听，或同向听下已有显著 shape progress；
  2. 对需要 U2 的同向听普通推进做 future veto：claim 的 U2 不得劣于 PASS；tempo 成本较高时至少一个核心未来指标必须严格更好。
- 向听严格下降的 CHOW/PONG 不被 U2 否决；U2 只用于多个同向听/同最低向听候选的弃牌选择和同向听普通推进 veto。
- 对“直接升级为爆头 ready / 明确财飘 ready”的特殊价值状态，generic U2 不得因普通 ukeire 指标不完整而误杀；只有同口径特殊 future 指标可比较时才参与 veto，否则保留 v1 结论。
- post-claim 最佳弃牌改为：所有最低向听候选先保留，再在共同完整 U2 层比较，最后才以 v1 shape cost / stable id 打破平手。
- 新增 reaction tempo 元数据：
  - `pass_draw_index=(hero-pending_owner)%4`（反应窗为 1..3）；
  - CHOW/PONG 后下一次本家摸牌按第 4 个未来活墙 draw 计；
  - `tempo_cost=4-pass_draw_index`；
  - 第一版不把 tempo 换算成浮点分数，仅用于决定 U2 guard 的严格程度。
- KONG shape-preserve gate 扩展到完整 progress：普通 ukeire、wait types、baotou ready、已知 baotou progression、piao progression 均不得被无解释地破坏。
- KONG score EV 增加 bounded continuation：replacement draw 不能立即胡时，选择最佳合法弃牌，并计入下一次本家摸牌的公开期望；与非杠 baseline 使用相同 continuation horizon，保持同单位。
- PONG 与 KONG_OPEN 同窗且双方都通过各自硬门时，启用少量 bounded slow-path：
  - shanten 更低者优先；
  - 同 shanten 且 KONG 牌型不劣时，用同一公开 score-unit continuation 比较 `Q_pong` 与 `Q_kong`；
  - exact tie 仍偏 PONG/PASS 的稳定保守次序，不因杠倍率自动抢占。
- 增加 shadow audit：本地 replay 同时记录 legacy-v1、legacy-react-v2、shape-v2 all-root teacher 的动作、U1/U2、tempo、Q 与 reason，用于后续阈值校准；teacher 不参与线上动作。
- 线上 v2 的 U2 是事务性的：预算/内核/coverage 不足时整层回退到 v1，不混用半套 U2；离线 profile 则 fail-loud。

### Out of Scope

- 不直接把 shape-v2/all-root 切成线上默认 evaluator。
- 不修改 Game 合法动作、响应顺序、死墙、抓打圈、财神/YCBK、倍率或结算规则。
- 不读取对手暗牌、真实墙序或任何非公开信息。
- 不允许 legacy 为番数主动增加向听；`claim.shanten > pass.shanten` 仍然硬拒绝。
- 不在本 change 内训练 BC/PPO/RL 或调整模型结构。
- 不引入“tempo × 某浮点权重”的总分公式；tempo 第一版只参与离散 guard。
- 不在本 change 内实现 opponent hidden-world claim/search；PASS 后其他响应者的完整行为仍由 shadow teacher 离线评估。
- 不把 U2 反过来作为“v1 原本 PASS，但 U2 看起来不错就新授权吃碰”的激进授权器；第一版只做 veto/择优。

## Capabilities

### New Capabilities

- `legacy-reaction-lookahead`: 可复用的 legacy standing U2 前瞻、reaction profile、tempo guard、事务性 fallback 与 shadow 诊断。
- `bot-kong-decision`: 将当前已实现的 KONG hard gate 纳入正式能力规范，并增加完整 shape preservation、bounded continuation 与 PONG/KONG_OPEN same-unit slow-path。

### Modified Capabilities

- `bot-react-decision`: legacy 从 shape-progress-v1 升级为可版本化 v1/v2；v2 在 v1 Gate 后增加 U2 veto/择优和 tempo guard，移除旧主 spec 中“legacy 固定 +2/+4”和“KONG_OPEN 整窗冻结”的过时语义。

## Impact

- **主要代码**：
  - `mj/legacy_eval.py`：新增 reusable standing-frontier API，复用 weighted Rust kernel；
  - 新增 `mj/legacy_react.py`：reaction profiles、U2/tempo、CHOW/PONG 决策；
  - 新增或拆分 `mj/legacy_kong.py`：KONG guards、continuation、PONG/KONG slow-path；
  - `mj/bot.py`：路由到 v1/v2 profile，减少 reaction/KONG 内联实现。
- **测试**：新增 v1 行为冻结、U2 veto、tempo 座位差异、transactional fallback、offline fail-loud、KONG 完整 shape preserve、KONG continuation、PONG/KONG same-unit 的固定牌例。
- **性能基线**：以 `5e0a405` 为冻结基线。实现前后同机同 Rust 内核交错跑 3×200 局；4-bots elapsed/games 中位退化必须 ≤15%。v2 同向听 U2 eligible 样本的完整/安全 partial 覆盖率必须 ≥90% 才允许默认开启。
- **下游**：默认 `legacyV2` 的 reaction 标签会变化；显式 `legacy` 保持当前 v1 rollback。训练用 `legacyV2-offline` 在 reaction 上不再静默退回 v1。
