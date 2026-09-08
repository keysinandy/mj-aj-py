"""引擎动作码 ↔ 平台动作 JSON 的双向映射。

吃必须显式附 "tiles"(两张手牌——服务端缺省取第一组可行顺子,多组合
场景不可靠);gang 一个动作名覆盖 an/ming/bu,由服务端按上下文推断,
反向映射(replay 用)按 阶段+手牌+副露 消歧。
"""

from ..game import (
    PASS, HU, PONG, KONG_OPEN, KONG_CLOSED_BASE, KONG_ADD_BASE, CHOW_LOW,
)
from .proto import tname, tidx


def action_to_payload(action: int, pending_tile) -> dict:
    """引擎动作码 → 平台 JSON。pending_tile 为 react 阶段的被反应牌。"""
    if action >= 0:
        return {"action": "discard", "tile": tname(action)}
    if action == HU:
        return {"action": "hu", "tile": ""}
    if action == PASS:
        return {"action": "pass", "tile": ""}
    if action == PONG:
        return {"action": "peng", "tile": tname(pending_tile)}
    if action == KONG_OPEN:
        return {"action": "gang", "tile": tname(pending_tile)}
    if CHOW_LOW - 2 <= action <= CHOW_LOW:
        pos = CHOW_LOW - action              # 被吃牌在顺子中的位置 0/1/2
        a = pending_tile - pos               # 顺子最低牌
        tiles = [x for x in (a, a + 1, a + 2) if x != pending_tile]
        return {"action": "chi", "tile": tname(pending_tile),
                "tiles": [tname(t) for t in tiles]}
    if KONG_CLOSED_BASE - 33 <= action <= KONG_CLOSED_BASE:
        return {"action": "gang", "tile": tname(KONG_CLOSED_BASE - action)}
    if KONG_ADD_BASE - 33 <= action <= KONG_ADD_BASE:
        return {"action": "gang", "tile": tname(KONG_ADD_BASE - action)}
    raise ValueError(f"非法引擎动作 {action}")


def payload_to_engine_action(payload: dict, hand34, pending_tile,
                             has_open_pong=None) -> int:
    """平台 JSON → 引擎动作码(replay 用)。

    hand34:动作者手牌计数;pending_tile:react 阶段被反应牌(draw 阶段
    传 None);has_open_pong: callable(t) → 是否有该牌的明碰(加杠消歧)。
    """
    name = payload.get("action")
    tile = tidx(payload["tile"]) if payload.get("tile") else None
    if name == "discard":
        return tile
    if name == "hu":
        return HU
    if name == "pass":
        return PASS
    if name == "peng":
        return PONG
    if name == "chi":
        if tile is None or tile != pending_tile:
            raise ValueError(f"chi 被吃牌 {tile} != pending {pending_tile}")
        tiles = sorted(tidx(t) for t in payload.get("tiles", []))
        a = min(min(tiles), tile)
        pos = tile - a
        if pos not in (0, 1, 2) or sorted((a, a + 1, a + 2)) != sorted(tiles + [tile]):
            raise ValueError(f"chi tiles {tiles} 与被吃牌 {tile} 不构成顺子")
        return CHOW_LOW - pos
    if name == "gang":
        if tile is None:
            raise ValueError("gang 缺 tile")
        if pending_tile is not None and tile == pending_tile:
            return KONG_OPEN
        if hand34 is not None and hand34[tile] == 4:
            return KONG_CLOSED_BASE - tile
        if has_open_pong is not None and has_open_pong(tile):
            return KONG_ADD_BASE - tile
        raise ValueError(f"gang 牌 {tile} 无法消歧 an/bu(手牌 {hand34[tile] if hand34 else '?'})")
    raise ValueError(f"未知动作名 {name!r}")
