"""平台协议常量:牌名映射与事件 schema 解析。

事件字段名以门户回放引擎 docs/杭州麻将对战平台_files/replay.js 与指南
v22 为准:type/seat/tile/data{tiles,kind,draw}/seq。真实平台首跑探针
(probe.py)后如有出入,只改本文件——所有事件消费方(mirror/synth/
replay/bot_client)都经由 parse_event 归一化,不直接摸原始键名。
"""

API_NAME = (
    [f"{i + 1}w" for i in range(9)]
    + [f"{i + 1}b" for i in range(9)]
    + [f"{i + 1}t" for i in range(9)]
    + list("东南西北中发白")
)  # 0-8 万 / 9-17 筒 / 18-26 条 / 27-33 字(33=白=财神)
NAME_TO_IDX = {n: i for i, n in enumerate(API_NAME)}

# 事件类型(门户 EVENT_LABEL 全集)
EV_DRAWN = "tile_drawn"
EV_DISCARDED = "tile_discarded"
EV_PASS = "pass"
EV_CHI = "chi"
EV_PENG = "peng"
EV_GANG = "gang"
EV_HU = "hu"
EV_TIMEOUT = "timeout"
EV_ROUND_ENDED = "round_ended"
EV_GAME_ENDED = "game_ended"
EVENT_TYPES = (
    EV_DRAWN, EV_DISCARDED, EV_PASS, EV_CHI, EV_PENG, EV_GANG,
    EV_HU, EV_TIMEOUT, EV_ROUND_ENDED, EV_GAME_ENDED,
)


class ProtocolError(Exception):
    """事件/快照结构与预期不符(字段名变化或未知形态)。"""


def tname(t: int) -> str:
    return API_NAME[t]


def tidx(name: str) -> int:
    try:
        return NAME_TO_IDX[name]
    except KeyError:
        raise ProtocolError(f"未知牌名 {name!r}") from None


def parse_tile(v) -> int:
    if v is None or v == "":
        raise ProtocolError(f"期望牌名,得到 {v!r}")
    return tidx(v)


def parse_event(raw: dict) -> dict:
    """原始事件 → 归一化 dict:
    {type, seat, tile(int|None), kind(str|None), tiles(list[int]|None),
     seq(int|None), ts(float|None 服务端 epoch,窗口截止锚定用),
     data(原始 data,round_ended 结算对账用)}

    chi 的 tiles = 去掉被吃牌后的两张手牌(replay.js:data.tiles 含河牌,
    fromHand = tiles − 被吃牌)。
    """
    etype = raw.get("type")
    if etype not in EVENT_TYPES:
        raise ProtocolError(f"未知事件类型 {etype!r}: {raw}")
    data = raw.get("data") or {}
    ev = {
        "type": etype,
        "seat": raw.get("seat"),
        "tile": tidx(raw["tile"]) if raw.get("tile") else None,
        "ts": raw.get("ts"),
        "kind": data.get("kind"),
        "tiles": None,
        "seq": raw.get("seq"),
        "data": data,
    }
    # ``seq`` is an inclusive event/state watermark.  For a
    # ``tile_discarded`` event its own event seq is also the authoritative
    # source-discard sequence; a snapshot's seq must never be promoted to
    # that identity.  Keep explicit aliases at the top level so window
    # identity consumers do not need to inspect raw payloads.
    for field in ("source_discard_seq", "last_discard_seq", "discard_seq"):
        if raw.get(field) is not None:
            ev[field] = raw[field]
            break
        if data.get(field) is not None:
            ev[field] = data[field]
            break
    if etype == EV_CHI:
        raw_tiles = data.get("tiles") or []
        tile = ev["tile"]
        if tile is None or len(raw_tiles) != 3:
            raise ProtocolError(f"chi 事件缺 tiles/被吃牌: {raw}")
        tiles = []
        seen_claimed = False
        for n in raw_tiles:
            t = tidx(n)
            if t == tile and not seen_claimed:
                seen_claimed = True  # 河牌那张
            else:
                tiles.append(t)
        if not seen_claimed or len(tiles) != 2:
            raise ProtocolError(f"chi tiles 应含被吃牌: {raw}")
        ev["tiles"] = sorted(tiles)
    return ev
