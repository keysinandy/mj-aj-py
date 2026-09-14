可以。下面我按你当前平台已有的 `StateDemand / StateThrottle / WINDOW_CONFIRM / SSE_DELTA / RESYNC / WindowId` 语义，整理成一份可以直接实施的 **OpenSpec 风格契约文档**。

这版的核心不是增加请求，而是：

> **在 15 req/s 总预算不变的前提下，通过提前请求、请求合并、配额预留和 slack-aware 调度，让 WINDOW_CONFIRM 更早、更稳定地拿到 `/state`。**

# Deadline-Aware State Request Scheduling Specification

## 0. 文档状态

```text
Change Name:
deadline-aware-state-request-scheduling

Status:
PROPOSED

Scope:
client platform only

Affected Components:
- StateDemand
- StateThrottle / global state scheduler
- BotClient WINDOW_CONFIRM lifecycle
- /state transport admission
- acceptance diagnostics

Explicitly Out of Scope:
- BOT strategy
- game rules
- WindowId authoritative identity rules
- action POST retry
- action submit margin tuning
- server implementation
- gateway implementation
- protocol phase generation
- state-rate > 15/s
- speculative action authorization
```

---

# 1. 背景

当前线上回放表明：

```text
C1_CONFIRM_NOT_CREATED              = 0
C2_CONFIRM_NOT_DISPATCHED           = 0
C3_CONFIRM_QUEUE_LATE               = 0
C4_CONFIRM_HTTP_LATE               > 0
C5_TIMELY_RESPONSE_NOT_AUTHORIZED   = 0
```

因此当前主要问题不是：

```text
StateDemand 没有创建
请求没有进入调度器
请求在 throttle 中等待过久
授权状态机本身漏判
```

主要风险集中在：

```text
WINDOW_CONFIRM 已经发出，
但 authoritative /state response
在客户端观察到时已经过晚。
```

同时平台存在一个硬约束：

```text
state request rate <= 15 requests / second
```

因此不能简单通过：

```text
hedged requests
aggressive polling
parallel duplicate GET
```

来降低 HTTP tail latency。

否则可能把当前：

```text
C3_QUEUE_LATE = 0
```

重新变成：

```text
C3_QUEUE_LATE > 0
```

或者增加：

```text
429
retry
backoff
quota pressure
```

---

# 2. 目标

本 change 的目标是在：

```text
physical /state request rate <= 15/s
```

始终成立的前提下，提高 WINDOW_CONFIRM 获得及时 authoritative snapshot 的概率。

主要方法：

```text
1. Adaptive Early WINDOW_CONFIRM
2. Request Coalescing
3. Request Substitution
4. Reserved Capacity with Borrowing
5. Minimum Slack Scheduling
6. Deadline-Aware Successor
```

本 change 默认：

```text
NO HEDGED REQUEST
```

一个逻辑窗口确认不得因为本 change 同时产生两条竞争中的 `/state` GET。

---

# 3. 非目标

本 change MUST NOT：

```text
提高全局 state-rate
绕过 StateThrottle
改变 BOT 决策策略
改变吃碰杠合法性
根据本地推测授权动作
根据粗略 deadline 提交动作
自动扩大 submit margin
自动缩短或增加 action retry
修改 WindowId 定义
引入 heuristic WindowId fallback
改变 409 uncertain-action 安全规则
```

尤其：

```text
WINDOW_CONFIRM priority
```

只能改变：

```text
/state 请求何时发出
/state 请求之间如何竞争有限 quota
```

不能改变：

```text
动作是否具有提交授权
```

---

# 4. 核心安全不变量

## 4.1 全局请求速率

任意时间：

```text
physical_state_requests <= configured StateThrottle limit
```

当前默认：

```text
15 req/s
```

任何 WINDOW_CONFIRM 优化都不能通过：

```text
bypass throttle
extra unaccounted request
shadow connection
duplicate GET outside scheduler
```

突破该限制。

---

## 4.2 Action safety 不变

WINDOW_CONFIRM 请求更早返回，并不代表动作自动授权。

动作授权仍必须满足：

```text
authoritative WindowId matches
AND authoritative phase matches expected phase
AND responding seat matches
AND authoritative exact deadline exists
AND exact deadline has not expired
AND legal action set contains requested non-PASS action
```

没有 authoritative snapshot：

```text
NO ACTION AUTHORIZATION
```

---

## 4.3 POST 不允许 hedge

以下行为永久禁止：

```text
duplicate action POST
hedged action POST
retry old action after uncertain result
replay old action after 409
```

本 Spec 所有并发、调度和重试规则只适用于：

```text
GET /state
```

---

# 5. 请求类型

平台继续支持以下 StateDemand reason：

```text
WINDOW_CONFIRM
RESYNC
SSE_DELTA
```

优先级：

