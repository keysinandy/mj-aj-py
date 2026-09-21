"""摸后弃牌听口分析。

这个模块只接受一个已经重建好的 ``Game`` 投影，且只读取观察座位的手牌、
四家牌河和副露。即使调用方传入的是本地全知 ``Game``，也不会读取其他座位
的暗手或真实牌墙顺序，因此线上 ``Mirror.build_game`` 和本地回放使用同一
套信息边界。
"""

from __future__ import annotations

from ..tiles import W
from ..win import is_baotou, waiting_tiles

__all__ = ["PublicMaterialError", "analyze_discard_hints"]


class PublicMaterialError(ValueError):
    """公开物料计数无法证明,调用方应隐藏提示并保留诊断。"""


def _public_visible_counts(game, seat):
    """只计算观察座位手牌、牌河和副露中的物理牌数量。"""
    visible = [int(value) for value in game.hands[seat]]
    if len(visible) != 34 or any(value < 0 for value in visible):
        raise PublicMaterialError("观察座位手牌计数无效")

    for river in game.discards:
        for tile in river:
            if type(tile) is not int or not 0 <= tile < 34:
                raise PublicMaterialError("牌河包含无效牌")
            visible[tile] += 1

    for melds in game.melds:
        for meld in melds:
            if not isinstance(meld, (list, tuple)) or len(meld) < 2:
                raise PublicMaterialError("副露格式无效")
            kind, tile = meld[0], meld[1]
            if type(tile) is not int or not 0 <= tile < 34:
                raise PublicMaterialError("副露包含无效牌")
            if kind == "chow":
                if tile < 0 or tile >= 25 or tile % 9 > 6:
                    raise PublicMaterialError("吃牌起张无效")
                for value in (tile, tile + 1, tile + 2):
                    visible[value] += 1
            elif str(kind).startswith("kong"):
                visible[tile] += 4
            else:
                # pong/peng 以及兼容的其它刻子表示都占用三张实体牌。
                visible[tile] += 3

    if any(value > 4 for value in visible):
        raise PublicMaterialError("公开物料计数超过四张")
    return visible


def _ordinary_draw_is_legal(game, standing, locked, tile):
    """复用 Game._can_hu 的普通摸牌门禁,不把候选当作杠后补牌。"""
    completed = list(standing)
    completed[tile] += 1
    if not getattr(game, "you_cai_bi_kao", False):
        return True
    if completed[W] <= 0:
        return True
    return is_baotou(standing, locked)


def analyze_discard_hints(game, seat):
    """返回当前摸后状态下每种合法弃牌的听口提示。

    返回值是可直接写入 ``ReplayFrame.discard_hints`` 的 JSON 形状。非摸后、
    缺少合法弃牌、终局或手牌张数无法自洽时返回空列表。公开物料超过四张
    则抛出 :class:`PublicMaterialError`,由帧管线隐藏本帧提示并记录诊断。
    """
    if type(seat) is not int or not 0 <= seat < 4:
        return []
    if getattr(game, "done", False) or getattr(game, "phase", None) != "discard":
        return []
    if getattr(game, "turn", seat) != seat:
        return []

    hands = getattr(game, "hands", None)
    drawn = getattr(game, "drawn", None)
    melds = getattr(game, "melds", None)
    if (not isinstance(hands, list) or len(hands) != 4
            or not isinstance(drawn, list) or len(drawn) != 4
            or not isinstance(melds, list) or len(melds) != 4):
        return []
    drawn_tile = drawn[seat]
    if type(drawn_tile) is not int or not 0 <= drawn_tile < 34:
        return []

    hand = [int(value) for value in hands[seat]]
    locked = len(melds[seat])
    expected = 14 - 3 * locked
    if (len(hand) != 34 or any(value < 0 for value in hand)
            or sum(hand) != expected or hand[drawn_tile] <= 0):
        return []

    legal_discards = {
        int(action) for action in game.legal_actions()
        if type(action) is int and 0 <= action < 34
    }
    if not legal_discards:
        return []

    visible = _public_visible_counts(game, seat)
    hints = []
    for discard_tile in sorted(legal_discards):
        if hand[discard_tile] <= 0:
            continue
        standing = list(hand)
        standing[discard_tile] -= 1
        structural = waiting_tiles(standing, locked)
        if not structural:
            continue
        structural_waits = [
            {"tile": int(tile), "unseen": int(4 - visible[tile])}
            for tile in structural
        ]
        legal_waits = [
            item for item in structural_waits
            if _ordinary_draw_is_legal(game, standing, locked, item["tile"])
        ]
        status = "legal_tenpai" if legal_waits else "rule_blocked_tenpai"
        hints.append({
            "discard_tile": int(discard_tile),
            "status": status,
            "legal_waits": legal_waits,
            "structural_waits": structural_waits,
            "total_legal_unseen": sum(item["unseen"] for item in legal_waits),
            "total_structural_unseen": sum(
                item["unseen"] for item in structural_waits),
            "count_basis": "public_unseen",
        })
    return hints
