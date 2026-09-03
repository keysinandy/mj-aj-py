"""含财神的向听数计算。

向听数 = 距离听牌所需的最少换牌次数;-1 表示已成牌。
13 张暗牌(有副露则相应减少)。

标准形(4 面子 + 雀头)分解为 m 个面子、t 个搭子(对子/两面/坎张,
即缺一张的面子候选),p 个对子(雀头候选),j 个财神:
  财神优先补搭子成面子,其次配单张成雀头,剩余作搭子
  shanten = 2*(4-locked-面子数) - 剩余搭子数 - 雀头
七对:7 - 对子数(财神配单张)。两者取小。

性能:shanten 结果按 (手牌字节串, locked) 记忆化;_std 剪枝界用
剩余牌数材料上界(纯自然牌 r 张最多省 2*(r//3)+(r%3)//2 向听,
每张财神最多省 2),是可证明的保守下界,比按位置数估计紧得多。
"""

from .tiles import W

_shanten_cache = {}
_CACHE_CAP = 1 << 20


def clear_caches():
    """清空记忆化缓存(长跑进程控内存时用)。"""
    _shanten_cache.clear()
    from .win import clear_cache as clear_win_cache
    clear_win_cache()


def shanten(counts, locked=0):
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


def ukeire(counts, locked=0, visible=None):
    """返回 (向听数, 进张种类列表, 进张总张数)。

    visible: 34 维各家可见牌计数(牌河+自己手牌+副露),用于扣除
    已见张;None 时按仅自己手牌估计。
    """
    from .win import is_win

    s = shanten(counts, locked)
    vis = visible if visible is not None else [0] * 34
    if s <= 0:
        if s == 0:
            acc = [t for t in range(34) if is_win(_add(counts, t), locked)]
            return s, acc, sum(_left(counts, t, vis) for t in acc)
        return s, [], 0
    acc = []
    for t in range(34):
        if counts[t] >= 4:
            continue
        if shanten(_add(counts, t), locked) < s:
            acc.append(t)
    return s, acc, sum(_left(counts, t, vis) for t in acc)


def _left(counts, t, vis):
    """牌 t 的剩余张数(4 - 自己持有 - 可见)。"""
    return max(0, 4 - counts[t] - vis[t])


def _add(counts, t):
    c = list(counts)
    c[t] += 1
    return c


def _win(c, locked):
    from .win import is_win
    return is_win(c, locked)
