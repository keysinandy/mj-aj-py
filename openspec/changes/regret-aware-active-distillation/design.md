# Design: Regret-aware Active Distillation

## 1. Architecture

```text
PolicyNet pi_k
   | on-policy rollout
   v
Candidate State Pool  (random / disagreement / uncertainty / historical hard / special)
   | active sampler (configurable ratios)
   v
Search Teacher  (adaptive budget + cache)
   | visits / Q / confidence
   v
Training Dataset  (recent generation / historical replay / hard / special)
   | regret-aware loss
   v
PolicyNet pi_k+1
   | gates: offline -> hard-set -> fast paired -> full paired -> runtime
   v
promote
```

Deployment shape is unchanged: search stays offline; the online runtime is
feature extraction + PolicyNet forward + legal mask + argmax.

## 2. Data Schema

Every teacher-labeled state MUST carry at least:

```text
state_id            stable hash of the canonical state
episode_id          source game/room identity (split owner)
generation_id       DAgger generation
state_features      planes/scalars + feature fingerprint
legal_mask
teacher_visit_count_by_action
teacher_policy
teacher_q_by_action
teacher_best_action
teacher_best_q
teacher_second_q
teacher_q_gap
teacher_search_budget
teacher_search_depth
teacher_confidence
policy_prob_by_action
policy_action
policy_entropy
policy_regret = teacher_best_q - teacher_q(policy_action)
state_source        normal/disagreement/hard/special/random/forced
special_tags
sample_weight
```

Raw values may be stored unnormalized; normalization happens in the trainer
against a declared score scale. `state_id` is the dedupe/cache/tracking key.

## 3. Splits and Hard-Set Separation

Splits are by **episode/game**, never by random state. The existing frozen
seed ranges (`train/validation/final-test`) and `source_group` atomicity
already satisfy this; any new sampler MUST preserve it. The hard-state
regression set is stored separately from validation/test.

## 4. Active Teacher Sampling

Candidate states are collected from `pi_k` rollouts and scored cheaply with
policy-only inference (entropy, top1-top2 gap, special tags, visit counts,
optional cheap-teacher disagreement). Only selected states consume the
expensive teacher.

First-version ratios (configurable, never hard-coded):

| source | ratio |
| --- | ---: |
| normal on-policy | 0.40 |
| policy/teacher disagreement | 0.20 |
| hard (regret/disagreement) | 0.20 |
| special state | 0.15 |
| random | 0.05 |

Hard candidate definition (any of): policy action != teacher best action;
`policy_regret > threshold`; policy high-probability action with clearly
worse teacher Q; historical failure pattern. Long-term, disagreement alone
is not sufficient: expensive mistakes (`regret` large) matter most.

## 5. Adaptive Teacher Budget and Cache

Budget tiers and deterministic promotion already exist (`TeacherBudgetProfile`).
This change adds confidence inputs: visit concentration, Q gap, search
stability and policy/teacher conflict; special/high-risk states escalate.

Teacher cache key:

```text
state_hash + teacher_version + teacher_config_hash
```

A cached result MAY be reused only when the new request's budget is equal or
lower than the cached run; otherwise re-search. A cache hit MUST NOT create a
second row for the same (state, generation) pair.

## 6. Regret-aware Policy Loss

Primary loss stays soft-target KL/CE on teacher visits with legal masking and
no one-hot collapse of near ties.

Ranking loss (first version): for `Q(i) > Q(j)` require `logit_i > logit_j`:

```text
L_pair(i,j) = softplus(-(logit_i - logit_j))
w_q         = clip(|Q_i - Q_j| / q_scale, 0, max_weight)
L_rank      = sum w_q * L_pair over best-vs-others (or top-k vs bad)
```

First-version total loss:

```text
L = 1.0 * L_policy + 0.25 * L_rank
```

Catastrophic margin loss is a later experiment (weight 0 initially):

```text
L_cat = max(0, margin - (logit_best - logit_bad))
```

## 7. Sample Weight

```text
sample_weight = clip(
    teacher_confidence * policy_error_factor * importance_factor,
    0.25, 4.0)
```

