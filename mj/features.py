"""NN 特征提取与动作空间编码(BC / RL 共用)。

平面布局(34 宽,值域 [0,1];PLANE_GROUPS 顺序,座位一律取相对序
自家/下家/对家/上家,保证跨座位平移不变):
  hand×4           自己暗牌(含刚摸)多重性,第 k 张平面 = 持有 > k 张
  meld_*×48        四座位 ×(吃/碰/杠各 4 张多重性平面;碰=3、杠=4、
                   吃按实际张数计)
  river_*×16       四座位 × 牌河时间帧:牌河按时间四等分,帧内出现置 1
  god×2            手中财神数、全牌河财神数(广播,/4)
  drawn×1          本人刚摸牌标记(react 阶段无,全 0)
  eng×4            向听数广播 (/9);弃后向听数地图(弃牌决策点逐张
                   试弃 (s+1)/9,无牌位 1.0;react 阶段全 1.0);
                   进张未见张数广播(站立手进张未见张数合计,4-visible
                   口径,/34 截断);进张种类图(站立手摸该牌向听降置 1)。
                   站立手 = 摸牌场景去刚摸牌;吃碰后的弃牌决策无摸牌
                   标记,取最优弃张后的手为基准

oracle=True 追加 16 平面(训练期完美信息 guiding,推理不用):
  oracle_hand_{next,across,prev}×4  三家暗牌多重性
  oracle_wall×4                     活牌墙组成——死墙不必单列:
                                    全体可见 + 三家手牌 + 活墙 = 136,
                                    死墙是余集,信息已完备

标量 N_SCALARS=8:庄家、活墙余/64、抓打圈余/3、本家在圈内、已吃/2、
阶段 one-hot(摸打 / 碰杠窗 / 吃窗)。

动作空间 N_ACTIONS=109(与平台指南口径一致):
  弃牌 0-33 / 过 34 / 吃 35-37(所吃牌在顺子低/中/高位)/ 碰 38 /
  明杠 39 / 暗杠 40-73(40+t)/ 加杠 74-107(74+t)/ 胡 108
"""

import numpy as np

from .game import (
    DEAD_WALL, PASS, HU, PONG, KONG_OPEN,
    KONG_CLOSED_BASE, KONG_ADD_BASE, CHOW_LOW,
)
from .shanten import shanten
from .tiles import W

_REL = ("self", "next", "across", "prev")

PLANE_GROUPS = (
    [("hand", 4)]
    + [(f"meld_{r}", 12) for r in _REL]
    + [(f"river_{r}", 4) for r in _REL]
    + [("god", 2), ("drawn", 1), ("eng", 4)]
)
ORACLE_GROUPS = [(f"oracle_hand_{r}", 4) for r in _REL[1:]] + [("oracle_wall", 4)]
N_PLANES = sum(n for _, n in PLANE_GROUPS)            # 75
N_PLANES_ORACLE = N_PLANES + sum(n for _, n in ORACLE_GROUPS)  # 91
N_SCALARS = 8

N_ACTIONS = 109


# ---------- 花色置换增广 ----------

# 3 花色重标注的 6 种置换:Q[new_idx] = old_idx(特征平面按列取 Q)。
# 花色块 m/p/s = 0-8/9-17/18-26,字牌 27-33 不动。动作空间中弃牌/
# 暗杠/加杠按牌种编码,同样置换;过/吃/碰/明杠/胡与牌种无关,不变。
import itertools as _it

