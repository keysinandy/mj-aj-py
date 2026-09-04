"""fan-calc 工具(平台指南 v6)与本引擎 scoring 的对拍。

对拍项:总倍率 + 番型明细标签。手留白板 + 链内飘出 = 4 的口径为
v6 修复点(此前工具只认手留 4 张),本引擎自始按"手留+飘出"实现。
"""

import json
import ssl
import sys
import time
import urllib.request

sys.path.insert(0, ".")
from mj.tiles import W, counts, name
from mj.scoring import hand_multiplier, settle
from mj.win import is_win

BASE = "https://10.240.169.190:18080/portal/api/tools/fan-calc"
# 内网平台自签证书:仅对本脚本这一次请求关闭校验,不改动全局 SSL 配置
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

API_NAME = {}
for t in range(9):
    API_NAME[t] = f"{t + 1}w"
for t in range(9, 18):
    API_NAME[t] = f"{t - 8}b"
for t in range(18, 27):
    API_NAME[t] = f"{t - 17}t"
for t in range(27, 34):
    API_NAME[t] = "东南西北中发白"[t - 27]


def api_tiles(c34):
    return [API_NAME[t] for t in range(34) for _ in range(c34[t])]


def call_fan_calc(hand13, draw, cc, cp):
    body = {
        "hand": api_tiles(hand13),
        "draw": API_NAME[draw],
        "chain": {"count": cc, "piao": cp},
    }
    req = urllib.request.Request(
        BASE, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    for attempt in range(3):
        time.sleep(0.15)  # 工具限流 10 次/秒/IP
        try:
            with urllib.request.urlopen(req, context=CTX) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            resp = json.loads(e.read())
            if "频繁" not in resp.get("message", "") or attempt == 2:
                return resp
            time.sleep(1.0)


# (说明, 13张站立手, 摸牌, 链次数, 链内飘数)
CASES = [
    ("三豪华七对+三财飘+4白板+爆头", "1111m2222m3333m w", 26, 3, 3),
    ("豪华七对+杠飘链3+手留2白+飘2", "1111m2222m3333m w", W, 3, 2),
    ("平胡+连杠6", "1122334455667m", 0, 6, 0),
    ("平胡爆头+杠飘链6+4白板(手留1+飘3)", "123m456m789m123p w", 26, 6, 3),
    ("平胡", "123m456m789m123p5p", 13, 0, 0),
    ("杠开", "123m456m789m123p5p", 13, 1, 0),
    ("连杠2", "123m456m789m123p5p", 13, 2, 0),
    ("爆头平胡(无链)", "123m456m789m123p w", 26, 0, 0),
    ("爆头+杠开", "123m456m789m123p w", 26, 1, 0),
    ("恰手留4白板(非爆头)", "123m456m789m wwww", 12, 0, 0),
    ("恰手留4白板+杠开", "123m456m789m wwww", 12, 1, 0),
    ("七对(无豪华)", "1122334455667m", 0, 0, 0),
    ("豪华七对x1", "1111m22334455 6m", 5, 0, 0),
    ("豪华七对x2", "1111m2222m3344 5m", 4, 0, 0),
    ("双财飘+爆头", "123m456m789m123p w", W, 2, 2),
    ("财飘+杠(杠飘链2)+爆头", "123m456m789m123p w", W, 2, 1),
    ("非爆头平胡摸白成胡", "123m456m789m123p 5p", W, 0, 0),
    ("财神补雀头平胡", "123m456m789m1234p", W, 0, 0),
    ("非胡:垃圾手", "1m3m5m7m9m2p4p6p8p1s3s5s7s", 8, 0, 0),
    ("非胡:差一对", "123m456m789m123p 5s", 23, 0, 0),
]


def main():
    fails = 0
    for desc, spec, draw, cc, cp in CASES:
        hand13 = counts(spec)
        assert sum(hand13) == 13, desc
        c14 = list(hand13)
        c14[draw] += 1
        ours_hu = is_win(c14)
        ours_mult, ours_parts = hand_multiplier(c14, hand13, 0, cc, cp)
        try:
            resp = call_fan_calc(hand13, draw, cc, cp)
        except urllib.error.HTTPError as e:
            resp = json.loads(e.read())
        if resp.get("code"):
            print(f"[API 400] {desc}: {resp.get('message', '')[:60]}")
            fails += 1
            continue
        api_hu = resp["hu"]
        api_mult = resp["fan"]
        api_parts = resp["detail"]
        checks = [
            ("hu", ours_hu, api_hu),
            ("mult", ours_mult if ours_hu else 0, api_mult),
            ("parts", ours_parts if ours_hu else None, api_parts),
        ]
        if ours_hu:
            dealer_pay = settle(0, 0, ours_mult)
            free_pay = settle(2, 0, ours_mult)
            # API 的 lose 为绝对值(正数);非庄家胡时顺序 [庄, 闲, 闲],
            # 与座次无关,故按绝对值排序比较
            checks += [
                ("score.dealer", sorted(map(abs, dealer_pay)),
                 sorted([resp["scores"]["dealer_hu"]["win"]] + resp["scores"]["dealer_hu"]["lose"])),
                ("score.nondealer", sorted(map(abs, free_pay)),
                 sorted([resp["scores"]["nondealer_hu"]["win"]] + resp["scores"]["nondealer_hu"]["lose"])),
            ]
        bad = [f"{k}: 引擎={o!r} 平台={a!r}" for k, o, a in checks if o != a]
        if bad:
            fails += 1
            print(f"[MISMATCH] {desc}")
            for b in bad:
                print(f"    {b}")
        else:
            print(f"[OK] {desc}: hu={ours_hu} mult={ours_mult} parts={ours_parts}")
    print(f"\n{'全部一致' if fails == 0 else f'{fails} 个用例不一致'}")


if __name__ == "__main__":
    main()
