# Specification: distributed-search-distillation-workers

## ADDED Requirements

### Requirement: Distributed execution SHALL preserve the existing training semantics

Cluster execution SHALL be an orchestration layer around the existing Search Teacher, dataset and paired-evaluation primitives. It MUST NOT change legal actions, Teacher search semantics, belief inputs, budget promotion, sample identities, active-sampling labels, paired schedules or promotion thresholds merely because work is executed on multiple machines.

#### Scenario: one worker versus two workers

- **WHEN** the same frozen campaign is executed with one eligible worker and then with two eligible workers
- **THEN** the merged semantic work set is identical, with the same expected `work_id`/schedule identities and no result difference attributable only to job completion order

#### Scenario: local mode remains available

- **WHEN** no coordinator is configured
- **THEN** existing single-machine commands such as `search_teacher_generate.py --workers N` and local paired evaluation continue to work without the distributed subsystem

### Requirement: Cross-vendor GPU gradient synchronization SHALL NOT be part of v1

The first version SHALL train PolicyNet only on the designated trainer machine and MUST NOT synchronize gradients between the RTX 2060 and RX 6600. Worker GPU metadata MAY be recorded for future capability matching, but an AMD worker MUST NOT be assigned a GPU-training job in v1.

#### Scenario: RX 6600 worker registers

- **WHEN** PC-B registers `gpu_vendor=amd`, `gpu_model=Radeon RX 6600` and `gpu_training=false`
- **THEN** it remains eligible for Teacher/rollout/paired CPU jobs and is never selected for PolicyNet gradient training

### Requirement: Every cluster campaign SHALL freeze reproducible provenance

Before jobs are created, the master SHALL persist a campaign manifest containing the git commit, generation, policy/dataset identity and all search/belief/teacher-budget/feature-contract fingerprints needed to reproduce the work. Workers MUST NOT override these values.

#### Scenario: worker code is stale

- **WHEN** a worker git commit does not match the campaign's required commit or declared compatible fingerprint
- **THEN** the coordinator refuses to lease that campaign's jobs to the worker and records the incompatibility

#### Scenario: Teacher config changes after submit

- **WHEN** a local config file is edited after a campaign has already been submitted
- **THEN** existing jobs continue to use the frozen campaign values and the edited config cannot silently alter their identity

### Requirement: Jobs SHALL have stable semantic identities and leased ownership

Each job SHALL have a deterministic `job_id` derived from campaign identity, job kind, normalized payload and relevant fingerprints. The coordinator SHALL lease a pending job to at most one live worker at a time. Retry attempt numbers MUST NOT change the semantic `job_id`.

#### Scenario: two workers request work simultaneously

- **WHEN** PC-A and PC-B concurrently request a Teacher job
- **THEN** one job lease is transactionally owned by only one worker and the other worker receives a different eligible job or no work

#### Scenario: retry after crash

- **WHEN** a worker dies before committing its result and its lease expires
- **THEN** the same `job_id` becomes eligible for a new attempt without creating a second semantic job

### Requirement: Scheduling SHALL be dynamic instead of fixed 50/50 partitioning

The coordinator SHALL create substantially more jobs than workers and workers SHALL lease the next eligible job as they become free. Scheduler correctness MUST NOT depend on the relative speed of PC-A and PC-B.

#### Scenario: PC-B completes work faster

- **WHEN** PC-B finishes its current Teacher chunk while PC-A is still computing
- **THEN** PC-B may immediately lease another pending Teacher chunk rather than waiting for a preassigned half of the campaign

### Requirement: Worker capability matching SHALL control job eligibility

Workers SHALL register roles and resource capabilities. A job SHALL declare its required role and MAY declare additional resource requirements. Eligibility SHALL be based on capabilities rather than hostname-specific branches in Teacher/evaluation code.

#### Scenario: third machine is added later

- **WHEN** a third machine registers the `teacher` role with a compatible git commit
- **THEN** it can receive pending Teacher jobs without changing Teacher search logic or campaign data format

### Requirement: Teacher jobs SHALL produce isolated immutable shards

A Teacher job SHALL write only to its own result namespace. Multiple workers MUST NOT append concurrently to the same dataset JSONL. A result SHALL contain a dataset shard and a result manifest with job/campaign/worker/fingerprint provenance.

#### Scenario: two Teacher jobs finish together

