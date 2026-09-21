# Spec Delta

## ADDED Requirements

### Requirement: 向听记忆化必须与参考实现逐位一致

shanten 内核 MAY 使用按花色预计算或记忆化的分解表，但结果 MUST 与 `mj/shanten.py` 参考
实现逐位一致，包括财神配刻/配对/成对雀头、`locked` 副露的剩余面子需求、七对分支与张数
校验。表 MUST 构建后只读并可被并行 worker 共享，MUST NOT 依赖调用顺序、线程调度或历史
调用内容；表不可用时 MUST 回退到参考实现路径且不改变结果与解释字段。启用新内核 MUST
以差分对拍与基准为门槛，未通过时保持 opt-in。

#### Scenario: 随机差分对拍
- **WHEN** 对随机手牌、不同财神数量与 locked 0-4 的组合比较新内核与参考实现
- **THEN** 所有 s 值逐位一致，包含 13/14 张与张数非法输入的错误行为

#### Scenario: 并发读表
- **WHEN** 多个 Stage B worker 同时读取同一张记忆化表
- **THEN** 每个 worker 得到相同结果，无锁竞争导致的值差异，且不引入可变全局状态

#### Scenario: 表不可用
- **WHEN** 表未构建完成或被显式关闭
- **THEN** 内核走参考路径，结果与关闭前一致，并在指标中记录该回退

#### Scenario: 只改变延迟
- **WHEN** 同一手牌、同一 profile 分别以新内核与参考内核完整执行
- **THEN** 动作、future 指标与 best discard 行完全相同；只有预算边界与耗时可能不同
