"""启发式出牌 bot。

决策原则(按优先级):
0. 摸牌/杠补牌成胡默认提交 HU;爆头态打白板仍听任意牌(财飘)时,
   活墙可摸张数 ≥ PIAO_WALL_GUARD(6)则弃胡打白飘(×4 起,下次摸牌
   必胡);活墙 < 6 落袋为安直接胡——跳过飘与杠的期望比较。
   v33 起杠后补牌仍是普通 draw 决策窗口;暗杠/补杠与 HU/弃牌按
   公开信息下一张摸牌的积分期望比较。
1. 摸牌阶段弃牌:最小化向听数 → 保护财神 → [持财神 + 听牌态
   叠加爆头档(openspec baotou-piao-aware-discard):弃后站立手为
   爆头听(听任意)的候选整体优先;其余候选进度信号用爆头进张
   (摸 t 后可弃成爆头听的未见加权数)替代普通胡牌张。受自适应
   收手调节:X 轮未转化 / 对手副露 ≥ Y / 活墙 < Z 任一触发即回
   速度线(验收口径 YCBK 关,开启场景不考虑)。爆头进张走 Rust
   内核(mj_kernels,节点预算,超限整局回退本条普通口径)] →
   最大化进张数 → 最小化牌型结构损失 → 少喂下家 →
   tile 编号(仅稳定排序)。
   同向听候选**全部**参与进张比较,不再按编号预截断。
2. 吃/碰:与 PASS 基准(反应时点站立牌面的 (shanten, ukeire))比较
   "副露 + 最佳弃牌"后的最终站立牌面——向听下降才做;等向听需
   爆头/财飘/听牌宽度/降向听能力发生显著推进;多候选按
   向听→爆头→财飘→进张→弃牌结构损失→稳定动作序 择优。
   KONG_OPEN 单独通过结构安全、牌效保持和杠开硬门后才进入补牌
   期望比较。
3. 打牌倾向:少喂牌——避开下家可能吃的相邻牌(简单启发)。
"""

import time
import weakref

from .tiles import W
from .shanten import shanten, ukeire, BAOTOU_UKEIRE_RUST
from .legacy_eval import (
    DEFAULT_BOT_EVALUATOR,
    LEGACY_V2_BASELINE_EVALUATORS,
    LEGACY_V2_PHASE_A_EVALUATORS,
    LEGACY_V2_PHASE_B_EVALUATORS,
    LEGACY_V2_EXPERIMENT_EVALUATORS,
    LEGACY_V2_SHAPE_PHASE_A_EVALUATORS,
    LEGACY_V2_SHAPE_PHASE_B_EVALUATORS,
    LEGACY_V2_PROFILE_VERSION,
    LEGACY_V2_OFFLINE_EVALUATORS,
    LEGACY_V2_EVALUATORS,
    LegacyRootCandidate,
    LegacyTwoPlyProfile,
    evaluate_legacy_two_ply,
)
from .legacy_react import (
    CHOW_MIN_ABS_GAIN,
    LEGACY_MIN_GAIN_RATIO,
    LEGACY_REACTION_V1 as LEGACY_SHAPE_PROGRESS_VERSION,
    PONG_MIN_ABS_GAIN,
    LegacyShapeProgress,
    _LEGACY_PROGRESS_STATS,
    _cached_legacy_shape_progress,
    choose_reaction_v1,
    choose_reaction_v2,
    legacy_progress_diagnostics as _legacy_progress_diagnostics,
    progress_not_worse as _progress_not_worse,
    progress_sort_key as _progress_sort_key,
    significant_progress as _significant_progress,
    LegacyReactionProfile,
)
from .big_hand_intent import evaluate_big_hand_discard_intents
from .shape_quality import standing_shape_quality
from . import legacy_kong as _legacy_kong
from . import legacy_react as _legacy_react
from .win import is_baotou, is_win, is_baotou_wait
from .scoring import hand_multiplier, settle
from .game import (
    PASS, HU, PONG, KONG_OPEN, KONG_CLOSED_BASE, KONG_ADD_BASE,
    CHOW_LOW, CHOW_MID, CHOW_HIGH,
)


# ---------- 爆头推进自适应收手(openspec baotou-piao-aware-discard) ----------
# 持财神普通听牌态默认推进爆头(倍率优先);下列任一情况收手转速度线
# (legacy 键排序,且不再弃胡博倍率):
#   X 轮:进入推进态后自己的第 BAOTOU_PUSH_MAX_ROUNDS 次弃牌决策仍未
#        转化(爆头听/胡)——倍率等不起;
#   Y 副露:任意对手副露数 ≥ BAOTOU_PUSH_OPP_MELDS——对手接近听牌,
#        被先胡的风险压过倍率期望;
#   Z 活墙:活墙可摸张数(死墙已扣)< BAOTOU_PUSH_MIN_LIVE——剩余
#        摸牌轮次不够重组。
# 轮数/副露只增、活墙只减 → 收手是吸收态,不反复摇摆。弃胡打白飘在
# 收手态同样落袋为安(只看轮数/副露,墙已有 PIAO_WALL_GUARD 硬门)。
# 轮数记账挂在 Game 实例上(自博弈/rl_env 的 Game 全程同一实例);
# 平台镜像每决策重建 Game,X 在线上不累积(Y/Z 仍生效),待平台层
# 接入后再打通。
# **验收口径:有财必拷响关闭**;YCBK 开场景当前项目不考虑。
BAOTOU_PUSH_MAX_ROUNDS = 2   # X:网格扫描最优(local/ab_baotou_sweep.py,30720 局)
BAOTOU_PUSH_OPP_MELDS = 2    # Y:同上;粗筛各 X 下 Y=2 一致优于 Y=99
BAOTOU_PUSH_MIN_LIVE = 16    # Z:X=2 下不约束(与 Z=0 精跑逐位等值),保留作晚局守卫

_push_rounds = weakref.WeakKeyDictionary()


def _legacy_shape_progress(standing, locked, visible, *,
                           include_baotou=False, piao_allowed=False):
    """Compatibility entrypoint for the extracted progress evaluator."""
    from .legacy_react import legacy_shape_progress
    return legacy_shape_progress(
        standing, locked, visible,
        include_baotou=include_baotou,
        piao_allowed=piao_allowed,
        baotou_kernel_available=BAOTOU_UKEIRE_RUST,
    )