```text
WINDOW_CONFIRM > RESYNC > SSE_DELTA
```

该优先级表示：

```text
quota admission priority
```

而不是：

```text
protocol truth priority
```

---

# 6. Logical State Demand 与 Physical Request 分离

必须严格区分：

```text
Logical Demand
Physical Request
```

例如：

```text
Demand A:
reason = SSE_DELTA

Demand B:
reason = WINDOW_CONFIRM
```

可以最终由：

```text
one physical /state request
```

同时满足。

因此：

```text
logical demand count
!=
physical request count
```

---

# 7. StateDemand 聚合模型

每个 gid 继续维护独立 StateDemand。

推荐状态：

```text
StateDemand {
    gid
    reasons: Set[Reason]
    generations
    desired_mode
    source_seq
    scheduler_deadline
    confirmation_budget_deadline
    window_attempt_key?
    created_at
}
```

其中：

```text
desired_mode:
DELTA
FULL
```

规则：

```text
WINDOW_CONFIRM => FULL
RESYNC         => FULL
SSE_DELTA      => DELTA
```

聚合规则：

```text
FULL dominates DELTA
```

因此：

```text
SSE_DELTA + WINDOW_CONFIRM
=> FULL
```

---

# 8. Request Coalescing

## 8.1 同 gid 合并

如果 gid 已经存在未发出的 StateDemand：

```text
pending:
    reason = SSE_DELTA
    mode = DELTA
```

此时产生：

```text
WINDOW_CONFIRM
```

则 MUST NOT 创建两条待发物理请求。

必须升级原 demand：

```text
reasons:
    {SSE_DELTA, WINDOW_CONFIRM}

mode:
    FULL

priority:
    WINDOW_CONFIRM

seq:
    0
```

即：

```text
GET /state?seq=<delta>
```

应被替换为：

```text
GET /state?seq=0
```

---

## 8.2 RESYNC 合并

若已有：

```text
RESYNC FULL
```

之后又产生：

```text
WINDOW_CONFIRM
```

若当前 FULL 请求尚未物理 dispatch，则：

```text
RESYNC + WINDOW_CONFIRM
=> single FULL request
```

不得发两条 seq=0。

---

## 8.3 已经 in-flight 的请求

如果某 gid 当前存在：

```text
in-flight SSE_DELTA
```

而新的 WINDOW_CONFIRM 到达：

不得取消已经实际发送的 HTTP 请求，仅为了重新发 FULL。

必须：

```text
register WINDOW_CONFIRM as pending successor demand
```

in-flight 返回后：

```text
reconcile snapshot
```

如果已经满足 WINDOW_CONFIRM：

```text
successor demand is SATISFIED
```

否则：

```text
if still PENDING:
    successor may dispatch
```

---

# 9. Request Substitution

Request Substitution 是本 change 的核心原则。

定义：

> 高价值 StateDemand 到达时，应优先替换或升级尚未发送的低价值 demand，而不是增加物理请求数量。

正式要求：

```text
WINDOW_CONFIRM MUST preferentially
replace or coalesce lower-priority
pending state requests before any
additional physical state request
is admitted.
```

例如：

```text
before:

queue:
1. gid-A SSE_DELTA
2. gid-B SSE_DELTA
3. gid-C WINDOW_CONFIRM
```

调度器不应仅仅：

```text
move gid-C to front
```

还应检查：

```text
gid-C 是否已经存在可升级 pending demand
```

如果存在，则：

```text
upgrade existing demand
```

而不是新增一项。

---

# 10. Adaptive Early WINDOW_CONFIRM

## 10.1 原则

WINDOW_CONFIRM 不再依赖固定提前量作为唯一策略。

调度器维护：

```text
observed WINDOW_CONFIRM HTTP latency
```

主要指标：

```text
send_to_headers_ms
```

而不是：

```text
send_to_body_ms
```

因为客户端获得 HTTP headers 是 transport response 开始可见的重要边界。

---

## 10.2 LatencyEstimator

新增逻辑组件：

```text
WindowConfirmLatencyEstimator
```

维护：

```text
rolling sample window
EWMA
p50
p90
p95
p99
```

推荐 sample：

```text
recent 50~200 successful WINDOW_CONFIRM physical requests
```

必须排除：

```text
429 backoff duration
local throttle queue delay
request creation → dispatch delay
```

主要统计：

```text
physical_send_at
→
response_headers_at
```

---

# 11. Adaptive Lead 计算

推荐公式：

```text
adaptive_lead =
    predicted_transport_latency
  + resolver_budget
  + action_submit_reserve
  + safety_jitter
```

其中：

```text
predicted_transport_latency
= rolling p90/p95 send_to_headers
```

建议默认使用：

```text
p95
```

示例：

```text
p95 headers        = 220ms
resolver budget    = 15ms
submit reserve     = 80ms
safety jitter      = 30ms

adaptive lead      = 345ms
```

