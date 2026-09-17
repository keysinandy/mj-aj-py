# Tasks: Distributed Search Distillation Workers

## 1. Contract and Job Store

- [ ] 1.1 Add `ClusterCampaign`, `WorkerCapabilities`, `JobSpec`, `JobLease` and `JobResultManifest` frozen dataclasses in `mj/training/distributed_jobs.py`.
- [ ] 1.2 Define stable fingerprint/job-id generation; retry attempt MUST NOT change semantic `job_id`.
- [ ] 1.3 Add `mj/training/job_store.py` with SQLite schema for campaigns/workers/jobs/leases/results.
- [ ] 1.4 Use SQLite WAL mode and transactional lease acquisition so two workers cannot own the same live lease.
- [ ] 1.5 Add lease expiry, heartbeat, retry limit and conflicting-completion quarantine.
- [ ] 1.6 Add tests for concurrent lease, expiry/requeue, coordinator restart and idempotent completion.

## 2. Coordinator and Worker Runtime

- [ ] 2.1 Add `scripts/search_cluster.py coordinator` with LAN HTTP/JSON endpoints for register/lease/heartbeat/complete/fail/status.
- [ ] 2.2 Add optional `MJ_CLUSTER_TOKEN` authentication and root-path normalization.
- [ ] 2.3 Add `scripts/search_cluster.py worker` with worker capability registration, per-role concurrency and graceful shutdown.
- [ ] 2.4 Add local SSD staging; SMB/UNC is used only for immutable input/result artifacts.
- [ ] 2.5 Publish result manifest last; ignore incomplete `.tmp`/orphan result directories.
- [ ] 2.6 Refuse campaign lease when worker git commit or required fingerprints are incompatible.

## 3. Teacher Refactor

- [ ] 3.1 Refactor `mj/training/teacher_generate.py` so current `generate_dataset()` delegates to a reusable source-spec batch primitive without changing local output.
- [ ] 3.2 Move active-pool sequential labeling logic out of `scripts/search_teacher_generate.py` into `mj.training.teacher_generate.label_candidate_chunk()`.
- [ ] 3.3 Add `mj/training/distributed_teacher.py` job builders/executors for source-spec and selected-candidate chunks.
- [ ] 3.4 Each Teacher job writes its own dataset JSONL + result manifest; no multi-worker append.
- [ ] 3.5 Preserve `TeacherCache` semantics and work/state identities across local/distributed modes.
- [ ] 3.6 Add fixed-seed tests proving local vs cluster merged dataset/work-id equivalence.

## 4. Dataset Merge Gate

- [ ] 4.1 Extend `scripts/search_dataset_merge.py` with `--job-manifests`, `--campaign-id`, `--expected-jobs`, `--require-complete`.
- [ ] 4.2 Validate generation, policy/search/belief/budget/feature fingerprints before merge.
- [ ] 4.3 Reject missing expected jobs, unknown job ids and conflicting duplicate artifacts.
- [ ] 4.4 Keep `merge_datasets()` as the deterministic row dedupe primitive.
- [ ] 4.5 Write cluster merge provenance and throughput summary.

## 5. Distributed Paired Evaluation

- [ ] 5.1 Add `play_rows`, raw row read/write and deterministic `merge_paired_rows` helpers to `mj/training/paired_eval.py`.
- [ ] 5.2 Key distributed pair results by `(matrix, schedule.index)` and reject conflicting duplicate rows.
- [ ] 5.3 Add `mj/training/distributed_paired.py` job builder/executor.
- [ ] 5.4 Extend `scripts/search_bc_paired.py` internals so local and distributed mode share the same row/report implementation.
- [ ] 5.5 Run `paired_score_report()` only after all expected raw rows are merged on PC-A.
- [ ] 5.6 Add local-vs-distributed equivalence tests for fast paired and a reduced full-paired schedule.

## 6. Windows Pipeline

- [ ] 6.1 Add `scripts/search_distill_pipeline.py` with Windows-compatible orchestration.
- [ ] 6.2 Pipeline flow: pool -> cluster Teacher -> complete merge -> local CUDA train -> offline/hard -> cluster fast paired -> cluster full paired -> runtime -> promotion.
- [ ] 6.3 Keep existing `scripts/search_distill_pipeline.sh` functional; no removal in this change.
- [ ] 6.4 Pause/disable PC-A CPU Teacher jobs during RTX 2060 PolicyNet training by default.
- [ ] 6.5 Append campaign ids/cluster metrics to policy iteration/run summary provenance.

## 7. Initial Two-Machine Deployment

- [ ] 7.1 Configure PC-A as coordinator/trainer with initial Teacher/paired concurrency 2.
- [ ] 7.2 Configure PC-B as compute worker with initial Teacher/paired concurrency 8; RX 6600 disabled for training.
- [ ] 7.3 Configure Windows private-network firewall for coordinator port and SMB share.
- [ ] 7.4 Benchmark PC-B concurrency at 4/6/8/10/12 and freeze the best stable value.
- [ ] 7.5 Benchmark job sizes: source specs 4/8/16, pool candidates 64/128/256, paired rows 16/32/64.
- [ ] 7.6 Record A-only, B-only, A+B throughput for the same frozen Teacher campaign.
- [ ] 7.7 Require correctness/equivalence before accepting any throughput improvement.

## 8. Recovery Tests

- [ ] 8.1 Kill PC-B worker mid-Teacher job; verify lease expiry/retry and complete merge.
- [ ] 8.2 Reboot PC-B with completed local staging but failed SMB publish; verify republish without re-search where possible.
- [ ] 8.3 Restart coordinator; verify SQLite state and expired leases recover.
- [ ] 8.4 Disconnect SMB temporarily; verify no partial artifact is treated as success.
- [ ] 8.5 Submit a worker on wrong git commit; verify it receives no incompatible job.

## 9. Acceptance

- [ ] 9.1 Same frozen input/config produces the same merged Teacher dataset semantics in local and two-machine modes.
- [ ] 9.2 Every expected paired schedule row appears exactly once after merge.
- [ ] 9.3 Partial/incomplete campaign cannot start training or promotion.
- [ ] 9.4 Two-machine Teacher throughput reaches the declared benchmark target (initial target: >=1.5x PC-A only) without correctness regression.
- [ ] 9.5 Existing single-machine CLI remains usable with no coordinator.
