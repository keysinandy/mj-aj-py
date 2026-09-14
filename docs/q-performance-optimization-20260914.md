# shape-v1 Q 性能优化记录

日期：2026-09-14。基线提交为 `72a4f65`；本记录只覆盖离线本地牌局，不是线上房验收。

## 已实施

- `EvalContext.fast_replace` 只用于已验证上下文派生的假想牌面，保留公共构造器的完整校验。
- 单次决策复用 Q0 基础特征和下一摸后的最佳弃牌结果；I 的“原手保留基线”由原始 U1 增量计算，不再逐摸重复 ukeire。
- 非财神最佳弃牌使用 Rust `rust-batch-v1` 两阶段批量内核：先找最低向听，再只对并列候选计算 U1；解释用的子牌进张按需回填。
- profile 指纹使用不可变参数的有界缓存。
- Q 阶段使用 `I/H2` 的保守上界进行严格低于已完成 Q 的候选剪枝，并输出 `q_pruned` 与 `q_upper_bound`；不使用牌号 top-K。

## 验证

环境：Python 3.11.14；Rust wheel 由 `maturin build --release --out /tmp/mj-kernel-wheels` 重建并解包到 `/tmp/mj-kernel-test`，运行时通过 `PYTHONPATH=/tmp/mj-kernel-test:.` 加载。

- `tests/test_hand_eval.py`：17 passed。
- 全量 `pytest -q -p no:cacheprovider`：394 passed，2 subtests passed（96.03s）。
- `scripts/rust_parity.py --n 100 --bench 500`：shanten 3000、ukeire 3000 随机对拍通过；该次 Rust 加速分别为 31.3x、69.7x。
- 80 组随机 Q 对拍（含 locked、YCBK、visible、live-wall）：完整语义字段 0 差异；超出 1000ms 预算的单例不作为对拍失败。
- 5 个固定首个摸牌状态使用 2000ms 宽预算做全候选 Q 穷举，对比上界剪枝后的最终弃牌：5/5 一致；其中 3 个状态安全剪枝 2–8 个候选。
- 20 局确定性本地牌局（seed 190000 起，shape-v1，一次复跑）：弃牌 795 次，均值 17.76ms、p95 22.87ms；完整 Q 88 次，安全剪枝候选 19 个。此前同命令复跑为 817 次/16.86ms/21.34ms/134 次 Q，显示机器负载和不可抢占内核长尾的影响；只用于趋势判断。

## 边界

当前默认 profile 仍为显式 `shape-v1`，生产默认仍是 legacy。反应窗 7ms 下完整 Q 仍按预算回退到完整 Q0；本轮没有把回退伪装成 Q，也没有放宽线上窗口。Rust 扩展需要随部署重建，仓库外的已安装旧 wheel 不会自动更新。

尚未完成 4096 局独立收益、十场并发性能闸门和三个新房逐窗验收；不能据此宣称线上 Q 回退率或窗口损失已解决。