必须提供上下限：

```text
MIN_CONFIRM_LEAD
MAX_CONFIRM_LEAD
```

例如初始实验值可考虑：

```text
MIN_CONFIRM_LEAD = 150ms
MAX_CONFIRM_LEAD = 500ms
```

具体值属于配置，不属于协议不变量。

---

# 12. Bootstrap 行为

进程刚启动、样本不足时：

```text
sample_count < MIN_LATENCY_SAMPLES
```

使用当前稳定默认 lead。

例如继续使用已有：

```text
EAGER_CHI_LEAD
```

而不是：

```text
0
```

或者极端高值。

当样本足够后才切换：

```text
fixed bootstrap lead
→ adaptive lead
```

---

# 13. Lead 不能授权动作

Adaptive Lead 只决定：

```text
何时开始确认
```

绝不能决定：

```text
server phase 已经存在
```

因此即使提前请求到：

```text
response_peng
```

而客户端当前等待：

```text
response_chi
```

仍必须保持已有语义：

```text
same WindowId
expected chi
actual peng
=> PENDING
```

不得因为 adaptive lead 更早而：

```text
close window
write claim_miss
authorize chi
```

---

# 14. 三种 Deadline 必须分离

调度器必须明确区分：

```text
scheduler_deadline
confirmation_budget_deadline
exact_window_deadline
```

---

# 15. scheduler_deadline

定义：

```text
用于 state scheduler 排序的本地估计时间
```

来源可以是：

```text
source event timestamp
known protocol timing
adaptive lead
```

用途仅限：

```text
scheduler priority
slack calculation
urgency estimation
```

不得用于：

```text
action authorization
server-window terminal proof
claim_miss
```

---

# 16. confirmation_budget_deadline

定义：

```text
客户端愿意继续观察该窗口的最大本地预算
```

作用：

```text
避免无限 successor polling
```

达到后：

```text
WINDOW_CONFIRM lifecycle
=> observation budget exhausted
```

推荐 terminal diagnostic：

```text
confirmation_observation_budget_exhausted
```

不能写成：

```text
server_deadline_expired
```

除非确实存在 authoritative exact deadline。

---

# 17. exact_window_deadline

定义：

```text
服务端 authoritative snapshot
提供的真实窗口 deadline
```

仅这个 deadline 可以用于：

```text
action authorization
submit safety check
deadline_left_exact
```

---

# 18. Reserved Capacity with Borrowing

## 18.1 目标

WINDOW_CONFIRM 不应因为普通 SSE_DELTA 已经持续占用全部 state quota 而无法及时获得 admission。

但不能永久降低总吞吐。

因此采用：

```text
Reserved Capacity with Borrowing
```

---

# 19. 逻辑配额模型

以 15/s 为例：

```text
TOTAL_CAPACITY = 15/s

URGENT_RESERVE = configurable
```

例如：

```text
URGENT_RESERVE = 3/s
NORMAL_SOFT_CAP = 12/s
```

但这不是硬分区。

---

# 20. Borrowing

当不存在 urgent demand：

```text
WINDOW_CONFIRM queue empty
```

普通请求可以借用全部容量：

```text
SSE_DELTA + RESYNC
up to 15/s
```

因此不存在：

```text
3/s capacity permanently idle
```

---

# 21. Reclaim

当 WINDOW_CONFIRM 到达：

普通请求不能继续无限借用 urgent reserve。

调度器在后续 token admission 中优先恢复：

```text
urgent reserved capacity
```

但不得：

```text
撤销已经获得 token 的请求
取消已经发送的请求
```

---

# 22. Reserve 不是独立 limiter

实现上不应创建：

```text
12/s normal limiter
+
3/s urgent limiter
```

因为可能在时间边界产生：

```text
12 + 3 > 15
```

实际必须存在：

```text
one global physical limiter
```

Reservation 只是：

```text
admission policy
```

而不是：

```text
第二个 token bucket
```

---

# 23. Minimum Slack Scheduling

在同优先级或多个紧急窗口之间，不只使用 EDF。

每个 request candidate 计算：

```text
estimated_completion_cost =
    predicted_headers_latency
  + resolver_budget
  + optional_submit_reserve
```

然后：

```text
slack =
    scheduler_deadline
  - now
  - estimated_completion_cost
```

按：

```text
smallest slack first
```

调度。

---

# 24. Slack 排序原则

完整排序建议：

```text
1. reason class
2. slack
3. scheduler_deadline
4. creation sequence
```

即：

```text
WINDOW_CONFIRM
> RESYNC
> SSE_DELTA
```

同类型内部：

```text
minimum slack first
```

相同 slack 时：

```text
earlier deadline first
```

再次相同：

```text
FIFO
```

保证 deterministic。

---

# 25. Negative Slack

如果：

```text
slack <= 0
```