- **WHEN** PC-A and PC-B complete different Teacher jobs at approximately the same time
- **THEN** each publishes a separate result directory and no shared JSONL append or cross-worker file lock is required

### Requirement: Result publication SHALL be crash-safe

A worker SHALL stage output locally, validate it, copy/publish it to a unique shared result location and publish the committed result manifest last. Merge logic MUST ignore temporary, orphaned or incomplete artifacts.

#### Scenario: worker loses power during upload

- **WHEN** a dataset shard is only partially copied and no committed manifest exists
- **THEN** the shard is ignored by merge and the job remains retryable

#### Scenario: late duplicate completion is identical

- **WHEN** a timed-out worker later submits a result for a job that was already successfully retried
- **THEN** the duplicate MAY be accepted as redundant only when its result fingerprint matches the committed result exactly

#### Scenario: late duplicate completion conflicts

- **WHEN** the late duplicate has a different result fingerprint
- **THEN** the campaign enters a conflict/error state and the conflicting result is quarantined rather than silently selecting one

### Requirement: Worker heartbeat and lease expiry SHALL provide automatic recovery

A running worker SHALL heartbeat before its lease expires. If heartbeat stops and the lease expires, the coordinator SHALL make unfinished work retryable up to the configured attempt limit.

#### Scenario: Windows machine reboots

- **WHEN** PC-B reboots while owning a job and does not return before lease expiry
- **THEN** the job is requeued and can be completed by PC-A or PC-B after restart

#### Scenario: retry limit is exhausted

- **WHEN** a job repeatedly fails until its attempt limit is reached
- **THEN** the job becomes `FAILED`, the campaign cannot be marked complete, and training/promotion is blocked

### Requirement: Worker temporary I/O SHALL use local storage

Workers SHALL use local SSD staging for temporary search/evaluation files. SMB/UNC storage SHALL be used for frozen input artifacts and committed outputs, not as the hot scratch filesystem for every simulation step.

#### Scenario: Teacher search runs on PC-B

- **WHEN** PC-B leases a Teacher chunk
- **THEN** scratch/cache files for that chunk are created under the configured local worker cache and only validated artifacts are published to the shared cluster root

### Requirement: Teacher chunk execution SHALL reuse the existing Teacher implementation

Distributed Teacher handlers SHALL call reusable functions in `mj.training.teacher_generate`; they MUST NOT implement a second Search Teacher code path. Local generation and distributed generation SHALL share label/search/cache logic.

#### Scenario: adaptive budget behavior changes

- **WHEN** `TeacherBudgetProfile` logic is updated
- **THEN** both local and distributed Teacher runs observe that change through the same `teacher_generate` implementation without duplicating scheduler-specific Teacher logic

### Requirement: Active-pool labeling SHALL be chunkable

The current selected candidate pool SHALL be splittable into deterministic candidate chunks. Each candidate SHALL belong to exactly one semantic job for the campaign; resume/merge SHALL preserve existing state/work dedupe rules.

#### Scenario: 10,000 selected candidates are submitted

- **WHEN** the configured candidate chunk size is 128
- **THEN** the master creates deterministic chunk jobs covering the selected candidate identities without omission, and the final merged dataset contains no duplicate semantic Teacher rows

### Requirement: Source-game generation SHALL be chunkable

Scheduled `SourceGameSpec` values SHALL be partitionable into deterministic job payloads. Job boundaries MUST NOT alter frozen split ownership, source group identity, seat/dealer schedule or generation provenance.

#### Scenario: train split is distributed

- **WHEN** source games from the frozen train split are distributed over both machines
- **THEN** every resulting sample retains its original `source_group`/split identity and no validation/final-test group is reclassified by the scheduler

### Requirement: Dataset merge SHALL enforce campaign completeness and fingerprint compatibility

Before a distributed Teacher dataset may be used for training, merge SHALL verify all expected jobs succeeded and all shards belong to the same campaign/generation/policy/search/belief/budget/feature identities. Unknown, missing or incompatible shards SHALL fail the merge.

#### Scenario: one Teacher job is still pending

- **WHEN** 119 of 120 expected Teacher jobs have committed successful results
- **THEN** a `require-complete` merge fails and PolicyNet training is not started from that partial dataset

#### Scenario: shard from another generation is present

