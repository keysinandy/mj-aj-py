## ADDED Requirements

### Requirement: Belief-v2 只从公开历史和本家可见信息推断隐藏世界

Belief-v2 SHALL 以 `InformationHistory + PublicDecisionContext` 为唯一语义证据，维护对其他玩家暗手和墙的后验近似。它 MUST NOT 读取原始 Game 的其他玩家暗手、真实 wall order、未来事件或 oracle feature；相同公开历史在隐藏真值变化时 MUST 产生相同初始化/更新逻辑与指纹。

#### Scenario: 改变真实墙但公开历史不变
- **WHEN** 两个离线 Game 具有相同 hero hand、公开事件和公开状态，但真实其他玩家暗手/墙不同
- **THEN** BeliefProfile、history hash、初始粒子随机流和 posterior 更新规则相同，不因真实隐藏状态变化

### Requirement: PublicEvent 保留对行为后验有意义的顺序

系统 SHALL 保存顺序敏感的公开事件，至少覆盖 DISCARD、PASS、CHOW、PONG、KONG_OPEN、公开可见的 KONG_CLOSED/KONG_ADD、HU 和 round boundary。事件 MUST 记录 actor、phase、公开 payload、前置 context hash 和 provenance；不能仅通过最终 river/meld snapshot 重建并丢失 PASS/响应顺序。

#### Scenario: 相同最终牌河但 PASS 历史不同
- **WHEN** 两条历史最终 rivers/melds 相同，但其中一条在某个碰/吃窗口发生过公开 PASS
- **THEN** 两条 `history_hash` 不同，belief update 可以给出不同 posterior

### Requirement: 粒子初始化满足现有公开物料约束

BeliefState SHALL 通过现有 unseen 物料规则生成 `N` 个 hidden-world particles，满足逐牌四张守恒、每座暗牌数量、死墙/活墙长度和所有公开副露/牌河约束。初始化失败 MUST 返回 unsupported/error，不得裁剪牌数或制造补牌。

#### Scenario: 物料守恒
- **WHEN** 成功初始化任意 particle
- **THEN** 每种 tile 的 `visible + all_hidden + wall == 4`，各座暗牌数和 full wall 长度与 public context 一致

### Requirement: 公开动作使用概率 likelihood 更新粒子权重

对每个可建 actor-private view 的 particle，系统 SHALL 使用版本化 ActorPolicy 计算观察动作 likelihood 并更新 log-weight。规则非法的观察动作在该 particle 下 MUST 得到零 likelihood；合法动作 MAY 使用 epsilon smoothing，但 smoothing MUST NOT 复活非法 particle。

#### Scenario: 粒子中观察 PONG 不合法
- **WHEN** 真实公开事件为某座 PONG，但某 particle 中该座在动作前没有两张 pending tile
- **THEN** 该 particle 权重归零，不通过 epsilon smoothing 保留

#### Scenario: 合法但模型概率很低
- **WHEN** 观察动作在某 particle 中合法但 ActorPolicy 只给极低概率
- **THEN** 使用版本化 smoothing 后保留有限非零 likelihood，并在 profile 中记录 epsilon/temperature

### Requirement: 权重计算具有数值稳定性和确定性

系统 SHALL 使用 log-weight/log-sum-exp 或等价稳定方法归一化权重。相同 history、profile、seed 和 worker-independent sample ids MUST 产生相同 normalized weights、ESS、resampling 结果和 belief fingerprint。

#### Scenario: 长事件历史
- **WHEN** 连续处理大量低概率公开动作
- **THEN** posterior 不因浮点下溢全部变成零/NaN，结果仍可复现

### Requirement: ESS 触发版本化重采样

BeliefState SHALL 计算 `ESS = 1 / sum(w_i^2)`。当 ESS 低于 profile 阈值时，系统 SHALL 使用指定 deterministic resampler（首版 systematic-v1）重采样，随后恢复等权。threshold、resampler 和 seed MUST 进入 belief fingerprint。

#### Scenario: ESS 低于阈值
- **WHEN** `ESS < ess_ratio * particle_count`
- **THEN** 完成一次 deterministic resample，记录 resample_count/reason，新粒子仍满足 public material constraints

### Requirement: Posterior collapse 必须显式降级

若所有合法 particle likelihood 均为零或有效粒子不足，系统 SHALL 标记 `posterior_reset`/`belief_degraded`，并可从当前 public constraints 重新初始化 uniform particles。MUST NOT 用真实 hidden state 修复后验，也不能静默继续使用过期权重。

#### Scenario: 行为模型与观察冲突导致全零权重
- **WHEN** 一次事件更新后没有有效 posterior mass
- **THEN** 输出 reset/degraded 证据并按 profile 执行公开信息重初始化或策略 fallback

### Requirement: Belief 输出默认只暴露安全 marginals

运行日志/teacher metadata SHALL 允许输出 opponent tile marginals、live/dead wall marginals、ESS、entropy、particle count、reset/resample counters 和 fingerprints。默认在线/teacher artifact MUST NOT 保存 sampled hidden hands 或 wall sequence；安全本地调试若显式启用完整 particle dump，必须与可发布 artifact 分离。

#### Scenario: 生成线上解释日志
- **WHEN** BeliefState 为某次决策生成 explanation
- **THEN** 日志可以包含 `P(seat holds tile)` 等统计，但不包含任意 particle 的完整对手暗手或墙顺序

### Requirement: Belief-v2 需要独立校准证据

发布 belief-v2 前 SHALL 在隐藏真值仅用于评估的 held-out simulator worlds 上报告至少 opponent tile marginal Brier/log-loss、true-held coverage、live-wall calibration、ESS/reset/resample rate，并与 `uniform_unseen-v1` 做相同 split 比较。真实 hidden truth MUST NOT 参与 posterior 更新本身。

#### Scenario: belief-v2 没有优于 uniform baseline
- **WHEN** final-test 上主要 calibration/likelihood 指标没有稳定改善或出现严重退化
- **THEN** belief-v2 不得作为 search/online 默认 belief，保留 shadow/实验状态