def _piao_context_allowed(g, seat):
    if getattr(g, "live_wall_left", lambda: 0)() < PIAO_WALL_GUARD:
        return False
    return _push_abort_reason(g, seat, include_live=False) is None


def _bump_push_rounds(g, seat):
    """推进态轮数 +1 并返回当前值;离开推进态由调用方清零。"""
    per_game = _push_rounds.setdefault(g, {})
    per_game[seat] = per_game.get(seat, 0) + 1
    return per_game[seat]


def _clear_push_rounds(g, seat):
    per_game = _push_rounds.get(g)
    if per_game and seat in per_game:
        del per_game[seat]


def _max_opp_melds(g, seat):
    return max((len(g.melds[o]) for o in range(4) if o != seat), default=0)


def _push_abort_reason(g, seat, include_live=True):
    """收手判定:返回触发原因(None=继续推进)。

    ``include_live=False`` 供弃胡打白飘决策复用轮数/副露两个软收手
    (墙量已有 PIAO_WALL_GUARD 硬门,Z 不重复作用于飘)。
    """
    rounds = _push_rounds.get(g, {}).get(seat, 0)
    if rounds >= BAOTOU_PUSH_MAX_ROUNDS:
        return "rounds"
    if _max_opp_melds(g, seat) >= BAOTOU_PUSH_OPP_MELDS:
        return "opp_melds"
    if include_live:
        try:
            live = g.live_wall_left()
        except AttributeError:
            live = 99  # 无墙信息的极简测试局不触发
        if live < BAOTOU_PUSH_MIN_LIVE:
            return "live_wall"
    return None


def choose_discard(g, seat, return_info=False, profile=None):
    """返回弃牌 tile;``return_info=True`` 时返回 ``(tile, info dict)``。

    ``profile`` 仅用于显式启用 ``legacy-two-ply-v1``；省略时严格走
    原 legacy 排序。V1 只覆盖普通摸后弃牌，爆头、HU、杠和反应窗口
    始终由既有规则分支处理。

    优先级:向听数(硬约束,不为打风牌让向听倒退)→ 财神保护 →
    进张数 → 牌型结构损失 → 喂牌风险 → tile 编号(仅作稳定排序)。

    持财神 + 听牌态叠加爆头档(openspec baotou-piao-aware-discard):
    弃后站立手为爆头听(听任意)的候选整体优先;其余候选的进度信号
    用爆头进张(摸 t 后可弃成爆头听的未见加权数)替代普通胡牌张进张
    ——爆头路径倍率更高(×2 起),但速度较慢,故受 X/Y/Z 自适应收手
    调节,任一触发即回本函数的 legacy 键(速度线,info 带 push_abort)。
    爆头进张计算超预算时整局回退 legacy 键(info 带 fallback_reason),
    不混用部分结果。不持财神时排序与既有基线逐候选一致。
    验收口径为有财必拷响**关闭**;YCBK 开场景当前项目不考虑。

    所有最小向听候选都参与精确进张比较,不按 tile 编号预截断——
    否则字牌编号靠后会被挤出候选,孤张字牌留着、数牌搭子反被先拆。
    进张计算贵(纯 Python 全量口径 2.28 局/秒、候选剪枝后 2.94),
    但 shanten/ukeire 已默认走 Rust 内核(rust/,mj/shanten.py 调度器,
    详见其模块 docstring),自博弈实测 ~108 局/秒单核。
    """
    hand = g.hands[seat]
    locked = len(g.melds[seat])
    vis = g.visible_counts(seat)

    # 冻结态(抓打圈)只能弃刚摸的牌——legal_actions 是合法性唯一
    # 真源,按 Game._legal_discards 同口径收窄候选(旧实现冻结盲:
    # 2026-09-18 A/B 实弹暴露,持有暗杠四张时 legacy 键会选非刚摸
    # 牌,线上同样会触发 409)。口径与 Game.in_freeze 一致,duck-type
    # 兼容 Game.__new__ 构造的测试局/镜像局。
    frozen = getattr(g, "freeze", 0) > 0 and seat != getattr(g, "freezer", None)
    only = g.drawn[seat] if frozen else None

    all_cands = []  # (tile, standing, shanten); expensive costs are lazy
    best_s = None
    for t in range(34):
        if hand[t] == 0 or (only is not None and t != only):
            continue
        c = list(hand)
        c[t] -= 1
        s = shanten(c, locked)
        if best_s is None or s < best_s:
            best_s = s
        all_cands.append((t, c, s))
    speed_cands = [
        (t, c, _discard_shape_cost(hand, t), _feed_risk(g, seat, t))
        for t, c, s in all_cands if s == best_s
    ]

    info = {}
    baotou_scope = False
    if hand[W] > 0 and best_s == 0:
        baotou_scope = True
        rounds = _bump_push_rounds(g, seat)
        abort = _push_abort_reason(g, seat)
        if abort is not None:
            # 收手:回速度线(legacy 键),可归因。X/Y/Z 语义与验收
            # 口径均以有财必拷响**关闭**为准;YCBK 开场景当前项目
            # 不考虑(openspec baotou-piao-aware-discard scope)。
            info["push_abort"] = abort
            info["push_rounds"] = rounds
            # 落到下方 legacy 键(速度线)
        else:
            best = _choose_discard_baotou(
                speed_cands, locked, vis, info,
                shape_aware=bool(profile and profile.shape_quality_enabled),
                diagnostics_enabled=profile is not None,
            )
            if best is not None:
                info["push_rounds"] = rounds
                if profile is not None:
                    scoped = _legacy_v1_scope_info(
                        profile, best, "baotou_scope")
                    scoped.update(info)
                    info = scoped
                return (best, info) if return_info else best
            # 预算回退:整局走下方 legacy 键,info 已带 fallback_reason
    else:
        _clear_push_rounds(g, seat)

    # BigHandIntent is only attached to the weighted online legacyV2 route.
    # V1, offline labels, and the frozen legacyV2 baseline still see only the
    # pre-change minimum-shanten speed pool.
    if profile is not None and not baotou_scope:
        roots = []
        use_big_hand = (
            profile.mode == "weighted" and
            profile.name == LEGACY_V2_PROFILE_VERSION and
            profile.big_hand_enabled
        )
        try:
            live_wall = g.live_wall_left() if use_big_hand else None
        except (AttributeError, TypeError, ValueError):
            live_wall = None
        try:
            max_opponent_melds = (_max_opp_melds(g, seat)
                                  if use_big_hand else None)
        except (AttributeError, IndexError, TypeError, ValueError):
            max_opponent_melds = None
        root_inputs = ([(t, c, s, None, None)
                        for t, c, s in all_cands] if use_big_hand else
                       [(t, c, best_s, shape, feed)
                        for t, c, shape, feed in speed_cands])
        try:
            root_inputs = [(t, tuple(c), s, shape, feed)
                           for t, c, s, shape, feed in root_inputs]
            intents = (evaluate_big_hand_discard_intents(
                hand, [root[0] for root in root_inputs], locked, vis,
                live_wall=live_wall,
                max_opponent_melds=max_opponent_melds)
                if use_big_hand else (None,) * len(root_inputs))
            for (t, c, s, shape, feed), intent in zip(root_inputs, intents):
                speed_eligible = (s == best_s)
                if shape is None and (speed_eligible or
                                      (intent is not None and
                                       intent.strength == "STRONG")):
                    shape = _discard_shape_cost(hand, t)
                    feed = _feed_risk(g, seat, t)
                elif shape is None:
                    shape, feed = 0.0, 0.0
                roots.append(LegacyRootCandidate(
                    tile=t, hand=c, shanten=s,
                    shape_loss=shape, feed_risk=feed,
                    shanten_verified=True,
                    speed_eligible=speed_eligible,
                    intent_kinds=tuple(intent.kinds) if intent else (),
                    intent_strength=intent.strength if intent else "NONE",
                    chiitoi_shanten=(intent.chiitoi_shanten
                                     if intent else None),
                    pair_units=intent.pair_units if intent else 0,
                    luxury_groups=intent.luxury_groups if intent else 0,
                    luxury_upgrade_tiles=(intent.luxury_upgrade_tiles
                                          if intent else ()),
                    luxury_upgrade_live=(intent.luxury_upgrade_live
                                         if intent else 0),
                    wild_count=intent.wild_count if intent else 0,
                    wild_live=intent.wild_live if intent else 0,
                    shanten_regression=max(0, s - best_s),
                    admission_hint=("big_hand_candidate" if intent and
                                    s > best_s and intent.kinds else None),
                    live_wall=(intent.live_wall if intent else None),
                    max_opponent_melds=(intent.max_opponent_melds
                                        if intent else None),
                ))
        except (TypeError, ValueError):
            # Intent is optional: unknown public material must not contaminate
            # the decision.  Preserve the transactional speed fallback.
            fallback = _legacy_best(speed_cands, locked, vis)[0]
            info = _legacy_v1_scope_info(
                profile, fallback, "big_hand_intent_unknown")
            info["big_hand_fallback_reason"] = "big_hand_intent_unknown"
            info["big_hand_profile"] = profile.big_hand_config()
            return (fallback, info) if return_info else fallback
        selected, evaluation = evaluate_legacy_two_ply(
            g, seat, roots, locked, vis, profile,
            shape_cost=_discard_shape_cost, feed_risk=_feed_risk)
        if not return_info:
            return selected
        info = evaluation.as_json()
        if (profile.mode == "weighted" and
                profile.name == LEGACY_V2_PROFILE_VERSION):
            info["big_hand_profile"] = profile.big_hand_config()
            info["big_hand_phase"] = (
                "disabled" if not profile.big_hand_enabled else
                "plus-one" if profile.big_hand_plus_one_enabled else
                "same-shanten")
        info["reason"] = ("discard_legacy_v1" if evaluation.complete
                           else "discard_legacy")
        if (not evaluation.complete and evaluation.fallback_reason
                and "fallback_reason" not in info):
            info["fallback_reason"] = evaluation.fallback_reason
        return (selected, info) if return_info else selected

    best, best_key = _legacy_best(speed_cands, locked, vis)
    info.setdefault("reason", "discard_legacy")
    if profile is not None and baotou_scope:
        scope_info = _legacy_v1_scope_info(profile, best, "baotou_scope")
        scope_info.update(info)
        info = scope_info
    return (best, info) if return_info else best


