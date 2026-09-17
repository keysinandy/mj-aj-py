# Design: Distributed Search Distillation Workers

## 1. Architecture

```text
                         PC-A / Master + Trainer
                  i5-9400F + RTX 2060 + Windows
        ┌────────────────────────────────────────────────┐
        │ search_cluster coordinator                    │
        │  - SQLite job store                           │
        │  - HTTP/JSON lease API on LAN                │
        │  - campaign/provenance gate                  │
        │                                                │
        │ PolicyNet CUDA training                       │
        │ optional CPU worker (small concurrency)       │
        └──────────────┬───────────────────────┬─────────┘
                       │                       │
             SMB/UNC immutable artifacts      │ HTTP lease/heartbeat
                       │                       │
        ┌──────────────▼───────────────────────▼─────────┐
        │               PC-B / Compute Worker            │
        │          i5-13400F + RX 6600 + Windows        │
        │                                                │
        │ search_cluster worker                         │
        │  - Teacher CPU jobs                           │
        │  - rollout/game simulation jobs               │
        │  - paired evaluation jobs                     │
        │  - local SSD staging                          │
        └────────────────────────────────────────────────┘
```

第一版不做跨机梯度同步。PolicyNet training 只在 PC-A 的 RTX 2060 上运行。PC-B 的 RX 6600 capability 会被登记，但 scheduler v1 不向它分配 GPU training job。

## 2. Why a Coordinator Instead of SMB File Locking

现有 dataset/reference shard 已经适合共享目录，但调度本身不能依赖两个 Windows client 同时 rename/lock `pending/` 文件：

- job lease、heartbeat、retry 需要一个权威状态源；
- 两台机器速度不同，固定 50/50 shard 会产生 tail idle；
- worker crash 后需要明确判断 job 是否可重新领取；
- 同一个 job 的重复 completion 必须能比较 fingerprint 并拒绝冲突结果。

因此 coordinator 在 PC-A 上使用 Python 标准库 `sqlite3` 保存 job/campaign/worker 状态，并通过局域网 HTTP/JSON 提供 lease API。实现可以使用标准库 `ThreadingHTTPServer`，避免为该能力强制新增 Web framework 依赖。

SMB/UNC 只用于大文件和不可变 artifacts：

```text
\\PC-A\mj-cluster\
  campaigns\<campaign_id>\
    inputs\
    jobs\
    results\
    merged\
    logs\
```

高频临时 I/O 在 worker 本地 SSD：

```text
D:\mj-worker-cache\<campaign_id>\<job_id>\
```

## 3. Machine Roles

### PC-A: `master-trainer`

Default roles:

```json
{
  "roles": ["coordinator", "teacher", "paired", "trainer"],
  "teacher_processes": 2,
  "paired_processes": 2,
  "gpu_training": true,
  "gpu_backend": "cuda"
}
```

PC-A 在 Teacher/paired 阶段可以领取少量 CPU job；进入 PolicyNet CUDA training 时，pipeline SHOULD 暂停本机 Teacher job，避免 CPU/RAM/磁盘争用影响训练。

### PC-B: `compute-worker`

Default roles:

```json
{
  "roles": ["teacher", "paired", "rollout"],
  "teacher_processes": 8,
  "paired_processes": 8,
  "gpu_training": false,
  "gpu_model": "AMD Radeon RX 6600 8GB"
}
```

`8` 只是第一版启动值，不是硬编码。落地后必须 benchmark `4/6/8/10/12` 外层进程，并以 states/min 或 pairs/min 选择吞吐最高且内存稳定的值。如果 Teacher 内部未来引入多线程，外层进程数必须相应降低。

## 4. Campaign Contract

一次 cluster campaign 冻结以下内容：

```json
{
  "schema": "search-cluster-campaign-v1",
  "campaign_id": "gen3-teacher-<fingerprint>",
  "job_type": "teacher_pool",
  "git_commit": "<sha>",
  "generation": 3,
  "policy_version_source": "...",
  "dataset_version": "...",
  "search_fingerprint": "...",
  "belief_fingerprint": "...",
  "teacher_budget_fingerprint": "...",
  "feature_contract_fingerprint": "...",
  "input_artifact": "\\\\PC-A\\mj-cluster\\campaigns\\...\\inputs\\pool.jsonl",
  "expected_jobs": 120,
  "created_at": "...",
  "oracle": false
}
```