- **WHEN** a result directory from generation `k-1` is accidentally included in generation `k`
- **THEN** fingerprint/generation validation rejects it before row merge

### Requirement: Paired evaluation SHALL distribute raw schedule rows and aggregate centrally

Fast and full paired evaluation MAY distribute `PairedSchedule` rows across workers. Workers SHALL emit raw pair rows. The final clustered bootstrap report SHALL be computed once from the complete merged raw-row set on the master.

#### Scenario: full paired gate is split across machines

- **WHEN** a 4096-pair gate is executed with PC-A and PC-B workers
- **THEN** every expected schedule `(matrix,index)` appears exactly once after merge and `paired_score_report()` runs on the complete merged rows

#### Scenario: duplicate paired row conflicts

- **WHEN** two result shards contain the same `(matrix,index)` but different score fields
- **THEN** merge fails instead of arbitrarily keeping one row

### Requirement: Paired schedule partitioning SHALL preserve cluster semantics

Distributed partitioning SHALL use the existing stable schedule index and MUST NOT change seed, hero seat, dealer, YCBK variant or source-game cluster assignment. Clustered bootstrap groups SHALL therefore remain identical to local evaluation.

#### Scenario: rows complete out of order

- **WHEN** paired jobs finish in arbitrary order
- **THEN** merged rows are restored by stable schedule identity and the final report is independent of completion order

### Requirement: Training SHALL start only after an eligible merged dataset is complete

PolicyNet training on PC-A SHALL remain a local CUDA operation and SHALL consume only a successfully validated merged dataset/replay input. Cluster partial state MUST NOT be interpreted as a valid generation boundary.

#### Scenario: coordinator reports campaign failure

- **WHEN** any required Teacher job is permanently failed or has a conflicting result
- **THEN** `search_distill_pipeline.py` stops before invoking `search_bc_train.py`

### Requirement: PC-A CPU work SHOULD yield to CUDA training by default

The Windows pipeline SHOULD pause or set PC-A Teacher/paired CPU concurrency to zero while PC-A performs PolicyNet CUDA training, unless an explicit profile enables overlap. PC-B MAY continue eligible independent work.

#### Scenario: PolicyNet training begins

- **WHEN** the merged Teacher dataset passes validation and the trainer starts on RTX 2060
- **THEN** the default profile stops leasing new CPU-heavy Teacher jobs to the PC-A worker until training completes

### Requirement: Coordinator restart SHALL preserve campaign state

Campaign/job/result state SHALL be stored durably so a coordinator process restart does not require rebuilding completed work. Expired leases SHALL be recoverable after restart.

#### Scenario: coordinator is restarted

- **WHEN** PC-A restarts only the coordinator process while completed result manifests remain available
- **THEN** succeeded jobs remain succeeded, pending jobs remain pending, and expired running leases become retryable according to the same campaign

### Requirement: Network or SMB interruption SHALL not create false success

A worker SHALL report success only after its result artifact is durably published and validated. If artifact publication fails after computation, the job SHALL remain retryable/publishable and MUST NOT be counted as succeeded.

#### Scenario: SMB becomes unavailable after search completes

- **WHEN** PC-B has a valid local shard but cannot publish it
- **THEN** the coordinator does not mark the job succeeded; the worker MAY retry publication without rerunning search when the local staged result still matches the job identity

### Requirement: Cluster provenance and throughput SHALL be reported

Every campaign summary SHALL report worker/job provenance and aggregate throughput sufficient to compare PC-A-only, PC-B-only and combined execution. Correctness metrics and performance metrics SHALL be kept separate.

#### Scenario: combined run is faster

- **WHEN** A+B finishes a frozen Teacher campaign faster than A-only
- **THEN** the report includes labeled states/minute, wall time, worker contribution, retries and merged dataset fingerprint; speed alone cannot override a correctness mismatch

### Requirement: Two-machine acceptance SHALL require correctness before speed

The initial deployment target SHOULD achieve at least 1.5x PC-A-only Teacher throughput on the same frozen campaign, but no throughput result SHALL be accepted if local/distributed semantic equivalence, campaign completeness or merge validation fails.

#### Scenario: 1.8x speedup with mismatched dataset

- **WHEN** the two-machine run is 1.8x faster but its expected semantic work set or merged dataset fingerprint differs from the validated local baseline for scheduler-only reasons
- **THEN** the cluster implementation fails acceptance despite the speedup