def _legacy_best(cands, locked, vis):
    """Complete pre-V1 legacy ordering, kept as the rollback oracle."""
    best, best_key = None, None
    for t, c, shape, feed in cands:
        uke = ukeire(c, locked, vis)[2]
        key = (t == W, -uke, shape, feed, t)
        if best_key is None or key < best_key:
            best, best_key = t, key
    return best, best_key


def _legacy_v1_scope_info(profile, action, reason):
    """Explain a V1 request that stayed in an established legacy branch."""
    is_scope = reason in {"baotou_scope", "hu_kong_scope"}
    decision_scope = ("baotou_scope" if reason == "baotou_scope"
                      else "legacy")
    return {
        "version": profile.version,
        "profile": profile.name,
        "profile_fingerprint": profile.fingerprint,
        "level": "legacy",
        "complete": False,
        "mode": profile.mode,
        "selected": action,
        "legacy_best": action,
        "future_model": profile.model,
        "candidates": [],
        "missing": [reason],
        "fallback_reason": None if is_scope else reason,
        "partial_accepted": False,
        "decision_scope": decision_scope,
        "stage_b_entered": False,
        "future_shape_quality_sum": None,
        "future_shape_quality_mean": None,
        "future_shape_denominator": None,
        "shape_quality_version": profile.shape_quality_version,
        "shape_quality_used": False,
        "shape_quality_stage": None,
        "shape_changed_winner": False,
        "shape_baseline_selected": action,
        "baotou_shape_used": False,
    }


# 持财神听牌态爆头进张计算的预算:只用**节点数**(确定性,决策可复现
# ——墙钟预算会让同种子轨迹随负载漂移,shape-v1 的历史教训);墙钟
# 耗时仅记入 info 作诊断。无 Rust 内核时整档跳过(纯 Python 枚举
# 90~220ms/决策,不可用),行为回退旧排序。
BAOTOU_UKE_BUDGET_NODES = 64


