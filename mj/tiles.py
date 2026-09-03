"""杭州麻将牌编码与工具。

34 类牌:0-8 万,9-17 筒,18-26 条,27-33 东/南/西/北/中/发/白。
白板(W=33)为财神(百搭),可替代任意牌,但不能被吃/碰/杠。
"""

W = 33

_SUIT = ("万", "筒", "条")
_HONOR = ("东", "南", "西", "北", "中", "发", "白")
_SUIT_BASE = {"m": 0, "p": 9, "s": 18}
_HONOR_IDX = {"E": 27, "S": 28, "W": 29, "N": 30, "C": 31, "F": 32, "B": 33, "w": 33}


def name(t: int) -> str:
    if t < 27:
        return f"{t % 9 + 1}{_SUIT[t // 9]}"
    return _HONOR[t - 27]


def names(counts) -> str:
    return " ".join(name(t) * n for t, n in enumerate(counts) if n)


def counts(spec: str) -> list:
    """解析牌串为 34 维计数,如 '123m456m789m123p55p w'。

    m/p/s 前置数字表示该花色牌;w 或 B 为财神(白板);
    E/S/W/N/C/F 为其余字牌(注意大写 W 是西风,小写 w 是财神)。
    """
    c = [0] * 34
    digits = ""
    for ch in spec.replace(" ", ""):
        if ch.isdigit():
            digits += ch
            continue
        if ch in _SUIT_BASE:
            if not digits:
                raise ValueError(f"花色 {ch} 前无数字")
            for d in digits:
                c[_SUIT_BASE[ch] + int(d) - 1] += 1
        elif ch in _HONOR_IDX:
            if digits:
                raise ValueError(f"字牌 {ch} 前有多余数字")
            c[_HONOR_IDX[ch]] += 1
        else:
            raise ValueError(f"非法字符 {ch}")
        digits = ""
    if digits:
        raise ValueError("末尾数字未跟花色")
    return c
