"""含财神的和牌/听牌/爆头判定。

所有函数接受 34 维暗牌计数;locked 为已副露的面子数(明/暗杠都算 1 个面子,
杠多出的第 4 张牌不占 14 张结构,故暗牌目标张数为 14-3*locked)。
财神不能被吃碰杠,只会出现在暗牌或牌河中。

is_win 结果按 (手牌字节串, locked) 记忆化——听牌/爆头判定各查 34 次,
重复手牌占比高。
"""

from .tiles import W

_win_cache = {}
_CACHE_CAP = 1 << 20


def clear_cache():
    _win_cache.clear()


def is_win(counts, locked=0):
    """和牌判定:标准形(4 面子 + 雀头)或七对。结果按手牌记忆化。"""
    key = (bytes(counts), locked)
    hit = _win_cache.get(key)
    if hit is not None:
        return hit
    ok = _is_win(counts, locked)
    if len(_win_cache) >= _CACHE_CAP:
        _win_cache.clear()
    _win_cache[key] = ok
    return ok


def _melds(nat, wilds, need):
    """回溯判定 nat(33 维自然牌)+ wilds 个财神能否组成 need 个面子。就地修改 nat 并回溯。"""
    t = 0
    while t < 33 and nat[t] == 0:
        t += 1
    if t == 33:
        return wilds == 3 * need
    if need == 0:
        return False
    # 刻子分支:k 张 t + (3-k) 个财神
    for k in range(min(nat[t], 3), 0, -1):
        w = 3 - k
        if wilds >= w:
            nat[t] -= k
            ok = _melds(nat, wilds - w, need - 1)
            nat[t] += k
            if ok:
                return True
    # 顺子分支:t 是当前最小自然牌,只能作为顺子首位;t+1/t+2 用自然牌或财神
    if t < 27 and t % 9 <= 6:
        for u1 in (1, 0):
            for u2 in (1, 0):
                if u1 and nat[t + 1] == 0:
                    continue
                if u2 and nat[t + 2] == 0:
                    continue
                w = 2 - u1 - u2
                if wilds < w:
                    continue
                nat[t] -= 1
                nat[t + 1] -= u1
                nat[t + 2] -= u2
                ok = _melds(nat, wilds - w, need - 1)
                nat[t] += 1
                nat[t + 1] += u1
                nat[t + 2] += u2
                if ok:
                    return True
    return False


def _is_win(counts, locked=0):
    if sum(counts) != 14 - 3 * locked:
        return False
    wilds = counts[W]
    nat = list(counts[:33])
    need = 4 - locked
    for t in range(33):
        if nat[t] >= 2:
            nat[t] -= 2
            ok = _melds(nat, wilds, need)
            nat[t] += 2
            if ok:
                return True
        if nat[t] >= 1 and wilds >= 1:
            nat[t] -= 1
            ok = _melds(nat, wilds - 1, need)
            nat[t] += 1
            if ok:
                return True
    if wilds >= 2 and _melds(nat, wilds - 2, need):
        return True
    return locked == 0 and is_chiitoi(counts)[0]


def is_chiitoi(counts):
    """返回 (是否七对, 豪华组数)。

    同种奇数张的自然牌必须配 1 个财神;豪华组 = 手中恰持该牌全部 4 张
    真牌(4 张真白板也算 1 组),3 真牌 + 财神补齐不算——平台 fan-calc
    实测口径(2026-09-01 对局七对判定修复)。
    """
    if sum(counts) != 14:
        return False, 0
    wilds = counts[W]
    singles = sum(c % 2 for c in counts[:33])
    if singles > wilds or (wilds - singles) % 2:
        return False, 0
    if sum(c // 2 for c in counts[:33]) + singles + (wilds - singles) // 2 != 7:
        return False, 0
    groups = sum(1 for c in counts if c == 4)
    return True, groups


def add_tile(counts, t):
    c = list(counts)
    c[t] += 1
    return c


def waiting_tiles(counts13, locked=0):
    """13(暗)张手的听牌集合。

    财神(白板)百搭,摸到财神本身也能成胡(平台指南 v2 已确认:
    爆头态摸到白板可提交 hu,也可弃胡打白飘博财飘)。
    """
    return [t for t in range(34) if is_win(add_tile(counts13, t), locked)]


def is_baotou_wait(counts13, locked=0):
    """原始判定:任意牌都能胡(不含 4 白板排除)。"""
    return all(is_win(add_tile(counts13, t), locked) for t in range(34))


def is_baotou(counts13, locked=0):
    """爆头态:站立手牌摸任意牌即胡;财神数不限、支持副露。

    恰好持有 4 张白板不视为爆头(此时按「4个白板」×2 计,平台
    fan-calc 实测:摸牌前持 4 白 → 非爆头;持 3 白摸白 → 爆头)。
    """
    if counts13[W] == 4:
        return False
    return is_baotou_wait(counts13, locked)
