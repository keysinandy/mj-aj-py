# Specification: minisuphx-distributed-rl

## ADDED Requirements

### Requirement: v1 distributed RL SHALL use a single learner

All gradient updates for the first two-machine version SHALL occur on the designated PC-A trainer. Cross-vendor RTX/RX gradient synchronization is out of scope.

#### Scenario: PC-B registers RX 6600
- **WHEN** PC-B reports an AMD GPU and `gpu_training=false`
- **THEN** it may run rollout/BC/DAgger/paired/search jobs but is not assigned learner-gradient work

### Requirement: PPO rollout SHALL be synchronous and policy-version locked

A PPO update SHALL consume samples from exactly one frozen policy version. Actors SHALL load that immutable policy and must not switch versions mid-job.

#### Scenario: stale shard arrives late
- **WHEN** policy_38 is current but a policy_37 rollout finishes after the policy_38 campaign began
- **THEN** the policy_37 shard remains attached to its original campaign and cannot be merged into the policy_38 update

### Requirement: Rollout results SHALL carry complete provenance

Each rollout result SHALL include campaign/job/worker identity, git commit, policy version/fingerprint, value and feature contracts, opponent profile, seeds/seat/dealer, transition tensors and terminal outcome metadata.

#### Scenario: missing policy fingerprint
- **WHEN** a shard contains transitions but no policy fingerprint
- **THEN** merge fails because the on-policy identity cannot be proven

### Requirement: Rollout publication SHALL be immutable and crash-safe

Workers SHALL stage locally and publish a result manifest last. Partial or orphaned artifacts MUST NOT count as successful rollouts.

#### Scenario: SMB fails during upload
- **WHEN** the worker computed a valid rollout locally but cannot commit the shared manifest
- **THEN** the job is not marked succeeded and may republish/retry without silently counting partial data

### Requirement: Distributed job ordering SHALL NOT change training semantics

Worker count, completion order and machine speed SHALL NOT alter seed ownership, policy version, opponent profile or the semantic transition set for a frozen rollout campaign.

#### Scenario: A-only versus A+B
- **WHEN** the same frozen mini rollout campaign is run with one worker and then two workers
- **THEN** the expected semantic job/seed set and merged provenance are equivalent modulo allowed nondeterministic neural sampling explicitly controlled by frozen RNG seeds

### Requirement: PC-A CPU work SHOULD yield while CUDA learner is active

By default the scheduler SHALL stop or reduce CPU-heavy rollout/teacher jobs on PC-A during CUDA updates, while PC-B may continue eligible independent work.

#### Scenario: learner update starts
- **WHEN** PC-A begins a CUDA PPO update
- **THEN** the default profile does not launch new CPU-heavy local jobs that materially contend with the learner

### Requirement: Resume SHALL restore optimizer and controller state

A learner checkpoint SHALL be resumable with model, optimizer, scheduler, RNG, global discard count, update index, entropy controller and run fingerprints.

#### Scenario: PC-A restarts
- **WHEN** training resumes from the latest committed learner checkpoint
- **THEN** the next rollout/update continues the same generation semantics rather than creating an implicit fresh optimizer run
