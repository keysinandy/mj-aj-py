# Design: Search Teacher Distillation BC

## 1. Optimization Target

最终优化目标不是 imitation accuracy，而是：

```text
pi* = argmax_pi E[hero_round_score | public information, belief, opponent population]
```

高预算 Search Teacher 给出近似 `Q_T(I,a)`。候选 student 动作的 reference regret 定义为：

```text
Regret_T(I, a_student) = max_a Q_T(I,a) - Q_T(I,a_student)
```

训练和 checkpoint selection 的核心目标是降低该 regret，并最终在独立 paired games 上提高 `hero_round_score_points`。

## 2. Architecture

```text
trajectory policy pi_k
        |
        v
public InformationHistory + PublicDecisionContext
        |
        v
BeliefState (belief-v2)
        |
        v
Search Teacher (search-v1)
  adaptive 512 -> 2k -> 8k -> 16k
        |
        +--> visit_counts
        +--> q_by_action
        +--> root_value
        +--> variance / ambiguity
        |
        v
SearchSample (oracle=False)
        |
        v
Search BC Trainer
  soft visit CE
  optional Q-soft CE
  optional Value v2
        |
        v
pi_(k+1) PolicyValueNet / policy-only net
        |
        v
high-budget regret + paired-score promotion gate
        |
        +---- pass ----> next DAgger generation
        |
        +---- fail ----> rollback last all-gates-passed
```

## 3. Teacher Definition

Teacher profile MUST freeze rules version, belief profile fingerprint, particle count, opponent population version, continuation policy version, leaf evaluator version, search profile, root noise, simulation budget schedule, search seeds, action space, source split and `oracle=false`.

Teacher MUST use information-set search. Real wall order and opponent concealed identities may exist inside a sampled simulation world but MUST NOT enter node key, network feature, dataset sample or serialized explanation.

### 3.1 Reference vs Training Teacher

`search-v1-reference-16000` is reserved for validation/final reference evaluation and hard-state adjudication. It MUST NOT be required for every training state.

Default training schedule:

```text
tier 0: 512 sims
tier 1: 2048 sims
tier 2: 8192 sims
tier 3: 16000 sims
```

Promotion between tiers is deterministic and based only on currently available teacher evidence: insufficient top1/top2 separation, high action-value variance, frozen baseline disagreement, ambiguity/incomplete result, critical-rule state, or explicit reference sampling. Exact thresholds belong to `TeacherBudgetProfile` and enter its fingerprint.

### 3.2 Forced States

States with exactly one legal action MUST NOT consume high-budget search. Default behavior is direct forced action, retaining only a 1–5% deterministic sanity subset. Forced states are excluded from strategic mean/p95 regret.

## 4. Trajectory Source / DAgger

Generation 0 freezes `pi0` as the strongest current low-latency baseline selected by frozen reference regret + paired score, not by evaluator name.

For generation k:

```text
pi_k trajectories
  -> collect multi-action states
  -> Search Teacher relabel
  -> dataset_k
  -> train pi_(k+1)
```

Aggregate training may combine generations, but every sample MUST retain source generation, source policy version, source group, opponent population, teacher fingerprint and feature fingerprint. No source group may cross train/validation/final-test.

## 5. Dataset Contract

Primary dataset is `SearchSample`, not legacy one-hot BC shard.

Required semantic fields:

```text
context_hash
history_hash
source_group
generation
policy_version_source
legal_mask[109]
visit_counts
q_by_action
root_value
simulations
ambiguous
confidence
search_variance
belief_fingerprint
search_fingerprint
opponent_policy_version
leaf_version
planes
scalars
oracle=false
```

Additional recommended fields:

```text
teacher_budget_tier
teacher_requested_simulations
teacher_completed_simulations
teacher_seed
teacher_status
teacher_top1_q
teacher_top2_q
teacher_q_gap
forced
special_state_tags[]
```

Serialization MUST NOT contain hidden-world tile identities or wall order.

## 6. Policy Targets

### 6.1 Primary: Search Visit Distribution

For legal actions:

```text
p_visit(a) = N(a) / sum_b N(b)
```

Loss:

```text
L_visit = -sum_a p_visit(a) log p_theta(a)
```

Illegal actions are masked before softmax. Ambiguous/near-tie states retain soft targets and MUST NOT become one-hot labels.

### 6.2 Optional: Q-Soft Target

If Q evidence is valid:

```text
p_Q(a) = softmax((Q(a)-Q_max)/tau_Q)
L_policy = lambda_visit * L_visit + lambda_Q * CE(p_Q, p_theta)
```

Initial ablations:

```text
visit-only: lambda_visit=1.0, lambda_Q=0.0
q-soft-only: lambda_visit=0.0, lambda_Q=1.0
hybrid: lambda_visit=0.7, lambda_Q=0.3
```

Final choice uses final-split regret + paired score, not training loss.

## 7. Sample Weight

Raw `1/(1+variance)` MUST NOT suppress high-value states solely because score variance has large units.

Recommended versioned weight:

```text
w = w_evidence * w_ambiguity * w_reset * w_importance
```

Example importance:

```text
w_importance = clip((Q_best-Q_2nd)/tau_gap, w_min, 1)
```

If variance is used, normalize it to a declared score scale:

```text
w_var = 1 / (1 + variance / sigma0^2)
```

All constants enter a fingerprint.

## 8. Value Head Contract

### 8.1 Phase 1

Phase 1 SHOULD train policy-only:

```text
policy_weight = 1
value_weight = 0
```

Reason: current `Net.value` uses tanh, while raw round-score/root-value may exceed the representable normalized range if the transform is not frozen consistently.

### 8.2 Value v2

Before enabling value loss, define a single transform/output contract and freeze target transform, scale, output activation, inverse transform, clipping behavior and reward units. No checkpoint may set `calibrated=true` when the contract is mismatched.

## 9. Checkpoint Selection

Top-1 imitation accuracy is diagnostic only.

Every checkpoint MUST run against a frozen high-budget reference context set.

Primary selector:

```text
mean_reference_regret
```

Secondary gates:

```text
p95_reference_regret
catastrophic_regret_rate
policy_KL_to_search
top1_action_agreement
batch1_latency
illegal_action_count
```

A checkpoint with higher top1 but worse mean/p95 regret MUST NOT be selected solely for accuracy.

## 10. Paired-Game Evaluation

After decision-level regret passes, compare student against previous promoted policy, shape-v2 and shape-v1 using identical seed/hero seat/dealer/YCBK/opponent population.

Metric per pair:

```text
Delta_i = Score_i(A) - Score_i(B)
```

Use source-game clustered bootstrap. A superiority claim requires `CI95_lower(mean Delta) > 0`.

## 11. Opponent Population

Release MUST report self_play, legacy_shape_v1 and frozen_population separately. Recommended initial frozen population contains legacy, shape-v1, shape-v2 and previous promoted BC/policy checkpoints with versioned weights.

## 12. Special-State Coverage

Dataset/evaluation MUST report separate coverage/regret for HU vs piao, wild discard/baotou, four white boards, seven pairs/luxury seven pairs, closed/add/open kong, PONG/PASS, CHOW/PASS, 抓打圈, wall tail, reaction cursor and dealer/non-dealer. YCBK coverage is reported for `you_cai_bi_kao=false` only (see §16).

## 13. Runtime

Normal released runtime:

```text
extract public/belief features
  -> model forward
  -> legal mask
  -> argmax
  -> action
```

No online high-budget POMCP is required. After offline/online release gates pass, the profile MAY set `confidence_threshold=0` so low margin alone does not trigger shape-v2.

Fallback remains mandatory for checkpoint/manifest mismatch, feature fingerprint mismatch, non-finite logits, invalid legal distribution and runtime exception. Fallback chain remains `shape-v2 -> legacy -> deterministic legal emergency`.

## 14. Reproducibility

Every artifact MUST record git revision, rules, feature contract, belief profile, teacher budget profile, search profile, opponent population, continuation, leaf, dataset source groups, training seed, model architecture, loss profile and checkpoint selector.

## 15. Reduced Generation-0 Scope (approved 2026-09-16)

Measured on the 6-core CPU training host after the 2026-09-16 throughput
optimization (history-hash caching, append fast path, lazy event hash):

```text
33.7 simulations/s/core (was 27.8)
512 sims  ~15 s/state
2048 sims ~61 s/state   (75 s before optimization)
8192 sims ~4.1 min/state
```

A full Gen0 at the original target (200k-500k states at the default 2048
tier) is therefore a ~23-59 day run on this host.  The first generation is
reduced and explicitly labeled as reduced evidence:

```text
training teacher tiers: 512 -> 1024   (artifacts/teacher_budget_reduced_gen0.json)
dataset0 target:        10k-20k useful multi-action states
                        (bounded by the frozen train split: 1024 games)
reference simulations:  8000          (validation split, forced states skipped)
paired evidence:        >= 1024 pairs (full 4096 remains the release gate)
YCBK:                   fixed off (§16)
```

The full ladder 512/2048/8192/16000, the 8k/16k reference, the 4096-pair
gate and every release requirement remain unchanged for the final release;
a promoted policy based on reduced Gen0 evidence MUST NOT switch the online
default until the full-scale gates are re-run.

The reduced profile is itself frozen and fingerprinted, and every dataset /
checkpoint records it, so a later full-scale run is a new dataset version
rather than a silent overwrite.

## 16. YCBK Training Rule (approved 2026-09-16)

`you_cai_bi_kao` is treated as **permanently disabled** for this change:

- trajectory generation, teacher search, reference sets and paired-game
  schedules MUST use `you_cai_bi_kao=false`;
- no dataset, checkpoint or promotion evidence may be produced from
  YCBK-on games;
- the engine and runtime keep their existing ability to honor the platform
  flag at inference time, but YCBK-on is not training input and not release
  evidence;
- coverage/reporting therefore carries only the `ycbk-off` bucket.

Rationale: the tournament/test-room configuration this pipeline targets
runs with YCBK off; splitting scarce teacher budget across an unused rule
variant only delays the first evaluable generation.
