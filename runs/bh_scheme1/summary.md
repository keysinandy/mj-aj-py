# legacyV2 BigHandIntent 方案 1 评估（2026-10-08）

+1 override 路径：已打通。独立积分收益：未确认。性能门禁：未通过。
默认 BigHandIntent 仍关闭。只有独立收益成立且性能失败才触发 Rust 改造；本次 Rust 判定：未满足触发条件。

## 实现与验证

Rust v5 仅评价同向听 speed frontier；Python 只补唯一 +1 challenger。速度胜者由 complete 或原安全 bounds 确定，challenger 必须 complete。共享剩余 50ms 搜索预算，超时不提交结果，frontier 不超过 3。
`perf_counter` 取代 Windows 15.625ms 分辨率的 coarse monotonic 计时。实验开关关闭时不补跑。模块回归 94 passed、16 subtests；OpenSpec strict 与 diff check 通过。

Baseline fingerprint：`9a2d4dba9c305668`；candidate：`2ce9fa0e5717d68c`。历史 F1 fingerprint 1215a0047e95ebe8 先于新增三个版本化 profile 字段，不是本次字段扩展后的指纹。各证据 JSON 保存源文件 SHA-256。

## 积分

粗扫四点每点 512 局，精扫按粗扫均值取有 override 的前两点，每点独立 2048 局。旧扫描庄家总在 candidate 组，故仅作探索记录。最终确认使用全新种子、均衡庄家与两组 hero 座位，并交替两臂执行顺序。

| 阶段 / 配置 | 局数 / 独立对数 | 每 hero 积分差 | 95% CI | override |
|---|---:|---:|---|---:|
| 探索粗扫 / conservative | 512 / 256 | -0.228516 | [-0.623047, +0.044922] | 24 |
| 探索粗扫 / balanced | 512 / 256 | -0.277344 | [-0.675781, +0.013672] | 29 |
| 探索粗扫 / early | 512 / 256 | -0.277344 | [-0.695312, +0.031250] | 31 |
| 探索粗扫 / diagnostic_wide | 512 / 256 | -0.277344 | [-0.695312, +0.031250] | 31 |
| 探索精扫 / conservative | 2048 / 1024 | -0.051270 | [-0.143555, +0.032715] | 56 |
| 探索精扫 / balanced | 2048 / 1024 | -0.084473 | [-0.193359, +0.017578] | 90 |
| 公平确认 / conservative | 2048 / 1024 | -0.109863 | [-0.267090, +0.033691] | 69 |
| +1 对同向听消融 / conservative | 1024 / 512 | -0.197266 | [-0.434570, -0.005859] | 37 |

积分是相同 source seed/dealer 下两个 hero 的平均结算差；生产对手保持冻结。区间按独立 source-seed pair bootstrap，不能把两局视为两个独立统计样本。消融只关闭 hero 的 +1 路径，其余 BigHand 门槛和生产对手相同。

## 公平确认的白板桶与补跑

| 决策前白板数 | 已评估弃牌 | challenger | override | override ‰ |
|---|---:|---:|---:|---:|
| 0 | 10520 | 0 | 0 | 0.000 |
| 1 | 6161 | 0 | 0 | 0.000 |
| 2 | 1284 | 108 | 66 | 51.402 |
| 3 | 174 | 13 | 3 | 17.241 |
| 4 | 1 | 0 | 0 | 0.000 |

Python 补跑 113/121 完整。p50=23.792ms，p95=39.006ms；失败原因：`{'hard_deadline': 8}`。override 结果：`{'override_intent_tie': 44, 'strong_intent_override': 69, 'big_hand_challenger_incomplete': 8}`。

| 对局开始时 hero 最大白板数 | 独立对数 | 积分差 | 95% CI |
|---|---:|---:|---|
| 0 | 421 | -0.016627 | [-0.049881, +0.000000] |
| 1 | 495 | -0.039394 | [-0.142424, +0.064646] |
| 2 | 101 | -0.851485 | [-2.336634, +0.519802] |
| 3 | 7 | +0.000000 | [+0.000000, +0.000000] |

完整 JSON 同时保存 candidate 对局过程最大白板数的积分桶与决策白板桶耗时。过程最大白板数受策略影响，仅是描述性结果；以上初始分桶固定于决策之前，也不能当作单次 override 的收益。

## 同机性能

单进程，四个机器人全部使用同一 profile；两 profile 各 3×200 局，每个匹配种子交替先后执行，包含正常决策与 step，不进行额外反事实审计调用。HU 窗口的动作选项数单独识别，不误算为普通弃牌 frontier；实际 frontier 最大为 3。

| Profile | 弃牌样本 | p50 ms | p95 ms | p99 ms | fallback % |
|---|---:|---:|---:|---:|---:|
| baseline | 20300 | 1.4067 | 12.5066 | 18.2458 | 0.000 |
| candidate | 20498 | 2.3909 | 13.9266 | 21.1853 | 0.029 |

弃牌 p95 退化 +11.35%；每批 elapsed/game 中位数退化 +23.13%。门禁（<=10%）：`{'discard_p95': False, 'elapsed_per_game': False, 'sample_size': True}`。

2048 局的公平确认和 1024 局消融不替代既有 4096 局发布门禁。本次按固定协议保留默认关闭；后续应先改进 override 收益判定，再安排确认样本。

协议与原始证据：`plan.json`、`coarse_512.json`、`fine_2048.json`、`fair_confirmation_2048.json`、`plus_one_ablation_1024.json`、`performance_3x200.json`。各 JSON 保留种子、paired score、profile 和源文件指纹。
