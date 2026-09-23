"""含财神的向听数计算。

向听数 = 距离听牌所需的最少换牌次数;-1 表示已成牌。
13 张暗牌(有副露则相应减少)。

标准形(4 面子 + 雀头)分解为 m 个面子、t 个搭子(对子/两面/坎张,
即缺一张的面子候选),p 个对子(雀头候选),j 个财神:
  财神优先补搭子成面子,其次配单张成雀头,剩余作搭子
  shanten = 2*(4-locked-面子数) - 剩余搭子数 - 雀头
七对:7 - 对子数(财神配单张)。两者取小。

性能:shanten/ukeire 对外接口优先走 Rust 内核(rust/src/lib.rs,
`python3 -m pip install -e rust/` 构建,快 40~97x);未安装扩展时
自动回退纯 Python(shanten_py/ukeire_py,shanten 按 (手牌字节串,
locked) 记忆化);MJ_KERNELS=python 强制纯 Python(排障/对拍)。
Rust 侧的按花色分解表是 opt-in(`MJ_KERNELS_SHANTEN=memo`);默认
走逐牌 DFS,两者语义由 scripts/rust_parity.py 在两种模式下分别对拍。
两实现语义由 scripts/rust_parity.py 随机差分锁定。_std 剪枝界用
剩余牌数材料上界(纯自然牌 r 张最多省 2*(r//3)+(r%3)//2 向听,
每张财神最多省 2),是可证明的保守下界,比按位置数估计紧得多。
"""

import os

from .tiles import W

_shanten_cache = {}
_CACHE_CAP = 1 << 20


def clear_caches():
    """清空记忆化缓存(长跑进程控内存时用)。"""
    _shanten_cache.clear()
    from .win import clear_cache as clear_win_cache
    clear_win_cache()


def shanten_py(counts, locked=0):
    """纯 Python 向听数(记忆化);对外入口见下方调度器 shanten。"""
    key = (bytes(counts), locked)
    hit = _shanten_cache.get(key)
    if hit is not None:
        return hit
    n = sum(counts)
    need = 13 - 3 * locked
    if n not in (need, need + 1):
        raise ValueError(f"暗牌张数 {n} 与副露不符")
    s = min(_std(counts, locked), _chiitoi(counts, locked))
    # 13(暗)张手最低听牌(向听 0);-1 只对摸牌后的 14 张成立
    if n == need:
        s = max(0, s)
    if len(_shanten_cache) >= _CACHE_CAP:
        _shanten_cache.clear()
    _shanten_cache[key] = s
    return s


def _score(m, t, p, w, need_melds):
    """m 面子、t 搭子、p 对子、w 财神的向听数。"""
    # 财神优先补搭子成面子,其次配单张成雀头,再互配成面子
    sup = min(w, t)
    melds = m + sup
    w -= sup
    t -= sup
    if p >= 1:
        pair = 1
        p -= 1
    elif w >= 1:
        pair = 1
        w -= 1
    else:
        pair = 0
    # 剩余财神两两互配成面子
    melds += w // 2
    w %= 2
    # 末位单财神作搭子;剩余对子可摸第三张成刻,也算搭子
    t_all = t + w + p
    gaps = need_melds - melds
    used_t = min(max(0, t_all), max(0, gaps))
    return 2 * max(0, gaps) - used_t - pair


