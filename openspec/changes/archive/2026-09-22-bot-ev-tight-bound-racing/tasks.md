## 1. Reward envelope 基础模型

- [x] 1.1 在 `mj/decision/score_value.py` 定义版本化 `RewardEnvelope`/证书数据结构，区分 `fast_upper`、rollout 有符号范围、absolute bound、组件和 fallback 状态。
- [x] 1.2 实现根普通弃牌后的公开资源投影，正确应用 `ScoreValue.discard()` 的 chain/chain_piao 转移，并校验 candidate 与 context 合法集。
- [x] 1.3 实现 hero Fast EV 上界：杠槽/既有 pong、财神材料、活墙、七对/豪华组、4 白板、爆头和庄闲支付因子的安全组合。
- [x] 1.4 实现 rollout 终局范围：覆盖四家潜在 winner、hero 正收益和对手负支付，不读取隐藏牌身份或真实墙序。
- [x] 1.5 保留 `theoretical_reward_bound()` 作为显式兼容宽界；实现未知字段时的 `unknown`/`legacy_conservative_fallback` 分类和不安全时禁止剪枝。

## 2. Fast EV 上界接入

- [x] 2.1 为 `ProfileSpec` 增加 bound version、模式和兼容 override 字段，并让这些字段参与 profile fingerprint。
- [x] 2.2 在 `evaluate_discard_context()` 为每个候选缓存 envelope，使用 candidate-specific `fast_upper` 构造同层上界并输出完整证书。
- [x] 2.3 保持严格小于才剪枝、等于不剪枝、未知界不剪枝和完整 Q0 事务式回退；确保未改变合法候选集合与稳定 tie-break。
- [x] 2.4 更新 Fast EV explanation/report 的 bound、certificate、fallback 和 missing 字段，确保线上只记录实际计算内容。

## 3. Paired-delta racing 核心

- [x] 3.1 在 `mj/rollout/evaluator.py` 增加候选界映射和 pair interval helper，使用 `B_a + B_b` 覆盖 `R_a - R_b`，并为所有 pair/look 分配 alpha。
- [x] 3.2 将 teacher batch 循环改为 active candidates 共享 world 后再比较，使用 paired delta 的上置信界严格小于零作为淘汰条件。
- [x] 3.3 实现确定性 leader 选择、批次末集中淘汰、单 active action 结束和 `nmax` 仍有竞争者时的 `ambiguous` 状态。
- [x] 3.4 保留 candidate marginal EV/CI 作为诊断输出，但移除其作为 paired racing 停止依据的作用，并标注统计方法/stop reason。
- [x] 3.5 记录每次淘汰的 leader、pair、look、alpha、每侧 bound、pair bound、delta interval、有效 n 和证书。

## 4. 失败、稀疏 rows 与续跑

- [x] 4.1 扩展 teacher row 保存 active snapshot/evaluated actions，使候选淘汰后后续 batch 可以安全缺席且不补写零 reward。
- [x] 4.2 保持 active group 任一 rollout 失败时该共享 row 的 paired statistics 无效，保留失败原因/分母，并隔离已淘汰候选对后续 group 的影响。
- [x] 4.3 扩展 teacher config/resume fingerprint，保存 active actions、elimination records、pair certificates、bound、looks、alpha 和 stop state。
- [x] 4.4 实现相同配置续跑的 sample 去重、active 状态恢复和与一次性执行一致的停止结论；拒绝旧 dense/缺版本 resume 混入新算法。
- [x] 4.5 验证 action 输入重排、缓存/worker 调度和中断位置不改变 sample id、world fingerprint、paired delta 或最终分类。

## 5. 规则安全性与统计契约测试

- [x] 5.1 为普通弃牌、财飘、断链、既有 pong 加杠、活墙边界、七对/豪华、4 白板、爆头、庄闲支付编写 envelope 单元和定向 fixture。
- [x] 5.2 在可枚举小墙/小状态模型上穷举合法终局，证明每个 Fast upper、rollout range 和 pair bound 覆盖实际 reward；验证稀有高番未被观测最大值替代。
- [x] 5.3 用合成共享样本验证 paired delta racing 能淘汰明显落后候选，并验证只看 marginal CI 不会重新触发停止。
- [x] 5.4 测试多候选、多 looks 的 alpha/interval metadata、严格 `high < 0` 条件、相等边界和 `nmax` ambiguous 分类。
- [x] 5.5 测试 active rollout 失败、部分成功、淘汰后失败、无有效 paired 样本和不允许的 fallback，确保没有零分/legacy/截断收益混入。
- [x] 5.6 测试 sparse resume、重复 row、旧 fingerprint、候选顺序和固定 seed 的确定性；验证隐藏手牌/墙序/token 不进入输出。

## 6. 证据、性能与验证

- [x] 6.1 更新 teacher/profile artifact schema、manifest、fingerprint 和解释压缩逻辑，明确旧 artifact 只读且不可与新版本混合续跑/拟合。
- [x] 6.2 运行相关 unit/contract tests、Rust/Python 既有 frontier parity 和全量测试，记录 bound 版本与测试环境。
- [x] 6.3 在同机基准中比较旧宽界与新 envelope 的 bound 分布、剪枝率、active candidates、rollout 调用数、有效样本数和耗时；不以该基准替代收益闸门。
- [x] 6.4 运行 `OPENSPEC_TELEMETRY=0 openspec validate bot-ev-tight-bound-racing --strict --no-interactive`、`git diff --check`，并检查变更只涉及本 change 及明确的测试/证据文件。