不代表可以绕过 throttle。

也不代表动作已经失败。

它表示：

```text
candidate is transport-critical
```

应获得该优先级类别中的最高 admission 优先级。

仍必须等待：

```text
global 15/s token
```

---

# 26. Low-Value Poll Suppression

当存在 urgent WINDOW_CONFIRM 时，可以延后部分低价值 SSE_DELTA。

但必须满足明确条件。

禁止使用：

```text
“感觉这个请求不重要”
```

这种隐式逻辑。

---

# 27. Suppression Eligibility

只有以下类型允许被 defer：

```text
reason == SSE_DELTA
AND
not already in-flight
AND
not carrying RESYNC
AND
not carrying WINDOW_CONFIRM
AND
not required to resolve uncertain action result
```

可以结合现有：

```text
structurally_no_nonpass_response
poll urgency
actor relation
```

判断是否低价值。

---

# 28. Suppression 上限

SSE_DELTA 只能延后有限时间。

必须存在：

```text
MAX_DELTA_DEFERRAL
```

超过后：

```text
must become eligible again
```

避免：

```text
urgent traffic starvation
```

---

# 29. RESYNC 不得被普通 suppression 无限延后

RESYNC 原因通常意味着：

```text
client state uncertainty
```

因此虽然优先级低于 WINDOW_CONFIRM，但不能视为普通 background delta。

建议：

```text
WINDOW_CONFIRM > RESYNC > SSE_DELTA
```

严格保留。

---

# 30. Deadline-Aware Successor

WINDOW_CONFIRM 的 retry 不应该主要隐藏在 HTTP transport helper 内部。

本 change 推荐逐步转向：

```text
one logical observation attempt
→ one bounded physical request
→ reconcile
→ optional successor
```

---

# 31. Successor 条件

物理请求结束后，如果：

```text
WINDOW_CONFIRM status == PENDING
```

可以创建 successor，但必须重新检查：

```text
same WindowAttemptKey
window still locally relevant
confirmation budget remains
global demand not already satisfied
no newer generation supersedes it
```

---

# 32. Successor 禁止条件

以下任一成立：

```text
window terminal
WindowId changed
round changed
game ended
authoritative action success observed
strategy pass terminal
confirmation budget exhausted
newer generation supersedes demand
```

则不得创建 successor。

---

# 33. HTTP Retry 与 Successor 的边界

普通请求可以继续使用通用 transport retry。

但 WINDOW_CONFIRM 应逐步减少：

```text
long hidden retry chains
```

特别是：

```text
429
sleep
retry
sleep
retry
```

不能在一个 logical WINDOW_CONFIRM 内无限消耗时间。

最终目标：

```text
WINDOW_CONFIRM retry budget
由 deadline-aware policy 显式决定
```

而不是单纯由通用 HTTP helper 决定。

---

# 34. 429 行为

429 不得触发：

```text
bypass limiter
immediate duplicate request
priority escalation beyond global cap
```

429 后必须：

```text
record retry evidence
respect configured backoff
re-evaluate remaining confirmation budget
```

如果：

```text
remaining confirmation budget
<= minimum useful retry cost
```

则：

```text
do not retry
```

回到 StateDemand reconcile。

---

# 35. 502 / Network Error 行为

同理：

```text
retry only if useful
```

判断：

```text
remaining_budget
>
predicted_request_cost + safety margin
```

否则：

```text
terminal transport observation for this attempt
```

但不能自动写：

```text
claim_miss
```

---

# 36. In-Flight 请求不可抢占

已经：

```text
physical request sent
```

之后：

```text
no cancellation solely for priority scheduling
```

原因：

```text
request may already consume server work
cancellation does not recover consumed rate token
```

本 Spec 主要优化：

```text
pre-dispatch admission
```

而不是：

```text
mid-flight preemption
```

---

# 37. 不允许 Hedged Request

本 change 默认明确禁止：

```text
same WINDOW_CONFIRM
→ two simultaneous physical /state requests
```

原因：

```text
可能增加 15/s queue pressure
可能增加 429
可能导致 C3 regression
```

后续如要实验 hedge，必须单独 OpenSpec change。

---

# 38. 未来 Hedge 的前置条件

如果将来重新考虑 hedge，至少必须满足：

```text
quota-neutral
single hedge maximum
spare global capacity available
no urgent queue waiting
request already beyond high-percentile latency
sufficient exact/local budget remains
```

不属于本 change。

---

# 39. 调度器建议数据结构

建议引入统一候选结构：

```text
StateRequestCandidate {
    gid

    reasons

    mode
    seq

    priority_class

    created_at

    scheduler_deadline

    confirmation_budget_deadline

    predicted_headers_latency_ms

    estimated_completion_cost_ms

    slack_ms

    logical_request_id

    transport_request_id

    generation

    window_attempt_key?
}
```