SUIT_PERMS = []
for _order in _it.permutations((0, 1, 2)):
    _q = [0] * 34
    for _s in range(3):
        for _o in range(9):
            _q[_order[_s] * 9 + _o] = _s * 9 + _o
    for _t in range(27, 34):
        _q[_t] = _t
    _a = list(range(N_ACTIONS))
    for _t in range(34):
        _src = _order[_t // 9] * 9 + _t % 9 if _t < 27 else _t
        _a[_t] = _src                      # 弃牌:旧 t → 新 σ(t)
        _a[40 + _t] = 40 + _src            # 暗杠
        _a[74 + _t] = 74 + _src            # 加杠
    SUIT_PERMS.append((np.asarray(_q), np.asarray(_a)))


def augment_sample(planes, mask, action, perm):
    """按 (Q, A) 置换一个样本,返回 (planes, mask, action)。

    平面:新列 j ← 旧列 Q[j](Q 为 σ⁻¹,直接 fancy index);
    掩码:新位 σ(t) ← 旧位 t(赋值式,避免再求逆);
    动作:新动作 = A[旧动作](A 为 σ 前向)。标量与 value 不受花色影响。
    """
    q, a = perm
    m2 = np.empty_like(mask)
    m2[a] = mask
    return planes[..., q], m2, int(a[action])


# ---------- 动作空间 ----------

def action_to_flat(a):
    """引擎动作码 → 0..108。"""
    if a >= 0:
        return a
    if a == HU:
        return 108
    if a == PASS:
        return 34
    if a == PONG:
        return 38
    if a == KONG_OPEN:
        return 39
    if CHOW_LOW - 2 <= a <= CHOW_LOW:
        return 35 + (CHOW_LOW - a)
    if KONG_CLOSED_BASE - 33 <= a <= KONG_CLOSED_BASE:
        return 40 + (KONG_CLOSED_BASE - a)
    if KONG_ADD_BASE - 33 <= a <= KONG_ADD_BASE:
        return 74 + (KONG_ADD_BASE - a)
    raise ValueError(f"非法动作 {a}")


def flat_to_action(i):
    if 0 <= i <= 33:
        return i
    if i == 34:
        return PASS
    if 35 <= i <= 37:
        return CHOW_LOW - (i - 35)
    if i == 38:
        return PONG
    if i == 39:
        return KONG_OPEN
    if 40 <= i <= 73:
        return KONG_CLOSED_BASE - (i - 40)
    if 74 <= i <= 107:
        return KONG_ADD_BASE - (i - 74)
    if i == 108:
        return HU
    raise ValueError(f"非法动作下标 {i}")


def legal_mask(g):
    """当前行动者(g.current_seat())的 109 维合法动作布尔掩码。"""
    acts = set(g.legal_actions())
    return [flat_to_action(i) in acts for i in range(N_ACTIONS)]


# ---------- 特征平面 ----------

def _mult(counts, out):
    for k in range(4):
        out.append([1.0 if c > k else 0.0 for c in counts])


def _meld_planes(melds, out):
    chi, pong, kong = [0] * 34, [0] * 34, [0] * 34
    for kind, t in melds:
        if kind == "chow":
            chi[t] += 1
            chi[t + 1] += 1
            chi[t + 2] += 1
        elif kind == "pong":
            pong[t] += 3
        else:
            kong[t] += 4
    for arr in (chi, pong, kong):
        _mult(arr, out)


def _river_planes(river, out):
    n = len(river)
    for k in range(4):
        frame = [0.0] * 34
        for t in river[k * n // 4:(k + 1) * n // 4]:
            frame[t] = 1.0
        out.append(frame)


def _eng_planes(g, seat, out):
    hand = g.hands[seat]
    locked = len(g.melds[seat])
    need = 13 - 3 * locked
    n = sum(hand)
    d = g.drawn[seat]
    base, post = None, None
    if n == need:
        base = list(hand)  # react 阶段:站立手,无弃牌决策
    else:  # n == need+1:摸牌后或吃碰后的弃牌决策
        post = [1.0] * 34
        best_t, best_s = None, None
        for t in range(34):
            if hand[t]:
                c = list(hand)
                c[t] -= 1
                st = shanten(c, locked)
                post[t] = (st + 1) / 9.0
                if best_s is None or st < best_s:
                    best_t, best_s = t, st
        base = list(hand)
        if d is not None:
            base[d] -= 1
        else:  # 吃碰后无摸牌标记:以最优弃张后的站立手为基准
            base[best_t] -= 1
    s = shanten(base, locked)
    out.append([(s + 1) / 9.0] * 34)
    out.append(post if post is not None else [1.0] * 34)
    vis = g.visible_counts(seat)
    acc = [0.0] * 34
    total = 0
    for t in range(34):
        c = list(base)
        c[t] += 1
        if shanten(c, locked) < s:
            acc[t] = 1.0
            total += max(0, 4 - vis[t])
    out.append([min(total, 34) / 34.0] * 34)
    out.append(acc)


def _scalars(g, seat):
    if g.phase == "discard":
        onehot = (1.0, 0.0, 0.0)
    elif g.react_mode() == "claim":
        onehot = (0.0, 1.0, 0.0)
    else:
        onehot = (0.0, 0.0, 1.0)
    return [
        1.0 if seat == g.dealer else 0.0,
        g.live_wall_left() / 64.0,
        g.freeze / 3.0,
        1.0 if g.in_freeze(seat) else 0.0,
        g.chows[seat] / 2.0,
        *onehot,
    ]


def extract(g, seat, oracle=False):
    """返回 (planes [C,34] float32, scalars [8] float32)。

    决策点样本取 seat == g.current_seat();oracle=True 追加 16 个
    完美信息平面(训练 guiding,推理时不提取或置零)。
    """
    planes = []
    _mult(g.hands[seat], planes)
    for r in range(4):
        _meld_planes(g.melds[(seat + r) % 4], planes)
    for r in range(4):
        _river_planes(g.discards[(seat + r) % 4], planes)
    w_river = sum(d.count(W) for d in g.discards)
    planes.append([g.hands[seat][W] / 4.0] * 34)
    planes.append([w_river / 4.0] * 34)
    planes.append([1.0 if t == g.drawn[seat] else 0.0 for t in range(34)])
    _eng_planes(g, seat, planes)
    if oracle:
        for r in (1, 2, 3):
            _mult(g.hands[(seat + r) % 4], planes)
        wall = [0] * 34
        for t in g.wall[DEAD_WALL:]:
            wall[t] += 1
        _mult(wall, planes)
    return (
        np.asarray(planes, dtype=np.float32),
        np.asarray(_scalars(g, seat), dtype=np.float32),
    )