def _choose_discard_baotou(cands, locked, vis, info, *,
                           shape_aware=False, diagnostics_enabled=False):
    """持财神听牌态的爆头档排序;预算超限返回 None(整局回退 legacy 键)。

    tier 0:弃后站立手为爆头听(听任意牌)——整体优先,档内沿用财神
    保护次序(不主动弃白;爆头态弃白飘由 _should_piao 在 HU 决策点
    统一裁决)。tier 1:普通听牌,进度信号用爆头进张替代普通胡牌张
    (有财必拷响下不可兑现);普通进张不再单独参与该状态排序。
    仅在 Rust 内核(mj_kernels.baotou_ukeire)可用时启用。
    """
    from .shanten import baotou_ukeire, BAOTOU_UKEIRE_RUST

    if not BAOTOU_UKEIRE_RUST:
        return None
    started = time.monotonic()
    nodes = 0
    ranked = []
    for t, c, shape, feed in cands:
        if is_baotou_wait(c, locked):
            ranked.append((0, t, 0, shape, feed, c))
            continue
        if nodes >= BAOTOU_UKE_BUDGET_NODES:
            # 整局回退,不混用部分爆头结果(可归因)
            info["fallback_reason"] = "baotou_budget_exceeded"
            info["baotou_nodes"] = nodes
            return None
        _acc, u1 = baotou_ukeire(c, locked, vis)
        nodes += 1
        ranked.append((1, t, u1, shape, feed, c))
    # Only compute shape after the whole baotou pass has succeeded.  Budget
    # fallback therefore never combines partial baotou and partial shape data.
    measured = []
    if shape_aware:
        try:
            measured = [
                (tier, t, u1, shape, feed, c,
                 standing_shape_quality(c, locked=locked))
                for tier, t, u1, shape, feed, c in ranked
            ]
        except (TypeError, ValueError):
            info["fallback_reason"] = "baotou_shape_invalid"
            return None
    else:
        measured = [(*item, None) for item in ranked]
    best, best_key, best_tier = None, None, None
    legacy_best, legacy_key = None, None
    candidate_rows = []
    for tier, t, u1, shape, feed, _hand, standing in measured:
        key = (tier, t == W, -u1,
               -(standing.encoded if shape_aware and standing else 0),
               shape, feed, t)
        old_key = (tier, t == W, -u1, shape, feed, t)
        if best_key is None or key < best_key:
            best, best_key, best_tier = t, key, tier
        if legacy_key is None or old_key < legacy_key:
            legacy_best, legacy_key = t, old_key
        if diagnostics_enabled:
            state = ukeire(c, locked, vis)
            row = {
                "tile": t,
                "shanten": shanten(c, locked),
                "current_ukeire": int(state[2]),
                "ukeire_tiles": list(state[1]),
                "baotou_tier": tier,
                "baotou_ukeire": u1,
                "discard_shape_cost": shape,
                "shape_loss": shape,
                "feed_risk": feed,
            }
            if shape_aware:
                row.update({
                    "standing_shape_quality": (
                        standing.encoded if standing is not None else None),
                    "standing_shape_signature": (
                        list(standing.signature)
                        if standing is not None else None),
                    "shape_quality_version": (
                        standing.version if standing is not None else None),
                })
            candidate_rows.append(row)
    info["reason"] = "discard_baotou"
    info["baotou_tier"] = best_tier
    info["baotou_nodes"] = nodes
    info["baotou_elapsed_ms"] = round((time.monotonic() - started) * 1000.0, 3)
    info["decision_scope"] = "baotou_scope"
    info["stage_b_entered"] = False
    info["future_shape_quality_sum"] = None
    info["future_shape_quality_mean"] = None
    info["future_shape_denominator"] = None
    info["baotou_shape_used"] = bool(shape_aware)
    info["shape_quality_used"] = bool(shape_aware)
    info["shape_quality_stage"] = "baotou" if shape_aware else None
    info["shape_quality_version"] = (
        "standing-shape-v1" if shape_aware else None)
    info["shape_changed_winner"] = bool(shape_aware and best != legacy_best)
    info["legacy_selected"] = legacy_best
    if diagnostics_enabled:
        info["candidates"] = candidate_rows
    return best


def _kong_action_tile(action):
    """Return ``(kind, tile)`` for a self-draw kong action."""
    return _legacy_kong.kong_action_tile(action)


def _kong_actions(actions):
    """Return the legal self-draw KONG actions in stable engine order."""
    return _legacy_kong.kong_actions(actions)


def _seat_value(value, seat):
    """Read a per-seat Game field while accepting Mirror's scalar fields."""
    if isinstance(value, (list, tuple)):
        return value[seat]
    return value


def _public_visible_counts(g, seat):
    """Return visible tile counts without reading opponent concealed hands.

    A full local ``Game`` contains hidden hands for simulation, while an
    online Mirror zeroes them. This policy helper intentionally uses only
    the hero hand, all rivers, and all exposed melds in both cases.
    """
    visible = list(g.hands[seat])
    for river in g.discards:
        for tile in river:
            visible[tile] += 1
    for melds in g.melds:
        for kind, tile in melds:
            if kind == "chow":
                for value in (tile, tile + 1, tile + 2):
                    visible[value] += 1
            elif kind.startswith("kong"):
                visible[tile] += 4
            else:
                visible[tile] += 3
    return visible


def _expected_next_draw_reward(g, seat, standing, locked, chain,
                               chain_piao, kong_draw, remaining=None,
                               draw_delay=1):
    """Estimate one next draw using only public unseen-tile mass.

    This is deliberately a small policy helper, not a second settlement
    implementation or a hidden-wall simulation. It reuses ``is_win``,
    ``hand_multiplier`` and ``settle`` so the comparison is in the same
    score units as an immediate HU. ``kong_draw`` applies the v33 YCBK
    legality exception to the replacement draw.
    """
    live_wall = g.live_wall_left()
    if live_wall < draw_delay:
        return {"value": 0.0, "win_probability": 0.0,
                "winning_tiles": (), "wall_left": max(0, live_wall)}
    if remaining is None:
        visible = _public_visible_counts(g, seat)
        remaining = [max(0, 4 - count) for count in visible]
    total_unseen = sum(remaining)
    if total_unseen <= 0:
        return {"value": 0.0, "win_probability": 0.0,
                "winning_tiles": (), "wall_left": max(0, live_wall - draw_delay)}

    ycbk = bool(getattr(g, "you_cai_bi_kao", False))
    total_reward = 0.0
    winning_mass = 0
    winning_tiles = []
    for tile, mass in enumerate(remaining):
        if mass <= 0:
            continue
        final = list(standing)
        final[tile] += 1
        if not is_win(final, locked):
            continue
        if (ycbk and final[W] > 0 and not kong_draw
                and not is_baotou(standing, locked)):
            continue
        multiplier, _parts = hand_multiplier(
            final, standing, locked, chain, chain_piao)
        reward = settle(
            seat, getattr(g, "dealer", 0), multiplier,
            getattr(g, "base", 1),
        )[seat]
        total_reward += mass * reward
        winning_mass += mass
        winning_tiles.append(tile)
    return {
        "value": total_reward / total_unseen,
        "win_probability": winning_mass / total_unseen,
        "winning_tiles": tuple(winning_tiles),
        "wall_left": max(0, live_wall - draw_delay),
    }