---

# 40. transport_request_id

transport_request_id 必须在进程范围内唯一。

不得只使用：

```text
state-1
state-2
```

建议：

```text
state:{gid}:{logical_request_id}
```

或者：

```text
{run_id}:state:{gid}:{logical_request_id}
```

---

# 41. 调度器核心伪代码

```text
on_state_demand(demand):

    merge_with_existing_gid_demand_if_possible()

    recompute_candidate()

    enqueue_or_update_candidate()
```

Token 可用：

```text
on_global_state_token():

    candidates = all_dispatchable_candidates()

    if candidates empty:
        return

    candidate =
        choose_by:
            priority_class
            then minimum slack
            then earliest scheduler_deadline
            then FIFO

    dispatch(candidate)
```

---

# 42. Coalescing 伪代码

```python
def merge(existing, incoming):
    existing.reasons |= incoming.reasons

    if (
        WINDOW_CONFIRM in existing.reasons
        or RESYNC in existing.reasons
    ):
        existing.mode = FULL
        existing.seq = 0

    existing.scheduler_deadline = min_valid_deadline(
        existing.scheduler_deadline,
        incoming.scheduler_deadline,
    )

    existing.confirmation_budget_deadline = merge_budget(
        existing,
        incoming,
    )

    return existing
```

注意：

```text
exact_window_deadline
```

不能在这里通过：

```text
min/max
```

推导。

它只能来自 authoritative response。

---

# 43. Priority Class

建议枚举：

```text
P0_WINDOW_CONFIRM
P1_RESYNC
P2_SSE_DELTA
```

不要使用散落整数：

```text
0
10
50
```

避免未来优先级语义不清。

---

# 44. WindowConfirmLatencyEstimator 更新规则

只用真实完成的 physical request 更新。

建议：

```text
eligible:
HTTP response headers observed
request reason contains WINDOW_CONFIRM
```

可记录：

```text
success HTTP 200
429
502
other HTTP
```

但用于正常网络预测时建议主要使用：

```text
successful HTTP response
```

异常响应单独统计。

---

# 45. 防止自激反馈

Adaptive lead 不得因为一次极端慢请求立即扩大到最大值。

推荐：

```text
rolling percentile
+
EWMA smoothing
+
bounded step change
```

例如：

```text
new_lead cannot change
more than X ms per N samples
```

避免：

```text
1 slow request
→ lead suddenly 500ms
→ large early traffic shift
```

---

# 46. Lead 更新频率

不要每次请求都重算全局策略。

可以：

```text
every N completed WINDOW_CONFIRM requests
```

或者：

```text
at most once every T seconds
```

具体参数可配置。

---

# 47. Metrics

必须新增至少以下指标。

```text
state_scheduler.queue_depth.total
state_scheduler.queue_depth.window_confirm
state_scheduler.queue_depth.resync
state_scheduler.queue_depth.sse_delta

state_scheduler.dispatch.window_confirm
state_scheduler.dispatch.resync
state_scheduler.dispatch.sse_delta

state_scheduler.coalesced.total
state_scheduler.substituted.total

state_scheduler.reserved_capacity_used
state_scheduler.borrowed_capacity_used

state_scheduler.slack_ms

window_confirm.adaptive_lead_ms

window_confirm.headers_latency_ms
window_confirm.body_latency_ms

window_confirm.successor_count

window_confirm.observation_budget_exhausted

state_request.physical_rate_1s
```

---

# 48. 每请求诊断字段

每个物理 `/state` attempt 至少记录：

```text
gid
logical_request_id
transport_request_id
attempt_index

reasons

mode
seq

priority_class

created_at
queued_at
throttle_grant_at
request_started_at
headers_received_at
body_finished_at

scheduler_deadline
confirmation_budget_deadline

slack_at_enqueue_ms
slack_at_dispatch_ms

adaptive_lead_ms

predicted_headers_latency_ms

http_status
retry_reason
```

---

# 49. WINDOW_CONFIRM 额外字段

```text
window_id
window_attempt_key
expected_phase

source_discard_seq

confirmation_generation

authoritative_phase_observed

exact_window_deadline_ms

deadline_left_at_headers_ms
deadline_left_at_body_ms
```

---

# 50. Coalescing 诊断

当多个 demand 被一条 physical request 满足时：

```text
coalesced_from:
[
    SSE_DELTA,
    WINDOW_CONFIRM
]
```

或者：

```text
satisfied_reasons
```

必须保留。

这样 acceptance 才能判断：

```text
WINDOW_CONFIRM 是否通过 substitution
减少了物理请求数
```

---

# 51. Hard Safety Acceptance

以下必须始终为 0：

```text
duplicate old action POST
unsafe action retry
pending confirmation → claim_miss
success → client loss
strategy pass → client loss
WindowId mismatch authorized
phase mismatch authorized
expired exact deadline authorized
```

