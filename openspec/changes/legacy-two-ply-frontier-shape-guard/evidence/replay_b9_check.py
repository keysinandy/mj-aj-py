import sys, os
sys.path.insert(0, ".")
from mj import logview
from mj.clientd.replay import online_session
from mj.game import Game
from mj.bot import choose_discard
from mj.legacy_eval import LegacyTwoPlyProfile

P = "local/games/20260921/u_9812ba08fe2f_a_a3de8f7235a2_r1_b9_t0.jsonl"
recs = logview.load_records(P)
sess = online_session(recs, session_id="x", path=P)
frames = {}
for st in sess["steps"]:
    st = st["state"]
    if st.get("seq_no") is not None:
        frames[st["seq_no"]] = st

def nm(t):
    t = int(t)
    if 0 <= t < 9: return f"{t+1}w"
    if 9 <= t < 18: return f"{t-8}p"
    if 18 <= t < 27: return f"{t-17}s"
    return {"27":"东","28":"南","29":"西","30":"北","31":"中","32":"发","33":"白"}[str(t)]

def build(seq, drawn):
    f = frames[seq]
    g = Game.__new__(Game)
    g.hands = [[int(v) for v in f["my_hand"]], [0]*34, [0]*34, [0]*34]
    melds = [[], [], [], []]
    for seat, ms in enumerate(f["melds"]):
        for m in ms:
            tiles = sorted(int(x) for x in m["tiles"])
            kind = m["kind"]
            melds[seat].append((kind, tiles[0]))
    g.melds = melds
    g.discards = [[int(x) for x in d] for d in f["discards"]]
    g.drawn = [drawn, None, None, None]
    g.freeze = 0; g.freezer = None; g.phase = "discard"; g.turn = 0
    g.dealer = int(f.get("dealer") or 0)
    g.wall = [0] * int(f.get("wall_remaining") or 0)
    g.done = False; g.pending = None
    g.chows = [0]*4; g.chain = [0]*4; g.chain_piao = [0]*4
    g.scores = list(f.get("scores") or [0,0,0,0])
    g.you_cai_bi_kao = False; g._kong_draw = False
    return g

targets = ((68, 22, 2), (107, 24, 21), (178, 18, 4))
prof = LegacyTwoPlyProfile.weighted_online()
for seq, drawn, recorded in targets:
    g = build(seq, drawn)
    action, info = choose_discard(g, 0, return_info=True, profile=prof)
    print(f"seq={seq} 记录={nm(recorded)} 现在={nm(action)} level={info.get('level')} kernel={info.get('actual_kernel')} fallback={info.get('fallback_reason')} short={ (info.get('search_metrics') or {}).get('short_circuit') }")
    for c in (info.get("candidates") or [])[:6]:
        fut = {k: v for k, v in c.items() if k.startswith("future") and v not in (None, {}, 0)}
        print("     %-3s uke=%-3s shape=%-3s %s" % (nm(c["tile"]), c.get("current_ukeire"), c.get("shape_loss"), fut or ""))