Coordinator SHALL reject a worker for a campaign if its checked-out git commit differs from `git_commit`, unless the campaign explicitly declares a compatible code fingerprint. Config/checkpoint paths alone are never trusted as identity; fingerprints are authoritative.

## 5. Worker Registration

Worker registration payload:

```json
{
  "worker_id": "pc-b-13400f",
  "hostname": "PC-B",
  "git_commit": "<sha>",
  "python_version": "3.x",
  "cpu_logical": 16,
  "ram_gb": 64,
  "gpu_vendor": "amd",
  "gpu_model": "Radeon RX 6600",
  "gpu_vram_gb": 8,
  "roles": ["teacher", "paired", "rollout"],
  "max_parallel": {
    "teacher": 8,
    "paired": 8,
    "rollout": 6
  }
}
```

Scheduler uses roles/capabilities, not machine name, for eligibility. The hard-coded PC-A/PC-B profile is only the initial deployment.

## 6. Job Model

`mj/training/distributed_jobs.py` adds frozen dataclasses:

```text
ClusterCampaign
WorkerCapabilities
JobSpec
JobLease
JobResultManifest
```

`JobSpec` minimum fields:

```json
{
  "schema": "search-cluster-job-v1",
  "job_id": "<stable fingerprint>",
  "campaign_id": "...",
  "kind": "teacher_source_specs | teacher_pool_chunk | paired_rows",
  "generation": 3,
  "payload": {},
  "input_fingerprints": {},
  "attempt_limit": 3,
  "required_role": "teacher"
}
```

`job_id` is a stable fingerprint over:

```text
campaign_id
+ kind
+ normalized payload
+ relevant config fingerprints
```

Retries reuse the same `job_id`; `attempt` is runtime state and MUST NOT enter semantic identity.

## 7. Job State Machine

```text
PENDING
  |
  | lease
  v
LEASED/RUNNING
  | heartbeat extends lease
  |
  +---- success ----> SUCCEEDED
  |
  +---- worker error -> RETRY_WAIT -> PENDING
  |
  +---- lease expiry ----------------> PENDING
  |
  +---- attempts exhausted ----------> FAILED
```

Required runtime columns:

```text
job_id
campaign_id
status
lease_owner
lease_expires_at
attempt
last_heartbeat_at
result_manifest_path
result_fingerprint
last_error
```

Default lease should be long enough for one job chunk (for example 5–15 minutes) and heartbeat substantially shorter (for example 30 seconds). Exact values are config, not constants.

## 8. Job Granularity

A job MUST be large enough to amortize process/model startup, but small enough that a crashed worker does not lose hours.

Initial target:

```text
teacher_source_specs:  4–16 SourceGameSpec per job
teacher_pool_chunk:    64–256 selected candidate states per job
paired_rows:           16–64 PairedSchedule rows per matrix/job
```

Master creates many more jobs than workers. Workers lease the next eligible job dynamically; no 50/50 machine partition exists.

## 9. Teacher Generation Refactor

Current `teacher_generate.generate_dataset()` already guarantees worker-count-independent row ordering and uses `ProcessPoolExecutor` locally. Cluster support keeps it as the local primitive.

### `mj/training/teacher_generate.py`

Add/refactor reusable public functions:

```python
run_source_specs(
    specs,
    config,
    *,
    local_workers,
    completed_work_ids=(),
    critical_tags=None,
) -> GenerationResult
```

`generate_dataset()` may delegate to it for backward compatibility.

Add:

```python
label_candidate_chunk(
    candidates,
    config,
    *,
    completed_work_ids=(),
) -> SearchDataset
```

This extracts the current sequential `_label_pool()` logic from the CLI into `mj.training`, so local and distributed execution share exactly one implementation.

No cluster networking code enters `teacher_generate.py`.

### `mj/training/distributed_teacher.py`

Adapters:

```text
build_source_spec_jobs(...)
build_candidate_jobs(...)
execute_teacher_job(job, campaign, local_workers)
write_teacher_result_shard(...)
```

Every job writes an isolated JSONL shard plus manifest:

```text
results\<job_id>\
  dataset.jsonl
  manifest.json
```

