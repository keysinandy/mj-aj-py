## Why

当前 legacyV2 已有 standing shape 与 weighted two-ply，但 current ukeire 产生唯一赢家时仍可走 frontier_singleton，导致 future search 完全不执行。

用户专项牌例：

~~~text
副露: PON 1m
摸 4p 后暗手:
3m 7m 2p 3p 4p 4p 7p 4s 8s 9s 9s

9s: shanten=3, ukeire=82, shape_loss=9
8s: shanten=3, ukeire=80, shape_loss=5
4p: shanten=3, ukeire=78, shape_loss=10
3m: shanten=3, ukeire=77, shape_loss=2
7p: shanten=3, ukeire=77, shape_loss=2
7m: shanten=3, ukeire=73, shape_loss=2
4s: shanten=3, ukeire=72, shape_loss=2
~~~

实际路径为：

~~~text
level=legacy-one-ply
search_used=false
future_nodes=0
short_circuit=frontier_singleton
weighted_two_ply_entered=false
stage_b_entered=false
~~~

问题不是简单的 pair bonus 太小，而是 899s 中第二张 9s 的边际结构角色没有被识别：它同时保留 99 对子/碰牌路线与 89 搭子路线。82 对 77 的小幅即时进张优势不应自动获得 one-ply 终局权。

同时不能把修复做成“对子永远保护”。反例是 7899s + 5w：第二张 9s 虽可组成 99，但 789 已完整，9s 可能只是带备用对子价值的冗余附着牌；5w 虽是孤张，却具有高连接性。

本 change 引入 marginal structure role，用于决定“是否值得进入 two-ply 比较”，而不是直接决定最终弃牌。

## What Changes

- 新增 versioned MarginalStructureRole。
- 区分 lost_pair_option、lost_taatsu_option、completed_meld_redundancy、alternative routes、singleton live connectivity。
- 899s -> 89s 应识别为非冗余的复合结构损失。
- 7899s -> 789s 应识别 completed-meld redundancy；不得仅因失去 99 就硬保护 9s。
- frontier_singleton 改为 conditional short-circuit：唯一速度赢家若破坏关键结构，且 slack 内存在低损失 challenger，则阻断短路并进入 bounded weighted two-ply。
- 初版 shanten-aware ukeire slack：
  - shanten 0: 0
  - shanten 1: 2
  - shanten 2: 4
  - shanten >=3: 6
- frontier 仍不超过 max_frontier_candidates=3。
- 用户 899s golden 在完整搜索成功时必须进入 weighted two-ply，最终不得弃 9s。
- 7899s + 5w 作为反过拟合 fixture：不规定固定弃 9s 或 5w，只要求 pair 不能形成硬保护。
- hard budget 保持 50ms，不增加搜索深度。

## Out of Scope

- 不做 three-ply、MCTS 或 hidden-world rollout。
- 不读取对手暗牌或真实墙序。
- 不把 same-tile unseen 当成对手未来出牌概率。
- 不为 899、7899 或任意具体牌号写硬编码。
- 不提高 frontier cap 或 online hard budget。
- 不修改 shanten、ukeire、胡牌、财神或计番规则。

## Capabilities

### New Capabilities

- legacy-marginal-structure-role

### Modified Capabilities

- legacy-two-ply-frontier-shape-guard
- legacy-two-ply-weighted-frontier
- bot-decision-explanations

## Impact

预计涉及：

- mj/legacy_eval.py
- mj/shape_quality.py 或新增 mj/structure_role.py
- mj/bot.py
- replay / evaluation diagnostics
- targeted tests / benchmark / paired A/B

第一版优先只改变 root frontier 构造；weighted Stage A/B 的既有 Rust 语义保持不变。
