"""启发式出牌 bot。

决策原则(按优先级):
0. 摸牌/杠补牌成胡默认提交 HU;爆头态打白板仍听任意牌(财飘)时,
   墙内活牌足够轮回到自己再摸则弃胡打白飘(×4 起,下次摸牌必胡)。
1. 摸牌阶段弃牌:最小化向听数 → 保护财神 → 最大化进张数 →
   最小化牌型结构损失 → 少喂下家 → tile 编号(仅稳定排序)。
   同向听候选**全部**参与进张比较,不再按编号预截断。
2. 吃/碰/杠:向听数下降才做;杠额外要求不破坏听牌结构
  (杠开 ×2 的期望价值由补充摸牌体现,简单起见杠在向听数
   不变时也做——加速摸牌且杠开有倍率加成)。
3. 打牌倾向:少喂牌——避开下家可能吃的相邻牌(简单启发)。
"""

from .tiles import W
from .shanten import shanten, ukeire
from .win import is_baotou
from .game import (
    PASS, HU, PONG, KONG_OPEN, KONG_CLOSED_BASE, KONG_ADD_BASE,
    CHOW_LOW, CHOW_MID, CHOW_HIGH,
)


def choose_discard(g, seat):
    """返回弃牌 tile。

    优先级:向听数(硬约束,不为打风牌让向听倒退)→ 财神保护 →
    进张数 → 牌型结构损失 → 喂牌风险 → tile 编号(仅作稳定排序)。

    所有最小向听候选都参与精确进张比较,不按 tile 编号预截断——
    否则字牌编号靠后会被挤出候选,孤张字牌留着、数牌搭子反被先拆。
    进张计算贵(纯 Python 全量口径 2.28 局/秒、候选剪枝后 2.94),
    但 shanten/ukeire 已默认走 Rust 内核(rust/,mj/shanten.py 调度器,
    详见其模块 docstring),自博弈实测 ~108 局/秒单核。
    """
    hand = g.hands[seat]
    locked = len(g.melds[seat])
    vis = g.visible_counts(seat)

    cands = []  # (tile, 去除该牌后的手牌, 牌型损失, 喂牌风险)
    best_s = None
    for t in range(34):
        if hand[t] == 0:
            continue
        c = list(hand)
        c[t] -= 1
        s = shanten(c, locked)
        if best_s is None or s < best_s:
            best_s, cands = s, []
        if s == best_s:
            cands.append((t, c, _discard_shape_cost(hand, t), _feed_risk(g, seat, t)))

    best, best_key = None, None
    for t, c, shape, feed in cands:
        uke = ukeire(c, locked, vis)[2]
        key = (t == W, -uke, shape, feed, t)
        if best_key is None or key < best_key:
            best, best_key = t, key
    return best


def _discard_shape_cost(hand, t):
    """打 t 的牌型结构损失,越小越适合打(0 = 孤张字牌,最该打)。

    字牌:孤张 0 < 对子 6 < 刻子 10;
    数牌:基数 2,对子 +4、刻子 +8,再按相邻(±1,+3)与嵌张(±2,+1)
    连接加价——只衡量"拆掉这张损失多少",向听与进张在更外层定夺。
    """
    n = hand[t]
    if t >= 27:
        if n >= 3:
            return 10
        return 6 if n == 2 else 0
    lo = t - (t % 9)
    cost = 2
    if n >= 3:
        cost += 8
    elif n == 2:
        cost += 4
    for d in (-1, 1):
        x = t + d
        if lo <= x < lo + 9 and hand[x]:
            cost += 3
    for d in (-2, 2):
        x = t + d
        if lo <= x < lo + 9 and hand[x]:
            cost += 1
    return cost


def _feed_risk(g, seat, t):
    """打 t 喂下家的粗略风险:下家已见的相邻/同种牌越多越危险。"""
    nxt = (seat + 1) % 4
    seen = [0] * 34
    for d in g.discards[nxt]:
        seen[d] += 1
    for kind, mt in g.melds[nxt]:
        if kind == "chow":
            seen[mt] += 1
            seen[mt + 1] += 1
            seen[mt + 2] += 1
        else:
            seen[mt] += 3
    risk = 0
    if t < 27:
        lo = t - t % 9
        for x in (t - 1, t + 1, t - 2, t + 2):
            if lo <= x < lo + 9:
                risk += seen[x] * 0.5
    risk += seen[t] * 0.3  # 下家可能碰
    return risk


def _should_piao(g, seat):
    """弃胡打白飘判定:成胡在手的 14 张里打出一张财神后,
    站立手牌仍听任意牌(爆头态保持,下次摸牌必胡,倍率翻倍)。

    前提:墙内活牌足够轮回到自己再摸(抓打圈内他家只能自摸胡,
    被抢胡风险低)。
    """
    hand = g.hands[seat]
    if hand[W] == 0:
        return False
    after = list(hand)
    after[W] -= 1
    if not is_baotou(after, len(g.melds[seat])):
        return False
    return g.live_wall_left() >= 5


def choose_action(g, seat):
    """统一入口:返回该 seat 的动作。"""
    acts = g.legal_actions()
    if len(acts) == 1:
        return acts[0]
    if g.phase == "discard":
        if HU in acts:
            if _should_piao(g, seat):
                return W  # 弃胡打白飘(财飘)
            return HU
        return choose_discard(g, seat)
    # react 阶段:吃碰杠决策
    return _choose_react(g, seat, acts)


def _choose_react(g, seat, acts):
    owner, tile = g.pending
    hand = g.hands[seat]
    locked = len(g.melds[seat])
    base_hand = list(hand)
    best_act, best_key = PASS, None

    def eval_after(act, remove):
        c = list(base_hand)
        for t, n in remove:
            c[t] -= n
        return shanten(c, locked + 1)

    cur_s = shanten(hand, locked)  # 含刚打出的候选牌(不精确,仅参考)

    if PONG in acts:
        s = eval_after(PONG, [(tile, 2)])
        key = (s, 0)
        if best_key is None or key < best_key:
            best_act, best_key = PONG, key
    if KONG_OPEN in acts:
        s = eval_after(KONG_OPEN, [(tile, 3)])
        # 杠开期望:向听数不变也杠(加速+倍率)
        key = (s, -1)
        if best_key is None or key < best_key:
            best_act, best_key = KONG_OPEN, key
    for a in (CHOW_LOW, CHOW_MID, CHOW_HIGH):
        if a not in acts:
            continue
        pos = CHOW_LOW - a
        start = tile - pos
        remove = [(x, 1) for x in (start, start + 1, start + 2) if x != tile]
        s = eval_after(a, remove)
        key = (s, 1)
        if best_key is None or key < best_key:
            best_act, best_key = a, key

    if best_act == PASS:
        return PASS
    # 只有当副露后向听数 <= 当前向听数才执行
    # (副露会减少手牌灵活度,要求不退步)
    after_s = best_key[0]
    if after_s <= cur_s:
        return best_act
    return PASS