def _immediate_hu_reward(g, seat):
    """Return the existing settlement-unit reward for the current HU."""
    drawn = g.drawn[seat]
    standing = list(g.hands[seat])
    standing[drawn] -= 1
    multiplier, _parts = hand_multiplier(
        g.hands[seat], standing, len(g.melds[seat]),
        _seat_value(g.chain, seat), _seat_value(g.chain_piao, seat),
    )
    return float(settle(
        seat, getattr(g, "dealer", 0), multiplier,
        getattr(g, "base", 1),
    )[seat])


def _post_discard_chain(g, seat, tile, standing, locked=None):
    """Apply the existing chain reset/piao rule to a baseline discard."""
    locked = len(g.melds[seat]) if locked is None else int(locked)
    if tile == W and is_baotou(standing, locked):
        return (_seat_value(g.chain, seat) + 1,
                _seat_value(g.chain_piao, seat) + 1)
    return 0, 0


def _kong_structure_guard(hand, locked, kind, tile):
    """Check target-tile material use in one optimal standard decomposition."""
    def bump_decomposition_calls():
        _LEGACY_PROGRESS_STATS["decomposition_calls"] += 1

    return _legacy_kong.structure_guard(
        hand, locked, kind, tile,
        on_decomposition=bump_decomposition_calls,
    )


def _kong_shape_gate(baseline, post):
    return _legacy_kong.shape_gate(baseline, post)


def _kong_winning_tiles(standing, locked, remaining):
    return _legacy_kong.winning_tiles(standing, locked, remaining)


def _kong_kai_gate(g, seat, standing, locked, post, remaining):
    return _legacy_kong.kong_kai_gate(
        g, seat, standing, locked, post, remaining)


def _post_kong_state(g, seat, kind, tile, visible):
    return _legacy_kong.post_kong_state(
        g,
        seat,
        kind,
        tile,
        visible,
        shape_progress=_legacy_shape_progress,
        piao_allowed=_piao_context_allowed(g, seat),
    )


def _evaluate_kong_open(g, seat, tile, baseline, visible, *, v2=False,
                        reaction_profile=None):
    return _legacy_kong.evaluate_kong_open(
        g,
        seat,
        tile,
        baseline,
        visible,
        structure_guard_fn=_kong_structure_guard,
        post_kong_state_fn=_post_kong_state,
        expected_next_draw_reward=_expected_next_draw_reward,
        seat_value=_seat_value,
        shape_gate_fn=(_legacy_kong.shape_gate_v2
                       if v2 else _legacy_kong.shape_gate),
        score_continuation_fn=(
            _legacy_kong.public_score_continuation if v2 else None),
        post_discard_chain=_post_discard_chain,
        continuation_node_budget=(
            reaction_profile.continuation_node_budget
            if reaction_profile is not None else 2048),
        continuation_soft_budget_ms=(
            reaction_profile.continuation_soft_budget_ms
            if reaction_profile is not None else 0.0),
        continuation_hard_budget_ms=(
            reaction_profile.continuation_hard_budget_ms
            if reaction_profile is not None else 15.0),
    )


def _evaluate_self_kong(g, seat, action, baseline, visible, *, v2=False,
                        reaction_profile=None):
    return _legacy_kong.evaluate_self_kong(
        g,
        seat,
        action,
        baseline,
        visible,
        structure_guard_fn=_kong_structure_guard,
        post_kong_state_fn=_post_kong_state,
        evaluate_kong_next_draw_fn=(
            (lambda g_, seat_, action_, remaining_:
             _evaluate_kong_next_draw(
                 g_, seat_, action_, remaining_,
                 reaction_profile=reaction_profile))
            if v2 else _evaluate_kong_next_draw),
        shape_gate_fn=(_legacy_kong.shape_gate_v2
                       if v2 else _legacy_kong.shape_gate),
    )


def _kong_public_result(result):
    return _legacy_kong.public_result(result)


def _evaluate_kong_next_draw(g, seat, action, remaining,
                             reaction_profile=None):
    """Score one already-guarded closed/add-kong replacement draw."""
    return _legacy_kong.evaluate_kong_next_draw(
        g,
        seat,
        action,
        remaining,
        expected_next_draw_reward=_expected_next_draw_reward,
        seat_value=_seat_value,
        score_continuation_fn=(
            _legacy_kong.public_score_continuation
            if reaction_profile is not None else None),
        post_discard_chain=_post_discard_chain,
        continuation_node_budget=(
            reaction_profile.continuation_node_budget
            if reaction_profile is not None else 0),
        continuation_soft_budget_ms=(
            reaction_profile.continuation_soft_budget_ms
            if reaction_profile is not None else 0.0),
        continuation_hard_budget_ms=(
            reaction_profile.continuation_hard_budget_ms
            if reaction_profile is not None else 0.0),
    )