---

# 52. Scheduler Hard Acceptance

必须：

```text
physical state rate <= configured limit
```

并且：

```text
no throttle bypass
```

另外：

```text
StateDemand end-of-game dirty = 0
```

---

# 53. Regression Gate：C3

当前基线：

```text
C3_CONFIRM_QUEUE_LATE = 0
```

本 change 的重要约束：

```text
C3 MUST remain 0
```

至少在正式 acceptance 中：

```text
no regression from transport optimization
```

如果 adaptive / reservation 导致：

```text
C3 > 0
```

则 change 不能通过正式 acceptance。

---

# 54. C4 改善目标

本 change 不要求：

```text
C4 == 0
```

因为客户端无法证明所有 HTTP tail latency 来源。

但必须比较：

```text
C4 / canonical WINDOW_CONFIRM
```

相对 frozen baseline：

```text
must not worsen
```

推荐目标：

```text
meaningful reduction
```

例如可在实施时设置：

```text
relative reduction target
```

但不建议在协议 Spec 写死百分比，避免样本波动导致伪门槛。

---

# 55. 请求数量 Gate

本 change 的成功不能依赖：

```text
显著增加 physical /state count
```

必须报告：

```text
logical state demand count
physical state request count
coalescing ratio
substitution ratio
```

理想结果是：

```text
WINDOW_CONFIRM latency improves
while physical requests stay flat
or decrease
```

---

# 56. 429 Gate

必须报告：

```text
429 count
429 rate
429 WINDOW_CONFIRM count
```

如果新调度算法：

```text
C4 decreases
```

但：

```text
429 sharply increases
```

则不能直接判定成功。

---

# 57. Starvation Gate

必须证明：

```text
SSE_DELTA
RESYNC
```

没有永久饥饿。

测试中：

```text
all deferred demands
must eventually become:
SATISFIED
or TERMINAL
```

不能永久：

```text
PENDING
```

---

# 58. Unit Test：Coalescing

测试：

```text
existing:
gid=A
SSE_DELTA
DELTA seq=100

incoming:
gid=A
WINDOW_CONFIRM
FULL
```

期望：

```text
one queued demand
mode=FULL
seq=0

reasons={
    SSE_DELTA,
    WINDOW_CONFIRM
}
```

物理请求：

```text
exactly one
```

---

# 59. Unit Test：Cross-gid 不合并

```text
gid=A SSE_DELTA
gid=B WINDOW_CONFIRM
```

不得合并为：

```text
one physical request
```

StateDemand 合并只能：

```text
same gid
```

---

# 60. Unit Test：Priority

队列：

```text
A SSE_DELTA
B RESYNC
C WINDOW_CONFIRM
```

token 到达后：

```text
dispatch C
```

之后：

```text
B
```

最后：

```text
A
```

---

# 61. Unit Test：Slack

同为 WINDOW_CONFIRM：

```text
A:
deadline_left = 300ms
cost = 50ms
slack = 250ms

B:
deadline_left = 450ms
cost = 350ms
slack = 100ms
```

必须：

```text
B before A
```

---

# 62. Unit Test：Reservation Borrowing

没有 urgent：

```text
15 normal requests eligible
```

允许：

```text
normal use all available capacity
```

不得因为 reserve 永久只允许：

```text
12/s
```

---

# 63. Unit Test：Reservation Reclaim

普通 traffic 已经存在。

新的 WINDOW_CONFIRM 到达后：

```text
next eligible global token
```

必须优先给：

```text
WINDOW_CONFIRM
```

但不得取消：

```text
already dispatched normal request
```

---

# 64. Unit Test：Adaptive Lead Bootstrap

样本不足：

```text
sample_count < threshold
```

使用：

```text
bootstrap lead
```

不得使用未初始化：

```text
0
NaN
infinite
```

---

# 65. Unit Test：Adaptive Lead Bounds

如果统计：

```text
p95 = 20ms
```

仍不得低于：

```text
MIN_CONFIRM_LEAD
```

如果：

```text
p95 = 3000ms
```

仍不得高于：

```text
MAX_CONFIRM_LEAD
```

---

# 66. Unit Test：Phase Safety

等待：

```text
response_chi
```

收到：

```text
same WindowId
response_peng
```

无论请求多早：

```text
status = PENDING
```

不得：

```text
TERMINAL
claim_miss
action authorize
```

---

# 67. Unit Test：Budget Exhaustion

没有 authoritative expected phase。

达到：

```text
confirmation_budget_deadline
```

结果：

```text
confirmation_observation_budget_exhausted
```

不得记录：

```text
server_window_expired
```

除非有 authoritative exact deadline。

---

# 68. Unit Test：No Hedge

同一个：

```text
WindowAttemptKey
```

在任意时刻：

```text
active WINDOW_CONFIRM physical GET <= 1
```

