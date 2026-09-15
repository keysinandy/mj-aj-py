## 1. P0 基线、范围和数据契约

- [x] 1.1 审核 `917e7f8`、`72a4f65`、现有 bot-shape change 与未提交工作，生成无秘密 baseline manifest、代码/规则/内核指纹和证据边界报告。
- [x] 1.2 重跑并保存当前 legacy/shape-v1 聚焦回归、全量 tests、Rust parity、200 局性能和已有 4096/线上结果；明确历史结果与本次新证据。
- [x] 1.3 预先冻结 train/validation/final-test 源局分组、16 个 seat/dealer 组合、you_cai_bi_kao 配置、随机种子、scope、过滤规则、样本扩展和统计 alpha。
- [x] 1.4 定义 profile/schema/version、reward、belief、continuation、kernel、scope、输入/输出和失败状态 manifest；加入 profile fingerprint 校验。

## 2. PublicDecisionContext 与全候选 frontier

- [x] 2.1 新增不可变 `PublicDecisionContext`，从 Game/Mirror/日志按字段来源投影本家可见状态、规则、进程、公开牌河/副露和完整性。
- [x] 2.2 实现 visible/hidden-count/live-wall/dead-wall 守恒、pending 转副露不重复计数、unknown/unsupported 拒绝和上下文输入 hash。
- [x] 2.3 建立独立完整世界构建接口；禁止复用 `Mirror.build_game()` 的零值他家暗手、占位墙和本家 react 队列作为 rollout 状态。
- [x] 2.4 实现 Python `discard_frontier` 参考，枚举所有不同合法舍牌并返回 shanten/U1/bitset/wait 分类；补齐 frozen/财神/吃碰后状态。
- [x] 2.5 实现 Rust batch frontier 与 Python 定向/随机差分；内核不可用时保持语义一致 Python 或完整安全回退并记录版本。
- [x] 2.6 将 v2 `discard` scope 接入 opt-in 入口，取消最低向听和财神 hard exclusion，但保留 legal_actions、HU/KONG 委托与整层回退。

## 3. ScoreValue 与 EV2

- [x] 3.1 新增 ScoreValue 适配器，对拍 `_can_hu`、`hand_multiplier`、`settle`、庄闲/base、四白板、爆头、有财必拷响和动作链。
- [x] 3.2 实现根动作转移和 EV1/EV2，所有未来合法弃牌全枚举；正确处理无放回 visible、四步轮回、死墙、抓打圈和 horizon tail=0。
- [x] 3.3 为 P2 模型加入完整输入/缺失状态/模型假设、EV 单位和 horizon 指纹；禁止把 EV1 与 EV2 重复相加。
- [x] 3.4 为 Q0/EV2 建立共享预算、有效上界证明、候选证书、缓存键和“最近完整层级”事务式回退。
- [x] 3.5 为 shape-v2 discard scope 增加可复算候选贡献、缺失字段和稳定 tie-break 解释，不触发 rollout 或额外平台请求。

## 4. BeliefSampler、完整模拟与 teacher

- [x] 4.1 实现基于 `context_hash + belief_version + seed + sample_id` 的均匀 unseen sampler，分配三家暗牌、完整墙并保留死墙。
- [x] 4.2 实现列全 Game 字段的 world builder，恢复四家视角、链/飘、吃额度、冻结、摸牌门禁、响应顺序和根合法集校验。
- [x] 4.3 实现固定 continuation policy；每个 actor 只获得自己的视角，禁止递归 teacher、未来墙和他家暗牌读取。
- [x] 4.4 实现共享世界的 paired rollout，终局 reward 使用本局 hero score；非法/异常/未终局整组失败且不以零分代替。
- [x] 4.5 实现 N0=32、batch=32、Nmax=512 的确定性序贯采样、有效同时区间、ambiguous/unsupported/failed 分类和断点续跑。
- [x] 4.6 生成包含 best/runner-up、EV/CI、配对差、样本数、win/mult/draw、停止/指纹的 teacher artifact，并完成小模型穷举对拍。

## 5. 校准、regret 和独立收益

- [ ] 5.1 在冻结切分上完成 I/EV2/B/C/硬门槛/阈值消融，拟合全局受约束线性模型并验证 LUT 稀疏桶回退。
- [ ] 5.2 冻结 shape-v2 profile，使用独立世界评估 legacy、shape-v1、shape-v2、teacher/actual 的 paired Q 和 regret，不截断负 regret。
- [ ] 5.3 运行至少 4096 对局对（8192 次），16 个 seat/dealer 组合均衡，输出按源局聚类 bootstrap/同时区间和覆盖率；不达标保持 legacy。
- [ ] 5.4 生成校准、验证、最终测试、teacher 和运行 manifest；检查规则、profile、kernel、策略或 scope 变化是否使证据失效。

## 6. 证据日志与 BC 契约

- [x] 6.1 扩展 Recorder/runner/logview 的实际 evaluation 字段、候选精简策略、输入 hash、层级/回退/预算和 counterfactual 关联；保持旧日志兼容。
- [x] 6.2 实现离线完整候选报告、regret、identity_unknown、server auto discard、strategy PASS 和提交阶段分离；不补造线上决策。
- [x] 6.3 扩展 BC/log2data 元信息和 teacher 标签字段，强制 `oracle=False`、legal mask、scope/置信度/来源隔离，保持实际 score 不被覆盖。
- [x] 6.4 增加隐藏信息、解释开关、候选重排、缓存冷热、并发日志、跨 gid 隔离及旧日志读取测试。

## 7. HU/财飘、KONG 和反应动作扩展

- [ ] 7.1 在普通舍牌 scope 通过后实现 hu-piao 根动作即时 HU vs 财飘继续价值，复用规则链/抓打圈并建立专用 fixture/teacher。
- [ ] 7.2 在 hu-piao 闸门通过后实现 all-root 暗杠/加杠补牌、墙尾限制和根动作同单位比较；保持合法动作权威。
- [ ] 7.3 为反应窗口恢复完整响应顺序和上下文适配，比较 PASS/CHOW/PONG/KONG_OPEN，未知窗口拒绝 teacher 并保留 legacy。
- [ ] 7.4 重基 `bot-react-decision` delta，逐项验证 shape-v1/legacy 与 v2 scope 的门槛、七对、visible 和性能边界。

## 8. 性能、线上验收与发布

- [ ] 8.1 在同机同内核交错三次 200 局压测，包含解释序列化，记录 p50/p95/p99/max、节点、内核、层级和回退率。
- [ ] 8.2 用实际十场并发调度验证完整决策 p95（弃牌≤20ms、反应≤10ms）及 elapsed/games ≤15% 增长；失败时优化后重跑受影响对拍。
- [ ] 8.3 为每个已校准 scope/profile 冻结运行 manifest，按固定 BOT、SSE+增量、15/s 在至少三个新房各十场运行。
- [ ] 8.4 逐窗审计 legal candidate、decision/action、deadline、身份、transport、server outcome、strategy PASS、未提交和代打；确认无 evaluator 导致窗口损失。
- [ ] 8.5 仅在对应 scope 的离线收益、regret、性能、BC/信息安全和线上闸门全部满足后切换默认；保留 legacy 开关，否则保持 opt-in。
- [x] 8.6 更新使用文档、profile/teacher/对手分布和证据索引；完成前不归档本变更或把未完成 P5–P7 标记为通过。
