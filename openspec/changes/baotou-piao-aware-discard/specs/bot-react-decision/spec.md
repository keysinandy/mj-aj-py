# Spec Delta

## MODIFIED Requirements

### Requirement: 弃牌排序使用共享评价且胡/飘路径保持不变

`choose_action()` 的 HU 优先 MUST 保持既有合法性语义；当活墙低于硬墙门时直接 HU，
活墙达到门槛时 SHALL 按 `bot-baotou-piao-discard` 的 HU-window action-root 统一仲裁
immediate HU、财飘、下一摸爆头与自杠。任何非白爆头 helper 不得在财飘候选和当前 HU
进入同层价值比较前提前覆盖。`_should_piao` SHALL 作为财飘候选判定的一部分，而不是
抢先/滞后的独立终局返回。
不持财神状态下 legacy 的 `choose_discard()` 排序 MUST 保持；持财神状态下的排序 SHALL
遵循 `bot-baotou-piao-discard` 的爆头听优先档与组合进度分。shape-v1 SHALL 在合法
性、最低向听及财神保护后使用共享评价，允许同向听中直接进张较少但完整收益更高者
胜出。不得为了保留旧“进张永远压过结构”的测试而暗中禁用新排序；该旧断言应明确
限定到 legacy（不持财神状态），新的取舍按本变更规范验证。

#### Scenario: HU-window 候选统一建立
- **WHEN** 任一 evaluator 进入 HU 合法窗口且活墙可摸张数 ≥ 6
- **THEN** 所有 evaluator 共享同一 action-root 候选语义；财飘、非白下一摸爆头与当前 HU
  必须先同时建立，再由对应 evaluator 的价值链选择

#### Scenario: 不再无条件覆盖 HU
- **WHEN** 存在合法非财神弃牌可形成全牌爆头听，同时弃白也可形成财飘
- **THEN** 非财神候选 MUST NOT 以 `hu_baotou_next_draw_override` 提前返回；财飘和 HU
  必须进入同层比较

#### Scenario: 不持财神的 legacy 弃牌不漂移
- **WHEN** 同一不含财神的状态显式选择 legacy
- **THEN** 弃牌排序和旧冻结基线一致

#### Scenario: 持财神的 legacy 弃牌走新排序
- **WHEN** 持财神状态显式选择 legacy
- **THEN** 排序遵循 `bot-baotou-piao-discard` 的爆头听优先档与组合进度分

#### Scenario: shape-v1 使用共同站立牌面评价
- **WHEN** 同一站立牌面分别由普通弃牌候选与吃碰后候选产生，评价输入与层级相同
- **THEN** 原始特征、共享 Q 和稳定次序语义一致
