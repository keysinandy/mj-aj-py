# Proposal: Optimization Evidence Contract

## Why

在 `main@e1bcab4` 与 Rust weighted v5 恢复后，训练和策略优化的证据链仍存在容易误读的缺口：`search_distill_pipeline.sh` 写出的离线与 fast-paired 文件名/结构与 `run_summary.py` 的消费方式不同；fast paired 仅打印 `verdict`，即使报告为 `regression`，脚本也可能宣布通过；没有前代报告时离线选择的 `promoted` 不代表优于前代。一次 teacher 恢复测试曾出现 `16 != 8`，但同一 v5 环境后续未能复现，不能凭表象改写身份规则。

此外，线上镜像每次重建 `Game`，爆头推进 X 轮数不能跨决策累计；已更新的 Shape Guard 主规格是严格字典序口径，而两个未归档 change delta 若机械同步可能覆盖该口径。这些问题应先形成可验证的契约，再分别实施与验收。

## What Changes

- 新增一套证据契约，要求晋级门真正消费 paired verdict/样本数/置信区间，并明确 fast 筛查、full 晋级、报告来源与拒绝原因；摘要兼容生产者当前的 nested matrix 与历史平铺格式。
- 要求 teacher 断点恢复的跳过身份与落盘身份一致；面对无法稳定复现的失败，先区分指纹错位和轨迹漂移，禁止无证据修复及静默去重。
- 要求 pending Shape Guard delta 在归档前保留现行主规格的严格字典序、相等不准入、准入与审计契约，不以“严格校验通过”替代语义核对。
- 将线上爆头 X 轮数持久化列为独立待验证优化，明确其行为开关和配对/性能门；不随本 change 启用 X 或 Piao pass cap。

## Scope

本 change 只定义规格、设计与任务，不更改评价器、训练、平台、测试、参数、正式比赛日志或历史 shard。它补充 `regret-aware-active-distillation`、`search-teacher-distillation-bc` 和现有 Shape Guard 变更，不重新宣布它们已完成的事项未完成。

## Impact

后续实施预计涉及 `mj/training/run_summary.py`、`mj/training/regret_selection.py`、`scripts/search_distill_pipeline.sh`、`mj/training/teacher_generate.py`、相应测试与旧 change 的 delta 文档；平台轮数持久化为独立后续工作。`PROGRESS.md` 是决策权威；任何实际策略行为改动必须同步它及测试，并以 v5 非降级环境重新建立可比较证据。
