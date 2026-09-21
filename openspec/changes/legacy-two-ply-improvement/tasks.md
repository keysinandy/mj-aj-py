# Tasks

## 1. Profile、数据契约与候选评价基础

- [x] 1.1 定义 `legacy-two-ply-v1` evaluator/profile、版本指纹、模型假设、预算和回退状态，并验证相同配置生成稳定 fingerprint、参数变化生成不同 fingerprint
- [x] 1.2 建立不可变的根候选/未来评价结果结构，明确 `future_improve_weight`、`future_ukeire`、`future_ukeire_mean`、最佳后续弃牌和 missing 状态，并验证 JSON 序列化不把缺失字段写成零
- [x] 1.3 实现根候选的合法性、最低向听、财神保护和当前 ukeire 前沿筛选，验证较低当前 ukeire 候选不会进入 V1 排序且冻结/抓打圈动作集不扩大

## 2. 二阶未来摸牌评价

- [x] 2.1 实现一次未来摸牌和一次最佳合法弃牌的非递归枚举，验证每个子状态按向听优先、进张次优先选择最佳摸切，且不触发第三层搜索
- [x] 2.2 实现 `visible_after_draw` 和 `4-visible` 无放回权重，验证牌被摸走后未知数减少、弃牌不恢复未知数、物料超过四张和未知输入会安全失败
- [x] 2.3 计算 `future_improve_weight`、加权总量 `future_ukeire` 和仅展示用的 `future_ukeire_mean`，验证权重为零、只剩一种牌和根状态已听牌等边界
- [x] 2.4 增加有容量上限的决策内 future memo，并将手牌、摸牌、locked、visible、规则/阶段/资格和 profile 纳入键；通过不同牌河相同手牌的隔离测试

## 3. Legacy BOT 集成与安全回退

- [x] 3.1 将 V1 评价接入普通 legacy 弃牌入口，按当前 ukeire、未来改良、未来 ukeire、牌型损失、喂牌风险的顺序选择，并验证现有 HU/KONG/反应分支仍由原规则入口处理
- [x] 3.2 实现固定节点/单调时间预算和整次事务式回退，验证任一前沿候选未完成时丢弃所有部分 V1 结果、恢复完整 legacy 动作并记录原因
- [x] 3.3 为 `20260920` `u_9812ba08fe2f_a_e18e58e3acb8_r1_b9_t0` `seq=100` 建立可复现 fixture，验证 `3t`/`9b` 当前进张并列时按未来特征选择预期候选，同时验证 `3b` 当前进张劣势不能越级胜出
- [x] 3.4 保留 V1 关闭时的 legacy 基线入口，验证同一 fixture 的基线结果、V1 结果、profile 和实际层级可区分且可回滚

## 4. 决策解释与回放证据

- [x] 4.1 扩展 `decision.evaluation` 候选字段、模型假设、future 节点/缓存统计、最佳后续弃牌和回退状态，验证精简解释与完整解释不重复搜索且不改变动作
- [x] 4.2 将实际 evaluator、profile fingerprint、level、预算、missing 和 fallback reason 关联 gid/decision/action，验证 V1 反事实重算不会被写成原线上执行动作
- [x] 4.3 保持旧日志兼容，验证没有评价对象的记录仍显示 `legacy_unrecorded`，缺失 future 字段不补零且不参与排序

## 5. 回归、性能与发布证据

- [x] 5.1 补充纯评价、BOT 固定牌例、财神/冻结/visible 守恒、缓存隔离和确定性测试，并运行 `python3 -m pytest tests/test_bot.py tests/test_shanten.py -q -p no:cacheprovider`
- [x] 5.2 增加 legacy 基线与 V1 的同种子小规模对照和候选特征报告，验证 `3t/9b` 之外没有非法动作、隐藏信息依赖或异常回退
- [x] 5.3 运行 Rust/Python parity、完整测试集和交错性能基准，报告节点、缓存、p50/p95/p99、超预算率和回退率；性能未达标时保持 V1 opt-in
- [x] 5.4 按项目独立成对收益、strict 校验和线上逐窗闸门生成发布 manifest，只有全部证据通过才评审将 V1 纳入 legacy 默认，否则保留基线默认和显式开关
