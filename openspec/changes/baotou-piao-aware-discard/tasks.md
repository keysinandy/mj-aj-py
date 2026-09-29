# Tasks

## 1. 爆头进张纯函数

- [x] 1.1 在 shanten.py 层实现 `baotou_ukeire(counts, locked, visible)`（含
  `is_baotou_wait` 的 `(手牌字节, locked)` 记忆化与候选剪枝），并写单测：
  构造已知手牌断言爆头进张集合与加权值（验证：`python3 -m pytest tests/test_shanten.py -k baotou -q`）
- [x] 1.2 随机差分验证剪枝安全性：随机手牌 × locked=0..4，剪枝候选与全量 34 枚举
  结果逐一相等（验证：新增差分测试用例全绿，形式对齐 `tests/test_shanten_props.py`）
- [x] 1.3 visible 口径回归：构造自持刻子/多财神手牌，断言 `baotou_ukeire` 的
  未见折算与 `ukeire` 同口径、无双重扣减（验证：对应断言用例通过）

## 2. 弃牌排序接入

- [x] 2.1 `choose_discard` 实现 D2 排序键（触发态 `hand[W]>0` 且 s==0；tier0
  爆头听优先档，同档比较 `-(1.5*baotou_uke+current_selfdraw_hu_ukeire)`；不持财神键不变）（验证：新排序单测——
  tier0 胜出 / tier1 组合进度排序 / 不持财神与旧基线逐候选一致）
- [x] 2.2 预算与回退：爆头进张计算加节点/单调时钟预算，超限回退既有 legacy 键
  并记 `fallback_reason`（验证：注入超小预算的用例断言回退且结果可归因）
- [x] 2.3 既有 legacy 排序断言显式限定到不持财神状态，持财神场景改用新档断言
  （验证：`python3 -m pytest tests/test_bot.py -q` 全绿）

## 3. 墙量守卫

- [x] 3.1 bot.py 模块级 `PIAO_WALL_GUARD = 6`：`_should_piao` 门改
  `live_wall_left() >= PIAO_WALL_GUARD`；`choose_action` 在 `HU ∈ acts` 且
  `live_wall_left() < PIAO_WALL_GUARD` 时短路直接返回 HU（跳过飘与杠比较）
  （验证：单测——墙 5 爆头态摸白直接 HU、墙 ≥6 仍弃胡打白飘、墙 <6 时杠不覆盖 HU）
- [x] 3.2 （旧实现，**由 3.4 替代**）HU 合法且活墙通过硬门时，检测合法非财神弃牌能否
  形成全牌爆头听并提前返回；该行为已被 2026-09-29 回放证明会绕过财飘/two-ply，保留仅作历史记录
- [x] 3.3 （旧回归，**需按 3.5 重写**）覆盖 X/Y/Z 与冻结合法性；原断言
  `hu_baotou_next_draw_override` 为最终动作的用例不再代表目标行为
- [x] 3.4 将 `_choose_draw_action` 改为 HU-window action-root 仲裁：硬墙门后一次性建立
  immediate HU / piao_discard / baotou_next_draw / self_kong；删除“发现非白爆头即 return”语义
- [x] 3.5 重写旧 override 单测：保留冻结合法性与墙 <6 直接 HU，但墙 ≥6 时断言候选集完整、
  不再断言非白爆头无条件胜出；X/Y/Z 不得非对称删除财飘候选
- [x] 3.6 接通 action-root continuation/two-ply：legacyV2 在应进入 Stage B 时不得被爆头分支绕过，
  immediate HU 作为基线，财飘/下一摸爆头/自杠按统一动作价值比较
- [x] 3.7 修正解释字段：`decision_scope=hu_window_arbitration`，准确记录 baotou/piao candidate scan、
  `stage_b_entered`、candidate type/value、selected/reason

## 4. 一致性与验收

- [x] 4.0 自适应收手实现：X/Y/Z 触发（轮数 per-Game 记账、对手副露、活墙）、
  吸收态、飘抑制（验证：tests/test_bot.py 扩 6 例全绿）
- [x] 4.6 X/Y/Z 参数扫描：`local/ab_baotou_sweep.py` 粗筛+精跑（**YCBK 关口径**
  的配对结算分，参照含 X=0 与 X=99 无条件），择优设为 bot 默认常量
  （验证：扫描结果 JSON；YCBK 开场景当前项目不考虑，见 spec/design scope）
- [x] 4.1 YCBK 无关性：同一持财神牌面在 `you_cai_bi_kao` 开/关下弃牌选择一致
  （验证：参数化用例两端断言相等）
- [x] 4.2 全量回归：`python3 -m pytest tests/ -q` 全绿；Rust 内核对拍不受影响
  （`python3 scripts/rust_parity.py`，如扩展已装）
- [x] 4.3 新旧 bot 对弈评估：`fair_match(n=192)` 新 bot vs 旧 bot（或 vs 3 启发式
  对照）确认无胜率/均分回退（验证：评估报告落 change artifacts/）
- [ ] 4.4 线上冒烟：match_runner 走 `Mirror.build_game` 路径跑 ≥10 局，确认墙守卫、
  新排序和 HU-window 统一仲裁在线上无异常（日志检查候选完整性、Stage B/continuation、reason 与弃牌合法性）
- [x] 4.5 同步文档：PROGRESS.md bot 决策原则节（新增爆头听优先档、爆头进张、
  墙守卫常量）与必要结论（验证：PROGRESS.md diff 审阅）
- [x] 4.7 2026-09-29 round4 回放回归：seq856 断言弃白不构成财飘；seq880 断言开始生成财飘候选；
  seq904/943/967/991/1015/1039/1063 均断言财飘候选持续存在，且 14/10/6 张活墙时不再
  由 `hu_baotou_next_draw_override` 提前返回；seq1070 对手胡牌作为“延迟胡存在抢胡风险”的证据记录

## 5. 归档

- [ ] 5.1 线上验收通过后归档 change 并同步主 spec（`openspec archive`）
