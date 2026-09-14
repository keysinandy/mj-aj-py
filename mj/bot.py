"""启发式出牌 bot。

决策原则(按优先级):
0. 摸牌/杠补牌成胡默认提交 HU;爆头态打白板仍听任意牌(财飘)时,
   墙内活牌足够轮回到自己再摸则弃胡打白飘(×4 起,下次摸牌必胡)。
1. 摸牌阶段弃牌:最小化向听数 → 保护财神 → 最大化进张数 →
   最小化牌型结构损失 → 少喂下家 → tile 编号(仅稳定排序)。
   同向听候选**全部**参与进张比较,不再按编号预截断。
2. 吃/碰:与 PASS 基准(反应时点站立牌面的 (shanten, ukeire))比较
   "副露 + 最佳弃牌"后的最终站立牌面——向听下降才做;等向听需
   进张增量达标(PONG≥2/CHOW≥4,补偿副露损失的手牌灵活度);
   多候选按 向听→进张→弃牌结构损失→稳定动作序 择优。
   KONG_OPEN 合法时整个 claim 窗口(含 PONG)沿用既有决策
   (明杠立即补牌、补牌期望模型另立 change)。
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


# 等向听时副露的最小进张增量(门槛):碰损失一点手牌自由度,
# 吃受上家位置与两摊上限(CHOW_LIMIT)限制更多,门槛更高。
PONG_UKE_GAIN = 2
CHOW_UKE_GAIN = 4


def _eval_standing(hand, locked, vis):
    """站立暗牌(need 态)的门槛判据 (shanten, ukeire)。

    PASS 基准与 claim 后站立牌面同口径;ukeire 按可见牌折算真实剩余
    进张(vis 须含被评估手牌——claim 前后可见总量不变,快照可复用)。
    _choose_react 出于性能将其内联为懒算(shanten 先行、等向听才
    ukeire),本函数是同语义的规范入口(测试用)。
    """
    return (shanten(hand, locked), ukeire(hand, locked, vis)[2])


def _post_claim_min_shanten(hand, locked):
    """need+1 态手牌各舍牌向听的最小值与候选(仅最小向听)。

    返回 (best_s, [(d, 舍牌后手牌), ...])。财神与 choose_discard
    同口径参与最小向听比较——保护只作用于同向听候选内部:
    若"打财神"是唯一能再降向听的舍牌,必须让 claim 评价看到,
    否则吃/碰会被系统性低估(甚至错判 PASS)。手牌非空则候选非空。
    """
    best_s, cands = None, []
    for d in range(34):
        if hand[d] <= 0:
            continue
        c = list(hand)
        c[d] -= 1
        s = shanten(c, locked)
        if best_s is None or s < best_s:
            best_s, cands = s, [(d, c)]
        elif s == best_s:
            cands.append((d, c))
    return best_s, cands


def _best_standing(cands, locked, vis, post_hand):
    """最小向听舍牌候选中取最优站立,返回 (ukeire, 结构损失, 舍牌)。

    排序与 choose_discard 同口径:非财神 → 进张多 → 结构损失小 →
    舍牌编号(稳定)——财神保护只在同向听候选内部生效,向听数
    仍是硬约束。
    """
    best = None  # (排序键, uke, shape, d)
    for d, c in cands:
        uke = ukeire(c, locked, vis)[2]
        shape = _discard_shape_cost(post_hand, d)
        key = (d == W, -uke, shape, d)
        if best is None or key < best[0]:
            best = (key, uke, shape, d)
    return (best[1], best[2], best[3])


def _best_post_claim_discard(hand, locked, vis):
    """need+1 态手牌(副露完成、待立即舍牌)枚举舍牌,返回最优
    (shanten, ukeire, 弃牌结构损失, 舍牌)。

    对每种合法舍牌后的 need 态站立手牌取 (shanten, ukeire) 字典序
    最优——实现上仅对最小向听候选计算 ukeire(向听是硬约束,更高
    向听的舍牌不可能胜出)。财神保护与 choose_discard 同口径:
    参与最小向听比较、同向听时非财神优先。
    """
    best_s, cands = _post_claim_min_shanten(hand, locked)
    uke, shape, d = _best_standing(cands, locked, vis, hand)
    return (best_s, uke, shape, d)


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


def choose_shape_action(g, seat):
    """Opt-in shape-v1 action plus a serialisable evaluation explanation."""
    from .hand_eval import (evaluate_discard_candidates, evaluate_reaction,
                            EvalProfile)

    acts = g.legal_actions()
    if len(acts) == 1:
        return acts[0], {
            "version": "shape-v1", "profile": "shape-v1",
            "profile_fingerprint": EvalProfile.shape_v1().fingerprint,
            "level": "legacy", "selected": acts[0],
            "reason": "only_legal_action", "candidates": [],
        }
    if g.phase == "discard":
        # HU/财飘 and KONG decisions are deliberately frozen to the legacy
        # rule entry points.  Shape-v1 only ranks ordinary discards.
        if HU in acts:
            action = W if _should_piao(g, seat) else HU
            return action, {
                "version": "shape-v1", "profile": "shape-v1",
                "profile_fingerprint": EvalProfile.shape_v1().fingerprint,
                "level": "legacy", "selected": action,
                "reason": "hu_or_piao_legacy", "candidates": [],
            }
        if any(a < 0 for a in acts):
            action = choose_discard(g, seat)
            return action, {
                "version": "shape-v1", "profile": "shape-v1",
                "profile_fingerprint": EvalProfile.shape_v1().fingerprint,
                "level": "legacy", "selected": action,
                "reason": "kong_branch_legacy", "candidates": [],
            }
        return evaluate_discard_candidates(g, seat)
    return evaluate_reaction(g, seat)


def choose_action(g, seat, evaluator="legacy", return_evaluation=False):
    """统一入口:返回该 seat 的动作。

    Existing callers keep the two-argument legacy behaviour.  Passing
    ``evaluator='shape-v1'`` opts into the shared shape evaluator; callers
    that need an explanation can additionally request ``return_evaluation``.
    """
    if evaluator not in (None, "legacy", "shape-v1", "shape_v1", "shape"):
        raise ValueError(f"unknown evaluator profile: {evaluator}")
    if evaluator not in (None, "legacy"):
        action, evaluation = choose_shape_action(g, seat)
        return (action, evaluation) if return_evaluation else action
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
    """吃/碰按"副露 + 最佳弃牌后站立牌面"与 PASS 基准比较。

    门槛:向听下降即做;等向听需进张增量 ≥ PONG_UKE_GAIN /
    CHOW_UKE_GAIN;向听上升不做。多个过门槛候选(限当前 react
    mode 的合法候选——引擎 claim 窗只有 PONG/KONG_OPEN、吃窗只有
    CHOW,二者不同窗竞争)按 更低向听 → 更高进张 → 更低弃牌
    结构损失 → 稳定动作序 择优。KONG_OPEN 合法时整窗走 legacy
    (PONG 换评价体系后与 KONG 的相对结果无法保持,见
    _legacy_claim_react)。
    """
    if KONG_OPEN in acts:
        return _legacy_claim_react(g, seat, acts)

    owner, tile = g.pending
    hand = g.hands[seat]
    locked = len(g.melds[seat])
    vis = g.visible_counts(seat)  # claim 前后可见总量不变,快照复用

    # 两阶段评价:先向听门槛(零 ukeire 止步多数被拒窗口),等向听
    # 才懒算 PASS/claim 进张;择优仅在多候选同向听时补算。
    pass_s = shanten(hand, locked)
    pass_uke = None

    options = []  # (act, s, cands, 副露后手牌, uke, shape)

    def consider(act, remove, gain):
        nonlocal pass_uke
        c = list(hand)
        for t, n in remove:
            c[t] -= n
        s, cands = _post_claim_min_shanten(c, locked + 1)
        if not cands or s > pass_s:
            return  # 向听门槛外:省去全部进张计算
        uke = shape = None
        if s == pass_s:  # 等向听:需进张增量 ≥ gain
            uke, shape, _d = _best_standing(cands, locked + 1, vis, c)
            if pass_uke is None:
                pass_uke = ukeire(hand, locked, vis)[2]
            if uke - pass_uke < gain:
                return
        options.append((act, s, cands, c, uke, shape))

    if PONG in acts:
        consider(PONG, [(tile, 2)], PONG_UKE_GAIN)
    for a in (CHOW_LOW, CHOW_MID, CHOW_HIGH):
        if a not in acts:
            continue
        pos = CHOW_LOW - a
        start = tile - pos
        remove = [(x, 1) for x in (start, start + 1, start + 2) if x != tile]
        consider(a, remove, CHOW_UKE_GAIN)

    if not options:
        return PASS
    best_s = min(o[1] for o in options)
    top = [o for o in options if o[1] == best_s]
    if len(top) == 1 and best_s < pass_s:
        return top[0][0]  # 唯一候选且向听严格下降:接受,无需进张
    best_act, best_key = None, None
    for act, s, cands, c, uke, shape in top:
        if uke is None:
            uke, shape, _d = _best_standing(cands, locked + 1, vis, c)
        key = (s, -uke, shape, act)
        if best_key is None or key < best_key:
            best_act, best_key = act, key
    return best_act


def _legacy_claim_react(g, seat, acts):
    """KONG_OPEN 同窗时的既有 claim 决策(行为保持,KONG out-of-scope)。

    口径:shanten(hand, locked) 与副露后 shanten(locked+1) 比较,
    等向听也执行;同向听时 KONG 优先于 PONG(key 次项 -1 < 0)。
    """
    owner, tile = g.pending
    hand = g.hands[seat]
    locked = len(g.melds[seat])
    best_act, best_key = PASS, None

    def eval_after(remove):
        c = list(hand)
        for t, n in remove:
            c[t] -= n
        return shanten(c, locked + 1)

    cur_s = shanten(hand, locked)

    if PONG in acts:
        key = (eval_after([(tile, 2)]), 0)
        if best_key is None or key < best_key:
            best_act, best_key = PONG, key
    if KONG_OPEN in acts:
        # 杠开期望:向听数不变也杠(加速+倍率)
        key = (eval_after([(tile, 3)]), -1)
        if best_key is None or key < best_key:
            best_act, best_key = KONG_OPEN, key

    if best_act == PASS:
        return PASS
    return best_act if best_key[0] <= cur_s else PASS