Worker never appends to another worker's JSONL.

## 10. Teacher Output Commit Protocol

Worker writes:

```text
dataset.jsonl.tmp
manifest.json.tmp
```

to local SSD, validates row count/fingerprints, copies to a unique SMB staging directory, then atomically publishes the manifest last.

A result is visible to merge only when `manifest.json` exists and contains:

```json
{
  "schema": "search-cluster-result-v1",
  "job_id": "...",
  "campaign_id": "...",
  "worker_id": "...",
  "git_commit": "...",
  "rows": 123,
  "artifact_sha256": "...",
  "dataset_fingerprint": "...",
  "input_fingerprints": {},
  "status": "SUCCEEDED"
}
```

A stray `.tmp` or dataset without committed manifest is ignored and may be cleaned later.

## 11. Teacher Merge Gate

Existing `scripts/search_dataset_merge.py` remains the merge primitive and is extended with cluster validation:

```text
--job-manifests <glob>
--expected-jobs N
--campaign-id ID
--require-complete
```

Before merge it MUST verify:

- every expected job has one committed success result;
- all job IDs are unique;
- generation and policy/search/belief/budget/feature fingerprints agree;
- no unknown job result is mixed into the campaign;
- `merge_datasets()` removes duplicate work/state rows deterministically;
- merged row fingerprint is written to the campaign summary.

Cluster merge failure blocks training.

## 12. Paired Evaluation Distribution

`PairedSchedule.rows()` already assigns a stable `index`, seed, seat, dealer, YCBK and cluster. Distribution partitions these rows by `index`.

### `mj/training/paired_eval.py`

Add:

```python
play_rows(rows, *, candidate, baseline, opponent_factory) -> list[dict]
write_paired_rows(path, rows)
read_paired_rows(path)
merge_paired_rows(shards, *, expected_indices)
```

`merge_paired_rows` keys rows by:

```text
(matrix, index)
```

and rejects conflicting duplicate rows.

### `mj/training/distributed_paired.py`

Build/execute `paired_rows` jobs. Worker output is raw pair rows only. The final `paired_score_report()` is run once on PC-A **after all rows are merged**, preserving the existing source-game clustered bootstrap semantics.

This applies to both fast paired and the full 4096-pair gate.

## 13. CLI

Add `scripts/search_cluster.py` with subcommands:

```text
coordinator
worker
submit-teacher
submit-pool
submit-paired
status
retry
merge
```

Examples:

PC-A:

```powershell
python scripts/search_cluster.py coordinator `
  --bind 0.0.0.0 `
  --port 8765 `
  --db D:\mj-cluster\cluster.sqlite `
  --artifact-root \\PC-A\mj-cluster
```

PC-B:

```powershell
python scripts/search_cluster.py worker `
  --coordinator http://PC-A:8765 `
  --worker-id pc-b-13400f `
  --cache-dir D:\mj-worker-cache `
  --teacher-processes 8 `
  --paired-processes 8
```

PC-A optional worker:

```powershell
python scripts/search_cluster.py worker `
  --coordinator http://127.0.0.1:8765 `
  --worker-id pc-a-9400f `
  --cache-dir D:\mj-worker-cache `
  --teacher-processes 2 `
  --paired-processes 2
```

All commands accept an optional shared token from environment, e.g. `MJ_CLUSTER_TOKEN`. Coordinator SHOULD bind only to the trusted LAN/private firewall profile.

## 14. Existing CLI Compatibility

`scripts/search_teacher_generate.py --workers N` keeps current local behavior.

Cluster submitter MAY reuse the same argument parser/profile builders, but it freezes those resolved profiles into the campaign manifest instead of executing all work in the submit process.

`scripts/search_bc_paired.py` keeps current one-process report behavior. Cluster mode uses the shared `paired_eval.py` primitives and then generates the same final report shape.

## 15. Windows-Compatible Pipeline

Add `scripts/search_distill_pipeline.py` as the cross-platform successor to the current `.sh` orchestration.

High-level flow:

```text
freeze campaign
  -> candidate pool / selected pool
  -> submit Teacher jobs
  -> wait until campaign complete
  -> validate + merge dataset shards
  -> stop/pause PC-A CPU worker
  -> train PolicyNet on RTX 2060
  -> offline + hard-set gates
  -> submit fast paired jobs
  -> merge raw rows + report
  -> if passed, submit full paired jobs
  -> merge raw rows + report
  -> runtime gate
  -> append PolicyIteration record / promote
```

