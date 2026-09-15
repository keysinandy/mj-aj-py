"""番型倍率与结算(平台指南 v34,沿用既有计分口径)。

v26-v34 未改动这里的倍率和自摸支付公式;v32 的杠后爆头重算由调用方
传入补牌前 standing hand 自然体现,v33 只改变杠后成胡的动作窗口。历史
落库分数不按新指南回溯覆盖,重算结果须单独标记。

总番 = 1 × 分支因子 × 2^动作链次数 ×(4 白板 ×2)×(爆头 ×2),
计算顺序:分支 → 动作链 → 4 白板 → 爆头(最后统一加)。

- 分支:平胡 ×1 / 七对 ×2×2^豪华组数(豪华组 = 手中恰持 4 张真牌;
  4 白板仅两两自配、未补配落单时算 1 组,白板补单不重复计豪华——
  v21 裁定)。两分支同真时七对番必高,直接取七对。
- 动作链:每个飘/杠动作 ×2,可连续可组合(杠开/财飘/连杠/杠飘链);
  打出非飘非杠的牌断链清零(链状态由 game.Game 跟踪)。
- 4 白板:胡牌时手牌留存 + 链内飘出的白板 = 4(v6 起指南与
  fan-calc 同口径)。
- 爆头:摸牌前站立手牌听任意牌(恰持 4 白板同样计爆头,与
  「4个白板」×2 叠加——v21 裁定)。

计分 = 底分 × 总番 ×(庄家 ×8 / 闲家 ×1),三家分别结算。
"""

from .tiles import W
from .win import is_baotou, is_chiitoi


def _chain_label(count, piao):
    if piao == 0:
        return "杠开" if count == 1 else f"连杠×{count}"
    if piao == count:
        return ("财飘", "双财飘", "三财飘")[min(count, 3) - 1]
    return f"杠飘链×{count}"


def hand_multiplier(concealed14, standing13, locked, chain_count=0, chain_piao=0):
    """返回 (总倍率, 番型明细)。

    concealed14: 和牌时暗牌计数;standing13: 摸牌前的站立暗牌计数
    (爆头判定依据);locked: 副露面子数;chain_count/chain_piao:
    本家动作链的飘/杠总次数与其中飘出白板数。
    """
    mult = 1
    parts = []
    chi, groups = is_chiitoi(concealed14) if locked == 0 else (False, 0)
    if chi:
        mult *= 2 << groups
        parts.append("七对" if groups == 0 else f"豪华七对×{groups}")
    else:
        parts.append("平胡")
    if chain_count > 0:
        mult *= 2 ** chain_count
        parts.append(_chain_label(chain_count, chain_piao))
    if concealed14[W] + chain_piao == 4:
        mult *= 2
        parts.append("4个白板")
    if is_baotou(standing13, locked):
        mult *= 2
        parts.append("爆头")
    return mult, parts


def settle(winner, dealer, mult, base=1):
    """自摸结算,返回四家分数增量(和牌家为正,总和为 0)。

    庄家和:三家闲家各付 base*mult*8;闲家和:庄家付 base*mult*8,
    其余两家各付 base*mult。
    """
    pay = [0] * 4
    unit = base * mult
    if winner == dealer:
        for s in range(4):
            pay[s] = 24 * unit if s == winner else -8 * unit
    else:
        for s in range(4):
            if s == winner:
                pay[s] = 10 * unit
            elif s == dealer:
                pay[s] = -8 * unit
            else:
                pay[s] = -unit
    return pay