def _std(counts, locked):
    wilds = counts[W]
    nat = list(counts[:33])
    need_melds = 4 - locked
    base = 2 * need_melds
    # 13 张手 clamp 后最低 0,14 张最低 -1;到达即不可能再改进,整体终止
    floor = 0 if sum(counts) == 13 - 3 * locked else -1
    best = [9]
    rem0 = sum(nat)
    save = (0, 0, 1, 2, 2, 3, 4, 4, 5, 6, 6, 7, 8, 8, 9)

    def dfs(i, m, t, p, w, rem):
        if best[0] <= floor:
            return
        # 保守下界:已实现节省 2m+t+p,剩余财神每张最多再省 2,
        # 剩余 rem 张自然牌最多再省 save[rem]
        if base - 2 * m - t - p - 2 * w - save[rem] >= best[0]:
            return
        if i >= 33:
            s = _score(m, t, p, w, need_melds)
            if s < best[0]:
                best[0] = s
            return
        c = nat[i]
        if c == 0:
            dfs(i + 1, m, t, p, w, rem)
            return
        # 刻子(三枚自然牌)
        if c >= 3:
            nat[i] -= 3
            dfs(i, m + 1, t, p, w, rem - 3)
            nat[i] += 3
        # 刻子(两枚 + 1 财神):不可省——_score 无"对子+财神成刻"记账,
        # locked 多/gaps 少时严格优于对子+财神作雀头
        if c >= 2 and w >= 1:
            nat[i] -= 2
            dfs(i, m + 1, t, p, w - 1, rem - 2)
            nat[i] += 2
        # 对子(雀头候选)
        if c >= 2:
            nat[i] -= 2
            dfs(i, m, t, p + 1, w, rem - 2)
            nat[i] += 2
        # 顺子(三张连续自然牌)
        if i < 27 and i % 9 <= 6 and nat[i + 1] > 0 and nat[i + 2] > 0:
            nat[i] -= 1
            nat[i + 1] -= 1
            nat[i + 2] -= 1
            dfs(i, m + 1, t, p, w, rem - 3)
            nat[i] += 1
            nat[i + 1] += 1
            nat[i + 2] += 1
        # 两面搭子
        if i < 27 and i % 9 <= 7 and nat[i + 1] > 0:
            nat[i] -= 1
            nat[i + 1] -= 1
            dfs(i, m, t + 1, p, w, rem - 2)
            nat[i] += 1
            nat[i + 1] += 1
        # 坎张搭子
        if i < 27 and i % 9 <= 6 and nat[i + 2] > 0:
            nat[i] -= 1
            nat[i + 2] -= 1
            dfs(i, m, t + 1, p, w, rem - 2)
            nat[i] += 1
            nat[i + 2] += 1
        # 孤张(放弃位置 i 剩余的 c 张)
        dfs(i + 1, m, t, p, w, rem - c)

    dfs(0, 0, 0, 0, wilds, rem0)
    return best[0]


