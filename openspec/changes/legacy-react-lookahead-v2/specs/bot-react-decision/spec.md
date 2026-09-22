## MODIFIED Requirements

### Requirement: 吃/碰按“副露 + 最佳弃牌后站立牌面”完整评价

启发式 BOT 对合法 CHOW_LOW/MID/HIGH 和 PONG SHALL 以“副露完成 + 合法立即弃牌后的站立牌面”为评价对象。legacy v1 继续使用冻结的 shape-progress gate；legacy v2 MUST 在不放宽向听硬门和 v1 授权面的前提下，使用 `legacy-reaction-lookahead` 的 U2 作为同向听普通推进的 veto/择优层。

- claim shanten 低于 PASS 时 SHALL 直接通过，不得被 U2 否决；
- claim shanten 高于 PASS 时 MUST 拒绝；
- claim 与 PASS 同 shanten 时 MUST 先通过 v1 significant-progress gate；
- v1 原本未通过的同向听 claim，v2 第一版 MUST NOT 仅凭 U2 新授权；
- 需要 U2 的多个 post-claim 最低向听弃牌候选 MUST 一起进入共同 frontier，再选 best discard；
- KONG_OPEN MUST 由 `bot-kong-decision` 单独评价，不得冻结整个 PONG/KONG claim 窗口。

#### Scenario: 向听下降不被 U2 否决
- **WHEN** 某 PONG/CHOW + 最佳弃牌使 shanten 从 2 降到 1，但 U2 指标弱于 PASS
- **THEN** 动作仍通过第一硬门；U2 只可在同最低向听候选之间择优

#### Scenario: v1 PASS 不被 U2 反向授权
- **WHEN** claim 与 PASS 同 shanten，且 v1 的爆头/财飘/听牌/ukeire significant-progress 均未达标
- **THEN** v2 仍选择 PASS，不因 U2 某一指标较高而新授权 claim

#### Scenario: 最佳弃牌不能在 U1 提前裁掉
- **WHEN** 一个 PONG/CHOW 有多个同最低 shanten 的立即弃牌，其中 U1 最优弃牌与 U2 最优弃牌不同
- **THEN** v2 使用共同 standing frontier 的 U2 结果选择最终弃牌

### Requirement: 等向听时按版本化评价收益门槛执行

legacy v1 SHALL 保持 `legacy-shape-progress-v1` 的冻结门槛。legacy v2 对同向听 claim 先应用 v1 gate，再按 reason 类型决定是否进入 U2 veto。

对 `wait_expansion` / `ukeire_expansion`：

- `claim.future_improve_weight >= pass.future_improve_weight`；
- 若双方同处 Stage-B，`claim.future_ukeire_mean >= pass.future_ukeire_mean`；
- `tempo_cost>=2` 时，上述可比核心指标至少一项 MUST 严格优于 PASS；
- `tempo_cost==1` 时“不劣”即可；
- 层级不一致/coverage 不足时 online 回 v1，offline fail-loud。

对直接 `baotou_ready_upgrade` / `piao_ready_upgrade`，generic ordinary U2 MUST NOT 单独否决；对仅定量特殊推进而缺少同口径 special-U2 时保留 v1 并记录诊断。

#### Scenario: ukeire 看起来更大但 U2 更差
- **WHEN** PONG 与 PASS 同 shanten，PONG 已通过 v1 `ukeire_expansion`，但 `future_improve_weight` 低于 PASS
- **THEN** v2 veto PONG 并选择 PASS，reason 记录 `u2_future_worse`

#### Scenario: 高 tempo 成本要求严格未来优势
- **WHEN** CHOW 与 PASS 同 shanten、v1 已通过普通进张 Gate，tempo_cost=3，U2 两个核心指标仅与 PASS 相等
- **THEN** v2 选择 PASS，reason 记录 `tempo_no_strict_future_gain`

#### Scenario: 低 tempo 成本且 U2 不劣
- **WHEN** PONG 的 tempo_cost=1，v1 已通过普通进张 Gate，U2 核心指标均不低于 PASS
- **THEN** PONG 保留为有效候选

#### Scenario: 直接爆头升级不被普通 U2 误杀
- **WHEN** claim 与 PASS 同 shanten，claim 从非爆头升级为 baotou_ready，而普通 U2 的 future ukeire 不完整或略低
- **THEN** generic U2 不得单独 veto 该 claim；结果沿 v1 特殊价值语义并记录 special-U2 未比较

### Requirement: PASS 基准为反应时点站立暗牌原样评价

PASS SHALL 继续以反应时点 hero 站立暗牌评价，不把 pending tile 加入 hero hand。legacy v2 在需要 U2 时还 SHALL 计算 PASS 的 future layer，并记录 PASS 的 next-draw index。

PASS 的 U2 与 claim 的 U2 MUST 使用同一 public visible 和同一 future profile；不同层级结果不得混合。

#### Scenario: 上家弃牌时 PASS 拥有立即摸牌优势
- **WHEN** pending owner 是 hero 上家
- **THEN** PASS 的 `pass_draw_index=1`，同向听 CHOW/PONG 的 tempo guard 使用该值而不是固定 +4/+6 经验常量

### Requirement: 反应侧决策性能受闸门约束

legacy v1 的冻结路径 SHALL 保持低成本。legacy v2 的 U2 只在需要的同向听/多候选窗口触发，并受 profile 节点/时钟/coverage 约束。

默认启用 v2 前 MUST 满足：

- 相对 `5e0a405`，同机同 Rust 内核交错至少 3×200 局，4-bots elapsed/games 中位退化 ≤15%；
- eligible reaction 的 complete + safe-partial U2 coverage ≥90%；
- U2 额外评价 p95 ≤10ms；
- U2 超时/不完整必须显式回 v1，不延长平台 action deadline；
- 不允许新增 Python 全枚举热路径。

#### Scenario: online 预算耗尽
- **WHEN** U2 hard budget 触发
- **THEN** 本窗口按冻结 v1 完整结果返回，并记录 budget/coverage/fallback；不得等待第二次搜索

#### Scenario: 默认启用门禁未通过
- **WHEN** 吞吐、coverage 或延迟任一门禁失败
- **THEN** v2 可以保留为 opt-in profile，但 DEFAULT reaction routing 继续使用 v1
