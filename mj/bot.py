"""启发式出牌 bot。

决策原则(按优先级):
0. 摸牌/杠补牌成胡默认提交 HU;爆头态打白板仍听任意牌(财飘)时,
   墙内活牌足够轮回到自己再摸则弃胡打白飘(×4 起,下次摸牌必胡)。
1. 摸牌阶段弃牌:最小化向听数,同向听数最大化进张数;
   财神永不打出(除非唯一选择);听牌后打无关牌。
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


def visible_counts(g, seat):
    """seat 视角的可见牌(自己手牌+牌河+所有副露)。"""
    vis = [0] * 34
    for t, n in enumerate(g.hands[seat]):
        vis[t] += n
    for d in g.discards:
        for t in d:
            vis[t] += 1
    for ms in g.melds:
        for kind, t in ms:
            if kind == "chow":
                vis[t] += 1
                vis[t + 1] += 1
                vis[t + 2] += 1
            elif kind.startswith("kong"):
                vis[t] += 4
            else:
                vis[t] += 3
    return vis


def choose_discard(g, seat, top_k=4):
    """返回弃牌 tile。先按向听数筛出最优候选,再对少数候选算进张。

    向听数 > 1 时跳过进张计算(纯速度优先),听牌前后才细算。
    """
    hand = g.hands[seat]
    locked = len(g.melds[seat])
    vis = visible_counts(g, seat)
    scored = []
    for t in range(34):
        if hand[t] == 0:
            continue
        c = list(hand)
        c[t] -= 1
        s = shanten(c, locked)
        wild_penalty = 100 if t == W else 0
        feed = _feed_risk(g, seat, t)
        scored.append((s, wild_penalty, feed, t))
    scored.sort()
    best_s = scored[0][0]
    if best_s > 1:
        # 远离听牌:速度优先,直接用第一轮排序
        for s, wild_penalty, feed, t in scored:
            if hand[t] > 0:
                return t
    finals = [x for x in scored if x[0] == best_s][:top_k]
    best, best_key = None, None
    for s, wild_penalty, feed, t in finals:
        c = list(hand)
        c[t] -= 1
        _, _, uke = ukeire(c, locked, vis)
        key = (s, -uke, wild_penalty, feed, t)
        if best_key is None or key < best_key:
            best, best_key = t, key
    return best


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
    vis = visible_counts(g, seat)
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