def _chiitoi(counts, locked):
    if locked:
        return 9
    wilds = counts[W]
    nat = counts[:33]
    pairs = sum(c // 2 for c in nat)
    singles = sum(c % 2 for c in nat)
    # 财神配单张成对;剩余奇数财神可配摸进的任意牌
    pairs += min(singles, wilds)
    rest = wilds - min(singles, wilds)
    pairs += rest // 2
    odd = rest % 2
    # 任意单张或奇数财神都意味着"摸进一张即可再成一对"
    return 7 - pairs - (1 if singles + odd > 0 else 0)


def waits(counts, locked=0):
    """听牌(向听数 0)时的和牌待牌种类。"""
    if shanten(counts, locked) != 0:
        return []
    out = []
    for t in range(34):
        c = list(counts)
        c[t] += 1
        if _win(c, locked):
            out.append(t)
    return out


def _ukeire_candidates(counts):
    """无财神时可能降低向听数的摸牌候选(升序列表)。

    数学依据:不在手(counts[t]==0)且与任何手数牌同花色距离 >2 的牌,
    在 _std 的任何分解分支里都只能走孤张——同种(对子/刻子)要求
    counts[t]>0,顺子/两面/坎张要求 ±1/±2 内有同花色手牌;七对在
    13 张奇数手必有单张,新孤张只增 singles 不增 pairs。因此这些牌
    摸到后向听数必不降,可安全跳过。字牌(27~32)无顺子语义,只做
    同种候选。财神(W)始终纳入(万能牌)。返回前排序,保证 acc
    与全量 range(34) 枚举同序。
    """
    useful = {W}
    for t in range(33):
        if counts[t] == 0:
            continue
        useful.add(t)  # 同种:对子/刻子/七对
        if t < 27:  # 数牌:顺子/两面/坎张只涉及同花色 ±2
            lo = t - t % 9
            for x in (t - 2, t - 1, t + 1, t + 2):
                if lo <= x < lo + 9:
                    useful.add(x)
    return sorted(useful)


def ukeire_py(counts, locked=0, visible=None):
    """纯 Python 进张枚举(含候选剪枝);对外入口见下方调度器 ukeire。

    visible: 34 维已见牌计数(自己手牌+四家牌河+全部副露),进张张数
    按 4 - visible[t] 折算。须含被评估手牌——弃牌候选场景传弃牌前的
    完整手牌即可(弃牌只是手→牌河转移,可见总量不变);
    None 时退化为仅按手牌折算(4 - 手牌张数)。

    性能:无财神且向听数 > 0 时用 _ukeire_candidates 剪枝(34 →
    约 15~22 次内层 shanten)。有财神时不剪——财神可配任意新单张
    成对(七对)或补结构(标准形),必须全量枚举;除非有完整证明 +
    大规模差分,不再细分。语义由 tests/test_shanten_props.py
    随机差分 + 非候选不降向听性质断言保证。
    """
    from .win import is_win

    s = shanten_py(counts, locked)
    vis = counts if visible is None else visible
    if s <= 0:
        if s == 0:
            acc = [t for t in range(34) if is_win(_add(counts, t), locked)]
            return s, acc, sum(_left(t, vis) for t in acc)
        return s, [], 0
    candidates = range(34) if counts[W] else _ukeire_candidates(counts)
    acc = []
    for t in candidates:
        if counts[t] >= 4:
            continue
        if shanten_py(_add(counts, t), locked) < s:
            acc.append(t)
    return s, acc, sum(_left(t, vis) for t in acc)


def baotou_ukeire_py(counts, locked=0, visible=None):
    """爆头进张枚举(纯 Python 回退;对外入口见下方调度器
    baotou_ukeire,Rust 内核优先)。

    对 13-3*locked 张站立手牌 S:摸 t 后存在弃牌 d 使 S+t-d 为爆头听
    (``is_baotou_wait``,听任意牌)的 t 集合 acc,及其未见加权和
    u1 = sum(4 - visible[t])。S 自身已是爆头听时任何摸牌都保持
    (弃掉刚摸的 t 即回到 S),全部牌计为进张。

    ``d`` 取 S+t 中的任意手牌——freeze 的「只弃刚摸牌」是引擎上下文
    约束,纯度量不感知;动作合法性以 legal_actions 为准。visible 口径
    与 ukeire 相同:须含被评估手牌,折算只在 4-visible 一处发生。

    候选剪枝比 ukeire 更激进:爆头听(听任意)的自然牌部分必须全部
    互相连接(面子/对子/刻子/±2 搭子),远处孤立牌唯一途径是
    (W,W,t) 刻子 + 第 3 个财神配任意摸牌成对——故 counts[W] >= 3
    时不剪(全量 34 枚举),否则用 ``_ukeire_candidates``(含财神
    本身)。W<3 剪枝安全性由随机差分保证(剪枝 vs 全量,含 W=1/2
    手牌,见 test_shanten;Rust 版同口径,scripts/rust_parity.py)。
    """
    from .win import is_baotou_wait

    vis = counts if visible is None else visible
    if is_baotou_wait(counts, locked):
        acc = [t for t in range(34) if counts[t] < 4]
        return acc, sum(_left(t, vis) for t in acc)
    candidates = range(34) if counts[W] >= 3 else _ukeire_candidates(counts)
    acc = []
    for t in candidates:
        if counts[t] >= 4:
            continue
        c = _add(counts, t)
        for d in range(34):
            if d == t or c[d] == 0:
                # 弃掉刚摸的 t 只回到原手 S(已知非爆头听)
                continue
            c[d] -= 1
            ok = is_baotou_wait(c, locked)
            c[d] += 1
            if ok:
                acc.append(t)
                break
    return acc, sum(_left(t, vis) for t in acc)


# ---------- Rust 内核调度(2026-09-11 接入默认路径) ----------
# mj_kernels(rust/,pip install -e rust/ 构建)可导入即优先 Rust;
# 未安装自动回退纯 Python。MJ_KERNELS=python 强制纯 Python(排障/对拍)。
try:
    from mj_kernels import shanten as _rust_shanten, ukeire as _rust_ukeire
except ImportError:
    _rust_shanten = None
    _rust_ukeire = None

try:
    from mj_kernels import best_future_discard as _rust_best_future_discard
except (ImportError, AttributeError):
    _rust_best_future_discard = None

try:
    from mj_kernels import discard_frontier as _rust_discard_frontier
except (ImportError, AttributeError):
    _rust_discard_frontier = None

try:
    from mj_kernels import discard_frontier_batch as _rust_discard_frontier_batch
except (ImportError, AttributeError):
    _rust_discard_frontier_batch = None

try:
    from mj_kernels import legacy_two_ply_frontier as _rust_legacy_two_ply_frontier
except (ImportError, AttributeError):
    _rust_legacy_two_ply_frontier = None

try:
    from mj_kernels import legacy_two_ply_kernel_version as _rust_legacy_two_ply_kernel_version
except (ImportError, AttributeError):
    _rust_legacy_two_ply_kernel_version = None

try:
    from mj_kernels import weighted_two_ply_frontier as _rust_weighted_two_ply_frontier
except (ImportError, AttributeError):
    _rust_weighted_two_ply_frontier = None

try:
    from mj_kernels import weighted_two_ply_kernel_version as _rust_weighted_two_ply_kernel_version
except (ImportError, AttributeError):
    _rust_weighted_two_ply_kernel_version = None

try:
    from mj_kernels import baotou_ukeire as _rust_baotou_ukeire
except (ImportError, AttributeError):
    _rust_baotou_ukeire = None


FUTURE_DISCARD_KERNEL_VERSION = (
    "rust-batch-v1" if _rust_best_future_discard is not None
    else "python-fallback")
DISCARD_FRONTIER_KERNEL_VERSION = (
    "rust-frontier-v1" if _rust_discard_frontier is not None
    else "python-frontier-v1")
DISCARD_FRONTIER_BATCH_KERNEL_VERSION = (
    "rust-frontier-batch-v1" if _rust_discard_frontier_batch is not None
    else None)

_FORCE_PY = os.environ.get("MJ_KERNELS", "").lower() == "python"
WEIGHTED_TWO_PLY_KERNEL_REQUIRED = "rust-weighted-two-ply-v3"

# bot 的爆头档只在 Rust 内核可用时启用(纯 Python 枚举 90~220ms/决策,
# 不可用);MJ_KERNELS=python 视同不可用。决策行为因此确定性可复现。
BAOTOU_UKEIRE_RUST = _rust_baotou_ukeire is not None and not _FORCE_PY

LEGACY_TWO_PLY_KERNEL_VERSION = (
    _rust_legacy_two_ply_kernel_version()
    if _rust_legacy_two_ply_frontier is not None
    and _rust_legacy_two_ply_kernel_version is not None
    and not _FORCE_PY else None
)
WEIGHTED_TWO_PLY_KERNEL_VERSION = (
    _rust_weighted_two_ply_kernel_version()
    if _rust_weighted_two_ply_frontier is not None
    and _rust_weighted_two_ply_kernel_version is not None
    and not _FORCE_PY else None
)


def kernel_runtime_diagnostic():
    """启动诊断:实际内核、版本与降级影响面。

    缺少兼容版本的原生 weighted 内核时 LegacyV2 会安全回退到 v1；
    运行侧必须在启动时显式报告，而不是只在个别决策字段里可查。
    """
    forced_python = _FORCE_PY
    shanten_rust = _rust_shanten is not None and not forced_python
    weighted_present = (WEIGHTED_TWO_PLY_KERNEL_VERSION is not None
                        and not forced_python)
    weighted_compatible = (weighted_present and
                           WEIGHTED_TWO_PLY_KERNEL_VERSION ==
                           WEIGHTED_TWO_PLY_KERNEL_REQUIRED)
    if forced_python:
        reason = "MJ_KERNELS=python"
    elif _rust_shanten is None or _rust_ukeire is None:
        reason = "mj_kernels_missing"
    elif not weighted_present:
        reason = "weighted_kernel_missing"
    elif not weighted_compatible:
        reason = "weighted_kernel_version_mismatch"
    else:
        reason = None
    degraded = reason is not None
    return {
        "shanten_kernel": "rust" if shanten_rust else "python",
        "weighted_kernel": "rust" if weighted_present else "unavailable",
        "weighted_kernel_version": WEIGHTED_TWO_PLY_KERNEL_VERSION,
        "weighted_kernel_required": WEIGHTED_TWO_PLY_KERNEL_REQUIRED,
        "weighted_kernel_compatible": weighted_compatible,
        "legacy_two_ply_kernel_version": LEGACY_TWO_PLY_KERNEL_VERSION,
        "baotou_kernel": "rust" if BAOTOU_UKEIRE_RUST else "python",
        "degraded": degraded,
        "reason": reason,
        "impact": ("legacyV2 weighted 前瞻不可用,当前决策将事务性回退 v1"
                   if degraded else None),
    }


def format_kernel_diagnostic():
    """单行启动诊断文本。"""
    info = kernel_runtime_diagnostic()
    state = "降级" if info["degraded"] else "正常"
    detail = (f"weighted={info['weighted_kernel']}"
              f"({info['weighted_kernel_version'] or 'n/a'})")
    if not info["weighted_kernel_compatible"]:
        detail += f" required={info['weighted_kernel_required']}"
    if info["reason"]:
        detail += f" reason={info['reason']}"
    return f"[kernel] {state} {detail} shanten={info['shanten_kernel']}"


def shanten(counts, locked=0):
    """向听数(调度器:Rust 内核优先,回退 shanten_py)。"""
    if _rust_shanten is not None and not _FORCE_PY:
        return _rust_shanten(counts, locked)
    return shanten_py(counts, locked)


def ukeire(counts, locked=0, visible=None):
    """进张枚举(调度器:Rust 内核优先,回退 ukeire_py)。"""
    if _rust_ukeire is not None and not _FORCE_PY:
        return _rust_ukeire(counts, locked, visible)
    return ukeire_py(counts, locked, visible)


def baotou_ukeire(counts, locked=0, visible=None):
    """爆头进张枚举(调度器:Rust 内核优先,回退 baotou_ukeire_py)。

    语义见 ``baotou_ukeire_py`` docstring;差分验收
    scripts/rust_parity.py(随机手牌 × 有/无财神 × 随机 visible)。
    """
    if _rust_baotou_ukeire is not None and not _FORCE_PY:
        acc, u1 = _rust_baotou_ukeire(counts, locked, visible)
        return list(acc), u1
    return baotou_ukeire_py(counts, locked, visible)


def best_future_discard(counts, locked=0, visible=None, include_tiles=True):
    """批量找出最低向听的立即弃牌(有 Rust 扩展时可用)。

    返回 ``(tile, shanten, ukeire_tiles, u1)``；扩展未安装或被强制
    使用 Python 时返回 ``None``，由上层保留可审计的 Python 路径。
    """
    if _rust_best_future_discard is None or _FORCE_PY:
        return None
    try:
        return _rust_best_future_discard(counts, locked, visible,
                                         include_tiles)
    except TypeError:
        # Keep an already-installed pre-include_tiles wheel usable during a
        # rolling deploy; its extra tile list is harmless when the caller
        # only requested the scalar total.
        result = _rust_best_future_discard(counts, locked, visible)
        if not include_tiles and result is not None:
            return (result[0], result[1], [], result[3])
        return result


def discard_frontier(counts, locked=0, visible=None, legal_discards=None,
                     include_tiles=True):
    """Optional Rust batch for every legal discard.

    ``None`` is an intentional capability signal.  The decision layer owns
    the semantic Python fallback and must never substitute the old
    min-shanten ``best_future_discard`` result.
    """
    if _rust_discard_frontier is None or _FORCE_PY:
        return None
    try:
        return _rust_discard_frontier(
            counts, locked, visible, legal_discards, include_tiles)
    except (TypeError, ValueError):
        return None


def discard_frontier_batch(states, locked=0, visibles=None,
                           legal_discards=None, include_tiles=True):
    """Optional Rust batch for several all-legal discard frontiers.

    ``None`` is a capability signal, matching :func:`discard_frontier`.
    The decision layer may then run its reference implementation state by
    state; this helper never substitutes ``best_future_discard``.
    """
    if _rust_discard_frontier_batch is None or _FORCE_PY:
        return None
    try:
        return _rust_discard_frontier_batch(
            states, locked, visibles, legal_discards, include_tiles)
    except (TypeError, ValueError):
        return None


def legacy_two_ply_frontier(roots, root_shantens, visible, legal_masks,
                            locked=0, frozen=False, node_budget=4096,
                            time_budget_ms=8.0, include_best_discards=True):
    """Optional native batch evaluator for legacy two-ply V1.

    ``None`` is a capability signal when the extension is unavailable or
    ``MJ_KERNELS=python`` is active.  Native validation/runtime errors are
    deliberately propagated so the legacy evaluator can record a precise
    transactional fallback reason instead of silently mixing partial values.
    """
    if _rust_legacy_two_ply_frontier is None or _FORCE_PY:
        return None
    return _rust_legacy_two_ply_frontier(
        roots, root_shantens, visible, legal_masks, locked, frozen,
        node_budget, float(time_budget_ms), include_best_discards,
    )


def weighted_two_ply_frontier(
        roots, root_shantens, visible, legal_masks, locked=0, frozen=False,
        node_budget=100000, soft_budget_ms=40.0, hard_budget_ms=50.0,
        cache_capacity=8192, min_partial_coverage=0.90,
        include_best_discards=True, workers=0, stage_a_only=False):
    """Optional weighted/partial native two-ply frontier."""
    if _rust_weighted_two_ply_frontier is None or _FORCE_PY:
        return None
    args = (
        roots, root_shantens, visible, legal_masks, locked, frozen,
        node_budget, float(soft_budget_ms), float(hard_budget_ms),
        cache_capacity, float(min_partial_coverage), include_best_discards,
        int(workers),
    )
    if stage_a_only:
        return _rust_weighted_two_ply_frontier(
            *args, stage_a_only=True)
    return _rust_weighted_two_ply_frontier(*args)


def _left(t, vis):
    """牌 t 的未见张数(4 - 已见;vis 须含被评估手牌,避免持牌双扣)。"""
    return max(0, 4 - vis[t])


def _add(counts, t):
    c = list(counts)
    c[t] += 1
    return c


def _win(c, locked):
    from .win import is_win
    return is_win(c, locked)