def _choose_draw_action(g, seat, actions=None, discard_profile=None,
                        reaction_profile=None):
    """Choose HU/piao, self-kong, or discard at a draw decision point.

    KONG is accepted only when its public next-replacement score expectation
    strictly exceeds the current baseline: an immediate HU, or the best
    legacy discard's next-draw expectation. This keeps the v33 action
    window useful without reducing the policy to ``if kong: return kong``.
    """
    actions = tuple(g.legal_actions() if actions is None else actions)
    if HU in actions and g.live_wall_left() < PIAO_WALL_GUARD:
        # 墙量守卫(openspec baotou-piao-aware-discard):活墙可摸不足
        # 6 张(死墙已扣)时落袋为安,直接胡——不弃胡博爆头/财飘、
        # 也不让杠的期望比较覆盖确定的 HU。与 _should_piao 同常量。
        return HU, {"reason": "hu_wall_guard_legacy"}
    kongs = _kong_actions(actions)
    if not kongs:
        if HU in actions:
            return (W if _should_piao(g, seat) else HU), {
                "reason": "hu_or_piao_legacy",
            }
        tile, info = choose_discard(
            g, seat, return_info=True, profile=discard_profile)
        info.setdefault("reason", "discard_legacy")
        return tile, info

    if HU in actions:
        if _should_piao(g, seat):
            baseline = W
            baseline_progress_tile = W
            standing = list(g.hands[seat])
            standing[W] -= 1
            chain, chain_piao = _post_discard_chain(
                g, seat, W, standing)
            visible = _public_visible_counts(g, seat)
            remaining = [max(0, 4 - count) for count in visible]
            if reaction_profile is None:
                baseline_eval = _expected_next_draw_reward(
                    g, seat, standing, len(g.melds[seat]), chain, chain_piao,
                    False, remaining, draw_delay=4)
                baseline_value = baseline_eval["value"]
            else:
                # Defer the expensive score continuation until at least one
                # self-KONG passes its hard gate.  A rejected KONG has no
                # score comparison to make and must not spend/abort the
                # continuation budget (especially in offline fail-loud mode).
                baseline_eval = None
                baseline_value = None
            baseline_reason = "piao_expected_value"
        else:
            baseline = HU
            baseline_progress_tile = choose_discard(
                g, seat, profile=discard_profile)
            baseline_value = _immediate_hu_reward(g, seat)
            baseline_eval = {
                "complete": True,
                "fallback_reason": None,
                "total_reward_ev": baseline_value,
            }
            baseline_reason = "hu_legacy"
    else:
        baseline = choose_discard(g, seat, profile=discard_profile)
        baseline_progress_tile = baseline
        standing = list(g.hands[seat])
        standing[baseline] -= 1
        chain, chain_piao = _post_discard_chain(
            g, seat, baseline, standing)
        visible = _public_visible_counts(g, seat)
        remaining = [max(0, 4 - count) for count in visible]
        if reaction_profile is None:
            baseline_eval = _expected_next_draw_reward(
                g, seat, standing, len(g.melds[seat]), chain, chain_piao,
                False, remaining, draw_delay=4)
            baseline_value = baseline_eval["value"]
        else:
            baseline_eval = None
            baseline_value = None
        baseline_reason = "discard_expected_value"

    visible = _public_visible_counts(g, seat)
    remaining = [max(0, 4 - count) for count in visible]
    baseline_standing = list(g.hands[seat])
    baseline_standing[baseline_progress_tile] -= 1
    baseline_progress = _legacy_shape_progress(
        baseline_standing, len(g.melds[seat]), visible,
        include_baotou=False, piao_allowed=_piao_context_allowed(g, seat))
    evaluations = [
        result for result in (
            _evaluate_self_kong(
                g, seat, action, baseline_progress, visible,
                v2=reaction_profile is not None,
                reaction_profile=reaction_profile)
            for action in kongs)
        if result is not None
    ]
    gated = [result for result in evaluations if result["gate_passed"]]
    if reaction_profile is not None and gated:
        if baseline_eval is None:
            baseline_chain, baseline_chain_piao = _post_discard_chain(
                g, seat, baseline_progress_tile, baseline_standing)
            baseline_eval = _legacy_kong.public_score_continuation(
                g, seat, baseline_standing, len(g.melds[seat]),
                baseline_chain, baseline_chain_piao, False, remaining,
                first_draw_delay=4,
                post_discard_chain=_post_discard_chain,
                node_budget=reaction_profile.continuation_node_budget,
                soft_budget_ms=reaction_profile.continuation_soft_budget_ms,
                hard_budget_ms=reaction_profile.continuation_hard_budget_ms)
            baseline_value = baseline_eval["total_reward_ev"]
        continuation_complete = baseline_eval.get("complete", True) and all(
            result.get("continuation_complete", True) for result in gated)
        if not continuation_complete:
            if reaction_profile.require_complete:
                from .legacy_react import IncompleteReactionFuture
                raise IncompleteReactionFuture(
                    "self-KONG continuation incomplete")
            action, detail = _choose_draw_action(
                g, seat, actions, discard_profile=discard_profile)
            detail["continuation_fallback_reason"] = (
                baseline_eval.get("fallback_reason")
                or next((result.get("continuation_fallback_reason")
                         for result in gated
                         if not result.get("continuation_complete", True)),
                        "continuation_incomplete"))
            return action, detail
    best = max(
        gated,
        key=lambda result: (result["value"], result["win_probability"],
                            -result["tile"]),
        default=None,
    )
    if best is not None and best["value"] > baseline_value + 1e-9:
        return best["action"], {
            "reason": "kong_expected_value",
            "baseline": baseline,
            "baseline_value": baseline_value,
            "selected_value": best["value"],
            "selected_win_probability": best["win_probability"],
            "selected_kind": best["kind"],
            "selected_tile": best["tile"],
            "wall_left": best["wall_left"],
            "kong_evaluation": _kong_public_result(best),
            "kong_candidates": [
                _kong_public_result(result) for result in evaluations],
        }
    return baseline, {"reason": baseline_reason,
                      "baseline_value": baseline_value,
                      "kong_candidates": [
                          _kong_public_result(result)
                          for result in evaluations],
                      "progress_diagnostics": _legacy_progress_diagnostics()}


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


# 活墙可摸(已扣死墙)< 此值时落袋为安直接胡:爆头态也不弃胡打
# 白飘、杠期望比较不覆盖确定 HU;_should_piao 与 choose_action 的
# HU 短路共用(openspec baotou-piao-aware-discard,2026-09-18)。
PIAO_WALL_GUARD = 6


def _eval_standing(hand, locked, vis):
    """站立暗牌(need 态)的门槛判据 (shanten, ukeire)。

    PASS 基准与 claim 后站立牌面同口径;ukeire 按可见牌折算真实剩余
    进张(vis 须含被评估手牌——claim 前后可见总量不变,快照可复用)。
    _choose_react 出于性能将其内联为懒算(shanten 先行、等向听才
    ukeire),本函数是同语义的规范入口(测试用)。
    """
    return _legacy_react.evaluate_standing(hand, locked, vis)


def _post_claim_min_shanten(hand, locked):
    """need+1 态手牌各舍牌向听的最小值与候选(仅最小向听)。

    返回 (best_s, [(d, 舍牌后手牌), ...])。财神与 choose_discard
    同口径参与最小向听比较——保护只作用于同向听候选内部:
    若"打财神"是唯一能再降向听的舍牌,必须让 claim 评价看到,
    否则吃/碰会被系统性低估(甚至错判 PASS)。手牌非空则候选非空。
    """
    return _legacy_react.post_claim_min_shanten(hand, locked)