- `teacher_confidence` in [0.5, 1.5]: confident teacher -> higher weight;
  near-tie teacher -> lower weight (ambiguity must not force hard learning).
- `policy_error_factor = 1 + alpha * min(normalized_regret, r_cap)`, alpha=1.
- `importance_factor` from active-sampling source (e.g. disagreement/hard
  states weighted up, random down), in [0.5, 2.0].

Clipping is mandatory so a few hard cases cannot dominate a batch.

## 8. Replay and DAgger

Replay buckets: recent / historical / hard / special. First-version batch
mix: recent 0.50, historical 0.25, hard 0.15, special 0.10. Historical data
uses reservoir sampling or per-generation quotas (newer generations larger).
Hard and special may overlap; the sampler resolves priority.

## 9. Hard-State Regression Set

A state enters permanently when: reference regret extremely high;
catastrophic action; special-state failure; a paired game exposes a clear
policy mistake; a real failure is found online/simulated; or a previously
fixed regression recurs. Removal requires proof of a bug in the state,
teacher label or rules. Dedupe by `state_id` plus feature similarity, phase,
action pattern and failure tag; the set optimizes failure-mode coverage, not
size.

Every checkpoint evaluation reports hard mean/p95/max regret, catastrophic
count, fixed/regressed counts and per-special-tag metrics, and stores the
action/probability/regret of every historical policy on the same states.

## 10. Staged Evaluation Gates

```text
Gate A offline:    KL, top1 agreement, mean/median/p90/p95/p99/max regret,
                   catastrophic rate, special-state regret.  Every checkpoint.
Gate A promotion:  mean not worse; p95/catastrophic/special not materially
                   worse; at least one core metric improved.
Gate B fast paired: 256-512 pairs, same seed/seat/dealer/opponent schedule;
                   only rejects obvious failures.
Gate C full paired: 4096 pairs, unchanged release standard.
Runtime gate:      p50/p95/p99 latency, memory, throughput; network-only.
```

Top-1 accuracy is diagnostic only in every gate.

## 11. Experiments

One variable per step; every run records run_id, git commit, model/teacher/
dataset config hashes, sampling/loss/optimizer config, seeds, and all gate
metrics. Forbidden: "that run from two days ago" ambiguity.

```text
E0 baseline reproduction
E1 + active sampling
E2 + ranking loss
E3 active sampling + ranking loss
E4 + replay
E5 + hard-set regression/oversampling
E6 catastrophic loss / temperature / weighting refinements (later)
```

Dashboard first screen: mean/p95 reference regret, catastrophic rate,
special-state regret, paired score, teacher search cost, training cost,
inference latency.

## 12. First-version Config

```yaml
sampling: {recent_normal: 0.40, disagreement: 0.20, hard: 0.20,
           special: 0.15, random: 0.05}
replay:   {recent: 0.50, historical: 0.25, hard: 0.15, special: 0.10}
loss:     {policy_weight: 1.0, ranking_weight: 0.25, catastrophic_weight: 0.0}
sample_weight: {min: 0.25, max: 4.0}
evaluation: {offline: true, hard_set: true, small_paired_games: 512,
             final_paired_games: 4096}
```

## 13. Success Criteria

- Same teacher budget -> lower mean reference regret, or same regret at
  lower teacher compute.
- Lower p95/tail regret and catastrophic rate.
- Historical hard states do not regress when new generations arrive.
- Full paired-score gate and runtime gate still pass; deployment remains
  network-only.

## 14. Priority

```text
P0 reference regret evaluator (exists: regret_selection)
P0 hard-state regression set
P0 active sampling
P1 teacher adaptive budget + cache
P1 ranking loss
P1 generation replay
P2 sample weighting refinement
P2 catastrophic loss
P3 value head (deferred; units contract required first)
P3 architecture changes (deferred)
```

## 15. Explicitly Out of Scope Now

No network changes, no Transformer/bigger net, no value head, no
multi-auxiliary losses, no unbounded teacher budget growth; training
accuracy/loss must never be the headline metric.