本 change 下这是 hard invariant。

---

# 69. Unit Test：Rate Limit

Fake clock 下构造：

```text
many WINDOW_CONFIRM
many RESYNC
many SSE_DELTA
```

验证任意 1 秒窗口：

```text
physical request count <= configured limit
```

具体应与当前 throttle 的精确定义一致。

---

# 70. Integration Test：10 gid burst

模拟：

```text
10 concurrent games
```

同时产生：

```text
SSE_DELTA
WINDOW_CONFIRM
```

要求：

```text
WINDOW_CONFIRM admitted first
same-gid deltas coalesced
no duplicate full snapshots
no >15/s violation
```

---

# 71. Integration Test：HTTP Tail

Fake transport：

```text
80% = 50ms
15% = 150ms
5% = 400ms
```

验证 adaptive estimator 最终：

```text
lead increases above bootstrap
```

同时：

```text
physical request count unchanged
```

---

# 72. Integration Test：网络恢复

先模拟慢：

```text
p95 ~ 350ms
```

再恢复：

```text
p95 ~ 80ms
```

adaptive lead 应：

```text
gradually decrease
```

不能永远卡在 max lead。

---

# 73. Integration Test：429

构造：

```text
WINDOW_CONFIRM -> 429
```

要求：

```text
no immediate duplicate request
no throttle bypass
remaining budget checked
```

如果预算不足：

```text
do not retry
```

---

# 74. Integration Test：RESYNC 与 WINDOW_CONFIRM

同 gid：

```text
RESYNC pending FULL
```

新增：

```text
WINDOW_CONFIRM
```

物理请求：

```text
one FULL
```

response 后分别 reconcile：

```text
RESYNC status
WINDOW_CONFIRM status
```

---

# 75. Replay Acceptance

首先使用现有 frozen room logs 离线回放。

由于无法重新模拟真实 HTTP latency：

离线 replay 主要验证：

```text
state-machine semantics
coalescing
priority
terminal attribution
claim_miss correctness
```

不用于证明：

```text
adaptive transport performance
```

---

# 76. Canary 1：Scheduler-Only

第一轮线上 canary 只启用：

```text
coalescing
substitution
reservation
slack diagnostics
```

可以先保持：

```text
adaptive lead disabled
```

或：

```text
observe-only
```

目标：

```text
证明 C3 仍为 0
证明 request count 不上升
证明 StateDemand clean
```

---

# 77. Canary 2：Adaptive Lead

Scheduler 稳定后：

```text
enable adaptive lead
```

仍然：

```text
NO HEDGE
same 15/s
same retry policy
same BOT
same action policy
```

比较：

```text
C4
headers latency
request count
429
C3
```

---

# 78. Formal Acceptance

完成 runtime change 后：

```text
freeze clean commit
```

正式执行：

```text
3–5 serial rooms
10 games each
```

保持：

```text
BOT unchanged
15/s unchanged
SSE enabled
incremental state enabled
same action semantics
```

---

# 79. Formal Acceptance Output

每房至少输出：

```text
commit sha
room id
game count

logical demand count
physical request count

WINDOW_CONFIRM count
RESYNC count
SSE_DELTA count

coalesced count
substituted count

adaptive lead p50/p95/max

queue latency p50/p95/max
headers latency p50/p95/max

C1
C2
C3
C4
C5

429
502
network errors

StateDemand dirty count

hard safety failures
```

---

# 80. Aggregate Acceptance

跨房聚合：

```text
total games
total windows
total WINDOW_CONFIRM

C1–C5 totals

physical request rate
physical/logical request ratio

coalescing ratio

headers latency p50/p95/max

429 / 502 / transport errors

StateDemand health

window completeness

identity completeness

game completeness
```

其中：

```text
identity protocol limitations
```

与：

```text
scheduler correctness
```

分开报告。

---

# 81. Hard Fail Conditions

以下任何一个出现：

```text
duplicate action submission
old action replay
pending confirmation writes claim_miss
WindowId mismatch authorization
phase mismatch authorization
exact deadline expired authorization
global state rate exceeded
StateDemand dirty at game end
C3 queue-late regression caused by scheduler
```

则正式 acceptance：

```text
FAIL
```

---

# 82. Non-Hard External Limitations

以下不自动导致 scheduler change FAIL：

```text
legacy_unresolved identity
protocol phase unobservable
game settlement unavailable
```

但必须：

```text
report separately
```

不得被 scheduler 指标掩盖。

---

# 83. 推荐配置

建议将以下参数配置化：

```text
STATE_RATE_LIMIT

URGENT_RESERVE

MIN_CONFIRM_LEAD
MAX_CONFIRM_LEAD

LATENCY_SAMPLE_WINDOW
MIN_LATENCY_SAMPLES

LEAD_PERCENTILE

RESOLVER_BUDGET

ACTION_SUBMIT_RESERVE

SAFETY_JITTER

MAX_DELTA_DEFERRAL

MAX_CONFIRM_SUCCESSORS

MIN_USEFUL_RETRY_BUDGET
```