def _best_standing(cands, locked, vis, post_hand):
    """最小向听舍牌候选中取最优站立,返回 (ukeire, 结构损失, 舍牌)。

    排序与 choose_discard 同口径:非财神 → 进张多 → 结构损失小 →
    舍牌编号(稳定)——财神保护只在同向听候选内部生效,向听数
    仍是硬约束。
    """
    return _legacy_react.best_standing(
        cands, locked, vis, post_hand, shape_cost=_discard_shape_cost)


def _best_post_claim_discard(hand, locked, vis):
    """need+1 态手牌(副露完成、待立即舍牌)枚举舍牌,返回最优
    (shanten, ukeire, 弃牌结构损失, 舍牌)。

    对每种合法舍牌后的 need 态站立手牌取 (shanten, ukeire) 字典序
    最优——实现上仅对最小向听候选计算 ukeire(向听是硬约束,更高
    向听的舍牌不可能胜出)。财神保护与 choose_discard 同口径:
    参与最小向听比较、同向听时非财神优先。
    """
    return _legacy_react.best_post_claim_discard(
        hand, locked, vis, shape_cost=_discard_shape_cost)


def _best_post_claim_state(hand, locked, visible, *, include_baotou,
                           piao_allowed):
    """Return the best post-claim standing under the legacy progress order."""
    return _legacy_react.best_post_claim_state(
        hand,
        locked,
        visible,
        include_baotou=include_baotou,
        piao_allowed=piao_allowed,
        shape_progress=_legacy_shape_progress,
        shape_cost=_discard_shape_cost,
    )


def _should_piao(g, seat):
    """弃胡打白飘判定:成胡在手的 14 张里打出一张财神后,
    站立手牌仍听任意牌(爆头态保持,下次摸牌必胡,倍率翻倍)。

    前提:墙内活牌足够轮回到自己再摸(抓打圈内他家只能自摸胡,
    被抢胡风险低)。墙门与 choose_action 的 HU 短路共用
    ``PIAO_WALL_GUARD``(2026-09-18 由 5 收紧到 6)。
    """
    hand = g.hands[seat]
    if hand[W] == 0:
        return False
    # 收手态落袋为安:轮数/副露软收手时不弃胡博倍率(墙量走
    # PIAO_WALL_GUARD 硬门,Z 不重复作用于飘)
    if _push_abort_reason(g, seat, include_live=False) is not None:
        return False
    # 抓打圈冻结态只能弃刚摸牌——弃白飘仅在刚摸的就是财神时合法
    # (旧实现冻结盲,2026-09-18 A/B 实弹暴露)。
    if (getattr(g, "freeze", 0) > 0 and seat != getattr(g, "freezer", None)
            and g.drawn[seat] != W):
        return False
    after = list(hand)
    after[W] -= 1
    if not is_baotou(after, len(g.melds[seat])):
        return False
    return g.live_wall_left() >= PIAO_WALL_GUARD


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
        # Shape-v1 still ranks ordinary discards, but v33 made self-kong a
        # real draw-window choice.  Keep that choice on the shared legacy
        # policy helper so it can compare HU/KONG/discard in score units.
        if HU in acts or _kong_actions(acts):
            action, choice = _choose_draw_action(g, seat, acts)
            return action, {
                "version": "shape-v1", "profile": "shape-v1",
                "profile_fingerprint": EvalProfile.shape_v1().fingerprint,
                "level": "legacy", "selected": action,
                "reason": choice["reason"], "candidates": [],
                "kong_evaluation": choice,
            }
        return evaluate_discard_candidates(g, seat)
    return evaluate_reaction(g, seat)


def choose_shape_v2_action(g, seat, profile=None):
    """Opt-in shape-v2 evaluator using the profile's explicit action scope.

    The default remains ``discard``.  Calibrated ``hu-piao`` and ``all-root``
    profiles are available to offline/root callers; unsupported or incomplete
    transitions return a legacy action with an explicit delegation reason.
    """
    from .decision.fast_ev import choose_game_action
    from .decision.profile import ProfileSpec
    return choose_game_action(
        g, seat, profile or ProfileSpec.shape_v2_discard())


