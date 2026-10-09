"""启发式出牌 bot。

决策原则(按优先级):
0. 摸牌/杠补牌成胡默认提交 HU;若合法弃非财神牌、保留财神后已听任意牌，
   且活墙达到 PIAO_WALL_GUARD，则主动过 HU 进入下摸爆头胡；此机会忽略
   X/Y/Z 软收手。爆头态打白板仍听任意牌(财飘)时,
   活墙可摸张数 ≥ PIAO_WALL_GUARD(6)则弃胡打白飘(×4 起,下次摸牌
   必胡);活墙 < 6 落袋为安直接胡——跳过飘与杠的期望比较。
   v33 起杠后补牌仍是普通 draw 决策窗口;暗杠/补杠与 HU/弃牌按
   公开信息下一张摸牌的积分期望比较。
1. 摸牌阶段弃牌:最小化向听数 → 保护财神 → [持财神 + 听牌态
   叠加爆头档(openspec baotou-piao-aware-discard):弃后站立手为
   爆头听(听任意)的候选整体优先;同档进度信号比较
   1.5×爆头进张 + 当前规则允许的自摸胡牌进张。受自适应
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
from .shanten import (
    shanten,
    ukeire,
    BAOTOU_UKEIRE_RUST,
    piao_draw_mask,
    baotou_wait_fast,
)
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
    DEFAULT_HU_DISCARD_DELAY_MIN_GAIN_RATIO,
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

# Piao Search is deliberately configured as a cheap admission gate.  The
# pass cap stays disabled until a match/tournament session can persist it
# across Mirror.build_game() rebuilds; a per-Game counter would reset and
# falsely claim to be a safety cap online.
PIAO_SEARCH_MIN_RATIO = 0.55
PIAO_SEARCH_MIN_SELF_DRAWS = 2
PIAO_SEARCH_MAX_PASSES = None
PIAO_SEARCH_NODE_BUDGET = 34

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


def _push_abort_reasons(g, seat, include_live=True):
    """Return all observed X/Y/Z soft-stop signals in stable order.

    The old helper returned only the first reason, which made it impossible
    for a candidate-specific HU policy to distinguish an observed risk from a
    risk that was deliberately ignored for a guaranteed next-draw HU.
    """
    reasons = []
    rounds = _push_rounds.get(g, {}).get(seat, 0)
    if rounds >= BAOTOU_PUSH_MAX_ROUNDS:
        reasons.append("rounds")
    if _max_opp_melds(g, seat) >= BAOTOU_PUSH_OPP_MELDS:
        reasons.append("opp_melds")
    if include_live:
        try:
            live = g.live_wall_left()
        except AttributeError:
            live = 99  # 极简测试局/兼容对象无墙信息时不触发
        if live < BAOTOU_PUSH_MIN_LIVE:
            reasons.append("live_wall")
    return tuple(reasons)


def _push_abort_reason(g, seat, include_live=True):
    """收手判定:返回触发原因(None=继续推进)。

    ``include_live=False`` 供弃胡打白飘决策复用轮数/副露两个软收手
    (墙量已有 PIAO_WALL_GUARD 硬门,Z 不重复作用于飘)。
    """
    reasons = _push_abort_reasons(g, seat, include_live=include_live)
    return reasons[0] if reasons else None


def choose_discard(g, seat, return_info=False, profile=None):
    """返回弃牌 tile;``return_info=True`` 时返回 ``(tile, info dict)``。

    ``profile`` 仅用于显式启用 ``legacy-two-ply-v1``；省略时严格走
    原 legacy 排序。V1 只覆盖普通摸后弃牌，爆头、HU、杠和反应窗口
    始终由既有规则分支处理。

    优先级:向听数(硬约束,不为打风牌让向听倒退)→ 财神保护 →
    进张数 → 牌型结构损失 → 喂牌风险 → tile 编号(仅作稳定排序)。

    持财神 + 听牌态叠加爆头档(openspec baotou-piao-aware-discard):
    弃后站立手为爆头听(听任意)的候选整体优先;同一爆头档内以
    1.5*爆头进张 + 当前规则允许的自摸胡牌进张比较进度,再比较牌形。
    爆头推进受 X/Y/Z 自适应收手
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
            baotou_weight = profile.baotou_progress_weight if profile is not None else 1.5
            score_tiebreak = None
            if profile is not None and profile.enabled and profile.baotou_score_tiebreak_enabled:
                from .legacy_ready_score import next_draw_score
                remaining = tuple(max(0, 4-value) for value in vis)

                def score_tiebreak(tile, standing, waits, deadline):
                    chain, piao = _post_discard_chain(g, seat, tile, standing, locked)
                    return next_draw_score(standing, locked, waits, remaining,
                        seat=seat, dealer=g.dealer, base=g.base, chain=chain,
                        chain_piao=piao, deadline=deadline)

            best = _choose_discard_baotou(
                speed_cands, locked, vis, info,
                shape_aware=bool(profile and profile.shape_quality_enabled),
                diagnostics_enabled=profile is not None,
                you_cai_bi_kao=bool(getattr(g, "you_cai_bi_kao", False)),
                progress_weight=baotou_weight, score_tiebreak=score_tiebreak,
                score_budget_ms=min(5.0, profile.hard_budget_ms) if profile is not None else 5.0,
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
                           shape_aware=False, diagnostics_enabled=False,
                           you_cai_bi_kao=False, progress_weight=1.5,
                           score_tiebreak=None, score_budget_ms=5.0):
    """持财神听牌态的爆头档排序;预算超限返回 None(整局回退 legacy 键)。

    tier 0:弃后站立手为爆头听(听任意牌)——整体优先,档内沿用财神
    保护次序(不主动弃白;爆头态弃白飘由 _should_piao 在 HU 决策点
    统一裁决)。同 tier 候选以 1.5*爆头进张 + 当前规则允许的自摸
    胡牌进张排序;YCBK 门禁只从当前自摸胡牌进张中剔除不可兑现听口。
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
            visible = c if vis is None else vis
            baotou_uke = sum(
                max(0, 4 - int(visible[tile])) for tile in range(34))
            ranked.append((0, t, baotou_uke, shape, feed, c))
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
    frozen_best, frozen_key = None, None
    score_candidates = []
    candidate_rows = []
    for tier, t, u1, shape, feed, _hand, standing in measured:
        state = ukeire(_hand, locked, vis)
        structural_waits = [
            tile for tile in state[1] if _hand[tile] < 4
        ]
        # A baotou-ready standing hand wins on any next draw, including tile
        # types outside its ordinary structural waits.
        legal_waits = (
            [tile for tile in range(34) if _hand[tile] < 4]
            if tier == 0 else structural_waits
        )
        if you_cai_bi_kao and tier != 0:
            # The next turn is a normal self-draw after this discard. Under
            # YCBK, a wait containing/introducing White is not a legal HU
            # unless the pre-draw standing hand is already baotou-ready.
            legal_waits = [
                tile for tile in structural_waits
                if _hand[W] == 0 and tile != W
            ]
        visible = _hand if vis is None else vis
        structural_ukeire = sum(
            max(0, 4 - int(visible[tile])) for tile in structural_waits)
        current_selfdraw_hu_ukeire = sum(
            max(0, 4 - int(visible[tile])) for tile in legal_waits)
        progress_score_x2 = (3 * u1 + 2 * current_selfdraw_hu_ukeire
                             if progress_weight == 1.5 else
                             2 * progress_weight * u1 + 2 * current_selfdraw_hu_ukeire)
        key = (tier, t == W, -progress_score_x2,
               -(standing.encoded if shape_aware and standing else 0),
               shape, feed, t)
        old_key = (tier, t == W, -progress_score_x2, shape, feed, t)
        if progress_weight != 1.5:
            fixed_key = (tier, t == W, -(3*u1 + 2*current_selfdraw_hu_ukeire),
                         -(standing.encoded if shape_aware and standing else 0), shape, feed, t)
            if frozen_key is None or fixed_key < frozen_key:
                frozen_best, frozen_key = t, fixed_key
        if score_tiebreak is not None:
            score_candidates.append((key, t, _hand, legal_waits, u1, current_selfdraw_hu_ukeire))
        if best_key is None or key < best_key:
            best, best_key, best_tier = t, key, tier
        if legacy_key is None or old_key < legacy_key:
            legacy_best, legacy_key = t, old_key
        if diagnostics_enabled:
            row = {
                "tile": t,
                "shanten": shanten(_hand, locked),
                "current_ukeire": structural_ukeire,
                "structural_ukeire": structural_ukeire,
                "ukeire_tiles": structural_waits,
                "current_selfdraw_hu_ukeire": current_selfdraw_hu_ukeire,
                "current_selfdraw_hu_tiles": legal_waits,
                "baotou_tier": tier,
                "baotou_ukeire": u1,
                "baotou_progress_score": progress_score_x2 / 2.0,
                "baotou_progress_score_x2": progress_score_x2,
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
    shape_selected = best
    if progress_weight == 1.5:
        frozen_best = best
    info["baotou_progress_weight"] = progress_weight
    info["baotou_weight_changed_winner"] = best != frozen_best
    info["baotou_frozen_selected"] = frozen_best
    if score_tiebreak is not None:
        before = best
        tick = time.perf_counter()
        anchor = next(row for row in score_candidates if row[1] == best)
        # Compare at most three roots tied on the existing combined progress
        # score. Retain the winner; immediate HU mass may not decrease.
        ties = [row for row in sorted(score_candidates) if row[0][:3] == best_key[:3]
                and row[5] >= anchor[5]][:3]
        detail = {"baseline": before, "attempted": len(ties) > 1,
                  "complete": False, "override": False, "roots": len(ties), "values": {}}
        if len(ties) > 1:
            try:
                from .legacy_budget import cap_ms
                deadline = tick + cap_ms(score_budget_ms)/1000.0
                values = {row[1]: score_tiebreak(row[1], row[2], row[3], deadline) for row in ties}
                if time.perf_counter() >= deadline:
                    raise TimeoutError("ready score deadline")
                candidate = max(ties, key=lambda row: (values[row[1]]["value"], row[1] == before))
                if values[candidate[1]]["value"] > values[before]["value"] + 1e-9:
                    best = candidate[1]
                detail.update(complete=True, values=values, override=best != before)
            except (TimeoutError, AttributeError, TypeError, ValueError) as error:
                detail["fallback_reason"] = type(error).__name__
        detail["elapsed_ms"] = (time.perf_counter()-tick)*1000.0
        info["baotou_score_tiebreak"] = detail
    info["baotou_tier"] = best_tier
    info["baotou_nodes"] = nodes
    info["baotou_elapsed_ms"] = round((time.monotonic() - started) * 1000.0, 3)
    info["decision_scope"] = "baotou_scope"
    info["baotou_progress_formula_version"] = "baotou-weighted-ukeire-v1"
    info["baotou_progress_formula"] = (
        f"{progress_weight:g}*baotou_ukeire+current_selfdraw_hu_ukeire")
    info["stage_b_entered"] = False
    info["future_shape_quality_sum"] = None
    info["future_shape_quality_mean"] = None
    info["future_shape_denominator"] = None
    info["baotou_shape_used"] = bool(shape_aware)
    info["shape_quality_used"] = bool(shape_aware)
    info["shape_quality_stage"] = "baotou" if shape_aware else None
    info["shape_quality_version"] = (
        "standing-shape-v1" if shape_aware else None)
    info["shape_changed_winner"] = bool(shape_aware and shape_selected != legacy_best)
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


def _piao_search_fast_feature(standing, locked, visible, live_wall,
                              *, min_ratio=None, min_self_draws=None):
    """Compute the bounded Piao Search admission feature.

    This function intentionally performs no shanten/ukeire/scoring call and
    never searches for an alternative discard.  ``piao_draw_mask`` fixes the
    post-draw action to dropping one White; only the O(34) visibility weighting
    happens here.  The result is deterministic and suitable for an audit
    payload.
    """
    min_ratio = (PIAO_SEARCH_MIN_RATIO if min_ratio is None else
                 float(min_ratio))
    min_self_draws = (PIAO_SEARCH_MIN_SELF_DRAWS if min_self_draws is None
                      else int(min_self_draws))
    standing = tuple(int(value) for value in standing)
    visible = tuple(int(value) for value in visible)
    from . import shanten as _shanten_mod
    if not _shanten_mod.PIAO_DRAW_MASK_RUST:
        return {
            "eligible": False,
            "piao_search_eligible": False,
            "piao_ready_now": False,
            "mask": (False,) * 34,
            "piao_draw_types": [],
            "piao_types": 0,
            "piao_live": 0,
            "draw_live": 0,
            "piao_ratio": 0.0,
            "full_piao_search": False,
            "self_draw_horizon": max(0, int(live_wall) // 4),
            "min_piao_ratio": min_ratio,
            "min_search_self_draws": min_self_draws,
            "search_allowed": False,
            "search_skip_reason": "KERNEL_UNAVAILABLE",
            "nodes": 0,
            "max_search_passes": PIAO_SEARCH_MAX_PASSES,
            "search_passes_cap_enabled": False,
        }
    eligible = bool(standing[W] >= 2 and
                    baotou_wait_fast(standing, locked))
    mask = piao_draw_mask(standing, locked) if eligible else (False,) * 34
    remaining = tuple(max(0, 4 - visible[tile]) for tile in range(34))
    drawable = tuple(tile for tile in range(34)
                     if standing[tile] < 4 and remaining[tile] > 0)
    draw_live = sum(remaining[tile] for tile in drawable)
    piao_tiles = tuple(tile for tile in drawable if mask[tile])
    piao_live = sum(remaining[tile] for tile in piao_tiles)
    ratio = (piao_live / draw_live) if draw_live else 0.0
    horizon = max(0, int(live_wall) // 4)
    full = bool(draw_live and piao_live == draw_live)
    search_allowed = bool(
        eligible and not full and ratio >= min_ratio and
        horizon >= min_self_draws)
    return {
        "eligible": eligible,
        "piao_search_eligible": eligible,
        "piao_ready_now": False,
        "mask": tuple(bool(value) for value in mask),
        "piao_draw_types": list(piao_tiles),
        "piao_types": len(piao_tiles),
        "piao_live": int(piao_live),
        "draw_live": int(draw_live),
        "piao_ratio": round(float(ratio), 6),
        "full_piao_search": full,
        "self_draw_horizon": horizon,
        "min_piao_ratio": min_ratio,
        "min_search_self_draws": min_self_draws,
        "search_allowed": search_allowed,
        "search_skip_reason": (
            None if search_allowed else
            "NOT_ELIGIBLE" if not eligible else
            "PIAO_READY" if full else
            "HORIZON_TOO_SHORT" if horizon < min_self_draws else
            "PIAO_RATIO_LOW"),
        "nodes": PIAO_SEARCH_NODE_BUDGET if eligible else 0,
        # Online sessions do not yet carry a persistent pass counter through
        # Mirror.build_game, so a max-pass cap is explicitly disabled.
        "max_search_passes": PIAO_SEARCH_MAX_PASSES,
        "search_passes_cap_enabled": False,
    }


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
                "winning_tiles": (), "winning_mass": 0,
                "total_unseen": 0, "wall_left": max(0, live_wall)}
    if remaining is None:
        visible = _public_visible_counts(g, seat)
        remaining = [max(0, 4 - count) for count in visible]
    total_unseen = sum(remaining)
    if total_unseen <= 0:
        return {"value": 0.0, "win_probability": 0.0,
                "winning_tiles": (), "winning_mass": 0,
                "total_unseen": 0,
                "wall_left": max(0, live_wall - draw_delay)}

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
        "winning_mass": int(winning_mass),
        "total_unseen": int(total_unseen),
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


def _next_draw_baotou_discards(g, seat, actions):
    """Return every legal non-W discard that leaves an all-tile wait.

    This is deliberately only a candidate scanner.  The HU window must build
    all action roots before selecting one; a scanner must never own the final
    decision or bypass the independent piao candidate.
    """
    hand = g.hands[seat]
    if hand[W] <= 0:
        return ()
    locked = len(g.melds[seat])
    candidates = []
    for tile in actions:
        if tile < 0 or tile >= 34 or tile == W or hand[tile] <= 0:
            continue
        standing = list(hand)
        standing[tile] -= 1
        if is_baotou_wait(standing, locked):
            # Every candidate waits on all tile types with the same visible
            # pool, so legacy ukeire ties; preserve its shape/feed/tile order.
            candidates.append((
                _discard_shape_cost(hand, tile),
                _feed_risk(g, seat, tile),
                tile,
            ))
    return tuple(tile for _shape, _feed, tile in sorted(candidates))


def _next_draw_baotou_discard(g, seat, actions):
    """Compatibility helper returning the first scanned non-W candidate."""
    candidates = _next_draw_baotou_discards(g, seat, actions)
    return candidates[0] if candidates else None


def _hu_window_score(g, seat, tile, *, reaction_profile, remaining,
                     standing, chain, chain_piao):
    """Score one delayed HU-window discard in the configured value units."""
    # A baotou wait is already tenpai for the next hero draw.  Running the
    # generic second-draw tree here only explores impossible non-winning
    # branches and routinely spends the continuation deadline; the exact
    # public next-draw reward is the completed Stage-B fast path for this
    # shape.
    if is_baotou_wait(standing, len(g.melds[seat])):
        evaluation = _expected_next_draw_reward(
            g, seat, standing, len(g.melds[seat]), chain, chain_piao, False,
            remaining, draw_delay=4,
        )
        return {
            **evaluation,
            "complete": True,
            "value": evaluation["value"],
            "total_reward_ev": evaluation["value"],
            "immediate_reward_ev": evaluation["value"],
            "continuation_reward_ev": 0.0,
            "fallback_reason": None,
            "continuation_nodes": 0,
            "continuation": reaction_profile is not None,
            "continuation_mode": "baotou_fast_path",
        }
    if reaction_profile is not None and reaction_profile.future_enabled:
        evaluation = _legacy_kong.public_score_continuation(
            g, seat, standing, len(g.melds[seat]), chain, chain_piao, False,
            remaining, first_draw_delay=4,
            post_discard_chain=_post_discard_chain,
            node_budget=reaction_profile.continuation_node_budget,
            soft_budget_ms=reaction_profile.continuation_soft_budget_ms,
            hard_budget_ms=reaction_profile.continuation_hard_budget_ms,
        )
        return {
            **evaluation,
            "value": evaluation["total_reward_ev"],
            "continuation": True,
        }
    evaluation = _expected_next_draw_reward(
        g, seat, standing, len(g.melds[seat]), chain, chain_piao, False,
        remaining, draw_delay=4,
    )
    return {
        **evaluation,
        "complete": True,
        "value": evaluation["value"],
        "total_reward_ev": evaluation["value"],
        "immediate_reward_ev": evaluation["value"],
        "continuation_reward_ev": 0.0,
        "fallback_reason": None,
        "continuation_nodes": 0,
        "continuation": False,
    }


def _hu_window_candidate(g, seat, kind, action, *, reaction_profile,
                         remaining, observed_delay_reasons=()):
    """Build one HU-window candidate with its own delay policy.

    A shared ``delay_factor`` used to turn every delayed root into zero when
    one X/Y/Z stop signal fired.  That was too conservative for a root whose
    post-discard hand is proven to win on every public unseen next draw.  The
    candidate now records both observed signals and the signals actually
    applied to its value.
    """
    standing = list(g.hands[seat])
    standing[action] -= 1
    chain, chain_piao = _post_discard_chain(g, seat, action, standing)
    score = _hu_window_score(
        g, seat, action, reaction_profile=reaction_profile,
        remaining=remaining, standing=standing,
        chain=chain, chain_piao=chain_piao,
    )
    raw_value = float(score.get("value", score.get("total_reward_ev", 0.0)))
    complete = bool(score.get("complete", True))
    fallback_reason = score.get("fallback_reason")
    conditional_probability = float(score.get("win_probability", 0.0))
    guaranteed = bool(
        kind in {"piao_discard", "baotou_next_draw"} and
        is_baotou_wait(standing, len(g.melds[seat])) and
        complete and not fallback_reason and
        conditional_probability >= 1.0 - 1e-9 and
        int(score.get("total_unseen", 0) or 0) > 0 and
        g.live_wall_left() >= PIAO_WALL_GUARD)
    observed = tuple(str(reason) for reason in observed_delay_reasons)
    ignored = observed if guaranteed else ()
    applied = () if guaranteed else observed
    delay_factor = 1.0 if not applied else 0.0
    value = raw_value * delay_factor
    return {
        "type": kind,
        "action": action,
        "tile": action,
        "value": value,
        "raw_value": raw_value,
        "effective_value": value,
        "win_probability": conditional_probability,
        "conditional_next_draw_win_probability": conditional_probability,
        "winning_tiles": list(score.get("winning_tiles", ())),
        "winning_mass": int(score.get("winning_mass", 0) or 0),
        "total_unseen": int(score.get("total_unseen", 0) or 0),
        "guaranteed_next_draw_hu": guaranteed,
        "complete": complete,
        "fallback_reason": fallback_reason,
        "wall_left": score.get("wall_left", g.live_wall_left()),
        "continuation_nodes": int(score.get("continuation_nodes", 0) or 0),
        "continuation_elapsed_ms": score.get("elapsed_ms"),
        "delay_factor": float(delay_factor),
        "delay_policy": ("guaranteed_next_draw" if guaranteed else
                         "soft_risk_penalty" if applied else "no_delay"),
        "observed_delay_reasons": list(observed),
        "applied_delay_reasons": list(applied),
        "ignored_delay_reasons": list(ignored),
        "standing": tuple(standing),
        "chain": chain,
        "chain_piao": chain_piao,
    }


def _hu_window_detail(profile, action, candidates, *, piao_candidates,
                      next_draw_candidates, observed_delay_reasons,
                      stage_b_entered, stage_b_complete, continuation_nodes,
                      piao_search=None):
    """Return the common explanation payload for HU-window arbitration."""
    selected = next((row for row in candidates
                     if row.get("action") == action), None)
    selected_type = selected.get("type") if selected else None
    reason = (f"hu_window_{selected_type}" if selected_type
              else "hu_window_arbitration")
    detail = {
        "reason": reason,
        "decision_scope": "hu_window_arbitration",
        "selected": action,
        "selected_type": selected_type,
        "candidates": candidates,
        "hu_window_candidates": candidates,
        "baotou_scope_entered": True,
        "baotou_scope": {
            "entered": True,
            "piao_candidates": list(piao_candidates),
            "baotou_next_draw_candidates": list(next_draw_candidates),
        },
        "piao_candidates": list(piao_candidates),
        "baotou_next_draw_candidates": list(next_draw_candidates),
        "immediate_hu_available": True,
        "stage_b_entered": bool(stage_b_entered),
        "stage_b_completed": bool(stage_b_complete),
        "weighted_two_ply_entered": bool(stage_b_entered),
        "continuation_nodes": int(continuation_nodes),
        "continuation_fallback": bool(
            any(row.get("fallback_reason") for row in candidates)),
        # ``delay_penalty_reason`` now means an actually applied soft penalty.
        # Observed-but-ignored risks are exposed separately below.
        "delay_penalty_reason": next(
            (reason for row in candidates
             for reason in row.get("applied_delay_reasons", ())), None),
        "observed_delay_reasons": list(observed_delay_reasons),
        "ignored_delay_reasons": sorted({
            reason for row in candidates
            for reason in row.get("ignored_delay_reasons", ())}),
        "applied_delay_reasons": sorted({
            reason for row in candidates
            for reason in row.get("applied_delay_reasons", ())}),
    }
    if piao_search is not None:
        detail["piao_search"] = piao_search
    if profile is not None:
        detail.update({
            "version": profile.version,
            "profile": profile.name,
            "profile_fingerprint": profile.fingerprint,
            # Keep the training-label level vocabulary stable; the new
            # action-root path is identified by decision_scope below.
            "level": ("weighted-two-ply-v1" if stage_b_complete else
                       "weighted-two-ply-partial"),
            "complete": bool(stage_b_complete),
            "mode": profile.mode,
            "legacy_best": action,
            "future_model": profile.model,
            "search_phase": "two_ply" if stage_b_entered else None,
            "search_metrics": {
                "root_candidates": len(candidates),
                "draw_nodes": int(continuation_nodes),
                "stage_b_entered": int(bool(stage_b_entered)),
            },
            "fallback_reason": next((row.get("fallback_reason")
                                     for row in candidates
                                     if row.get("fallback_reason")), None),
            "partial_accepted": False,
        })
    return detail


def _choose_hu_window_action(g, seat, actions, *, discard_profile,
                             reaction_profile, piao_candidates,
                             next_draw_candidates, kongs,
                             piao_search=None):
    """Compare immediate HU, piao, baotou-next-draw and self-kong roots."""
    visible = _public_visible_counts(g, seat)
    remaining = [max(0, 4 - count) for count in visible]
    push_reasons = _push_abort_reasons(g, seat)
    roots = [{
        "type": "immediate_hu",
        "action": HU,
        "tile": HU,
        "value": _immediate_hu_reward(g, seat),
        "raw_value": _immediate_hu_reward(g, seat),
        "win_probability": 1.0,
        "complete": True,
        "fallback_reason": None,
        "wall_left": g.live_wall_left(),
        "continuation_nodes": 0,
        "delay_factor": 1.0,
        "effective_value": _immediate_hu_reward(g, seat),
        "guaranteed_next_draw_hu": False,
        "conditional_next_draw_win_probability": 1.0,
        "delay_policy": "immediate_hu",
        "observed_delay_reasons": list(push_reasons),
        "applied_delay_reasons": [],
        "ignored_delay_reasons": [],
    }]
    for tile in piao_candidates:
        roots.append(_hu_window_candidate(
            g, seat, "piao_discard", tile,
            reaction_profile=reaction_profile, remaining=remaining,
            observed_delay_reasons=push_reasons))
    for tile in next_draw_candidates:
        roots.append(_hu_window_candidate(
            g, seat, "baotou_next_draw", tile,
            reaction_profile=reaction_profile, remaining=remaining,
            observed_delay_reasons=push_reasons))

    # Self-kong keeps the existing hard gates and score continuation.  Its
    # baseline is the best shared standing available in this window.
    baseline_tile = (piao_candidates[0] if piao_candidates else
                     choose_discard(g, seat, profile=discard_profile))
    baseline_standing = list(g.hands[seat])
    baseline_standing[baseline_tile] -= 1
    baseline_progress = _legacy_shape_progress(
        baseline_standing, len(g.melds[seat]), visible,
        include_baotou=False, piao_allowed=_piao_context_allowed(g, seat))
    kong_results = [
        result for result in (
            _evaluate_self_kong(
                g, seat, action, baseline_progress, visible,
                v2=reaction_profile is not None,
                reaction_profile=reaction_profile)
            for action in kongs)
        if result is not None
    ]
    for result in kong_results:
        row = dict(result)
        row.update({
            "type": "self_kong",
            "action": result["action"],
            "raw_value": float(result.get("value", 0.0)),
            "effective_value": float(result.get("value", 0.0)),
            "value": float(result.get("value", 0.0)),
            "complete": bool(result.get("continuation_complete", True)),
            "fallback_reason": result.get("continuation_fallback_reason"),
            "continuation_nodes": int(result.get("continuation_nodes", 0)
                                       or 0),
            "delay_factor": 1.0,
            "guaranteed_next_draw_hu": False,
            "conditional_next_draw_win_probability": float(
                result.get("win_probability", 0.0)),
            "delay_policy": "soft_risk_penalty" if push_reasons else
            "no_delay",
            "observed_delay_reasons": list(push_reasons),
            "applied_delay_reasons": list(push_reasons),
            "ignored_delay_reasons": [],
        })
        if push_reasons:
            row["value"] = 0.0
            row["effective_value"] = 0.0
            row["delay_factor"] = 0.0
        roots.append(row)

    continuation_nodes = sum(int(row.get("continuation_nodes", 0) or 0)
                             for row in roots)
    delayed = [row for row in roots if row["type"] != "immediate_hu"]
    stage_b_entered = bool(reaction_profile is not None and delayed)
    stage_b_complete = bool(
        not delayed or all(row.get("complete", True) for row in delayed))
    # Stable tie-breaks are explicit: value, win probability, immediate HU,
    # then action number.  Candidate enumeration order cannot decide ties.
    rank = {"immediate_hu": 0, "piao_discard": 1,
            "baotou_next_draw": 2, "self_kong": 3}
    selected = max(
        roots,
        key=lambda row: (float(row.get("value", 0.0)),
                         float(row.get("win_probability", 0.0)),
                         -rank.get(row.get("type"), 99),
                         -int(row.get("action", 0))),
    )
    hu_delay_guard = None
    if (reaction_profile is not None and reaction_profile.enabled
            and reaction_profile.hu_discard_delay_min_gain_ratio > 1.0
            and stage_b_complete):
        ratio = reaction_profile.hu_discard_delay_min_gain_ratio
        immediate = roots[0]
        eligible = [row for row in roots if row["type"] not in {"piao_discard", "baotou_next_draw"}
                    or float(row["value"])+1e-9 >= float(immediate["value"])*ratio]
        frozen_selected = selected["action"]
        selected = max(eligible, key=lambda row: (float(row.get("value", 0.0)),
            float(row.get("win_probability", 0.0)), -rank.get(row.get("type"), 99),
            -int(row.get("action", 0))))
        hu_delay_guard = {"min_gain_ratio": ratio, "frozen_selected": frozen_selected,
                          "selected": selected["action"],
                          "override": selected["action"] != frozen_selected,
                          "rejected_actions": [row["action"] for row in roots if row not in eligible]}
    detail = _hu_window_detail(
        discard_profile, selected["action"], roots,
        piao_candidates=piao_candidates,
        next_draw_candidates=next_draw_candidates,
        observed_delay_reasons=push_reasons,
        stage_b_entered=stage_b_entered,
        stage_b_complete=stage_b_complete,
        continuation_nodes=continuation_nodes,
        piao_search=piao_search,
    )
    detail["selected_value"] = selected.get("value")
    if hu_delay_guard is not None:
        detail["hu_discard_delay_guard"] = hu_delay_guard
    detail["selected_raw_value"] = selected.get("raw_value")
    detail["selected_win_probability"] = selected.get("win_probability")
    detail["kong_candidates"] = [
        _kong_public_result(result) for result in kong_results]
    return selected["action"], detail


def _choose_draw_action(g, seat, actions=None, discard_profile=None,
                        reaction_profile=None):
    """Choose HU/piao, self-kong, or discard at a draw decision point.

    KONG is accepted only when its public next-replacement score expectation
    strictly exceeds the current baseline: an immediate HU, or the best
    legacy discard's next-draw expectation. This keeps the v33 action
    window useful without reducing the policy to ``if kong: return kong``.
    An explicit experimental reaction profile can additionally try an early
    replacement for the redundant tile selected by the original discard.
    """
    actions = tuple(g.legal_actions() if actions is None else actions)
    wall_left = g.live_wall_left() if HU in actions else None
    if HU in actions and wall_left < PIAO_WALL_GUARD:
        # 墙量守卫(openspec baotou-piao-aware-discard):活墙可摸不足
        # 6 张(死墙已扣)时落袋为安,直接胡——不弃胡博爆头/财飘、
        # 也不让杠的期望比较覆盖确定的 HU。与 _should_piao 同常量。
        return HU, {
            "reason": "hu_wall_guard_legacy",
            "wall_left": wall_left,
            "wall_guard": PIAO_WALL_GUARD,
        }
    if HU in actions:
        # The hard wall guard is the only HU-window early return.  Once it
        # passes, build every legal delayed root before selecting one.
        kongs = _kong_actions(actions)
        hand = g.hands[seat]
        locked = len(g.melds[seat])
        piao_candidates = []
        if W in actions and hand[W] > 0:
            standing = list(hand)
            standing[W] -= 1
            if is_baotou_wait(standing, locked):
                piao_candidates.append(W)
        next_draw_candidates = list(_next_draw_baotou_discards(
            g, seat, actions))
        piao_search = {
            "piao_search_eligible": False,
            "piao_ready_now": bool(piao_candidates),
            "search_allowed": False,
            "search_skip_reason": "NO_BAOTOU_NEXT_DRAW",
            "self_draw_horizon": max(0, int(wall_left) // 4),
            "piao_live": 0,
            "draw_live": 0,
            "piao_types": 0,
            "piao_ratio": 0.0,
            "full_piao_search": False,
            "piao_draw_types": [],
            "nodes": 0,
            "max_search_passes": PIAO_SEARCH_MAX_PASSES,
            "search_passes_cap_enabled": False,
        }
        if piao_candidates:
            # PIAO_READY bypasses the search shutter; the existing piao root
            # is compared directly with immediate HU.
            piao_search["search_skip_reason"] = "PIAO_READY"
        elif next_draw_candidates:
            # First release only inspects the best existing baotou-next-draw
            # candidate.  No candidate × 34 expansion is allowed here.
            best_next = next_draw_candidates[0]
            standing = list(hand)
            standing[best_next] -= 1
            piao_search = _piao_search_fast_feature(
                standing, locked, _public_visible_counts(g, seat), wall_left)
            piao_search["piao_ready_now"] = False
        if piao_candidates or next_draw_candidates or kongs:
            return _choose_hu_window_action(
                g, seat, actions,
                discard_profile=discard_profile,
                reaction_profile=reaction_profile,
                piao_candidates=tuple(piao_candidates),
                next_draw_candidates=tuple(next_draw_candidates),
                kongs=kongs,
                piao_search=piao_search,
            )
        return HU, {
            "reason": "hu_legacy",
            "immediate_hu_available": True,
            "selected": HU,
        }

    kongs = _kong_actions(actions)
    if not kongs:
        tile, info = choose_discard(
            g, seat, return_info=True, profile=discard_profile)
        info.setdefault("reason", "discard_legacy")
        return tile, info

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
    if (reaction_profile is not None and reaction_profile.enabled
            and reaction_profile.self_kong_progress_enabled and best is None
            and not g.in_freeze(seat)):
        progress_kong = _legacy_kong.redundant_self_kong_candidate(
            evaluations, baseline, live_wall=g.live_wall_left(), hand=g.hands[seat])
        if progress_kong is not None:
            return progress_kong["action"], {
                "reason": "kong_redundant_progress_experiment",
                "baseline": baseline,
                "selected_kind": progress_kong["kind"],
                "selected_tile": progress_kong["tile"],
                "wall_left": g.live_wall_left(),
                "kong_progress_override": True,
                "kong_evaluation": _kong_public_result(progress_kong),
                "kong_candidates": [_kong_public_result(row) for row in evaluations],
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
                  return_evaluation=False,
                  marginal_structure_guard_enabled=None,
                  speed_band_enabled=None,
                  pareto_frontier_enabled=None,
                  speed_band_min_ratio_by_shanten=None,
                  hu_discard_delay_min_gain_ratio=None,
                  quality_profile=None, quality_calibration=None,
                  quality_hand_plan=None, quality_state=None):
    """统一入口:返回该 seat 的动作。

    The two-argument production path uses the weighted two-ply frontier.
    ``None`` and ``evaluator='legacy'`` are compatibility aliases for the
    enabled legacyV2 route. ``evaluator='legacy-v1'`` explicitly selects the
    frozen rollback oracle; ``evaluator='shape-v1'`` opts into the shared
    shape evaluator.
    Callers that need an explanation can additionally request
    ``return_evaluation``.
    Speed-band/Pareto rollout flags are explicit optional overrides; leaving
    them ``None`` preserves the profile defaults and therefore the rollback
    ordering until the paired rollout gates pass.
    Online legacyV2 uses a 1.10 HU-delay reward multiple by default. Pass
    hu_discard_delay_min_gain_ratio=1.0 to restore the frozen behavior.
    """
    if quality_profile is not None:
        from .legacy_quality import choose_quality
        action, evaluation = choose_quality(
            g, seat, lambda: choose_action(
                g, seat, evaluator=evaluator, return_evaluation=True,
                marginal_structure_guard_enabled=marginal_structure_guard_enabled,
                speed_band_enabled=speed_band_enabled,
                pareto_frontier_enabled=pareto_frontier_enabled,
                speed_band_min_ratio_by_shanten=speed_band_min_ratio_by_shanten,
                hu_discard_delay_min_gain_ratio=hu_discard_delay_min_gain_ratio),
            profile=quality_profile, calibration=quality_calibration,
            hand_plan=quality_hand_plan, state=quality_state)
        return (action, evaluation) if return_evaluation else action
    if evaluator is None or evaluator == "legacy":
        evaluator = DEFAULT_BOT_EVALUATOR
    if (hu_discard_delay_min_gain_ratio is None
            and evaluator in LEGACY_V2_EVALUATORS):
        hu_discard_delay_min_gain_ratio = DEFAULT_HU_DISCARD_DELAY_MIN_GAIN_RATIO
    if (hu_discard_delay_min_gain_ratio is not None
            and evaluator not in LEGACY_V2_EVALUATORS):
        raise ValueError("HU discard-delay overrides require online legacyV2")
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
                    shape_quality_guard_enabled=False,
                    marginal_structure_guard_enabled=False)
                reaction_profile = LegacyReactionProfile.v2_online()
            elif evaluator in LEGACY_V2_PHASE_A_EVALUATORS:
                profile = LegacyTwoPlyProfile.weighted_online(
                    big_hand_enabled=True,
                    big_hand_same_shanten_enabled=True,
                    big_hand_plus_one_enabled=False,
                    shape_quality_enabled=False,
                    shape_quality_guard_enabled=False,
                    marginal_structure_guard_enabled=False)
                reaction_profile = LegacyReactionProfile.v2_online()
            elif evaluator in LEGACY_V2_PHASE_B_EVALUATORS:
                profile = LegacyTwoPlyProfile.weighted_online(
                    big_hand_enabled=True,
                    big_hand_same_shanten_enabled=True,
                    big_hand_plus_one_enabled=True,
                    shape_quality_enabled=False,
                    shape_quality_guard_enabled=False,
                    marginal_structure_guard_enabled=False)
                reaction_profile = LegacyReactionProfile.v2_online()
            elif evaluator in LEGACY_V2_SHAPE_PHASE_A_EVALUATORS:
                profile = LegacyTwoPlyProfile.weighted_online(
                    big_hand_enabled=False,
                    big_hand_same_shanten_enabled=False,
                    big_hand_plus_one_enabled=False,
                    shape_quality_enabled=True,
                    shape_quality_stage="root",
                    shape_quality_guard_enabled=True,
                    marginal_structure_guard_enabled=False,
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
                    marginal_structure_guard_enabled=False,
                )
                reaction_profile = LegacyReactionProfile.v2_online()
            elif evaluator in LEGACY_V2_EVALUATORS:
                profile_kwargs = {
                    "marginal_structure_guard_enabled": (
                        True if marginal_structure_guard_enabled is None else
                        bool(marginal_structure_guard_enabled)),
                }
                if speed_band_enabled is not None:
                    profile_kwargs["speed_band_enabled"] = bool(
                        speed_band_enabled)
                if pareto_frontier_enabled is not None:
                    profile_kwargs["pareto_frontier_enabled"] = bool(
                        pareto_frontier_enabled)
                if speed_band_min_ratio_by_shanten is not None:
                    profile_kwargs["speed_band_min_ratio_by_shanten"] = (
                        speed_band_min_ratio_by_shanten)
                profile = LegacyTwoPlyProfile.weighted_online(
                    **profile_kwargs)
                reaction_profile = LegacyReactionProfile.v2_online()
            else:
                profile = LegacyTwoPlyProfile.default()
                reaction_profile = LegacyReactionProfile.v1()
            if hu_discard_delay_min_gain_ratio is not None:
                reaction_profile = LegacyReactionProfile.v2_online(
                    hu_discard_delay_min_gain_ratio=hu_discard_delay_min_gain_ratio)
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
                if hu_discard_delay_min_gain_ratio is not None:
                    evaluation["reaction_profile"] = reaction_profile.as_json()
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
                if hu_discard_delay_min_gain_ratio is not None:
                    evaluation["reaction_profile"] = reaction_profile.as_json()
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