Training itself remains a normal local call to `scripts/search_bc_train.py`.

## 16. Resume and Failure Recovery

### Worker crash / reboot

- lease expires;
- coordinator requeues the same semantic job;
- new attempt may run on either machine;
- committed success result always wins;
- late duplicate completion is accepted only if its result fingerprint matches the committed result; otherwise it is quarantined as a conflict.

### PC-A coordinator restart

SQLite WAL-backed state is reopened; active leases that expired while coordinator was down become pending.

### SMB unavailable

Worker keeps validated result in local staging and reports `artifact_publish_failed`. Job remains retryable; compute SHOULD NOT be repeated if the local result fingerprint matches the job and can be republished.

### Partial campaign

Training/evaluation merge is forbidden until `expected_jobs == succeeded_jobs`, unless the campaign was explicitly created with `allow_partial=true` for diagnostics only. Partial artifacts can never be used for promotion.

## 17. Provenance

Every cluster result/report records:

```text
campaign_id
job_id(s)
worker_id(s)
git_commit
generation
policy_version
dataset_version
search_fingerprint
belief_fingerprint
teacher_budget_fingerprint
feature_contract_fingerprint
worker capability snapshot
wall time
cpu process count
retry count
artifact fingerprints
```

`run_summary.py` adds aggregate cluster metrics:

```text
teacher_states_per_minute
paired_rows_per_minute
job_p50/p95_seconds
lease_wait_p50/p95_seconds
worker_busy_fraction
worker_failures
retry_jobs
```

## 18. Determinism and Correctness Tests

A fixed mini campaign MUST prove:

1. one-machine local vs cluster-1-worker produce the same set of `work_id`s and dataset fingerprint;
2. cluster-1-worker vs cluster-2-workers produce the same merged dataset fingerprint;
3. reordering job completion does not change merge output;
4. killed worker / expired lease produces exactly one semantic result after retry;
5. duplicated identical result is harmless; conflicting duplicate is rejected;
6. paired rows contain every expected `(matrix,index)` exactly once;
7. paired report computed after distributed merge equals local report for the same raw rows;
8. git/config fingerprint mismatch prevents job lease.

Floating-point search implementation changes are not hidden by this layer; if a hardware/backend change itself changes Teacher results, that is a separate teacher-parity issue and must fail the equivalence test.

## 19. Performance Validation

Record baseline on PC-A alone, then:

```text
A only
B only
A + B
```

for the same frozen Teacher campaign.

Primary measure:

```text
teacher labeled states / wall-clock minute
```

Secondary:

```text
search simulations / second
paired rows / minute
CPU utilization
RAM peak
SMB bytes/sec
```

The initial cluster target is at least `1.5x` PC-A-only Teacher throughput without changing merged dataset semantics. This is a deployment target, not a reason to relax correctness.

For PC-B, benchmark external process counts `4/6/8/10/12`; do not assume 16 logical CPUs implies 16 independent Teacher processes.

## 20. Security

- coordinator listens on LAN/private interface only;
- optional bearer/shared token is supported and recommended;
- no arbitrary command strings in jobs;
- job `kind` selects a fixed handler registered in code;
- artifact paths are normalized under the configured cluster root;
- worker never executes repository code from a job payload;
- checkpoints/configs are referenced by frozen artifact identity/fingerprint.

## 21. Rollout Plan

### Phase A: local cluster semantics

- implement job model/store and one local worker;
- prove equivalence to current local Teacher and paired commands.

### Phase B: two-machine Teacher

- PC-A coordinator + optional 2-process worker;
- PC-B 8-process worker;
- benchmark and tune job size/process count;
- merge gate required before training.

### Phase C: distributed paired evaluation

- fast paired first;
- then full 4096 paired gate.

### Phase D: optional extensions

- rollout/candidate-pool jobs;
- RX 6600 inference only if profiling shows policy inference dominates and a supported backend is validated;
- additional machines.

DDP remains out of scope until profiling proves PolicyNet backprop is a dominant wall-clock bottleneck.