def choose_action(g, seat, evaluator=DEFAULT_BOT_EVALUATOR,
                  return_evaluation=False):
    """统一入口:返回该 seat 的动作。

    The two-argument production path uses the weighted two-ply frontier.
    ``None`` and ``evaluator='legacy'`` are compatibility aliases for the
    enabled legacyV2 route. ``evaluator='legacy-v1'`` explicitly selects the
    frozen rollback oracle; ``evaluator='shape-v1'`` opts into the shared
    shape evaluator.
    Callers that need an explanation can additionally request
    ``return_evaluation``.
    """
    if evaluator is None or evaluator == "legacy":
        evaluator = DEFAULT_BOT_EVALUATOR
    if evaluator not in (None, "legacy", "shape-v1", "shape_v1", "shape",
                         "shape-v2", "shape_v2", "ev2", "policy-v3",
                         "policy_v3", "legacy-two-ply-v1", "legacy_v1",
                         "legacy-v1", *LEGACY_V2_EVALUATORS,
                         *LEGACY_V2_EXPERIMENT_EVALUATORS,
                         *LEGACY_V2_OFFLINE_EVALUATORS):
        raise ValueError(f"unknown evaluator profile: {evaluator}")
    if evaluator in ("policy-v3", "policy_v3"):
        from .decision.policy_v3 import PolicyV3Runtime
        runtime = getattr(g, "_policy_v3_runtime", None)
        if runtime is None:
            runtime = PolicyV3Runtime()
        result = runtime.choose(g, seat, return_evaluation=True)
        return result if return_evaluation else result[0]
    if evaluator in ("shape-v2", "shape_v2", "ev2"):
        action, evaluation = choose_shape_v2_action(g, seat)
        return (action, evaluation) if return_evaluation else action
    if evaluator not in (None, "legacy"):
        if evaluator in (("legacy-two-ply-v1", "legacy_v1", "legacy-v1")
                         + LEGACY_V2_EVALUATORS
                         + LEGACY_V2_EXPERIMENT_EVALUATORS
                         + LEGACY_V2_OFFLINE_EVALUATORS):
            if evaluator in LEGACY_V2_OFFLINE_EVALUATORS:
                profile = LegacyTwoPlyProfile.weighted_offline()
                reaction_profile = LegacyReactionProfile.v2_offline()
            elif evaluator in LEGACY_V2_BASELINE_EVALUATORS:
                profile = LegacyTwoPlyProfile.weighted_online(
                    big_hand_enabled=False,
                    big_hand_same_shanten_enabled=False,
                    big_hand_plus_one_enabled=False,
                    shape_quality_enabled=False,
                    shape_quality_guard_enabled=False)
                reaction_profile = LegacyReactionProfile.v2_online()
            elif evaluator in LEGACY_V2_PHASE_A_EVALUATORS:
                profile = LegacyTwoPlyProfile.weighted_online(
                    big_hand_enabled=True,
                    big_hand_same_shanten_enabled=True,
                    big_hand_plus_one_enabled=False,
                    shape_quality_enabled=False,
                    shape_quality_guard_enabled=False)
                reaction_profile = LegacyReactionProfile.v2_online()
            elif evaluator in LEGACY_V2_PHASE_B_EVALUATORS:
                profile = LegacyTwoPlyProfile.weighted_online(
                    big_hand_enabled=True,
                    big_hand_same_shanten_enabled=True,
                    big_hand_plus_one_enabled=True,
                    shape_quality_enabled=False,
                    shape_quality_guard_enabled=False)
                reaction_profile = LegacyReactionProfile.v2_online()
            elif evaluator in LEGACY_V2_SHAPE_PHASE_A_EVALUATORS:
                profile = LegacyTwoPlyProfile.weighted_online(
                    big_hand_enabled=False,
                    big_hand_same_shanten_enabled=False,
                    big_hand_plus_one_enabled=False,
                    shape_quality_enabled=True,
                    shape_quality_stage="root",
                    shape_quality_guard_enabled=True,
                )
                reaction_profile = LegacyReactionProfile.v2_online()
            elif evaluator in LEGACY_V2_SHAPE_PHASE_B_EVALUATORS:
                profile = LegacyTwoPlyProfile.weighted_online(
                    big_hand_enabled=False,
                    big_hand_same_shanten_enabled=False,
                    big_hand_plus_one_enabled=False,
                    shape_quality_enabled=True,
                    shape_quality_stage="full",
                    shape_quality_guard_enabled=True,
                )
                reaction_profile = LegacyReactionProfile.v2_online()
            elif evaluator in LEGACY_V2_EVALUATORS:
                profile = LegacyTwoPlyProfile.weighted_online()
                reaction_profile = LegacyReactionProfile.v2_online()
            else:
                profile = LegacyTwoPlyProfile.default()
                reaction_profile = LegacyReactionProfile.v1()
            acts = g.legal_actions()
            if len(acts) == 1:
                evaluation = {
                    "version": profile.version,
                    "profile": profile.name,
                    "profile_fingerprint": profile.fingerprint,
                    "level": "legacy",
                    "complete": False,
                    "mode": profile.mode,
                    "selected": acts[0],
                    "legacy_best": acts[0],
                    "future_model": profile.model,
                    "candidates": [],
                    "missing": ["only_legal_action"],
                    "fallback_reason": "only_legal_action",
                    "partial_accepted": False,
                }
                return ((acts[0], evaluation) if return_evaluation else acts[0])
            if g.phase == "discard":
                action, evaluation = _choose_draw_action(
                    g, seat, acts, discard_profile=profile,
                    reaction_profile=(reaction_profile
                                      if reaction_profile.enabled
                                      and reaction_profile.future_enabled
                                      else None))
                if not isinstance(evaluation, dict) or "version" not in evaluation:
                    scoped = _legacy_v1_scope_info(
                        profile, action, "hu_kong_scope")
                    scoped["legacy_detail"] = evaluation
                    evaluation = scoped
                return ((action, evaluation)
                        if return_evaluation else action)
            action, evaluation = _choose_react_evaluated(
                g, seat, acts, return_evaluation=True,
                reaction_profile=reaction_profile)
            return ((action, evaluation) if return_evaluation else action)
        action, evaluation = choose_shape_action(g, seat)
        return (action, evaluation) if return_evaluation else action
    acts = g.legal_actions()
    if len(acts) == 1:
        return acts[0]
    if g.phase == "discard":
        return _choose_draw_action(g, seat, acts)[0]
    # react 阶段:吃碰杠决策
    return _choose_react(g, seat, acts, return_evaluation=return_evaluation)


def _choose_react(g, seat, acts, return_evaluation=False,
                  reaction_profile=None):
    """Choose CHOW/PONG/KONG_OPEN using shape progress and hard gates."""
    action, evaluation = _choose_react_evaluated(
        g, seat, acts, return_evaluation=return_evaluation,
        reaction_profile=reaction_profile)
    return (action, evaluation) if return_evaluation else action


def _choose_react_evaluated(g, seat, acts, return_evaluation=True,
                            reaction_profile=None):
    """Return ``(action, evaluation)`` for the legacy react window."""

    profile_disabled = (reaction_profile is not None
                        and not reaction_profile.enabled)
    chooser = (choose_reaction_v2 if reaction_profile is not None
               and not profile_disabled
               else choose_reaction_v1)
    kwargs = {}
    if reaction_profile is not None and not profile_disabled:
        kwargs.update({
            "profile": reaction_profile,
            "shape_cost": _discard_shape_cost,
            "post_discard_chain": _post_discard_chain,
        })
    evaluate_kong = _evaluate_kong_open
    if reaction_profile is not None and not profile_disabled:
        def evaluate_kong(g_, seat_, tile_, baseline_, visible_):
            return _evaluate_kong_open(
                g_, seat_, tile_, baseline_, visible_, v2=True,
                reaction_profile=reaction_profile)
    result = chooser(
        g,
        seat,
        acts,
        piao_allowed=_piao_context_allowed(g, seat),
        shape_progress=_legacy_shape_progress,
        post_claim_min_shanten=_post_claim_min_shanten,
        best_post_claim_state=_best_post_claim_state,
        evaluate_kong_open=evaluate_kong,
        kong_public_result=_kong_public_result,
        baotou_kernel_available=BAOTOU_UKEIRE_RUST,
        return_evaluation=return_evaluation,
        **kwargs,
    )
    if profile_disabled:
        action, evaluation = result
        if evaluation is not None:
            evaluation["reaction_profile"] = reaction_profile.as_json()
            evaluation["u2_fallback_reason"] = "profile_disabled"
            evaluation["selected"] = action
        return action, evaluation
    return result


def _legacy_claim_react(g, seat, acts):
    """Shape-v1 compatibility oracle for its historical KONG_OPEN window.

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
