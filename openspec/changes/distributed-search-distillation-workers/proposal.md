# Proposal: Distributed Search Distillation Workers

## Why

当前 search-distillation 训练闭环已经具备单机多进程 Teacher、active candidate pool、teacher cache、dataset shard merge、paired evaluation 和 staged gates，但重计算仍然被限制在单台主机：

1. `mj.training.teacher_generate.generate_dataset(..., workers=N)` 通过本机 `ProcessPoolExecutor` 扩展，只能使用一台机器的 CPU。
2. active-pool labeling 仍由单进程顺序执行，无法把 selected candidate states 分给第二台机器。
3. `scripts/search_bc_paired.py` 按稳定 schedule 顺序执行所有 pair，没有跨机器 raw-row shard 协议。
4. 两台 Windows 机器为异构硬件（RTX 2060 + i5-9400F、RX 6600 + i5-13400F），不适合把第一版复杂度投入 NVIDIA/AMD 跨机同步梯度。
5. 当前最昂贵且最容易横向拆分的 workload 是 Search Teacher、rollout/game simulation 和 paired evaluation，而不是 PolicyNet 的单步反向传播。

本 change 在不改变 Teacher 语义、PolicyNet 网络结构和发布 gate 的前提下，引入一个可恢复、确定性、可扩展的 Job/Worker 层，让两台 Windows 主机共同完成离线重计算。

## Goal

第一版部署采用：

- **PC-A / Master + Trainer**：i5-9400F + RTX 2060，运行 coordinator、PolicyNet CUDA 训练、可选少量 CPU worker。
- **PC-B / Compute Worker**：i5-13400F + RX 6600，第一版只把 CPU 用于 Teacher / simulation / paired evaluation；RX 6600 不参与梯度训练。
- 两台机器通过局域网 coordinator 动态领取 job；共享目录只交换不可变输入和已提交输出，不以 SMB 文件锁充当调度器。
- 单机 local mode 保持兼容；worker 数量、机器数量和 job 粒度不能改变最终数据/paired schedule 的语义。
- Worker 崩溃、Windows 重启、网络中断后可从 lease/job manifest 恢复，不重算已完成 work。
- 后续新增第三台 CPU/NVIDIA/AMD 主机时无需修改 Teacher / paired evaluation 核心逻辑。

## Non-Goals

- 不做跨 NVIDIA/AMD 的 DistributedDataParallel、gradient all-reduce 或参数服务器训练。
- 第一版不要求 RX 6600 承担 PyTorch 训练或 Teacher GPU inference。
- 不改变 Search Teacher 的 information-set / belief / budget / cache 语义。
- 不改变 active sampling、regret-aware loss、replay、hard-state 或 paired release 标准。
- 不把 coordinator 暴露到公网，不引入云队列或外部数据库。
- 不允许 worker 自行修改 generation/config/checkpoint；所有 campaign provenance 由 master 冻结。

## Capabilities

### New

- `distributed-search-distillation-workers`
  - cluster campaign manifest；
  - worker registration/capability matching；
  - deterministic job identity；
  - lease/heartbeat/retry 状态机；
  - Teacher source-game / active-pool chunk jobs；
  - paired-evaluation chunk jobs；
  - crash-safe immutable shard output；
  - completeness/dedup merge gate；
  - cluster status/throughput metrics。

### Modified

- `search-teacher-dataset`
  - 允许同一 generation 的 source specs / selected candidates 被拆成独立 shards；
  - merge 前必须校验 generation、policy/search/belief/budget fingerprints。
- `staged-evaluation-gates`
  - fast/full paired 可先生成分布式 raw pair rows，再统一计算最终 clustered bootstrap report。
- `search-distillation-pipeline`
  - 增加 Windows 可执行的 Python orchestration path；现有 local commands 仍可单独执行。

## Impact

预计新增：

- `mj/training/distributed_jobs.py`
- `mj/training/job_store.py`
- `mj/training/worker_runtime.py`
- `mj/training/distributed_teacher.py`
- `mj/training/distributed_paired.py`
- `scripts/search_cluster.py`
- `scripts/search_distill_pipeline.py`
- `tests/test_distributed_jobs.py`
- `tests/test_distributed_teacher.py`
- `tests/test_distributed_paired.py`

预计修改：

- `mj/training/teacher_generate.py`
- `mj/training/paired_eval.py`
- `scripts/search_teacher_generate.py`
- `scripts/search_bc_paired.py`
- `scripts/search_dataset_merge.py`
- `mj/training/run_summary.py`（cluster provenance/throughput）
- `mj/training/__init__.py`

现有 `scripts/search_distill_pipeline.sh` 保留，Python pipeline 成为 Windows cluster 的推荐入口。