---

# 84. 默认配置原则

初始值应：

```text
conservative
```

不得因为启用本 change：

```text
突然显著增加请求密度
突然把 confirm lead 拉到极端
```

建议逐项 rollout。

---

# 85. 推荐代码边界

建议不要把所有逻辑继续塞进 BotClient。

推荐：

```text
mj/platform/state_demand.py
    logical demand semantics

mj/platform/state_scheduler.py
    global admission
    reservation
    slack
    coalescing coordination

mj/platform/state_latency.py
    latency estimator
    adaptive lead

mj/platform/api.py
    physical HTTP only

mj/platform/bot_client.py
    game/window lifecycle
```

---

# 86. Api 层职责

`api.py` 不应决定：

```text
WINDOW_CONFIRM priority
scheduler slack
game phase semantics
```

它只负责：

```text
perform physical request
capture timing
return transport result
```

---

# 87. StateScheduler 职责

StateScheduler 负责：

```text
global 15/s admission
priority
reservation
borrowing
slack
queue diagnostics
```

---

# 88. StateDemand 职责

StateDemand 负责：

```text
which state information is needed
FULL vs DELTA
which reasons are pending
whether returned snapshot satisfies demand
whether successor is needed
```

---

# 89. BotClient 职责

BotClient 负责：

```text
WindowAttempt lifecycle
expected phase
authorization
decision
action
terminal attribution
```

不要让 Scheduler 了解：

```text
chi/peng legal strategy details
```

---

# 90. Migration Plan

建议实施顺序：

```text
Phase 1
新增 diagnostics 和 transport_request_id

Phase 2
实现 same-gid coalescing/substitution

Phase 3
引入 global StateScheduler abstraction
保持现有 priority 行为

Phase 4
加入 reservation with borrowing

Phase 5
加入 slack calculation
先 observe-only

Phase 6
启用 minimum-slack ordering

Phase 7
加入 latency estimator
先 observe-only

Phase 8
启用 adaptive early WINDOW_CONFIRM

Phase 9
引入 deadline-aware successor policy

Phase 10
online formal acceptance
```

---

# 91. Observe-Only 模式

以下新算法最好支持：

```text
observe_only
```

例如 slack scheduler：

实际仍按旧算法：

```text
dispatch_old_policy()
```

但同时计算：

```text
what_would_slack_scheduler_choose
```

记录：

```text
actual_candidate
shadow_candidate
```

这样线上先验证：

```text
新算法是否会频繁改变排序
```

再真正启用。

Adaptive lead 同理：

```text
actual lead = fixed
shadow lead = adaptive
```

先记录差异。

---

# 92. Rollback

所有调度优化必须支持快速回退到：

```text
legacy scheduler
```

建议单独 feature flags：

```text
enable_state_coalescing
enable_reserved_capacity
enable_slack_scheduling
enable_adaptive_confirm_lead
enable_deadline_successor
```

不能只有一个：

```text
enable_new_scheduler
```

否则线上出问题难以定位具体机制。

---

# 93. 最终预期行为

理想状态：

```text
SSE arrives
    ↓
logical StateDemand created
    ↓
same-gid demands coalesced
    ↓
WINDOW_CONFIRM upgrades DELTA to FULL
    ↓
adaptive lead creates demand early enough
    ↓
global scheduler calculates slack
    ↓
urgent reserve guarantees admission opportunity
    ↓
global 15/s token granted
    ↓
single physical /state GET
    ↓
authoritative snapshot
    ↓
StateDemand reconcile
    ↓
WindowConfirm authorize / pending / terminal
```

全过程：

```text
no duplicate state request unless successor is genuinely required
no hedge
no throttle bypass
no speculative action
```

---

# 94. Success Definition

本 change 成功的定义不是：

```text
所有 window miss = 0
```

而是：

```text
在相同 15/s 配额下：

WINDOW_CONFIRM 能更早进入 transport

C3 保持 0

C4 不增加并尽可能明显下降

physical /state 数量不显著增加

429 不恶化

StateDemand 保持 clean

所有 action safety invariant 保持成立
```

---

# 95. 一句话契约

```text
The client SHALL improve WINDOW_CONFIRM latency
primarily by earlier admission, request coalescing,
quota substitution, reserved capacity, and
deadline-aware scheduling, while preserving the
global physical /state rate limit and all existing
authoritative action-safety invariants.
```

实施时我建议你**不要一次把全部算法打开**。最安全的落地顺序是先完成 `coalescing/substitution → reservation → shadow slack → adaptive lead`，每一步都能单独观察是否让 `C3/429/physical request count` 恶化，再继续往后开。
